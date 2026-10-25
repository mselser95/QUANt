import argparse
import asyncio
import json
import os
from pathlib import Path

from web3 import AsyncWeb3
from web3.contract.contract import Contract
from web3.exceptions import BlockNotFound
from web3.providers.persistent import WebSocketProvider
from web3.middleware import ExtraDataToPOAMiddleware

HERE = Path(__file__).parent

# Endpoint WebSocket de Polygon. Por defecto un nodo publico SIN API key;
# para usar el propio (Alchemy, Infura, etc.) exportar la variable de entorno:
#   export WSS_URL_POLYGON="wss://..."   (nunca pegar la key en el codigo)
WSS_URL_POLYGON = os.environ.get("WSS_URL_POLYGON", "wss://polygon-bor-rpc.publicnode.com")

# Parametros del modelo de arbitraje (todo en modo simulacion / solo cotizacion)
UNIV2_FEE = 0.003          # fee de Uniswap V2 / Sushi / Quickswap: 0.3% sobre el input
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
usdc_token = Token(
    address="0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
    decimals=6,
    name="USDC"
)

weth_token = Token(
    address="0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619",
    decimals=18,
    name="wETH"
)

# WPOL (ex WMATIC): token nativo de Polygon, para convertir el gas a USD
wpol_token = Token(
    address="0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270",
    decimals=18,
    name="WPOL"
)


def get_amount_out(amount_in: int, reserve_in: int, reserve_out: int) -> int:
    # Formula de UniswapV2Library.getAmountOut (enteros, fee 0.3% = 997/1000):
    # out = in*997*reserve_out / (reserve_in*1000 + in*997)
    amount_in_with_fee = amount_in * 997
    return amount_in_with_fee * reserve_out // (reserve_in * 1000 + amount_in_with_fee)


class AsyncUNIV2Exchange():

    def __init__(
        self,
        factory_address: str,
        factory_abi: list,
        router_address: str,
        router_abi: list,
        pair_abi: list,
        w3
    ):
        super().__init__()
        self.w3 = w3
        self.router_contract: Contract = self.w3.eth.contract(address=router_address, abi=router_abi)
        self.factory_contract: Contract = self.w3.eth.contract(address=factory_address, abi=factory_abi)
        self.pair_abi = pair_abi

    async def get_output_amount(self, input_amount: int, input_token: Token, output_token: Token) -> float:
        #function getAmountsOut(uint amountIn, address[] memory path) public view returns (uint[] memory amounts);
        hex_address = [
            self.w3.to_checksum_address(input_token.address),
            self.w3.to_checksum_address(output_token.address),
        ]
        amounts_out = await self.router_contract.functions.getAmountsOut(input_amount, hex_address).call()
        return amounts_out[-1] / (10 ** output_token.decimals)

    async def swap(self, input_token: Token, output_token: Token) -> bool:
        pass

    async def get_reserves(self, base_token: Token, quote_token: Token) -> tuple:
        # Devuelve las reservas crudas (enteros) como (reserva_base, reserva_quote)
        #function getPair(address tokenA, address tokenB) external view returns (address pair);
        pair_address = await self.factory_contract.functions.getPair(
            self.w3.to_checksum_address(base_token.address),
            self.w3.to_checksum_address(quote_token.address),
        ).call()
        pair_contract = self.w3.eth.contract(address=pair_address, abi=self.pair_abi)

        # function token0() external view returns (address);
        token0_address = await pair_contract.functions.token0().call()
        # function getReserves() external view returns (uint112 reserve0, uint112 reserve1, uint32 blockTimestampLast);
        reserves = await pair_contract.functions.getReserves().call()

        if token0_address.lower() == base_token.address.lower():
            return reserves[0], reserves[1]
        return reserves[1], reserves[0]

    async def get_current_price(self, base_token: Token, quote_token: Token) -> float:
        # Precio = Reservas del base token / Reservas del quote token
        # Pools UNIV2 (AMM de producto constante)
        base_raw, quote_raw = await self.get_reserves(base_token, quote_token)
        base_token_reserves = base_raw / 10 ** base_token.decimals
        quote_token_reserves = quote_raw / 10 ** quote_token.decimals

        return base_token_reserves / quote_token_reserves


async def get_block_retry(w3, block_hash, tries: int = 6):
    # Los nodos publicos son balanceadores de carga: el nodo que atiende el pedido puede ir
    # un instante atras del que emitio el header, y devuelve BlockNotFound. Reintentamos.
    for n in range(tries):
        try:
            return await w3.eth.get_block(block_hash)
        except BlockNotFound:
            await asyncio.sleep(0.5)
    return await w3.eth.get_block(block_hash)


async def simulate_arbitrage(w3, dex_a, dex_b, name_a: str, name_b: str, flash_loan: bool = True) -> None:
    """
    Arbitraje entre dos DEXs V2, SOLO COTIZACION (no se envia ninguna transaccion):
    pedimos USDC prestado (flash loan), compramos wETH en A, vendemos en B,
    devolvemos el prestamo + premium y lo que queda, menos gas, es la ganancia.
    Se prueban ambas direcciones y varios tamanos de trade (el slippage crece con el tamano).
    """
    res = {}
    for name, dex in ((name_a, dex_a), (name_b, dex_b)):
        res[name] = await dex.get_reserves(usdc_token, weth_token)  # (USDC, wETH)

    # Costo de gas en USD: gas_price (wei de POL) * gas * precio POL/USDC (reservas de WPOL/USDC en dex_b)
    gas_price = await w3.eth.gas_price
    pol_usdc_reserves, pol_reserves = await dex_b.get_reserves(usdc_token, wpol_token)
    pol_price_usd = (pol_usdc_reserves / 10 ** usdc_token.decimals) / (pol_reserves / 10 ** wpol_token.decimals)
    gas_cost_usd = gas_price * GAS_UNITS / 10 ** 18 * pol_price_usd

    best = None
    for buy_name, sell_name in ((name_a, name_b), (name_b, name_a)):
        usdc_buy, weth_buy = res[buy_name]
        usdc_sell, weth_sell = res[sell_name]
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
    print(f"Arbitraje (simulacion, sin enviar tx): gas={gas_price / 1e9:.1f} gwei, POL=${pol_price_usd:.4f}, "
          f"costo gas ~${gas_cost_usd:.4f}")
    print(f"  Mejor: comprar wETH en {buy_name}, vender en {sell_name}, tamano {size} USDC -> "
          f"bruto ${gross:.4f}, neto ${net:.4f} ({'RENTABLE' if net > 0 else 'no rentable'})")


async def run(args, deadline_s: float) -> int:
    """Una sesion de conexion. Devuelve cuantos bloques proceso."""
    provider = WebSocketProvider(WSS_URL_POLYGON)
    async with AsyncWeb3(provider) as w3:
        # Polygon es PoA: el extraData del header es mas largo que en Ethereum
        w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

        factory_abi = json.load(open(HERE / "factory_abi.json"))
        router_abi = json.load(open(HERE / "router_abi.json"))
        pair_abi = json.load(open(HERE / "pair_abi.json"))
        sushi_exchange = AsyncUNIV2Exchange(
            factory_address="0xc35DADB65012eC5796536bD9864eD8773aBc74C4",
            factory_abi=factory_abi,
            router_abi=router_abi,
            router_address="0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506",
            pair_abi=pair_abi,
            w3=w3
        )
        quickswap_exchange = AsyncUNIV2Exchange(
            factory_address="0x5757371414417b8C6CAad45bAeF941aBc7d3Ab32",
            factory_abi=factory_abi,
            router_abi=router_abi,
            router_address="0xa5E0829CaCEd8fFDD4De3c43696c57F7D7A678ff",
            pair_abi=pair_abi,
            w3=w3
        )
        # subscribe to new block headers
        subscription_id = await w3.eth.subscribe("newHeads")
        i = 0
        try:
            async with asyncio.timeout(deadline_s):
                async for response in w3.socket.process_subscriptions():
                    data = response['result']
                    block_number = data["number"]
                    block_hash = data["hash"]
                    block_info = await get_block_retry(w3, block_hash)
                    print("-" * 30)
                    print(f"New Block: {block_number}. Number of transactions: {len(block_info.transactions)}")
                    print(f"""Prices (USDC por wETH):
              Sushiswap: {await sushi_exchange.get_current_price(base_token=usdc_token, quote_token=weth_token)}
              Quickswap: {await quickswap_exchange.get_current_price(base_token=usdc_token, quote_token=weth_token)}
              """)
                    await simulate_arbitrage(w3, sushi_exchange, quickswap_exchange, "Sushiswap", "Quickswap")
                    i += 1
                    args.done = i
                    if i >= args.blocks:
                        # unsubscribe from new block headers and break out of iterator
                        await w3.eth.unsubscribe(subscription_id)
                        break
        except TimeoutError:
            print(f"Tiempo maximo alcanzado ({args.seconds} s). Bloques procesados: {i}")
        return i


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
    parser = argparse.ArgumentParser(description="Precios Sushi vs Quickswap por bloque + arbitraje simulado")
    parser.add_argument("--blocks", type=int, default=int(os.environ.get("BLOCKS", 3)), help="parar tras N bloques nuevos")
    parser.add_argument("--seconds", type=int, default=int(os.environ.get("SECONDS_MAX", 45)), help="tiempo maximo")
    raise SystemExit(asyncio.run(main(parser.parse_args())))
