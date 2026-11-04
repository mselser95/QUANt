import argparse
import asyncio
import os

from univ2 import build_exchanges, print_prices, read_pool_reserves, simulate_arbitrage, stream


def make_handler(w3):
    exchanges = build_exchanges(w3)

    async def on_block(header) -> None:
        print("-" * 30)
        print(f"New Block: {header['number']}")
        reserves = await read_pool_reserves(exchanges)   # una lectura por bloque: sirve para precios y arbitraje
        print_prices(reserves)
        await simulate_arbitrage(w3, exchanges, reserves)

    return on_block


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Precios Sushi vs Quickswap por bloque + arbitraje simulado")
    parser.add_argument("--blocks", type=int, default=int(os.environ.get("BLOCKS", 3)), help="parar tras N bloques nuevos")
    parser.add_argument("--seconds", type=int, default=int(os.environ.get("SECONDS_MAX", 45)), help="tiempo maximo")
    args = parser.parse_args()
    done = asyncio.run(stream("newHeads", None, make_handler, args.blocks, args.seconds, "Bloques"))
    raise SystemExit(0 if done > 0 else 1)
