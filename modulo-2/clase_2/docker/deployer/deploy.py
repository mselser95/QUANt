"""Deploy QuantToken (contracts/QuantToken.sol) to the local anvil chain and make a few transfers.

anvil starts with 10 accounts full of test ETH and UNLOCKED: the node signs for them, so this
script needs no private key (on a real network you sign locally, as in web3.ipynb section 6).

Settings (RPC_URL, SHARED_DIR) live in common.py.
"""
import json

from common import ARTIFACT, SHARED_DIR, w3


def main():
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
