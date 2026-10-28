"""Send QNT (or native ETH) between anvil accounts.

  python transfer.py --to 2 --amount 3              # 3 QNT from account 0 to account 2
  python transfer.py --from 1 --to 4 --amount 0.5   # from account 1 to account 4
  python transfer.py --to 0xABC... --amount 1 --eth # native ETH to any address

Accounts are given by index in anvil's list (0-9) or as an address.
"""
import argparse
import json
import os
from pathlib import Path

from web3 import Web3

RPC_URL = os.environ.get("RPC_URL", "http://anvil:8545")
SHARED_DIR = Path(os.environ.get("SHARED_DIR", "/shared"))
ARTIFACT = json.loads((Path(__file__).parent / "QuantToken.json").read_text())


def account(w3, value):
    # Index into anvil's accounts ("2") or a checksummed address ("0x...")
    return w3.eth.accounts[int(value)] if value.isdigit() else Web3.to_checksum_address(value)


def main():
    parser = argparse.ArgumentParser(description="Send QNT or ETH between anvil accounts")
    parser.add_argument("--from", dest="sender", default="0", help="account index or address (default 0)")
    parser.add_argument("--to", required=True, help="account index or address")
    parser.add_argument("--amount", type=float, required=True)
    parser.add_argument("--eth", action="store_true", help="send native ETH instead of QNT")
    args = parser.parse_args()

    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    sender, recipient = account(w3, args.sender), account(w3, args.to)

    if args.eth:
        tx_hash = w3.eth.send_transaction({"from": sender, "to": recipient, "value": w3.to_wei(args.amount, "ether")})
        unit, balance = "ETH", lambda a: w3.from_wei(w3.eth.get_balance(a), "ether")
    else:
        token_file = SHARED_DIR / "token.json"
        if not token_file.exists():
            raise SystemExit("QuantToken is not deployed yet: run the deployer first (docker compose up)")
        token = w3.eth.contract(address=json.loads(token_file.read_text())["address"], abi=ARTIFACT["abi"])
        decimals = token.functions.decimals().call()
        tx_hash = token.functions.transfer(recipient, int(args.amount * 10 ** decimals)).transact({"from": sender})
        unit, balance = "QNT", lambda a: token.functions.balanceOf(a).call() / 10 ** decimals

    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    print(f"{args.amount} {unit} {sender} -> {recipient}: status {receipt['status']}, block {receipt['blockNumber']}, "
          f"tx {tx_hash.to_0x_hex()}")
    print(f"  {sender}: {balance(sender)} {unit}")
    print(f"  {recipient}: {balance(recipient)} {unit}")


if __name__ == "__main__":
    main()
