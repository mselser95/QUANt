import json
import asyncio

from web3 import AsyncWeb3
from web3.contract.contract import Contract

# Required to work with most tesnets
from web3.middleware import ExtraDataToPOAMiddleware

from web3.providers.persistent import (
    WebSocketProvider,
)


import ccxt

binance_us = ccxt.binanceus()
bitmex = ccxt.bitmex()

class Token:
    def __init__(self, address: str, decimals: int, name: str) -> None:
        self.address = address
        self.decimals = decimals
        self.name = name
    
    def get_token_amount(self, amount:int) -> int:
        return amount * 10 ** self.decimals
    
usdc_token = Token(
    address = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
    decimals = 6,
    name = "USDC"
)

weth_token = Token(
    address = "0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619",
    decimals = 18,
    name = "wETH"
)
    
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


    async def get_output_amount(self, input_amount:int, input_token: Token, output_token: Token) -> float:
        #function getAmountsOut(uint amountIn, address[] memory path) public view returns (uint[] memory amounts);
        hex_address = [
            self.w3.to_checksum_address(input_token.address),
            self.w3.to_checksum_address(output_token.address),
        ]
        amounts_out = await self.router_contract.functions.getAmountsOut(input_amount, hex_address).call()
        return amounts_out[-1] / (10 ** output_token.decimals)


    async def swap(self, input_token: Token, output_token: Token) -> bool:
        pass

    async def get_current_price(self, base_token: Token, quote_token: Token) -> float:
        # Precio = Reservas del base token / Reservas del quote token
        # Pools UNIV2 (AMM de producto constante)

        #function getPair(address tokenA, address tokenB) external view returns (address pair);
        pair_address = await self.factory_contract.functions.getPair(base_token.address, quote_token.address).call()
        pair_contract = self.w3.eth.contract(address=pair_address, abi=self.pair_abi)

        # function token0() external view returns (address);
        token0_address = await pair_contract.functions.token0().call()
        # function token1() external view returns (address);
        token1_address = await pair_contract.functions.token1().call()
        # function getReserves() external view returns (uint112 reserve0, uint112 reserve1, uint32 blockTimestampLast);

        reserves = await pair_contract.functions.getReserves().call()
        token_0_reserves = reserves[0]
        token_1_reserves = reserves[1]
        
        if token0_address == base_token.address:
            base_token_reserves = token_0_reserves / 10 ** base_token.decimals
            quote_token_reserves = token_1_reserves / 10 ** quote_token.decimals
        else:
            base_token_reserves = token_1_reserves / 10 ** base_token.decimals
            quote_token_reserves = token_0_reserves / 10 ** quote_token.decimals
            
        return base_token_reserves / quote_token_reserves


async def ws_v2_subscription_context_manager_example():

    async with AsyncWeb3(WebSocketProvider("wss://polygon-mainnet.g.alchemy.com/v2/CIbzNnYgUgzchulujOTtzhVAdIdIG_n4")) as w3:
        w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

        sushi_exchange = AsyncUNIV2Exchange(
            factory_address = "0xc35DADB65012eC5796536bD9864eD8773aBc74C4",
            factory_abi = json.load(open("factory_abi.json")),
            router_abi = json.load(open("router_abi.json")),
            router_address="0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506",
            pair_abi = json.load(open("pair_abi.json")),
            w3=w3
        )
        quickswap_exchange = AsyncUNIV2Exchange(
            factory_address = "0x5757371414417b8C6CAad45bAeF941aBc7d3Ab32",
            factory_abi = json.load(open("factory_abi.json")),
            router_abi = json.load(open("router_abi.json")),
            router_address="0xa5E0829CaCEd8fFDD4De3c43696c57F7D7A678ff",
            pair_abi = json.load(open("pair_abi.json")),
            w3=w3
        )
        # subscribe to new block headers
        subscription_id = await w3.eth.subscribe("newHeads")
        i = 0
        async for response in w3.socket.process_subscriptions():
            data = response['result']
            block_number = data["number"]
            block_hash = data["hash"]
            block_info = await w3.eth.get_block(block_hash)
            print("-"*30)
            print(f"New Block: {block_number}. Number of transactions: {len(block_info.transactions)}")
            print(f"""Prices:
                  Sushiswap: {await sushi_exchange.get_current_price(base_token=usdc_token,quote_token=weth_token)}
                  Quickswap: {await quickswap_exchange.get_current_price(base_token=usdc_token,quote_token=weth_token)}
                  Binance us: {binance_us.fetch_ticker(symbol='ETH/USDC')['vwap']}
                  Bitmex: {bitmex.fetch_ticker(symbol='ETHUSD')['vwap']}
                  """)
            # handle responses here
            if i == 5:
            #    # unsubscribe from new block headers and break out of
            #    # iterator
                await w3.eth.unsubscribe(subscription_id)
                break
            i += 1

        # the connection closes automatically when exiting the context
        # manager (the `async with` block)

asyncio.run(ws_v2_subscription_context_manager_example())