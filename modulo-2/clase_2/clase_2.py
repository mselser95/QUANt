"""Clase 2 - Comparar precios de SushiSwap vs QuickSwap (V2) en Polygon mainnet.

Sin variables de entorno funciona con RPCs publicos sin API key.
Variables opcionales:
  RPC_URL_POLYGON   endpoint HTTP(S) de Polygon (default: publicnode)
  WSS_URL_POLYGON   endpoint WebSocket de Polygon (default: publicnode)
  STOP_AFTER_BLOCKS cantidad de bloques nuevos a seguir en la demo (default 3)
  STOP_AFTER_SECONDS tiempo maximo de la demo en segundos (default 30)
Tambien: python clase_2.py --blocks 3 --seconds 30
NUNCA pongas una API key dentro del codigo: usa variables de entorno.
"""
import argparse
import asyncio
import json
import os
import time
from pathlib import Path

from web3 import AsyncWeb3, Web3, WebSocketProvider
from web3.contract.contract import Contract
from web3.middleware import ExtraDataToPOAMiddleware

HERE = Path(__file__).resolve().parent

# RPC publicos y gratuitos (sin API key). Si tenes un proveedor propio (Alchemy, etc.)
# exportalo como variable de entorno en vez de escribirlo en el codigo.
provider_url = os.environ.get("RPC_URL_POLYGON", "https://polygon-bor-rpc.publicnode.com")
wss_url = os.environ.get("WSS_URL_POLYGON", "wss://polygon-bor-rpc.publicnode.com")

w3 = Web3(Web3.HTTPProvider(provider_url, request_kwargs={"timeout": 20}))
w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)  # Polygon es una cadena PoA


def load_abi(name: str) -> list:
    with open(HERE / name) as f:
        return json.load(f)


## Interfaz!
class Token:
    def __init__(self, token_address: str, token_decimals: int, token_name: str) -> None:
        self.token_address = token_address
        self.token_decimals = token_decimals
        self.token_name = token_name


class MarketInterface:

    def __init__(self) -> None:
        pass

    def get_output_amount(self, input_amount: int, input_token: Token, output_token: Token) -> float:
        raise NotImplementedError

    def swap(self, input_token: Token, output_token: Token) -> bool:
        raise NotImplementedError

    def get_current_price(self, base_token: Token, quote_token: Token) -> float:
        raise NotImplementedError


class UniswapV2Exchange(MarketInterface):
    """SushiSwap y QuickSwap son forks de Uniswap V2: misma interfaz, distintas direcciones."""

    def __init__(self,
                 router_address: str,
                 router_abi: list,
                 factory_address: str,
                 factory_abi: list,
                 pair_abi: list) -> None:
        super().__init__()
        self.router_contract: Contract = w3.eth.contract(address=w3.to_checksum_address(router_address), abi=router_abi)
        self.factory_contract: Contract = w3.eth.contract(address=w3.to_checksum_address(factory_address), abi=factory_abi)
        self.pair_abi: list = pair_abi

    def get_output_amount(self, input_amount: int, input_token: Token, output_token: Token) -> float:
        path = [
            w3.to_checksum_address(input_token.token_address),
            w3.to_checksum_address(output_token.token_address),
        ]
        amounts_out = self.router_contract.functions.getAmountsOut(input_amount, path).call()
        return amounts_out[-1] / (10 ** output_token.token_decimals)

    def swap(self, input_token: Token, output_token: Token) -> bool:
        raise NotImplementedError

    def get_current_price(self, base_token: Token, quote_token: Token) -> float:
        """Precio de 1 base_token expresado en quote_token, segun las reservas del pool."""
        pair_address = self.factory_contract.functions.getPair(
            w3.to_checksum_address(base_token.token_address),
            w3.to_checksum_address(quote_token.token_address),
        ).call()
        pair_contract = w3.eth.contract(address=pair_address, abi=self.pair_abi)

        reserves = pair_contract.functions.getReserves().call()
        token_0_reserves = reserves[0]
        token_1_reserves = reserves[1]

        # We need to check which token is which!
        token_0_address = pair_contract.functions.token0().call()
        if token_0_address.lower() == base_token.token_address.lower():
            base_reserves, quote_reserves = token_0_reserves, token_1_reserves
        else:
            base_reserves, quote_reserves = token_1_reserves, token_0_reserves
        # precio = reservas del quote / reservas del base (ambas normalizadas por decimales)
        return (quote_reserves / 10 ** quote_token.token_decimals) / (base_reserves / 10 ** base_token.token_decimals)


class SushiSwapExchange(UniswapV2Exchange):
    pass


class QuickSwapExchange(UniswapV2Exchange):
    pass


def with_retries(fn, attempts: int = 4, delay: float = 1.5):
    """Los RPC publicos estan balanceados y a veces fallan: reintentamos."""
    for i in range(attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - demo didactica
            if i == attempts - 1:
                raise
            print(f"[INFO] reintento {i + 1}/{attempts - 1} tras {type(exc).__name__}")
            time.sleep(delay * (i + 1))


# Nota: getAmountsOut incluye comision (0.3%) e impacto de precio para 1 WETH; el precio de reservas no.
def show_spread(sushi: UniswapV2Exchange, quick: UniswapV2Exchange, base: Token, quote: Token, label: str = "") -> None:
    one = 1 * 10 ** base.token_decimals
    s_out = with_retries(lambda: sushi.get_output_amount(one, base, quote))
    q_out = with_retries(lambda: quick.get_output_amount(one, base, quote))
    spread = (q_out - s_out) / s_out * 10_000
    print(f"{label}1 {base.token_name} -> {quote.token_name}: Sushi={s_out:.4f}  QuickSwap={q_out:.4f}  spread={spread:+.1f} bps")


async def follow_blocks(sushi, quick, base, quote, max_blocks: int, max_seconds: float) -> None:
    """Suscripcion newHeads por WebSocket (web3.py 8: AsyncWeb3 + WebSocketProvider)."""
    seen = 0

    async def loop() -> None:
        nonlocal seen
        async with AsyncWeb3(WebSocketProvider(wss_url)) as aw3:
            aw3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
            sub_id = await aw3.eth.subscribe("newHeads")
            async for msg in aw3.socket.process_subscriptions():
                number = msg["result"]["number"]
                seen += 1
                try:
                    # llamada sincronica en un thread para no bloquear el event loop
                    await asyncio.to_thread(show_spread, sushi, quick, base, quote, f"bloque {number}: ")
                except Exception as exc:  # noqa: BLE001
                    print(f"[INFO] bloque {number}: no se pudo leer el precio ({type(exc).__name__})")
                if seen >= max_blocks:
                    break
            await aw3.eth.unsubscribe(sub_id)

    try:
        await asyncio.wait_for(loop(), timeout=max_seconds)
    except asyncio.TimeoutError:
        print(f"[INFO] demo detenida por tiempo ({max_seconds:.0f}s), bloques vistos: {seen}")
    except Exception as exc:  # noqa: BLE001
        print(f"[INFO] suscripcion WebSocket no disponible ({type(exc).__name__}: {exc})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--blocks", type=int, default=int(os.environ.get("STOP_AFTER_BLOCKS", "3")))
    parser.add_argument("--seconds", type=float, default=float(os.environ.get("STOP_AFTER_SECONDS", "30")))
    args = parser.parse_args()

    # Create the tokens (Polygon mainnet)
    weth_token = Token("0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619", 18, "WETH")
    # USDC.e (bridged, 0x2791...) tiene la liquidez historica en estos pools V2.
    # El USDC nativo de Circle es 0x3c49...c3359 (otro token, ver pools propios).
    usdc_token = Token("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174", 6, "USDC.e")

    router_abi = load_abi("router_abi.json")
    factory_abi = load_abi("factory_abi.json")
    pair_abi = load_abi("pair_abi.json")

    markets = {
        "Sushiswap": ("0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506", "0xc35DADB65012eC5796536bD9864eD8773aBc74C4", SushiSwapExchange),
        "QuickSwap": ("0xa5E0829CaCEd8fFDD4De3c43696c57F7D7A678ff", "0x5757371414417b8C6CAad45bAeF941aBc7d3Ab32", QuickSwapExchange),
    }

    print(f"RPC: {provider_url.split('//')[1].split('/')[0]} | bloque actual: {with_retries(lambda: w3.eth.block_number)}")
    # Verificamos que cada direccion sea realmente un contrato
    for name, (router, factory, _) in markets.items():
        for label, addr in (("router", router), ("factory", factory)):
            code = with_retries(lambda: w3.eth.get_code(w3.to_checksum_address(addr)))
            assert len(code) > 0, f"{name} {label} {addr} no tiene codigo"

    exchanges = {}
    for name, (router, factory, cls) in markets.items():
        print(f"{name}!")
        ex = cls(router, router_abi, factory, factory_abi, pair_abi)
        exchanges[name] = ex
        one = 1 * 10 ** weth_token.token_decimals
        print(with_retries(lambda: ex.get_output_amount(one, weth_token, usdc_token)))
        print(with_retries(lambda: ex.get_current_price(weth_token, usdc_token)))
        print(with_retries(lambda: ex.get_current_price(usdc_token, weth_token)))
        print("-" * 30)

    print("Spread Sushiswap vs QuickSwap")
    show_spread(exchanges["Sushiswap"], exchanges["QuickSwap"], weth_token, usdc_token)
    print("-" * 30)

    print(f"Siguiendo bloques nuevos por WebSocket (max {args.blocks} bloques / {args.seconds:.0f}s)")
    asyncio.run(follow_blocks(exchanges["Sushiswap"], exchanges["QuickSwap"], weth_token, usdc_token, args.blocks, args.seconds))


if __name__ == "__main__":
    main()
