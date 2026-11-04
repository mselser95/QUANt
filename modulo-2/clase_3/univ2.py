"""Piezas compartidas por clase_3.py, clase_3_2.py y clase_3_3.py (websocket de Polygon + pools UniswapV2)."""
import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

from web3 import AsyncWeb3
from web3.middleware import ExtraDataToPOAMiddleware
from web3.providers.persistent import WebSocketProvider

HERE = Path(__file__).parent

# Endpoint WebSocket de Polygon. Por defecto un nodo publico SIN API key;
# para usar el propio (Alchemy, Infura, etc.) exportar la variable de entorno:
#   export WSS_URL_POLYGON="wss://..."   (nunca pegar la key en el codigo)
WSS_URL_POLYGON = os.environ.get("WSS_URL_POLYGON", "wss://polygon-bor-rpc.publicnode.com")

# Fee de Uniswap V2 / Sushi / Quickswap: 0.3% del input. En enteros (como el contrato):
# al pool llegan 997 de cada 1000 unidades del input.
FEE_DENOMINATOR = 1000
AMOUNT_AFTER_FEE = 997
# Parametros del modelo de arbitraje (todo en modo simulacion / solo cotizacion)
FLASH_LOAN_FEE = 0.0005    # premium de Aave V3 en Polygon: 0.05% del monto prestado
GAS_UNITS = 350_000        # gas estimado: flash loan + 2 swaps V2 (aprox.)


class Token:
    def __init__(self, address: str, decimals: int, name: str) -> None:
        self.address = address
        self.decimals = decimals
        self.name = name

    def get_token_amount(self, amount: int) -> int:
        return amount * 10 ** self.decimals


# USDC.e (bridged) es el USDC con liquidez en los pools V2 de Polygon.
# El USDC nativo (0x3c49...3359) casi no tiene liquidez en estos DEXs.
usdc_token = Token(address="0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174", decimals=6, name="USDC")
weth_token = Token(address="0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619", decimals=18, name="wETH")
# WPOL (ex WMATIC): token nativo de Polygon, para convertir el gas a USD
wpol_token = Token(address="0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270", decimals=18, name="WPOL")


def get_amount_out(amount_in: int, reserve_in: int, reserve_out: int) -> int:
    # Formula de UniswapV2Library.getAmountOut (enteros):
    # out = in*997*reserve_out / (reserve_in*1000 + in*997)
    amount_in_with_fee = amount_in * AMOUNT_AFTER_FEE
    return amount_in_with_fee * reserve_out // (reserve_in * FEE_DENOMINATOR + amount_in_with_fee)


def load_abi(name: str) -> list:
    return json.loads((HERE / f"{name}_abi.json").read_text())


class AsyncUNIV2Exchange:

    def __init__(self, factory_address: str, router_address: str, w3) -> None:
        self.w3 = w3
        self.router_contract = w3.eth.contract(address=router_address, abi=load_abi("router"))
        self.factory_contract = w3.eth.contract(address=factory_address, abi=load_abi("factory"))
        self.pair_abi = load_abi("pair")
        # La direccion del par y su token0 no cambian nunca: se piden una sola vez por par de tokens
        self.pairs = {}

    async def get_output_amount(self, input_amount: int, input_token: Token, output_token: Token) -> float:
        #function getAmountsOut(uint amountIn, address[] memory path) public view returns (uint[] memory amounts);
        path = [
            self.w3.to_checksum_address(input_token.address),
            self.w3.to_checksum_address(output_token.address),
        ]
        amounts_out = await self.router_contract.functions.getAmountsOut(input_amount, path).call()
        return amounts_out[-1] / (10 ** output_token.decimals)

    async def get_pair(self, base_token: Token, quote_token: Token) -> tuple:
        # Devuelve (contrato del par, direccion de token0), con cache
        key = (base_token.address, quote_token.address)
        if key not in self.pairs:
            #function getPair(address tokenA, address tokenB) external view returns (address pair);
            pair_address = await self.factory_contract.functions.getPair(
                self.w3.to_checksum_address(base_token.address),
                self.w3.to_checksum_address(quote_token.address),
            ).call()
            pair_contract = self.w3.eth.contract(address=pair_address, abi=self.pair_abi)
            # function token0() external view returns (address);
            token0_address = await pair_contract.functions.token0().call()
            self.pairs[key] = (pair_contract, token0_address)
        return self.pairs[key]

    async def get_reserves(self, base_token: Token, quote_token: Token) -> tuple:
        # Devuelve las reservas crudas (enteros) como (reserva_base, reserva_quote)
        pair_contract, token0_address = await self.get_pair(base_token, quote_token)
        # function getReserves() external view returns (uint112 reserve0, uint112 reserve1, uint32 blockTimestampLast);
        reserves = await pair_contract.functions.getReserves().call()
        if token0_address.lower() == base_token.address.lower():
            return reserves[0], reserves[1]
        return reserves[1], reserves[0]


def price_from_reserves(base_raw: int, quote_raw: int, base_token: Token, quote_token: Token) -> float:
    # Precio = Reservas del base token / Reservas del quote token (pools UNIV2: AMM de producto constante)
    return (base_raw / 10 ** base_token.decimals) / (quote_raw / 10 ** quote_token.decimals)


def build_exchanges(w3) -> dict:
    return {
        "Sushiswap": AsyncUNIV2Exchange(
            factory_address="0xc35DADB65012eC5796536bD9864eD8773aBc74C4",
            router_address="0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506",
            w3=w3,
        ),
        "Quickswap": AsyncUNIV2Exchange(
            factory_address="0x5757371414417b8C6CAad45bAeF941aBc7d3Ab32",
            router_address="0xa5E0829CaCEd8fFDD4De3c43696c57F7D7A678ff",
            w3=w3,
        ),
    }


async def read_pool_reserves(exchanges: dict) -> dict:
    """Lee UNA vez por bloque las reservas (USDC, wETH) de cada DEX; se usan para el precio y el arbitraje."""
    reserves = {}
    for name, dex in exchanges.items():
        reserves[name] = await dex.get_reserves(usdc_token, weth_token)
    return reserves


def print_prices(reserves: dict) -> None:
    print("Prices (USDC por wETH):")
    for name, (usdc_raw, weth_raw) in reserves.items():
        print(f"    {name}: {price_from_reserves(usdc_raw, weth_raw, usdc_token, weth_token)}")


async def simulate_arbitrage(w3, exchanges: dict, reserves: dict, flash_loan: bool = True) -> None:
    """
    Arbitraje entre los dos DEXs V2, SOLO COTIZACION (no se envia ninguna transaccion):
    pedimos USDC prestado (flash loan), compramos wETH en A, vendemos en B,
    devolvemos el prestamo + premium y lo que queda, menos gas, es la ganancia.
    Se prueban ambas direcciones y varios tamanos de trade (el slippage crece con el tamano).
    """
    name_a, name_b = list(reserves)

    # Costo de gas en USD: gas_price (wei de POL) * gas * precio POL/USDC (reservas de WPOL/USDC en el DEX B)
    gas_price = await w3.eth.gas_price
    pol_usdc_raw, pol_raw = await exchanges[name_b].get_reserves(usdc_token, wpol_token)
    pol_price_usd = price_from_reserves(pol_usdc_raw, pol_raw, usdc_token, wpol_token)
    gas_cost_usd = gas_price * GAS_UNITS / 10 ** 18 * pol_price_usd

    best = None
    for buy_name, sell_name in ((name_a, name_b), (name_b, name_a)):
        usdc_buy, weth_buy = reserves[buy_name]
        usdc_sell, weth_sell = reserves[sell_name]
        for size in (100, 1_000, 10_000, 100_000):
            amount_in = usdc_token.get_token_amount(size)
            weth_out = get_amount_out(amount_in, usdc_buy, weth_buy)        # USDC -> wETH en el DEX barato
            usdc_back = get_amount_out(weth_out, weth_sell, usdc_sell)      # wETH -> USDC en el DEX caro
            premium = amount_in * FLASH_LOAN_FEE if flash_loan else 0
            gross = (usdc_back - amount_in - premium) / 10 ** usdc_token.decimals
            net = gross - gas_cost_usd
            if best is None or net > best[0]:
                best = (net, gross, size, buy_name, sell_name)

    net, gross, size, buy_name, sell_name = best
    verdict = "RENTABLE" if net > 0 else "no rentable"
    print(f"Arbitraje (simulacion, sin enviar tx): gas={gas_price / 1e9:.1f} gwei, POL=${pol_price_usd:.4f}, "
          f"costo gas ~${gas_cost_usd:.4f}")
    print(f"  Mejor: comprar wETH en {buy_name}, vender en {sell_name}, tamano {size} USDC -> "
          f"bruto ${gross:.4f}, neto ${net:.4f} ({verdict})")


@asynccontextmanager
async def connect():
    async with AsyncWeb3(WebSocketProvider(WSS_URL_POLYGON)) as w3:
        # Polygon es PoA: el extraData del header es mas largo que en Ethereum
        w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
        yield w3


async def stream(topic: str, params, make_handler, limit: int, seconds: int, what: str) -> int:
    """
    Se suscribe a `topic` ("newHeads" o "logs" + `params`), llama a `handler(resultado)` por cada mensaje y para tras `limit` mensajes o `seconds`.
    `make_handler(w3)` arma el handler con la conexion ya abierta.
    Si la conexion se cae, reconecta UNA vez. Devuelve cuantos mensajes proceso.
    """
    loop = asyncio.get_running_loop()
    end = loop.time() + seconds
    done = 0
    for attempt in (1, 2):
        try:
            async with connect() as w3:
                handler = make_handler(w3)
                if params is None:
                    subscription_id = await w3.eth.subscribe(topic)
                else:
                    subscription_id = await w3.eth.subscribe(topic, params)
                try:
                    async with asyncio.timeout(max(end - loop.time(), 1)):
                        async for response in w3.socket.process_subscriptions():
                            await handler(response["result"])
                            done += 1
                            if done >= limit:
                                await w3.eth.unsubscribe(subscription_id)
                                return done
                except TimeoutError:
                    print(f"Tiempo maximo alcanzado ({seconds} s). {what} procesados: {done}")
                    return done
        except Exception as e:  # la conexion se cayo
            retry_msg = "Reintentando..." if attempt == 1 else "Saliendo."
            print(f"Conexion perdida ({type(e).__name__}: {str(e)[:80]}). {retry_msg}")
            if loop.time() >= end:
                break
    return done
