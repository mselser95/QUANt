from web3 import Web3
from web3.contract.contract import Contract
import json

# Get the RPC wss endpoint from alchemy for polygon mainnet
provider_url = "wss://polygon-mainnet.g.alchemy.com/v2/CIbzNnYgUgzchulujOTtzhVAdIdIG_n4"

w3 = Web3(Web3.LegacyWebSocketProvider(provider_url))

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
        raise NotImplemented
    
    def swap(self, input_token: Token, output_token: Token) -> bool:
        raise NotImplemented
    
    def get_current_price(self, input_token: Token, output_token: Token) -> float:
        raise NotImplemented
    


class SushiSwapExchange(MarketInterface):
    def __init__(self, 
                 router_address: str,
                   router_abi: list,
                     factory_address: str,
                       factory_abi: list,
                         pair_abi: list) -> None:
        super().__init__()        
        self.router_contract: Contract = w3.eth.contract(address=router_address,abi=router_abi)
        self.factory_contract: Contract = w3.eth.contract(address=factory_address,abi=factory_abi)
        self.pair_abi : list = pair_abi

    def get_output_amount(self, input_amount: int,  input_token: Token, output_token: Token) -> float:

        hex_addresses = [
            w3.to_checksum_address(input_token.token_address),
            w3.to_checksum_address(output_token.token_address),
            ]
        
        amounts_out = self.router_contract.functions.getAmountsOut(input_amount, hex_addresses).call()
        return amounts_out[-1] / (10 ** output_token.token_decimals)
        
    def swap(input_token: Token, output_token: Token) -> bool:
        raise NotImplemented
    
    def get_current_price(self, base_token: Token, quote_token: Token) -> float:
        pair_address = self.factory_contract.functions.getPair(
            w3.to_checksum_address(base_token.token_address),
              w3.to_checksum_address(quote_token.token_address)
              ).call()
        pair_contract = w3.eth.contract(address=pair_address, abi=self.pair_abi)

        reserves = pair_contract.functions.getReserves().call()
        token_0_reserves = reserves[0]
        token_1_reserves = reserves[1]

        # We need to check which token is which!
        token_0_address = pair_contract.functions.token0().call()
        if token_0_address == base_token.token_address:
            return (token_0_reserves / 10**base_token.token_decimals) / (token_1_reserves/ 10**quote_token.token_decimals)
        else: 
            return (token_1_reserves / 10**base_token.token_decimals) / (token_0_reserves/ 10**quote_token.token_decimals)




class QuickSwapExchange(MarketInterface):
    def __init__(self, 
                 router_address: str,
                   router_abi: list,
                     factory_address: str,
                       factory_abi: list,
                         pair_abi: list) -> None:
        super().__init__()        
        self.router_contract: Contract = w3.eth.contract(address=router_address,abi=router_abi)
        self.factory_contract: Contract = w3.eth.contract(address=factory_address,abi=factory_abi)
        self.pair_abi : list = pair_abi

    def get_output_amount(self, input_amount: int,  input_token: Token, output_token: Token) -> float:

        hex_addresses = [
            w3.to_checksum_address(input_token.token_address),
            w3.to_checksum_address(output_token.token_address),
            ]
        
        amounts_out = self.router_contract.functions.getAmountsOut(input_amount, hex_addresses).call()
        return amounts_out[-1] / (10 ** output_token.token_decimals)
        
    def swap(input_token: Token, output_token: Token) -> bool:
        raise NotImplemented
    
    def get_current_price(self, base_token: Token, quote_token: Token) -> float:
        pair_address = self.factory_contract.functions.getPair(
            w3.to_checksum_address(base_token.token_address),
              w3.to_checksum_address(quote_token.token_address)
              ).call()
        pair_contract = w3.eth.contract(address=pair_address, abi=self.pair_abi)

        reserves = pair_contract.functions.getReserves().call()
        token_0_reserves = reserves[0]
        token_1_reserves = reserves[1]

        # We need to check which token is which!
        token_0_address = pair_contract.functions.token0().call()
        if token_0_address == base_token.token_address:
            return (token_0_reserves / 10**base_token.token_decimals) / (token_1_reserves/ 10**quote_token.token_decimals)
        else: 
            return (token_1_reserves / 10**base_token.token_decimals) / (token_0_reserves/ 10**quote_token.token_decimals)



if __name__ == "__main__":

    # Create the tokens
    weth_address =  "0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619"
    weth_decimals = 18
    weth_name = "WETH"
    weth_token = Token(weth_address, weth_decimals, weth_name)
    

    usdc_address =  "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
    usdc_decimals = 6
    usdc_name = "USDC"
    usdc_token = Token(usdc_address, usdc_decimals, usdc_name)
    
    print("Sushiswap!")

    # Create the markets
    sushiswap_router_address = "0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506"
    sushiswap_router_abi = json.load(open("router_abi.json"))

    sushiswap_factory_address = "0xc35DADB65012eC5796536bD9864eD8773aBc74C4"
    sushiswap_factory_abi = json.load(open("factory_abi.json"))

    sushiswap_pair_abi = json.load(open("pair_abi.json"))

    sushiswap_market = SushiSwapExchange(
        sushiswap_router_address,
          sushiswap_router_abi,
        sushiswap_factory_address,
          sushiswap_factory_abi,
        sushiswap_pair_abi
          )

    print(sushiswap_market.get_output_amount(1 * 10 ** weth_token.token_decimals, weth_token, usdc_token))
    print(sushiswap_market.get_current_price(weth_token, usdc_token))
    print(sushiswap_market.get_current_price(usdc_token, weth_token))

    print("-"*30)

    print("QuickSwap!")

    # Create the markets
    quickswap_router_address = "0xa5E0829CaCEd8fFDD4De3c43696c57F7D7A678ff"
    quickswap_router_abi = json.load(open("router_abi.json"))

    quickswap_factory_address = "0x5757371414417b8C6CAad45bAeF941aBc7d3Ab32"
    quickswap_factory_abi = json.load(open("factory_abi.json"))

    quickswap_pair_abi = json.load(open("pair_abi.json"))

    quickswap_market = QuickSwapExchange(
        quickswap_router_address,
          quickswap_router_abi,
        quickswap_factory_address,
          quickswap_factory_abi,
        quickswap_pair_abi
          )

    print(quickswap_market.get_output_amount(1 * 10 ** weth_token.token_decimals, weth_token, usdc_token))
    print(quickswap_market.get_current_price(weth_token, usdc_token))
    print(quickswap_market.get_current_price(usdc_token, weth_token))

    print("-"*30)

