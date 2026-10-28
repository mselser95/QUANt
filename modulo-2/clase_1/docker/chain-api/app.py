"""chain-api: a small HTTP API on top of web3.py.

The same calls as web3.ipynb (latest block, balances, gas, transactions, contract calls),
exposed as HTTP endpoints so they can be queried from curl, the browser or another service.

Configuration (environment variables only, never hard-coded):
  RPC_URL   JSON-RPC endpoint (default: Polygon mainnet public node, no API key)
"""
import os

from fastapi import FastAPI, HTTPException
from web3 import Web3
from web3.exceptions import TransactionNotFound
from web3.middleware import ExtraDataToPOAMiddleware

RPC_URL = os.environ.get("RPC_URL", "https://polygon-bor-rpc.publicnode.com")
POA_CHAINS = {137, 80002}   # Polygon mainnet and Amoy need the PoA middleware

w3 = Web3(Web3.HTTPProvider(RPC_URL, request_kwargs={"timeout": 15}))
_chain_id = None

# Uniswap-V2-style routers on Polygon and the tokens of the WETH/USDC.e pools (same as clase_2)
ROUTERS = {
    "SushiSwap": "0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506",
    "QuickSwap": "0xa5E0829CaCEd8fFDD4De3c43696c57F7D7A678ff",
}
WETH = "0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619"
USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
ROUTER_ABI = [{"name": "getAmountsOut", "type": "function", "stateMutability": "view",
               "inputs": [{"name": "amountIn", "type": "uint256"}, {"name": "path", "type": "address[]"}],
               "outputs": [{"name": "amounts", "type": "uint256[]"}]}]
ERC20_ABI = [{"name": name, "type": "function", "stateMutability": "view", "inputs": inputs,
              "outputs": [{"name": "", "type": kind}]}
             for name, inputs, kind in (("name", [], "string"), ("symbol", [], "string"),
                                        ("decimals", [], "uint8"), ("totalSupply", [], "uint256"),
                                        ("balanceOf", [{"name": "a", "type": "address"}], "uint256"))]

app = FastAPI(title="chain-api", description=f"web3.py over HTTP. RPC: {RPC_URL.split('//')[-1].split('/')[0]}")


def chain_id():
    # Chain id of the node, cached; injects the PoA middleware the first time on Polygon chains
    global _chain_id
    if _chain_id is None:
        _chain_id = rpc(lambda: w3.eth.chain_id)
        if _chain_id in POA_CHAINS:
            w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    return _chain_id


def rpc(call):
    # Run one node call; a node that does not answer becomes a readable 503 instead of a stack trace
    try:
        return call()
    except Exception as err:
        raise HTTPException(503, f"RPC error ({type(err).__name__}): {str(err)[:200]}")


def checksum(address):
    # Validate and checksum an address, 400 if it is not one
    if not Web3.is_address(address):
        raise HTTPException(400, f"not an address: {address}")
    return Web3.to_checksum_address(address)


@app.get("/health")
def health():
    return {"rpc": RPC_URL.split("//")[-1].split("/")[0], "chain_id": chain_id(),
            "latest_block": rpc(lambda: w3.eth.block_number)}


@app.get("/block")
@app.get("/block/{number}")
def block(number: int | None = None):
    chain_id()
    data = rpc(lambda: w3.eth.get_block(number if number is not None else "latest"))
    return {"number": data["number"], "hash": data["hash"].to_0x_hex(), "timestamp": data["timestamp"],
            "transactions": len(data["transactions"]), "gas_used": data["gasUsed"]}


@app.get("/balance/{address}")
def balance(address: str):
    address = checksum(address)
    wei = rpc(lambda: w3.eth.get_balance(address))
    return {"address": address, "wei": str(wei), "balance": str(w3.from_wei(wei, "ether"))}


@app.get("/gas")
def gas():
    price = rpc(lambda: w3.eth.gas_price)
    return {"gas_price_wei": price, "gas_price_gwei": round(price / 1e9, 3)}


@app.get("/tx/{tx_hash}")
def tx(tx_hash: str):
    chain_id()
    try:
        receipt = w3.eth.get_transaction_receipt(tx_hash)
    except TransactionNotFound:
        raise HTTPException(404, f"transaction not found: {tx_hash}")
    except Exception as err:
        raise HTTPException(503, f"RPC error ({type(err).__name__}): {str(err)[:200]}")
    return {"hash": tx_hash, "block": receipt["blockNumber"], "status": receipt["status"],
            "from": receipt["from"], "to": receipt["to"], "gas_used": receipt["gasUsed"],
            "logs": len(receipt["logs"]), "contract_created": receipt["contractAddress"]}


@app.get("/token/{token}")
def token(token: str, holder: str | None = None):
    contract = w3.eth.contract(address=checksum(token), abi=ERC20_ABI)
    decimals = rpc(lambda: contract.functions.decimals().call())
    info = {"address": contract.address, "name": rpc(lambda: contract.functions.name().call()),
            "symbol": rpc(lambda: contract.functions.symbol().call()), "decimals": decimals,
            "total_supply": rpc(lambda: contract.functions.totalSupply().call()) / 10 ** decimals}
    if holder:
        holder = checksum(holder)
        info["holder"] = holder
        info["holder_balance"] = rpc(lambda: contract.functions.balanceOf(holder).call()) / 10 ** decimals
    return info


@app.get("/price")
def price(amount_eth: float = 1.0):
    if chain_id() != 137:
        raise HTTPException(400, "/price reads SushiSwap/QuickSwap pools: only on Polygon mainnet (chain id 137)")
    amount_in = int(amount_eth * 10**18)
    quotes = {}
    for dex, router in ROUTERS.items():
        contract = w3.eth.contract(address=Web3.to_checksum_address(router), abi=ROUTER_ABI)
        amounts = rpc(lambda: contract.functions.getAmountsOut(amount_in, [WETH, USDC_E]).call())
        quotes[dex] = amounts[-1] / 10**6
    return {"pair": "WETH/USDC.e", "amount_eth": amount_eth, "usdc_out": quotes}
