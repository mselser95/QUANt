"""chain-api: a small HTTP API on top of web3.py.

The same calls as web3.ipynb (latest block, balances, gas, transactions, contract calls),
exposed as HTTP endpoints so they can be queried from curl, the browser or another service.

Configuration (environment variables only, never hard-coded):
  RPC_URL   JSON-RPC endpoint (default: Polygon mainnet public node, no API key)
"""
import functools
import os

import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from web3 import Web3
from web3.exceptions import TransactionNotFound, Web3Exception
from web3.middleware import ExtraDataToPOAMiddleware

RPC_URL = os.environ.get("RPC_URL", "https://polygon-bor-rpc.publicnode.com")

w3 = Web3(Web3.HTTPProvider(RPC_URL, request_kwargs={"timeout": 15}))
# Polygon (137) and Amoy (80002) are PoA chains and need this middleware; on other chains it changes nothing
w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

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
ERC20_ABI = [
    {"name": "name", "type": "function", "stateMutability": "view", "inputs": [],
     "outputs": [{"name": "", "type": "string"}]},
    {"name": "symbol", "type": "function", "stateMutability": "view", "inputs": [],
     "outputs": [{"name": "", "type": "string"}]},
    {"name": "decimals", "type": "function", "stateMutability": "view", "inputs": [],
     "outputs": [{"name": "", "type": "uint8"}]},
    {"name": "totalSupply", "type": "function", "stateMutability": "view", "inputs": [],
     "outputs": [{"name": "", "type": "uint256"}]},
    {"name": "balanceOf", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "a", "type": "address"}], "outputs": [{"name": "", "type": "uint256"}]},
]

# Router contracts only hold an address and an ABI (no network call): build them once
router_contracts = {dex: w3.eth.contract(address=Web3.to_checksum_address(address), abi=ROUTER_ABI)
                    for dex, address in ROUTERS.items()}

app = FastAPI(title="chain-api", description=f"web3.py over HTTP. RPC: {RPC_URL.split('//')[-1].split('/')[0]}")


@app.exception_handler(TransactionNotFound)
def transaction_not_found(request: Request, err: TransactionNotFound):
    return JSONResponse({"detail": f"transaction not found: {request.path_params['tx_hash']}"}, status_code=404)


@app.exception_handler(Web3Exception)
@app.exception_handler(requests.RequestException)
@app.exception_handler(ValueError)
def rpc_error(request: Request, err: Exception):
    # A node that does not answer becomes a readable 503 instead of a stack trace
    return JSONResponse({"detail": f"RPC error ({type(err).__name__}): {str(err)[:200]}"}, status_code=503)


@functools.cache
def chain_id():
    # Chain id of the node; asked once (a failed call is not cached, so a node that was down is retried)
    return w3.eth.chain_id


def checksum(address):
    # Validate and checksum an address, 400 if it is not one
    if not Web3.is_address(address):
        raise HTTPException(400, f"not an address: {address}")
    return Web3.to_checksum_address(address)


@app.get("/health")
def health():
    return {"rpc": RPC_URL.split("//")[-1].split("/")[0], "chain_id": chain_id(),
            "latest_block": w3.eth.block_number}


@app.get("/block")
@app.get("/block/{number}")
def block(number: int | None = None):
    if number is None:
        number = "latest"
    data = w3.eth.get_block(number)
    return {"number": data["number"], "hash": data["hash"].to_0x_hex(), "timestamp": data["timestamp"],
            "transactions": len(data["transactions"]), "gas_used": data["gasUsed"]}


@app.get("/balance/{address}")
def balance(address: str):
    address = checksum(address)
    wei = w3.eth.get_balance(address)
    return {"address": address, "wei": str(wei), "balance": str(w3.from_wei(wei, "ether"))}


@app.get("/gas")
def gas():
    price = w3.eth.gas_price
    return {"gas_price_wei": price, "gas_price_gwei": round(price / 1e9, 3)}


@app.get("/tx/{tx_hash}")
def tx(tx_hash: str):
    receipt = w3.eth.get_transaction_receipt(tx_hash)   # TransactionNotFound -> 404 (handler above)
    return {"hash": tx_hash, "block": receipt["blockNumber"], "status": receipt["status"],
            "from": receipt["from"], "to": receipt["to"], "gas_used": receipt["gasUsed"],
            "logs": len(receipt["logs"]), "contract_created": receipt["contractAddress"]}


@app.get("/token/{token}")
def token(token: str, holder: str | None = None):
    contract = w3.eth.contract(address=checksum(token), abi=ERC20_ABI)
    decimals = contract.functions.decimals().call()
    info = {"address": contract.address, "name": contract.functions.name().call(),
            "symbol": contract.functions.symbol().call(), "decimals": decimals,
            "total_supply": contract.functions.totalSupply().call() / 10 ** decimals}
    if holder:
        holder = checksum(holder)
        info["holder"] = holder
        info["holder_balance"] = contract.functions.balanceOf(holder).call() / 10 ** decimals
    return info


@app.get("/price")
def price(amount_eth: float = 1.0):
    if chain_id() != 137:
        raise HTTPException(400, "/price reads SushiSwap/QuickSwap pools: only on Polygon mainnet (chain id 137)")
    amount_in = int(amount_eth * 10**18)
    quotes = {}
    for dex, contract in router_contracts.items():
        amounts = contract.functions.getAmountsOut(amount_in, [WETH, USDC_E]).call()
        quotes[dex] = amounts[-1] / 10**6
    return {"pair": "WETH/USDC.e", "amount_eth": amount_eth, "usdc_out": quotes}
