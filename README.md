# QUANt — UCEMA Blockchain & Crypto Course

Course materials for "Blockchain y Criptomonedas" at Universidad del CEMA (Buenos Aires).

## Module 1 — Theory & Analytics

| Class | Topics |
|-------|--------|
| 1 | Soft vs hard finality and reorgs, token mapping, CEX vs DEX volume, architecture scoring — measured live on 13 chains (Bitcoin, Ethereum, Solana, BSC, Tron, Hyperliquid, Avalanche, Monad, Polygon PoS, Base, OP Mainnet, Arbitrum One, Robinhood Chain) |
| 2 | Stablecoin risk analysis, impermanent loss, DeFi yield, funding rates (Hyperliquid), portfolio optimization |
| 3 | Macro correlations, MA crossover backtesting, funding rate arbitrage, real-world asset (RWA) analysis |

## Module 2 — Smart Contracts & Web3

| Class | Topics |
|-------|--------|
| 1 | Solidity smart contracts, Web3.py basics |
| 2 | Uniswap-style DEX interaction (factory, pair, router) |
| 3 | Advanced on-chain analytics and DEX integration |

Each class includes lecture slides (PDF) and hands-on Jupyter notebooks.

## Setup (Python 3.15)

```bash
brew install python@3.15
python3.15 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m ipykernel install --user --name quant-py315 --display-name "Python 3.15 (QUANt)"
.venv/bin/jupyter lab
```

The module 1 class 1 notebooks use the `Python 3.15 (QUANt)` kernel and share `modulo-1/clase_1/chain_data.py` (chain registry, RPC endpoints and data helpers), so open them from that folder.
