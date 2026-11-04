"""Uniswap V3 helpers for the class 6 notebook (section 5e).

A V3 pool keeps its liquidity in ranges between ticks. Inside one range it behaves exactly like a
V2 pool whose reserves are "virtual": x = L / sqrt(P) and y = L * sqrt(P). When a trade reaches the
next initialized tick, the active liquidity L changes and the trade continues on the next range.

This module reads a pool snapshot (price, active liquidity and the initialized ticks around the
price) in a few Multicall3 calls pinned to ONE block, and simulates swaps range by range with the
same math as the Uniswap V3 contracts (in floating point).
"""
import math

from eth_abi import decode
from web3 import Web3

MULTICALL3 = Web3.to_checksum_address("0xcA11bde05977b3631167028862bE2a173976CA11")   # same address on every chain
V3_FACTORY = Web3.to_checksum_address("0x1F98431c8aD98523631AE4a59f267346ea31F984")
QUOTER_V2 = Web3.to_checksum_address("0x61fFE014bA17989E743c5F6cB21bF9697530B21e")    # Polygon

MULTICALL_ABI = [{"name": "aggregate3", "type": "function", "stateMutability": "payable",
                  "inputs": [{"name": "calls", "type": "tuple[]", "components": [
                      {"name": "target", "type": "address"}, {"name": "allowFailure", "type": "bool"},
                      {"name": "callData", "type": "bytes"}]}],
                  "outputs": [{"name": "returnData", "type": "tuple[]", "components": [
                      {"name": "success", "type": "bool"}, {"name": "returnData", "type": "bytes"}]}]}]
FACTORY_ABI = [{"name": "getPool", "type": "function", "stateMutability": "view",
                "inputs": [{"name": "tokenA", "type": "address"}, {"name": "tokenB", "type": "address"},
                           {"name": "fee", "type": "uint24"}],
                "outputs": [{"name": "", "type": "address"}]}]
POOL_ABI = [
    {"name": "slot0", "type": "function", "stateMutability": "view", "inputs": [],
     "outputs": [{"name": "sqrtPriceX96", "type": "uint160"}, {"name": "tick", "type": "int24"},
                 {"name": "observationIndex", "type": "uint16"}, {"name": "observationCardinality", "type": "uint16"},
                 {"name": "observationCardinalityNext", "type": "uint16"}, {"name": "feeProtocol", "type": "uint8"},
                 {"name": "unlocked", "type": "bool"}]},
    {"name": "liquidity", "type": "function", "stateMutability": "view", "inputs": [],
     "outputs": [{"name": "", "type": "uint128"}]},
    {"name": "tickSpacing", "type": "function", "stateMutability": "view", "inputs": [],
     "outputs": [{"name": "", "type": "int24"}]},
    {"name": "fee", "type": "function", "stateMutability": "view", "inputs": [],
     "outputs": [{"name": "", "type": "uint24"}]},
    {"name": "token0", "type": "function", "stateMutability": "view", "inputs": [],
     "outputs": [{"name": "", "type": "address"}]},
    {"name": "tickBitmap", "type": "function", "stateMutability": "view", "inputs": [{"name": "wordPosition", "type": "int16"}],
     "outputs": [{"name": "", "type": "uint256"}]},
    {"name": "ticks", "type": "function", "stateMutability": "view", "inputs": [{"name": "tick", "type": "int24"}],
     "outputs": [{"name": "liquidityGross", "type": "uint128"}, {"name": "liquidityNet", "type": "int128"},
                 {"name": "feeGrowthOutside0X128", "type": "uint256"}, {"name": "feeGrowthOutside1X128", "type": "uint256"},
                 {"name": "tickCumulativeOutside", "type": "int56"}, {"name": "secondsPerLiquidityOutsideX128", "type": "uint160"},
                 {"name": "secondsOutside", "type": "uint32"}, {"name": "initialized", "type": "bool"}]},
]
QUOTER_ABI = [{"name": "quoteExactInputSingle", "type": "function", "stateMutability": "nonpayable",
               "inputs": [{"name": "params", "type": "tuple", "components": [
                   {"name": "tokenIn", "type": "address"}, {"name": "tokenOut", "type": "address"},
                   {"name": "amountIn", "type": "uint256"}, {"name": "fee", "type": "uint24"},
                   {"name": "sqrtPriceLimitX96", "type": "uint160"}]}],
               "outputs": [{"name": "amountOut", "type": "uint256"}, {"name": "sqrtPriceX96After", "type": "uint160"},
                           {"name": "initializedTicksCrossed", "type": "uint32"}, {"name": "gasEstimate", "type": "uint256"}]}]

SLOT0_TYPES = ["uint160", "int24", "uint16", "uint16", "uint16", "uint8", "bool"]
TICK_TYPES = ["uint128", "int128", "uint256", "uint256", "int56", "uint160", "uint32", "bool"]
QUOTE_TYPES = ["uint256", "uint160", "uint32", "uint256"]
CHUNK = 150   # calls per multicall: public nodes cap the size of one response


def multicall(w3, calls, block):
    """Run many read calls in Multicall3 at one block. calls: list of (contract, function, args, output types)."""
    multicall_contract = w3.eth.contract(address=MULTICALL3, abi=MULTICALL_ABI)
    decoded = []
    for start in range(0, len(calls), CHUNK):
        part = calls[start:start + CHUNK]
        payload = []
        for contract, function, args, _ in part:
            payload.append((contract.address, False, contract.encode_abi(function, args)))
        results = multicall_contract.functions.aggregate3(payload).call(block_identifier=block)
        for call, result in zip(part, results):
            output_types = call[3]
            decoded.append(decode(output_types, result[1]))
    return decoded


def sqrt_price_at_tick(tick):
    return 1.0001 ** (tick / 2)


def get_pool_address(w3, token_a, token_b, fee):
    factory = w3.eth.contract(address=V3_FACTORY, abi=FACTORY_ABI)
    return factory.functions.getPool(token_a, token_b, fee).call()


def load_pool(w3, address, block, quotes=(), words=3):
    """Snapshot of a V3 pool at `block`: price, active liquidity, fee, token0 and the initialized ticks
    in `words` bitmap words on each side of the price (each word = 256 tick spacings).

    `quotes` = list of (token_in, token_out, amount_in) priced by the QuoterV2 in the SAME block, to check
    the simulator. Returns (pool, quoter_outputs)."""
    pool_contract = w3.eth.contract(address=Web3.to_checksum_address(address), abi=POOL_ABI)
    quoter = w3.eth.contract(address=QUOTER_V2, abi=QUOTER_ABI)
    slot0, spacing, fee, token0 = multicall(w3, [
        (pool_contract, "slot0", [], SLOT0_TYPES),
        (pool_contract, "tickSpacing", [], ["int24"]),
        (pool_contract, "fee", [], ["uint24"]),
        (pool_contract, "token0", [], ["address"]),
    ], block)
    current_tick = slot0[1]
    spacing = spacing[0]
    center_word = math.floor(current_tick / spacing) >> 8   # which 256-bit word of the bitmap holds the price

    word_ids = list(range(center_word - words, center_word + words + 1))
    bitmap_calls = []
    for word in word_ids:
        bitmap_calls.append((pool_contract, "tickBitmap", [word], ["uint256"]))
    initialized = []
    for word, result in zip(word_ids, multicall(w3, bitmap_calls, block)):
        bitmap = result[0]
        for bit in range(256):
            if bitmap >> bit & 1:   # bit set = this tick has liquidity starting or ending there
                initialized.append((word * 256 + bit) * spacing)

    calls = [(pool_contract, "liquidity", [], ["uint128"])]
    for tick in initialized:
        calls.append((pool_contract, "ticks", [tick], TICK_TYPES))
    for token_in, token_out, amount_in in quotes:
        calls.append((quoter, "quoteExactInputSingle", [(token_in, token_out, amount_in, fee[0], 0)], QUOTE_TYPES))
    results = multicall(w3, calls, block)

    liquidity_net = {}
    for tick, result in zip(initialized, results[1:1 + len(initialized)]):
        liquidity_net[tick] = result[1]
    pool = {
        "address": pool_contract.address,
        "fee": fee[0],
        "gamma": 1 - fee[0] / 1e6,
        "token0": Web3.to_checksum_address(token0[0]),
        "sqrt_price": slot0[0] / 2**96,     # sqrt(price of token0 in token1), raw units
        "tick": current_tick,
        "liquidity": results[0][0],
        "liquidity_net": liquidity_net,
        "ticks": sorted(initialized),
    }
    quoter_outputs = results[1 + len(initialized):]
    return pool, quoter_outputs


def virtual_reserves(pool):
    """The V2 pool that behaves like this V3 pool inside its current range: (x, y) in raw units."""
    return pool["liquidity"] / pool["sqrt_price"], pool["liquidity"] * pool["sqrt_price"]


def next_tick(pool, zero_for_one):
    """Next initialized tick in the direction of the trade (down when selling token0, up when selling token1)."""
    if zero_for_one:
        candidates = [tick for tick in pool["ticks"] if tick <= pool["tick"]]
        if not candidates:
            raise ValueError("no loaded tick below the price: load more bitmap words")
        return max(candidates)
    candidates = [tick for tick in pool["ticks"] if tick > pool["tick"]]
    if not candidates:
        raise ValueError("no loaded tick above the price: load more bitmap words")
    return min(candidates)


def in_range_capacity(pool, zero_for_one):
    """Largest input (raw, fee included) that stays inside the current range."""
    sqrt_target = sqrt_price_at_tick(next_tick(pool, zero_for_one))
    if zero_for_one:
        net_input = pool["liquidity"] * (1 / sqrt_target - 1 / pool["sqrt_price"])
    else:
        net_input = pool["liquidity"] * (sqrt_target - pool["sqrt_price"])
    return net_input / pool["gamma"]


def swap(pool, amount_in, zero_for_one):
    """Exact-input swap range by range. Returns (amount_out, pool_after, ticks_crossed); raw units.

    zero_for_one=True sells token0 (price goes down), False sells token1 (price goes up)."""
    remaining = amount_in * pool["gamma"]   # the fee stays in the pool
    sqrt_price = pool["sqrt_price"]
    liquidity = pool["liquidity"]
    tick = pool["tick"]
    amount_out = 0.0
    crossed = 0
    while True:
        state = dict(pool)
        state["sqrt_price"], state["liquidity"], state["tick"] = sqrt_price, liquidity, tick
        boundary = next_tick(state, zero_for_one)
        sqrt_target = sqrt_price_at_tick(boundary)
        if zero_for_one:
            needed = liquidity * (1 / sqrt_target - 1 / sqrt_price)   # token0 to reach the boundary
        else:
            needed = liquidity * (sqrt_target - sqrt_price)           # token1 to reach the boundary
        if remaining < needed:   # the trade ends inside this range
            if zero_for_one:
                new_sqrt_price = 1 / (1 / sqrt_price + remaining / liquidity)
                amount_out += liquidity * (sqrt_price - new_sqrt_price)
            else:
                new_sqrt_price = sqrt_price + remaining / liquidity
                amount_out += liquidity * (1 / sqrt_price - 1 / new_sqrt_price)
            state["sqrt_price"] = new_sqrt_price
            state["tick"] = math.floor(math.log(new_sqrt_price ** 2, 1.0001))
            return amount_out, state, crossed
        # use up this range, then cross the tick: the liquidity of the positions that start/end there changes
        if zero_for_one:
            amount_out += liquidity * (sqrt_price - sqrt_target)
            liquidity -= pool["liquidity_net"][boundary]
            tick = boundary - 1
        else:
            amount_out += liquidity * (1 / sqrt_price - 1 / sqrt_target)
            liquidity += pool["liquidity_net"][boundary]
            tick = boundary
        remaining -= needed
        sqrt_price = sqrt_target
        crossed += 1


def maximize(profit, high, iterations=80):
    """Maximum of a concave function on [0, high] by ternary search. Returns (x, profit(x))."""
    low = 0.0
    for _ in range(iterations):
        a = low + (high - low) / 3
        b = high - (high - low) / 3
        if profit(a) < profit(b):
            low = a
        else:
            high = b
    best = (low + high) / 2
    return best, profit(best)
