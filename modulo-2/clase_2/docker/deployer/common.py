"""Settings and connection shared by deploy.py and transfer.py.

Configuration (environment variables):
  RPC_URL      node to talk to (default: http://anvil:8545, the compose service)
  SHARED_DIR   where the deployed address is written for transfer.py (default: /shared)
"""
import json
import os
from pathlib import Path

from web3 import Web3

RPC_URL = os.environ.get("RPC_URL", "http://anvil:8545")
SHARED_DIR = Path(os.environ.get("SHARED_DIR", "/shared"))
ARTIFACT = json.loads((Path(__file__).parent / "QuantToken.json").read_text())

# No waiting loop: compose only starts us once anvil's healthcheck passes (depends_on: service_healthy)
w3 = Web3(Web3.HTTPProvider(RPC_URL))
