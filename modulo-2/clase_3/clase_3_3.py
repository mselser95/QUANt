import argparse
import asyncio
import json
import os
from pathlib import Path

from web3 import AsyncWeb3
from web3.providers.persistent import WebSocketProvider
from web3.middleware import ExtraDataToPOAMiddleware

HERE = Path(__file__).parent

# Endpoint WebSocket de Polygon. Por defecto un nodo publico SIN API key;
# para usar el propio (Alchemy, Infura, etc.) exportar la variable de entorno:
#   export WSS_URL_POLYGON="wss://..."   (nunca pegar la key en el codigo)
WSS_URL_POLYGON = os.environ.get("WSS_URL_POLYGON", "wss://polygon-bor-rpc.publicnode.com")

SWAP_SIGNATURE = "Swap(address,uint256,uint256,uint256,uint256,address)"


async def run(args, deadline_s: float) -> None:
    """Una sesion de conexion: escucha eventos Swap de CUALQUIER pool tipo UniswapV2."""
    async with AsyncWeb3(WebSocketProvider(WSS_URL_POLYGON)) as w3:
        # Polygon es PoA: el extraData del header es mas largo que en Ethereum
        w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

        pair_contract = w3.eth.contract(abi=json.load(open(HERE / "pair_abi.json")))
        event_signature_hash = AsyncWeb3.keccak(text=SWAP_SIGNATURE).to_0x_hex()

        # address vacio = todos los contratos; filtramos solo por el topic0 del evento Swap
        subscription_id = await w3.eth.subscribe("logs", {
            "topics": [event_signature_hash],
        })
        try:
            async with asyncio.timeout(deadline_s):
                async for response in w3.socket.process_subscriptions():
                    log = response["result"]
                    tx_hash = log["transactionHash"].to_0x_hex()
                    # Decodificamos el evento directo del log recibido. Pedir el receipt
                    # (w3.eth.get_transaction_receipt) por cada swap tarda mucho mas.
                    swap = pair_contract.events.Swap().process_log(log)
                    a = swap["args"]
                    print(f"Swap en pool {log['address']} (bloque {log['blockNumber']}) tx {tx_hash}")
                    print(f"    amount0In={a['amount0In']} amount1In={a['amount1In']} "
                          f"amount0Out={a['amount0Out']} amount1Out={a['amount1Out']}")
                    args.done += 1
                    if args.done >= args.events:
                        # unsubscribe from logs and break out of iterator
                        await w3.eth.unsubscribe(subscription_id)
                        break
        except TimeoutError:
            print(f"Tiempo maximo alcanzado ({args.seconds} s). Eventos procesados: {args.done}")
        # the connection closes automatically when exiting the context
        # manager (the `async with` block)


async def main(args) -> int:
    loop = asyncio.get_running_loop()
    end = loop.time() + args.seconds
    args.done = 0
    for attempt in (1, 2):  # si la conexion se cae, reconectamos UNA vez
        try:
            await run(args, max(end - loop.time(), 1))
            return 0
        except Exception as e:
            print(f"Conexion perdida ({type(e).__name__}: {str(e)[:80]}). "
                  f"{'Reintentando...' if attempt == 1 else 'Saliendo.'}")
            if attempt == 2 or loop.time() >= end:
                break
    return 0 if args.done > 0 else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stream de eventos Swap (UniswapV2-style) en Polygon")
    parser.add_argument("--events", type=int, default=int(os.environ.get("EVENTS", 5)), help="parar tras N swaps")
    parser.add_argument("--seconds", type=int, default=int(os.environ.get("SECONDS_MAX", 45)), help="tiempo maximo")
    raise SystemExit(asyncio.run(main(parser.parse_args())))
