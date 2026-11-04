"""detector: for every block stored by the collector, evaluate the arbitrage cycles that start and end in USDC.e.

Maths (notebook sections 5b/5d). A cycle is a list of legs, each leg a pool seen as (r_in, r_out, gamma)
with gamma = 1 - fee (0.997 for V2, 0.9995 / 0.997 for V3).
  1. Fold the cycle into ONE equivalent pool with reserves (E_in, E_out).
  2. Optimal input:        dx* = (sqrt(gamma_1 * E_in * E_out) - E_in) / gamma_1
  3. There is an opportunity if and only if  gamma_1 * ... * gamma_n * prod(r_out / r_in) > 1.

IMPORTANT for V3 legs: a V3 pool is only a "V2 pool with virtual reserves" while the trade does not move the
price out of the current tick range. We ignore the range edge (in-range approximation), so dx* on a V3 leg is
an upper bound that must be checked against the real tick spacing before trusting it.
"""
import math
import os
import time

import psycopg

DATABASE_URL = os.environ["DATABASE_URL"]
POLL_SECONDS = 1.0
HEALTH_FILE = "/tmp/healthy"

FLASH_LOAN_FEE = 0.0005       # Aave V3 premium: 0.05% of the borrowed amount
GAS_UNITS_2_LEGS = 350_000    # flash loan + 2 swaps (same estimate as the notebook)
GAS_UNITS_PER_EXTRA_LEG = 100_000

DECIMALS = {"USDC.e": 6, "WETH": 18, "WPOL": 18}
# Venues: V2 style pools exist for every pair, V3 pools only for WETH/USDC.e
TRIANGLE_DEXES = ["SushiSwap", "QuickSwap"]


# ---------------------------------------------------------------- reading the stored state

def new_blocks(conn, last_block: int) -> list:
    query = "SELECT DISTINCT block_number FROM pool_states WHERE block_number > %s ORDER BY 1 LIMIT 50"
    with conn.cursor() as cursor:
        cursor.execute(query, (last_block,))
        rows = cursor.fetchall()
    blocks = []
    for row in rows:
        blocks.append(row[0])
    return blocks


def load_block(conn, block_number: int) -> dict:
    # Returns {"pools": {(dex, pair): row}, "gas_price_gwei": ..., "pol_price_usd": ..., "block_time": ...}
    state = {"pools": {}}
    query = """SELECT dex, pair, token0, token1, fee_ppm, reserve0, reserve1
               FROM pool_states WHERE block_number = %s"""
    with conn.cursor() as cursor:
        cursor.execute(query, (block_number,))
        for dex, pair, token0, token1, fee_ppm, reserve0, reserve1 in cursor.fetchall():
            state["pools"][(dex, pair)] = {"token0": token0, "token1": token1, "fee_ppm": fee_ppm,
                                           "reserve0": float(reserve0), "reserve1": float(reserve1)}
        cursor.execute("SELECT block_time, gas_price_gwei, pol_price_usd FROM blocks WHERE block_number = %s",
                       (block_number,))
        block_time, gas_price_gwei, pol_price_usd = cursor.fetchone()
    state["block_time"] = block_time
    state["gas_price_gwei"] = float(gas_price_gwei)
    state["pol_price_usd"] = float(pol_price_usd)
    return state


# ---------------------------------------------------------------- legs and cycles

def make_leg(state: dict, dex: str, pair: str, token_in: str):
    # Returns (r_in, r_out, gamma) in TOKEN UNITS (not raw), or None if the pool does not exist
    pool = state["pools"].get((dex, pair))
    if pool is None:
        return None
    amount0 = pool["reserve0"] / 10 ** DECIMALS[pool["token0"]]
    amount1 = pool["reserve1"] / 10 ** DECIMALS[pool["token1"]]
    gamma = 1 - pool["fee_ppm"] / 1_000_000
    if pool["token0"] == token_in:
        return (amount0, amount1, gamma)
    return (amount1, amount0, gamma)


def pair_name(token_a: str, token_b: str) -> str:
    # Pools are stored as "<token>/USDC.e" or "WETH/WPOL"
    if token_a == "USDC.e":
        return token_b + "/USDC.e"
    if token_b == "USDC.e":
        return token_a + "/USDC.e"
    return "WETH/WPOL"


def swap_out(amount_in: float, leg: tuple) -> float:
    # Constant product swap with fee: out = gamma*in*r_out / (r_in + gamma*in)  (getAmountOut, float version)
    r_in, r_out, gamma = leg
    return gamma * amount_in * r_out / (r_in + gamma * amount_in)


def cycle_out(amount_in: float, legs: list) -> float:
    # Swap by swap through the whole cycle
    amount = amount_in
    for leg in legs:
        amount = swap_out(amount, leg)
    return amount


def marginal_product(legs: list) -> float:
    # gamma_1*...*gamma_n * prod(r_out / r_in): the price of the first dust of input after going round
    product = 1.0
    for r_in, r_out, gamma in legs:
        product = product * gamma * r_out / r_in
    return product


def fold(legs: list) -> tuple:
    # Fold the legs into one equivalent pool (E_in, E_out). Each appended leg uses ITS OWN gamma.
    e_in, e_out, _ = legs[0]
    for r_in, r_out, gamma in legs[1:]:
        denominator = r_in + gamma * e_out
        e_in, e_out = e_in * r_in / denominator, gamma * e_out * r_out / denominator
    return e_in, e_out


def optimal_input(legs: list) -> float:
    # dx* = (sqrt(gamma_1 * E_in * E_out) - E_in) / gamma_1 ; the first gamma stays with the folded pool
    e_in, e_out = fold(legs)
    gamma_first = legs[0][2]
    return (math.sqrt(gamma_first * e_in * e_out) - e_in) / gamma_first


def gas_cost_usdc(state: dict, n_legs: int) -> float:
    gas_units = GAS_UNITS_2_LEGS + GAS_UNITS_PER_EXTRA_LEG * (n_legs - 2)
    pol_spent = gas_units * state["gas_price_gwei"] * 1e9 / 1e18
    return pol_spent * state["pol_price_usd"]


def evaluate(state: dict, name: str, legs: list) -> dict:
    n_legs = len(legs)
    product = marginal_product(legs)
    gas_usdc = gas_cost_usdc(state, n_legs)
    row = {"cycle": name, "n_legs": n_legs, "marginal_product": product, "gap_bps": (product - 1) * 10000,
           "dx_star_usdc": 0.0, "gross_usdc": 0.0, "flash_fee_usdc": 0.0, "gas_usdc": gas_usdc,
           "net_usdc": 0.0, "profitable": False}
    if product <= 1:
        return row
    dx_star = optimal_input(legs)
    gross = cycle_out(dx_star, legs) - dx_star
    flash_fee = FLASH_LOAN_FEE * dx_star
    row["dx_star_usdc"] = dx_star
    row["gross_usdc"] = gross
    row["flash_fee_usdc"] = flash_fee
    row["net_usdc"] = gross - flash_fee - gas_usdc
    row["profitable"] = row["net_usdc"] > 0
    return row


def venues_for(state: dict, pair: str) -> list:
    venues = []
    for dex, pool_pair in state["pools"]:
        if pool_pair == pair:
            venues.append(dex)
    venues.sort()
    return venues


def two_pool_cycles(state: dict) -> list:
    # USDC.e -> X on venue A, X -> USDC.e on venue B, for every ordered pair A != B
    rows = []
    for token in ["WETH", "WPOL"]:
        pair = pair_name(token, "USDC.e")
        venues = venues_for(state, pair)
        for buy_dex in venues:
            for sell_dex in venues:
                if buy_dex == sell_dex:
                    continue
                legs = [make_leg(state, buy_dex, pair, "USDC.e"), make_leg(state, sell_dex, pair, token)]
                name = "USDC.e→" + token + " (" + buy_dex + ") → USDC.e (" + sell_dex + ")"
                rows.append(evaluate(state, name, legs))
    return rows


def triangle_cycles(state: dict) -> list:
    # USDC.e -> WETH -> WPOL -> USDC.e and the reverse, each leg on SushiSwap or QuickSwap (2^3 = 8 per path)
    rows = []
    paths = [["USDC.e", "WETH", "WPOL", "USDC.e"], ["USDC.e", "WPOL", "WETH", "USDC.e"]]
    for path in paths:
        for dex1 in TRIANGLE_DEXES:
            for dex2 in TRIANGLE_DEXES:
                for dex3 in TRIANGLE_DEXES:
                    dexes = [dex1, dex2, dex3]
                    legs = []
                    for index in range(3):
                        token_in = path[index]
                        pair = pair_name(token_in, path[index + 1])
                        legs.append(make_leg(state, dexes[index], pair, token_in))
                    name = path[0] + "→" + path[1] + " (" + dex1 + ") → " + path[2] + " (" + dex2 + ") → " \
                        + path[3] + " (" + dex3 + ")"
                    rows.append(evaluate(state, name, legs))
    return rows


# ---------------------------------------------------------------- storage and main loop

INSERT_OPPORTUNITY = """
INSERT INTO opportunities (block_number, block_time, cycle, n_legs, marginal_product, gap_bps, dx_star_usdc,
                           gross_usdc, flash_fee_usdc, gas_usdc, net_usdc, profitable)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING
"""


def process_block(conn, block_number: int) -> None:
    state = load_block(conn, block_number)
    rows = two_pool_cycles(state) + triangle_cycles(state)
    with conn.cursor() as cursor:
        for row in rows:
            cursor.execute(INSERT_OPPORTUNITY, (
                block_number, state["block_time"], row["cycle"], row["n_legs"], row["marginal_product"],
                row["gap_bps"], row["dx_star_usdc"], row["gross_usdc"], row["flash_fee_usdc"],
                row["gas_usdc"], row["net_usdc"], row["profitable"]))
    conn.commit()
    open(HEALTH_FILE, "w").close()   # touch: the container healthcheck looks at this file
    print("block", block_number, ":", len(rows), "cycles evaluated", flush=True)


def run() -> None:
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT COALESCE(MAX(block_number), 0) FROM opportunities")
            last_block = cursor.fetchone()[0]
        while True:
            blocks = new_blocks(conn, last_block)
            if len(blocks) == 0:
                time.sleep(POLL_SECONDS)
                continue
            for block_number in blocks:
                process_block(conn, block_number)
                last_block = block_number


def main() -> None:
    # The ONLY try/except of the service: if the database drops the connection, wait and reconnect.
    while True:
        try:
            run()
        except psycopg.OperationalError as error:
            print("database connection problem:", repr(error), "- retrying in 5 s", flush=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
