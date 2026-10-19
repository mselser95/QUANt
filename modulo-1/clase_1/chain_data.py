"""
Shared chain registry and data helpers for the Module 1 / Class 1 notebooks.

Every notebook (1a-1d) imports from here, so all of them analyze the same chains,
with the same RPC endpoints and the same measurement methods.

Data sources (all public, no API key):
- Chain RPC nodes (EVM JSON-RPC, Solana JSON-RPC) and mempool.space / blockstream.info for Bitcoin
- DefiLlama: TVL, DEX volume, stablecoin supply per chain, historical prices
- CoinGecko: market data, token contract addresses per chain, exchange tickers
- Hyperliquid: perpetuals volume (its own public API)
"""

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, List, Optional
from urllib.parse import urlparse

import pandas as pd
import requests

# ---------------------------------------------------------------------------
# Chain registry
# ---------------------------------------------------------------------------
# kind:            how we read blocks ('bitcoin', 'solana' or 'evm')
# layer:           L1, L2 (rollup settling on Ethereum) or Sidechain
# finality_type:   Probabilistic (PoW), Deterministic (BFT-style), or Inherited (rollups inherit Ethereum's)
# gas_token:       token used to pay fees; token_id is the chain's own token on CoinGecko (None if it has none)
# cg_platform:     CoinGecko asset-platform id, used to find token contracts deployed on the chain
# llama_name:      chain name in DefiLlama (TVL, stablecoins, per-chain DEX history)
# llama_slug:      chain key in DefiLlama's per-protocol volume breakdown
# system_txs:      protocol transactions included in every block (OP Stack L1-info deposit, ArbOS start-block)
CHAINS: Dict[str, Dict] = {
    'Bitcoin': {
        'kind': 'bitcoin', 'layer': 'L1', 'architecture': 'UTXO', 'consensus': 'Proof of Work (Nakamoto)',
        'finality_type': 'Probabilistic', 'settlement': 'Itself',
        'gas_token': 'BTC', 'token_id': 'bitcoin', 'cg_platform': None,
        'llama_name': 'Bitcoin', 'llama_slug': 'bitcoin', 'system_txs': 0, 'color': '#F7931A',
    },
    'Ethereum': {
        'kind': 'evm', 'rpc': 'https://ethereum-rpc.publicnode.com',
        'layer': 'L1', 'architecture': 'Account (EVM)', 'consensus': 'Proof of Stake (Gasper)',
        'finality_type': 'Deterministic (Casper FFG)', 'settlement': 'Itself',
        'gas_token': 'ETH', 'token_id': 'ethereum', 'cg_platform': 'ethereum',
        'llama_name': 'Ethereum', 'llama_slug': 'ethereum', 'system_txs': 0, 'color': '#627EEA',
    },
    'Solana': {
        'kind': 'solana', 'rpc': 'https://api.mainnet-beta.solana.com',
        'layer': 'L1', 'architecture': 'Account (SVM, parallel)', 'consensus': 'Proof of Stake + Proof of History',
        'finality_type': 'Deterministic (vote-based)', 'settlement': 'Itself',
        'gas_token': 'SOL', 'token_id': 'solana', 'cg_platform': 'solana',
        'llama_name': 'Solana', 'llama_slug': 'solana', 'system_txs': 0, 'color': '#14F195',
    },
    'BSC': {
        'kind': 'evm', 'rpc': 'https://bsc-rpc.publicnode.com',
        'layer': 'L1', 'architecture': 'Account (EVM)', 'consensus': 'Proof of Staked Authority',
        'finality_type': 'Deterministic (fast finality)', 'settlement': 'Itself',
        'gas_token': 'BNB', 'token_id': 'binancecoin', 'cg_platform': 'binance-smart-chain',
        'llama_name': 'BSC', 'llama_slug': 'bsc', 'system_txs': 0, 'color': '#F3BA2F',
    },
    'Tron': {
        'kind': 'evm', 'rpc': 'https://api.trongrid.io/jsonrpc',
        'layer': 'L1', 'architecture': 'Account (TVM, EVM-compatible)', 'consensus': 'Delegated PoS (27 SRs)',
        'finality_type': 'Deterministic (19 of 27 SRs)', 'settlement': 'Itself',
        'gas_token': 'TRX', 'token_id': 'tron', 'cg_platform': 'tron',
        'llama_name': 'Tron', 'llama_slug': 'tron', 'system_txs': 0, 'color': '#FF060A',
    },
    'Hyperliquid': {
        'kind': 'evm', 'rpc': 'https://rpc.hyperliquid.xyz/evm',
        'layer': 'L1', 'architecture': 'Account (HyperEVM + HyperCore)', 'consensus': 'HyperBFT',
        'finality_type': 'Deterministic (BFT)', 'settlement': 'Itself',
        'gas_token': 'HYPE', 'token_id': 'hyperliquid', 'cg_platform': 'hyperevm',
        'llama_name': 'Hyperliquid L1', 'llama_slug': 'hyperliquid', 'system_txs': 0, 'color': '#97FCE4',
    },
    'Avalanche': {
        'kind': 'evm', 'rpc': 'https://api.avax.network/ext/bc/C/rpc',
        'layer': 'L1', 'architecture': 'Account (EVM)', 'consensus': 'Snowman',
        'finality_type': 'Deterministic (Snowman)', 'settlement': 'Itself',
        'gas_token': 'AVAX', 'token_id': 'avalanche-2', 'cg_platform': 'avalanche',
        'llama_name': 'Avalanche', 'llama_slug': 'avax', 'system_txs': 0, 'color': '#E84142',
    },
    'Monad': {
        'kind': 'evm', 'rpc': 'https://rpc.monad.xyz',
        'layer': 'L1', 'architecture': 'Account (EVM, parallel)', 'consensus': 'MonadBFT',
        'finality_type': 'Deterministic (BFT)', 'settlement': 'Itself',
        'gas_token': 'MON', 'token_id': 'monad', 'cg_platform': 'monad',
        'llama_name': 'Monad', 'llama_slug': 'monad', 'system_txs': 0, 'color': '#836EF9',
    },
    'Polygon PoS': {
        'kind': 'evm', 'rpc': 'https://polygon-bor-rpc.publicnode.com',
        'layer': 'Sidechain', 'architecture': 'Account (EVM)', 'consensus': 'PoS (Heimdall + Bor)',
        'finality_type': 'Deterministic (milestones)', 'settlement': 'Checkpoints to Ethereum',
        'gas_token': 'POL', 'token_id': 'polygon-ecosystem-token', 'cg_platform': 'polygon-pos',
        'llama_name': 'Polygon', 'llama_slug': 'polygon', 'system_txs': 0, 'color': '#8247E5',
    },
    'Base': {
        'kind': 'evm', 'rpc': 'https://mainnet.base.org',
        'layer': 'L2', 'architecture': 'Account (EVM)', 'consensus': 'Optimistic rollup (OP Stack)',
        'finality_type': 'Inherited from Ethereum', 'settlement': 'Ethereum',
        'gas_token': 'ETH', 'token_id': None, 'cg_platform': 'base',
        'llama_name': 'Base', 'llama_slug': 'base', 'system_txs': 1, 'color': '#0052FF',
    },
    'OP Mainnet': {
        'kind': 'evm', 'rpc': 'https://mainnet.optimism.io',
        'layer': 'L2', 'architecture': 'Account (EVM)', 'consensus': 'Optimistic rollup (OP Stack)',
        'finality_type': 'Inherited from Ethereum', 'settlement': 'Ethereum',
        'gas_token': 'ETH', 'token_id': 'optimism', 'cg_platform': 'optimistic-ethereum',
        'llama_name': 'OP Mainnet', 'llama_slug': 'optimism', 'system_txs': 1, 'color': '#FF0420',
    },
    'Arbitrum One': {
        'kind': 'evm', 'rpc': 'https://arb1.arbitrum.io/rpc',
        'layer': 'L2', 'architecture': 'Account (EVM)', 'consensus': 'Optimistic rollup (Arbitrum Nitro)',
        'finality_type': 'Inherited from Ethereum', 'settlement': 'Ethereum',
        'gas_token': 'ETH', 'token_id': 'arbitrum', 'cg_platform': 'arbitrum-one',
        'llama_name': 'Arbitrum', 'llama_slug': 'arbitrum', 'system_txs': 1, 'color': '#28A0F0',
    },
    'Robinhood Chain': {
        'kind': 'evm', 'rpc': 'https://rpc.mainnet.chain.robinhood.com',
        'layer': 'L2', 'architecture': 'Account (EVM)', 'consensus': 'Optimistic rollup (Arbitrum Orbit)',
        'finality_type': 'Inherited from Ethereum', 'settlement': 'Ethereum',
        'gas_token': 'ETH', 'token_id': None, 'cg_platform': 'robinhood',
        'llama_name': 'Robinhood Chain', 'llama_slug': 'robinhood', 'system_txs': 1, 'color': '#CCFF00',
    },
}

LAYER_COLORS = {'L1': '#627EEA', 'L2': '#FF6B35', 'Sidechain': '#8247E5'}

LLAMA_API = 'https://api.llama.fi'
LLAMA_COINS_API = 'https://coins.llama.fi'
LLAMA_STABLECOINS_API = 'https://stablecoins.llama.fi'
COINGECKO_API = 'https://api.coingecko.com/api/v3'
HYPERLIQUID_API = 'https://api.hyperliquid.xyz/info'
BITCOIN_APIS = ['https://mempool.space/api', 'https://blockstream.info/api']


def chain_table() -> pd.DataFrame:
    """The registry as a DataFrame, for display."""
    cols = ['layer', 'architecture', 'consensus', 'finality_type', 'settlement', 'gas_token']
    df = pd.DataFrame(CHAINS).T[cols]
    df.columns = ['Layer', 'Architecture', 'Consensus', 'Finality Type', 'Settles On', 'Gas Token']
    df.index.name = 'Chain'
    return df


def chain_colors(chains) -> List[str]:
    return [CHAINS[c]['color'] if c in CHAINS else '#999999' for c in chains]


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------
# CoinGecko's free tier allows only a few calls per minute, so its calls are spaced out.
# Responses to GET requests are cached in memory: re-running a cell does not hit the APIs again.
_MIN_INTERVAL_BY_HOST = {'api.coingecko.com': 2.5}
_last_call_by_host: Dict[str, float] = {}
_cache: Dict[tuple, object] = {}


def api_call(url: str, params: Optional[Dict] = None, method: str = 'GET', json_data=None,
             max_retries: int = 5, timeout: int = 30, use_cache: bool = True):
    """GET/POST with retries and exponential backoff on rate limits and network errors. Returns parsed JSON or None."""
    key = (url, tuple(sorted((params or {}).items())))
    if method == 'GET' and use_cache and key in _cache:
        return _cache[key]

    host = urlparse(url).netloc
    for attempt in range(max_retries):
        wait = _MIN_INTERVAL_BY_HOST.get(host, 0) - (time.time() - _last_call_by_host.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        _last_call_by_host[host] = time.time()
        try:
            if method == 'GET':
                response = requests.get(url, params=params, timeout=timeout)
            else:
                response = requests.post(url, json=json_data, timeout=timeout)
            if response.status_code == 429:
                backoff = 5 * (2 ** attempt)
                print(f"[WARNING] Rate limit on {host}. Waiting {backoff}s (retry {attempt + 1}/{max_retries})...")
                time.sleep(backoff)
                continue
            if 400 <= response.status_code < 500:
                # A client error (bad request, paid endpoint, not found) will not fix itself: don't retry
                print(f"[ERROR] {host}: HTTP {response.status_code}: {response.text[:150]}")
                return None
            response.raise_for_status()
            data = response.json()
            if method == 'GET' and use_cache:
                _cache[key] = data
            return data
        except (requests.exceptions.RequestException, ValueError) as error:
            if attempt == max_retries - 1:
                print(f"[ERROR] {host}: request failed after {max_retries} attempts: {error}")
                return None
            time.sleep(2 * (attempt + 1))
    return None


def json_rpc(url: str, method: str, params: List, retries: int = 3):
    """Minimal JSON-RPC call (EVM or Solana). Returns the 'result' field, or None on failure."""
    for attempt in range(retries):
        try:
            response = requests.post(url, json={'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}, timeout=15)
            if response.status_code == 429:
                time.sleep(2 * (attempt + 1))
                continue
            response.raise_for_status()
            body = response.json()
            if body.get('result') is not None:
                return body['result']
            error = body.get('error') or {}
            # Transient errors reported inside the JSON body: rate limits, and load-balanced nodes that
            # are a block behind (Hyperliquid answers -32603 "invalid block height") -> retry
            if 'rate' in str(error.get('message', '')).lower() or error.get('code') in (-32005, -32603, 429):
                time.sleep(1 + attempt)
                continue
            if error:
                return None  # the node answered: the method or tag is not supported
        except (requests.exceptions.RequestException, ValueError):
            time.sleep(2 * (attempt + 1))
    return None


def for_each_chain(fn: Callable[[str], Optional[Dict]], chains=None, workers: int = 6) -> List[Dict]:
    """Run fn(chain_name) for every chain in parallel (each chain has its own endpoint). Drops failures."""
    chains = list(chains or CHAINS)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(fn, chains))
    return [r for r in results if r]


# ---------------------------------------------------------------------------
# Blocks
# ---------------------------------------------------------------------------
def _bitcoin_get(path: str):
    for base in BITCOIN_APIS:
        data = api_call(f"{base}{path}", max_retries=2, use_cache=False)
        if data:
            return data
    return None


def get_latest_block(chain: str) -> Optional[Dict]:
    """Latest block: height, timestamp, tx count and hash."""
    cfg = CHAINS[chain]
    if cfg['kind'] == 'bitcoin':
        blocks = _bitcoin_get('/v1/blocks') or _bitcoin_get('/blocks')
        if not blocks:
            return None
        b = blocks[0]
        return {'Chain': chain, 'height': b['height'], 'timestamp': b['timestamp'], 'tx_count': b['tx_count'], 'hash': b['id']}
    if cfg['kind'] == 'solana':
        slot = json_rpc(cfg['rpc'], 'getSlot', [{'commitment': 'confirmed'}])
        if slot is None:
            return None
        block = json_rpc(cfg['rpc'], 'getBlock', [slot, {'encoding': 'json', 'transactionDetails': 'signatures',
                                                          'maxSupportedTransactionVersion': 0, 'rewards': False,
                                                          'commitment': 'confirmed'}])
        if not block:
            return {'Chain': chain, 'height': slot, 'timestamp': None, 'tx_count': None, 'hash': None}
        return {'Chain': chain, 'height': slot, 'timestamp': block.get('blockTime'),
                'tx_count': len(block.get('signatures', [])), 'hash': block.get('blockhash')}
    block = json_rpc(cfg['rpc'], 'eth_getBlockByNumber', ['latest', False])
    if not block:
        return None
    return {'Chain': chain, 'height': int(block['number'], 16), 'timestamp': int(block['timestamp'], 16),
            'tx_count': max(len(block['transactions']) - cfg['system_txs'], 0), 'hash': block['hash']}


def get_recent_blocks(chain: str, n: int = 20) -> pd.DataFrame:
    """
    The last n blocks: height, timestamp, tx_count (user transactions only).
    Bitcoin: up to 30 blocks from mempool.space (15 per page).
    Solana:  one row per 60-second performance sample (slot timestamps are too coarse for single slots);
             tx_count is the average non-vote transactions per slot in that minute.
    """
    cfg = CHAINS[chain]
    if cfg['kind'] == 'bitcoin':
        rows = []
        page = _bitcoin_get('/v1/blocks') or []
        rows += page
        if page and len(rows) < n:
            rows += _bitcoin_get(f"/v1/blocks/{page[-1]['height'] - 1}") or []
        df = pd.DataFrame([{'height': b['height'], 'timestamp': b['timestamp'], 'tx_count': b['tx_count']} for b in rows[:n]])
    elif cfg['kind'] == 'solana':
        samples = json_rpc(cfg['rpc'], 'getRecentPerformanceSamples', [n]) or []
        df = pd.DataFrame([{'height': s['slot'], 'timestamp': None,
                            'tx_count': s['numNonVoteTransactions'] / s['numSlots'],
                            'slot_time': s['samplePeriodSecs'] / s['numSlots']} for s in samples if s['numSlots']])
    else:
        head = json_rpc(cfg['rpc'], 'eth_blockNumber', [])
        if head is None:
            return pd.DataFrame()
        head = int(head, 16)
        rows = []
        for number in range(head - n + 1, head + 1):
            block = json_rpc(cfg['rpc'], 'eth_getBlockByNumber', [hex(number), False])
            if block:
                rows.append({'height': number, 'timestamp': int(block['timestamp'], 16),
                             'tx_count': max(len(block['transactions']) - cfg['system_txs'], 0)})
        df = pd.DataFrame(rows)
    return df.sort_values('height').reset_index(drop=True) if not df.empty else df


def get_block_production(chain: str, n: int = 20, span: int = 1000) -> Optional[Dict]:
    """
    Block time, transactions per block and TPS.
    - Block time is averaged over a long span (EVM: `span` blocks) because block timestamps have
      1-second resolution: on chains with sub-second blocks, consecutive blocks share a timestamp.
    - Block interval variability (coefficient of variation) is only reported when blocks are at
      least 2 seconds apart; below that, 1-second timestamps cannot resolve individual intervals.
    """
    cfg = CHAINS[chain]
    blocks = get_recent_blocks(chain, n)
    if blocks.empty:
        print(f"[ERROR] {chain}: no block data")
        return None

    if cfg['kind'] == 'solana':
        block_time = float(blocks['slot_time'].mean())
        intervals = pd.Series(dtype=float)
    elif cfg['kind'] == 'bitcoin':
        block_time = (blocks['timestamp'].iloc[-1] - blocks['timestamp'].iloc[0]) / (blocks['height'].iloc[-1] - blocks['height'].iloc[0])
        intervals = blocks['timestamp'].diff().dropna()
    else:
        head = blocks.iloc[-1]
        past = json_rpc(cfg['rpc'], 'eth_getBlockByNumber', [hex(int(head['height']) - span), False])
        if past:
            block_time = (head['timestamp'] - int(past['timestamp'], 16)) / span
        else:
            block_time = (blocks['timestamp'].iloc[-1] - blocks['timestamp'].iloc[0]) / (len(blocks) - 1)
        intervals = blocks['timestamp'].diff().dropna()

    tx_per_block = float(blocks['tx_count'].mean())
    cv = float(intervals.std() / intervals.mean() * 100) if block_time >= 2 and len(intervals) > 2 and intervals.mean() > 0 else None
    return {
        'Chain': chain, 'Layer': cfg['layer'],
        'Block Time (s)': block_time,
        'Tx/Block': tx_per_block,
        'TPS': tx_per_block / block_time if block_time else None,
        'Block Time CV (%)': cv,
        'blocks': blocks,
        'intervals': intervals,
    }


# ---------------------------------------------------------------------------
# Finality: soft -> safe -> hard
# ---------------------------------------------------------------------------
BITCOIN_CONFIRMATIONS = {'soft': 1, 'safe': 3, 'hard': 6}


def get_finality_levels(chain: str, avg_block_time: Optional[float] = None) -> Optional[Dict]:
    """
    How far behind the chain head each commitment level is, in seconds and in blocks.

    EVM chains expose three block tags:
      'latest'    -> soft: produced by the current proposer / sequencer, can still be reorganized
      'safe'      -> safe: very unlikely to be reorganized (meaning differs per chain, see notebook 1a)
      'finalized' -> hard: cannot be reverted without breaking the protocol's safety guarantees
    Solana exposes the same idea as commitment levels: processed / confirmed / finalized.
    Bitcoin has no finality gadget: we use the 1 / 3 / 6 confirmations convention (a model, not a measurement).
    """
    cfg = CHAINS[chain]
    result = {'Chain': chain, 'Layer': cfg['layer'], 'Finality Type': cfg['finality_type']}

    if cfg['kind'] == 'bitcoin':
        block_time = avg_block_time or 600
        for level, confirmations in BITCOIN_CONFIRMATIONS.items():
            result[f'{level}_blocks'] = confirmations
            result[f'{level}_s'] = confirmations * block_time
        result['Method'] = 'model: 1/3/6 confirmations'
        return result

    if cfg['kind'] == 'solana':
        # Read the lagging levels first so the head cannot be overtaken between calls
        finalized = json_rpc(cfg['rpc'], 'getSlot', [{'commitment': 'finalized'}])
        confirmed = json_rpc(cfg['rpc'], 'getSlot', [{'commitment': 'confirmed'}])
        processed = json_rpc(cfg['rpc'], 'getSlot', [{'commitment': 'processed'}])
        if None in (finalized, confirmed, processed):
            return None
        slot_time = avg_block_time or 0.4
        result.update({
            'soft_blocks': 0, 'soft_s': 0.0,
            'safe_blocks': max(processed - confirmed, 0), 'safe_s': max(processed - confirmed, 0) * slot_time,
            'hard_blocks': max(processed - finalized, 0), 'hard_s': max(processed - finalized, 0) * slot_time,
            'Method': 'measured: commitment levels',
        })
        return result

    # EVM: read 'finalized' and 'safe' right before 'latest'; on BFT chains the finalized head moves
    # every block, so reading it later could overtake our 'latest' and give a negative lag
    finalized = json_rpc(cfg['rpc'], 'eth_getBlockByNumber', ['finalized', False])
    safe = json_rpc(cfg['rpc'], 'eth_getBlockByNumber', ['safe', False])
    latest = json_rpc(cfg['rpc'], 'eth_getBlockByNumber', ['latest', False])
    if not latest:
        return None
    head, head_ts = int(latest['number'], 16), int(latest['timestamp'], 16)
    result.update({'soft_blocks': 0, 'soft_s': 0.0, 'Method': "measured: block tags"})
    for level, block in (('safe', safe), ('hard', finalized)):
        if block:
            result[f'{level}_blocks'] = max(head - int(block['number'], 16), 0)
            result[f'{level}_s'] = float(max(head_ts - int(block['timestamp'], 16), 0))
        else:
            result[f'{level}_blocks'] = None
            result[f'{level}_s'] = None
    return result


# ---------------------------------------------------------------------------
# DefiLlama
# ---------------------------------------------------------------------------
def get_llama_tvl() -> pd.Series:
    """TVL per chain in USD, indexed by DefiLlama chain name."""
    data = api_call(f"{LLAMA_API}/v2/chains") or []
    return pd.Series({c['name']: c.get('tvl') or 0.0 for c in data}).sort_values(ascending=False)


def get_llama_24h_by_chain(kind: str = 'dexs') -> pd.Series:
    """
    24h DEX volume ('dexs') or fees ('fees') per chain in USD, indexed by DefiLlama slug.
    DefiLlama reports it per protocol, so we sum each protocol's per-chain breakdown.
    """
    params = {'excludeTotalDataChart': 'true', 'excludeTotalDataChartBreakdown': 'true'}
    data = api_call(f"{LLAMA_API}/overview/{kind}", params=params) or {}
    totals: Dict[str, float] = {}
    for protocol in data.get('protocols', []):
        for chain, by_version in (protocol.get('breakdown24h') or {}).items():
            totals[chain] = totals.get(chain, 0) + sum(v for v in by_version.values() if isinstance(v, (int, float)))
    totals.pop('off_chain', None)  # off-chain venues are not a blockchain
    return pd.Series(totals, dtype=float).sort_values(ascending=False)


def get_chain_dex_history(chain: str, days: int = 30) -> pd.Series:
    """
    Daily DEX volume (USD) on one chain for the last `days` complete days.
    DefiLlama's newest point is the current UTC day, still being filled in, so it is dropped.
    """
    name = CHAINS[chain]['llama_name']
    data = api_call(f"{LLAMA_API}/overview/dexs/{name}", params={'excludeTotalDataChartBreakdown': 'true'}) or {}
    points = data.get('totalDataChart') or []
    series = pd.Series({pd.to_datetime(ts, unit='s').normalize(): value for ts, value in points}, dtype=float)
    today_utc = pd.Timestamp.now(tz='UTC').tz_localize(None).normalize()
    return series[series.index < today_utc].sort_index().tail(days)


def get_stablecoins_by_chain(top_n: int = 10) -> pd.DataFrame:
    """Circulating supply (USD) of the largest stablecoins on each chain. Rows: stablecoin, columns: DefiLlama chain name."""
    data = api_call(f"{LLAMA_STABLECOINS_API}/stablecoins", params={'includePrices': 'false'}) or {}
    rows = {}
    for asset in (data.get('peggedAssets') or [])[:top_n]:
        rows[asset['symbol']] = {chain: (v.get('current') or {}).get('peggedUSD', 0) or 0
                                 for chain, v in asset.get('chainCirculating', {}).items()}
    return pd.DataFrame(rows).T.fillna(0)


LLAMA_CHART_MAX_POINTS = 500  # the chart endpoint rejects requests for more than coins x points = 500


def get_price_history(coingecko_ids: List[str], days: int = 30) -> pd.DataFrame:
    """Daily USD prices for several coins (DefiLlama coins API), batching coins per call. Columns: CoinGecko id."""
    per_call = max(1, LLAMA_CHART_MAX_POINTS // (days + 1))
    frame = {}
    for start in range(0, len(coingecko_ids), per_call):
        coins = ','.join(f'coingecko:{cid}' for cid in coingecko_ids[start:start + per_call])
        data = api_call(f"{LLAMA_COINS_API}/chart/{coins}", params={'span': days + 1, 'period': '1d'}) or {}
        for key, value in (data.get('coins') or {}).items():
            frame[key.split(':', 1)[1]] = {pd.to_datetime(p['timestamp'], unit='s').normalize(): p['price'] for p in value['prices']}
    return pd.DataFrame(frame).sort_index()


# ---------------------------------------------------------------------------
# CoinGecko and Hyperliquid
# ---------------------------------------------------------------------------
def get_coingecko_markets(coingecko_ids: List[str]) -> pd.DataFrame:
    """Price, market cap, 24h volume, 24h change and rank for many coins in one call."""
    data = api_call(f"{COINGECKO_API}/coins/markets",
                    params={'vs_currency': 'usd', 'ids': ','.join(coingecko_ids), 'price_change_percentage': '24h,7d,30d'}) or []
    return pd.DataFrame(data).set_index('id') if data else pd.DataFrame()


def get_coingecko_coin(coingecko_id: str) -> Optional[Dict]:
    """Full coin metadata (includes 'platforms': contract address per chain, and 'categories')."""
    return api_call(f"{COINGECKO_API}/coins/{coingecko_id}",
                    params={'localization': 'false', 'tickers': 'false', 'market_data': 'false',
                            'community_data': 'false', 'developer_data': 'false', 'sparkline': 'false'})


def get_coingecko_tickers(coingecko_id: str) -> List[Dict]:
    """Top 100 markets (by volume) where a coin trades, across centralized and decentralized exchanges."""
    data = api_call(f"{COINGECKO_API}/coins/{coingecko_id}/tickers", params={'depth': 'false', 'order': 'volume_desc'}) or {}
    return data.get('tickers', [])


def get_hyperliquid_perps() -> pd.DataFrame:
    """24h notional volume and open interest of every Hyperliquid perpetual market (USD)."""
    data = api_call(HYPERLIQUID_API, method='POST', json_data={'type': 'metaAndAssetCtxs'})
    if not data:
        return pd.DataFrame()
    meta, contexts = data
    rows = []
    for asset, ctx in zip(meta['universe'], contexts):
        mark = float(ctx.get('markPx') or 0)
        rows.append({'market': asset['name'], 'volume_24h': float(ctx.get('dayNtlVlm') or 0),
                     'open_interest': float(ctx.get('openInterest') or 0) * mark})
    return pd.DataFrame(rows).sort_values('volume_24h', ascending=False).reset_index(drop=True)
