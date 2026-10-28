"""Deploy QuantToken (contracts/QuantToken.sol) to the local anvil chain and make a few transfers.

anvil starts with 10 accounts full of test ETH and UNLOCKED: the node signs for them, so this
script needs no private key (on a real network you sign locally, as in web3.ipynb section 6).

Configuration (environment variables):
  RPC_URL      node to deploy to (default: http://anvil:8545, the compose service)
  SHARED_DIR   where the deployed address is written for transfer.py (default: /shared)
"""
import json
import os
import time
from pathlib import Path

from web3 import Web3

RPC_URL = os.environ.get("RPC_URL", "http://anvil:8545")
SHARED_DIR = Path(os.environ.get("SHARED_DIR", "/shared"))
ARTIFACT = json.loads((Path(__file__).parent / "QuantToken.json").read_text())


def connect():
    # Web3 client for RPC_URL, waiting up to 30 s for the node to come up
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    for _ in range(30):
        if w3.is_connected():
            return w3
        time.sleep(1)
    raise SystemExit(f"node not reachable at {RPC_URL}")


def main():
    w3 = connect()
    accounts = w3.eth.accounts
    deployer = accounts[0]
    print(f"Connected to chain {w3.eth.chain_id} at block {w3.eth.block_number}")
    print(f"Deployer {deployer}: {w3.from_wei(w3.eth.get_balance(deployer), 'ether')} ETH")

    # 1. Deploy: a transaction with no `to` whose data is the bytecode + encoded constructor args
    QuantToken = w3.eth.contract(abi=ARTIFACT["abi"], bytecode=ARTIFACT["bytecode"])
    tx_hash = QuantToken.constructor("Quant Token", "QNT").transact({"from": deployer})
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    token = w3.eth.contract(address=receipt["contractAddress"], abi=ARTIFACT["abi"])
    print(f"QuantToken deployed at {token.address} (block {receipt['blockNumber']}, gas {receipt['gasUsed']:,})")

    SHARED_DIR.mkdir(parents=True, exist_ok=True)
    (SHARED_DIR / "token.json").write_text(json.dumps({"address": token.address, "deployer": deployer,
                                                       "block": receipt["blockNumber"]}))

    # 2. A few ERC-20 transfers: each one emits a Transfer event that the indexer stores
    decimals = token.functions.decimals().call()
    for recipient, amount in ((accounts[1], 10), (accounts[2], 5), (accounts[3], 1)):
        tx_hash = token.functions.transfer(recipient, amount * 10 ** decimals).transact({"from": deployer})
        w3.eth.wait_for_transaction_receipt(tx_hash)
        print(f"Transferred {amount} QNT to {recipient} (tx {tx_hash.to_0x_hex()})")

    print("Balances:")
    for account in accounts[:4]:
        print(f"  {account}: {token.functions.balanceOf(account).call() / 10 ** decimals} QNT")


if __name__ == "__main__":
    main()
