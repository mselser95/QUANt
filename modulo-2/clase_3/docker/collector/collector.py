"""collector: on every new Polygon block, read 8 pools in ONE call (Multicall3) and store them in Postgres.

Pools (all paired so that the same block gives a consistent snapshot):
  Uniswap-V2 style (SushiSwap, QuickSwap): WETH/USDC.e, WPOL/USDC.e, WETH/WPOL   -> getReserves()
  Uniswap V3 (fee 0.05% and 0.3%):         WETH/USDC.e                           -> slot0() + liquidity()
"""
import os
import time
from datetime import datetime, timezone

import psycopg
from eth_abi import decode, encode
from web3 import Web3
from web3.exceptions import Web3Exception
from web3.middleware import ExtraDataToPOAMiddleware

RPC_URL = os.environ.get("RPC_URL", "https://polygon-bor-rpc.publicnode.com")
DATABASE_URL = os.environ["DATABASE_URL"]
POLL_SECONDS = 1.0

MULTICALL3 = "0xcA11bde05977b3631167028862bE2a173976CA11"
V3_FACTORY = "0x1F98431c8aD98523631AE4a59f267346ea31F984"
V2_FACTORIES = {
    "SushiSwap": "0xc35DADB65012eC5796536bD9864eD8773aBc74C4",
    "QuickSwap": "0x5757371414417b8C6CAad45bAeF941aBc7d3Ab32",
}
V2_FEE_PPM = 3000                 # 0.3%
V3_FEES_PPM = [500, 3000]         # 0.05% and 0.3%

# symbol -> (address, decimals). Same tokens as univ2.py
TOKENS = {
    "USDC.e": ("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174", 6),
    "WETH": ("0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619", 18),
    "WPOL": ("0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270", 18),
}
V2_PAIRS = [("WETH", "USDC.e"), ("WPOL", "USDC.e"), ("WETH", "WPOL")]
V3_PAIRS = [("WETH", "USDC.e")]
HEALTH_FILE = "/tmp/healthy"
Q96 = 2 ** 96


# ---------------------------------------------------------------- low level eth_call helpers

def selector(signature: str) -> bytes:
    # First 4 bytes of keccak("name(types)") identify the function
    return Web3.keccak(text=signature)[:4]


def eth_call(w3, to: str, signature: str, arg_types: list, args: list, return_types: list):
    # Plain eth_call of a view function; returns the decoded values
    data = selector(signature) + encode(arg_types, args)
    raw = w3.eth.call({"to": to, "data": data})
    return decode(return_types, raw)


def symbol_of(address: str) -> str:
    for symbol, (token_address, _) in TOKENS.items():
        if token_address.lower() == address.lower():
            return symbol
    raise ValueError("unknown token " + address)


# ---------------------------------------------------------------- pool discovery (once, at startup)

def make_pool(dex: str, pair: str, address: str, kind: str, fee_ppm: int, token0: str, token1: str) -> dict:
    return {"dex": dex, "pair": pair, "address": address, "kind": kind, "fee_ppm": fee_ppm,
            "token0": token0, "token1": token1}


def resolve_pool(w3, dex: str, pair: str, address: str, kind: str, fee_ppm: int) -> dict:
    # token0 never changes, so we ask it once and keep it
    (token0_address,) = eth_call(w3, address, "token0()", [], [], ["address"])
    (token1_address,) = eth_call(w3, address, "token1()", [], [], ["address"])
    return make_pool(dex, pair, address, kind, fee_ppm, symbol_of(token0_address), symbol_of(token1_address))


def discover_pools(w3) -> list:
    pools = []
    for dex, factory in V2_FACTORIES.items():
        for symbol_a, symbol_b in V2_PAIRS:
            # factory.getPair(tokenA, tokenB) -> pair address
            (pair_address,) = eth_call(w3, factory, "getPair(address,address)", ["address", "address"],
                                       [TOKENS[symbol_a][0], TOKENS[symbol_b][0]], ["address"])
            pair = symbol_a + "/" + symbol_b
            pools.append(resolve_pool(w3, dex, pair, Web3.to_checksum_address(pair_address), "v2", V2_FEE_PPM))
    for fee_ppm in V3_FEES_PPM:
        for symbol_a, symbol_b in V3_PAIRS:
            # factory.getPool(tokenA, tokenB, fee) -> pool address
            (pool_address,) = eth_call(w3, V3_FACTORY, "getPool(address,address,uint24)",
                                       ["address", "address", "uint24"],
                                       [TOKENS[symbol_a][0], TOKENS[symbol_b][0], fee_ppm], ["address"])
            dex = "UniV3-" + str(fee_ppm / 10000) + "%"
            pair = symbol_a + "/" + symbol_b
            pools.append(resolve_pool(w3, dex, pair, Web3.to_checksum_address(pool_address), "v3", fee_ppm))
    return pools


# ---------------------------------------------------------------- one Multicall3 call per block

def build_calls(pools: list) -> list:
    # The calldata of every pool never changes, so it is built once.
    # Each entry is a Multicall3 Call3 struct: (target, allowFailure, callData).
    calls = []
    for pool in pools:
        if pool["kind"] == "v2":
            calls.append((pool["address"], False, selector("getReserves()")))
        else:
            calls.append((pool["address"], False, selector("slot0()")))
            calls.append((pool["address"], False, selector("liquidity()")))
    # Last call: the timestamp of the block the multicall is executed in
    calls.append((MULTICALL3, False, selector("getCurrentBlockTimestamp()")))
    return calls


def run_multicall(w3, calls: list, block_number: int) -> list:
    # aggregate3(Call3[]) returns Result[] = list of (success, returnData). allowFailure=False,
    # so if any pool call fails the whole call reverts and we try again.
    data = selector("aggregate3((address,bool,bytes)[])") + encode(["(address,bool,bytes)[]"], [calls])
    raw = w3.eth.call({"to": MULTICALL3, "data": data}, block_identifier=block_number)
    (results,) = decode(["(bool,bytes)[]"], raw)
    return results


def decode_pool_row(pool: dict, results: list, position: int) -> tuple:
    # Returns (reserve0, reserve1, sqrt_price, liquidity, tick, next position in results)
    if pool["kind"] == "v2":
        reserve0, reserve1, _ = decode(["uint112", "uint112", "uint32"], results[position][1])
        return reserve0, reserve1, None, None, None, position + 1
    slot0 = decode(["uint160", "int24", "uint16", "uint16", "uint16", "uint8", "bool"], results[position][1])
    sqrt_price, tick = slot0[0], slot0[1]
    (liquidity,) = decode(["uint128"], results[position + 1][1])
    # Virtual reserves of a V3 pool: x = L / sqrtP and y = L * sqrtP (raw units).
    # They behave like a V2 pool, but only while the price stays inside the current tick range.
    reserve0 = liquidity * Q96 // sqrt_price
    reserve1 = liquidity * sqrt_price // Q96
    return reserve0, reserve1, sqrt_price, liquidity, tick, position + 2


# ---------------------------------------------------------------- prices

def amount_in_tokens(raw: int, symbol: str):
    return raw / 10 ** TOKENS[symbol][1]


def mid_price_usd(pool: dict, reserve0: int, reserve1: int, pol_price_usd: float):
    # USD price of the non-USDC.e token = USDC.e amount / token amount.
    amount0 = amount_in_tokens(reserve0, pool["token0"])
    amount1 = amount_in_tokens(reserve1, pool["token1"])
    if pool["token0"] == "USDC.e":
        return amount0 / amount1
    if pool["token1"] == "USDC.e":
        return amount1 / amount0
    # WETH/WPOL pool: price of WETH in WPOL times the POL price in USD
    if pool["token0"] == "WETH":
        return amount1 / amount0 * pol_price_usd
    return amount0 / amount1 * pol_price_usd


def find_pol_price(pools: list, decoded: list) -> float:
    # POL price = the QuickSwap WPOL/USDC.e pool
    for index, pool in enumerate(pools):
        if pool["dex"] == "QuickSwap" and pool["pair"] == "WPOL/USDC.e":
            return mid_price_usd(pool, decoded[index][0], decoded[index][1], 0.0)
    raise ValueError("QuickSwap WPOL/USDC.e pool not found")


# ---------------------------------------------------------------- storage

INSERT_BLOCK = """
INSERT INTO blocks (block_number, block_time, gas_price_gwei, pol_price_usd)
VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING
"""
INSERT_POOL = """
INSERT INTO pool_states (block_number, block_time, dex, pair, pool, kind, token0, token1, fee_ppm,
                         reserve0, reserve1, sqrt_price, liquidity, tick, price_usd)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING
"""


def store_block(conn, w3, pools: list, calls: list, block_number: int) -> None:
    results = run_multicall(w3, calls, block_number)
    gas_price_gwei = w3.eth.gas_price / 1e9   # latest gas price (not tied to the block, good enough)

    # Decode every pool, then the timestamp (last result of the multicall)
    decoded = []
    position = 0
    for pool in pools:
        reserve0, reserve1, sqrt_price, liquidity, tick, position = decode_pool_row(pool, results, position)
        decoded.append((reserve0, reserve1, sqrt_price, liquidity, tick))
    (timestamp,) = decode(["uint256"], results[position][1])
    block_time = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    pol_price_usd = find_pol_price(pools, decoded)

    with conn.cursor() as cursor:
        cursor.execute(INSERT_BLOCK, (block_number, block_time, gas_price_gwei, pol_price_usd))
        for index, pool in enumerate(pools):
            reserve0, reserve1, sqrt_price, liquidity, tick = decoded[index]
            price = mid_price_usd(pool, reserve0, reserve1, pol_price_usd)
            cursor.execute(INSERT_POOL, (block_number, block_time, pool["dex"], pool["pair"], pool["address"],
                                         pool["kind"], pool["token0"], pool["token1"], pool["fee_ppm"],
                                         reserve0, reserve1, sqrt_price, liquidity, tick, price))
    conn.commit()


# ---------------------------------------------------------------- main loop

def run() -> None:
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    # Polygon is a PoA chain: its block headers have extra data that web3 must be told to accept
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    pools = discover_pools(w3)
    calls = build_calls(pools)
    print("pools:", [pool["dex"] + " " + pool["pair"] + " " + pool["address"] for pool in pools], flush=True)

    with psycopg.connect(DATABASE_URL) as conn:
        last_block = 0
        while True:
            block_number = w3.eth.block_number
            if block_number == last_block:
                time.sleep(POLL_SECONDS)
                continue
            store_block(conn, w3, pools, calls, block_number)
            last_block = block_number
            open(HEALTH_FILE, "w").close()   # touch: the container healthcheck looks at this file
            print("stored block", block_number, flush=True)


def main() -> None:
    # The ONLY try/except of the service: if the RPC node or the database drops the connection,
    # wait and start again (pools are re-discovered, which is cheap).
    while True:
        try:
            run()
        except (OSError, Web3Exception, psycopg.OperationalError) as error:
            print("connection problem:", repr(error), "- retrying in 5 s", flush=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
