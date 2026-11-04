import argparse
import asyncio
import os

from web3 import AsyncWeb3

from univ2 import load_abi, stream

SWAP_SIGNATURE = "Swap(address,uint256,uint256,uint256,uint256,address)"


def make_handler(w3):
    pair_contract = w3.eth.contract(abi=load_abi("pair"))

    async def on_swap(log) -> None:
        tx_hash = log["transactionHash"].to_0x_hex()
        # Decodificamos el evento directo del log recibido. Pedir el receipt
        # (w3.eth.get_transaction_receipt) por cada swap tarda mucho mas.
        swap = pair_contract.events.Swap().process_log(log)
        a = swap["args"]
        print(f"Swap en pool {log['address']} (bloque {log['blockNumber']}) tx {tx_hash}")
        print(f"    amount0In={a['amount0In']} amount1In={a['amount1In']} "
              f"amount0Out={a['amount0Out']} amount1Out={a['amount1Out']}")

    return on_swap


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stream de eventos Swap (UniswapV2-style) en Polygon")
    parser.add_argument("--events", type=int, default=int(os.environ.get("EVENTS", 5)), help="parar tras N swaps")
    parser.add_argument("--seconds", type=int, default=int(os.environ.get("SECONDS_MAX", 45)), help="tiempo maximo")
    args = parser.parse_args()
    # address vacio = todos los contratos; filtramos solo por el topic0 del evento Swap
    topic0 = AsyncWeb3.keccak(text=SWAP_SIGNATURE).to_0x_hex()
    done = asyncio.run(stream("logs", {"topics": [topic0]}, make_handler, args.events, args.seconds, "Eventos"))
    raise SystemExit(0 if done > 0 else 1)
