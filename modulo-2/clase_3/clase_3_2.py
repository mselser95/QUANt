import argparse
import asyncio
import os

import ccxt

from univ2 import build_exchanges, print_prices, read_pool_reserves, simulate_arbitrage, stream

# Precios CEX: solo datos publicos de mercado (sin API keys).
# bitmex ya no existe en ccxt 4.5 y binanceus no siempre responde desde todas las regiones;
# verificados en vivo desde aca: binanceus, kraken, binance, bybit, okx, coinbase responden ETH/USDC.
# Usamos binanceus (como en la clase) y kraken como segundo exchange en lugar de bitmex.
CEX_TIMEOUT_MS = 8000
binance_us = ccxt.binanceus({"timeout": CEX_TIMEOUT_MS})
kraken = ccxt.kraken({"timeout": CEX_TIMEOUT_MS})


async def cex_price(exchange, symbol: str):
    # ccxt (sync) corre en un thread para no bloquear el event loop del websocket.
    # Usamos 'last' (ultimo trade) y NO 'vwap': el vwap de ccxt es el de las ultimas 24 h,
    # no comparable con un precio spot actual de un pool AMM.
    try:
        ticker = await asyncio.to_thread(exchange.fetch_ticker, symbol)
        return ticker["last"]
    except Exception as e:  # exchange caido / bloqueado por region: no rompemos la clase
        print(f"{exchange.id}: sin precio ({type(e).__name__})")
        return None


def show(price) -> str:
    if price is None:
        return "n/d"
    return str(price)


def make_handler(w3):
    exchanges = build_exchanges(w3)

    async def on_block(header) -> None:
        print("-" * 30)
        print(f"New Block: {header['number']}")
        reserves = await read_pool_reserves(exchanges)   # una lectura por bloque: sirve para precios y arbitraje
        print_prices(reserves)
        print("CEX (ETH/USDC, ultimo trade):")
        print(f"    Binance us: {show(await cex_price(binance_us, 'ETH/USDC'))}")
        print(f"    Kraken: {show(await cex_price(kraken, 'ETH/USDC'))}")
        await simulate_arbitrage(w3, exchanges, reserves)

    return on_block


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Precios Sushi vs Quickswap vs CEX por bloque + arbitraje simulado")
    parser.add_argument("--blocks", type=int, default=int(os.environ.get("BLOCKS", 3)), help="parar tras N bloques nuevos")
    parser.add_argument("--seconds", type=int, default=int(os.environ.get("SECONDS_MAX", 45)), help="tiempo maximo")
    args = parser.parse_args()
    done = asyncio.run(stream("newHeads", None, make_handler, args.blocks, args.seconds, "Bloques"))
    raise SystemExit(0 if done > 0 else 1)
