"""Index every block and every ERC-20 Transfer event of a chain into Postgres.

Subscribes to new blocks over a websocket (newHeads) and, for each block, stores the block and
its Transfer logs. On start it backfills whatever it missed; if the chain it finds does not
match the stored one (anvil restarted, or a reorg), it starts over.

Configuration (environment variables):
  WS_URL         websocket endpoint of the node (default: ws://anvil:8545, the compose service)
  DATABASE_URL   Postgres connection string
  START_BLOCK    first block when the database is empty: a number, or "latest" (default 0).
                 Use "latest" on a real chain: Polygon has ~100 million blocks
"""
import asyncio
import os

import psycopg
from web3 import AsyncWeb3, Web3, WebSocketProvider
from web3.middleware import ExtraDataToPOAMiddleware

WS_URL = os.environ.get("WS_URL", "ws://anvil:8545")
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://quant:quant@postgres:5432/chain")
START_BLOCK = os.environ.get("START_BLOCK", "0")
TRANSFER_TOPIC = Web3.keccak(text="Transfer(address,address,uint256)").to_0x_hex()

SCHEMA = """
CREATE TABLE IF NOT EXISTS blocks (
    number      BIGINT PRIMARY KEY,
    hash        TEXT NOT NULL,
    timestamp   TIMESTAMPTZ NOT NULL,
    tx_count    INT NOT NULL
);
CREATE TABLE IF NOT EXISTS transfers (
    block_number BIGINT NOT NULL REFERENCES blocks(number) ON DELETE CASCADE,
    tx_hash      TEXT NOT NULL,
    log_index    INT NOT NULL,
    token        TEXT NOT NULL,
    from_address TEXT NOT NULL,
    to_address   TEXT NOT NULL,
    value        NUMERIC(78, 0) NOT NULL,   -- uint256 does not fit in BIGINT
    PRIMARY KEY (tx_hash, log_index)
);
CREATE OR REPLACE VIEW balances AS
SELECT token, address, SUM(delta) AS balance
FROM (SELECT token, to_address AS address, value AS delta FROM transfers
      UNION ALL
      SELECT token, from_address, -value FROM transfers) moves
WHERE address <> '0x0000000000000000000000000000000000000000'
GROUP BY token, address;
"""


def topic_to_address(topic):
    # An indexed address is the last 20 bytes of a 32-byte topic
    return Web3.to_checksum_address(topic[-20:])


async def index_block(w3, db, number):
    # Store one block and its ERC-20 Transfer logs (ERC-721 Transfers have 4 topics: skipped)
    block = await w3.eth.get_block(number)
    logs = await w3.eth.get_logs({"fromBlock": number, "toBlock": number, "topics": [TRANSFER_TOPIC]})
    with db.transaction():
        db.execute("INSERT INTO blocks VALUES (%s, %s, to_timestamp(%s), %s) ON CONFLICT DO NOTHING",
                   (number, block["hash"].to_0x_hex(), block["timestamp"], len(block["transactions"])))
        for log in logs:
            if len(log["topics"]) != 3:
                continue
            row = (number, log["transactionHash"].to_0x_hex(), log["logIndex"], log["address"],
                   topic_to_address(log["topics"][1]), topic_to_address(log["topics"][2]), int(log["data"].hex() or "0", 16))
            db.execute("INSERT INTO transfers VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING", row)
            print(f"block {number}: Transfer {row[6]} of {row[3]} {row[4]} -> {row[5]}", flush=True)
    return len(block["transactions"]), len(logs)


async def first_block(w3):
    # Where to start on an empty database (START_BLOCK)
    return await w3.eth.block_number if START_BLOCK == "latest" else int(START_BLOCK)


async def resume_point(w3, db):
    # Next block to index; wipes the tables if the stored chain is not the node's chain
    last = db.execute("SELECT number, hash FROM blocks ORDER BY number DESC LIMIT 1").fetchone()
    if last is None:
        return await first_block(w3)
    head = await w3.eth.block_number
    same_chain = last[0] <= head and (await w3.eth.get_block(last[0]))["hash"].to_0x_hex() == last[1]
    if not same_chain:
        print(f"stored block {last[0]} is not on this chain (node restarted or reorg): starting over", flush=True)
        db.execute("TRUNCATE blocks CASCADE")
        return await first_block(w3)
    return last[0] + 1


async def run(db):
    async with AsyncWeb3(WebSocketProvider(WS_URL)) as w3:
        if await w3.eth.chain_id in (137, 80002):
            w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
        start, head = await resume_point(w3, db), await w3.eth.block_number
        print(f"connected to {WS_URL} (chain {await w3.eth.chain_id}): backfilling blocks {start}..{head}", flush=True)
        for number in range(start, head + 1):
            await index_block(w3, db, number)

        next_block = head + 1
        await w3.eth.subscribe("newHeads")
        async for message in w3.socket.process_subscriptions():
            number = message["result"]["number"]
            for missing in range(next_block, number + 1):   # never skip a block, even if a head was missed
                txs, transfers = await index_block(w3, db, missing)
                print(f"block {missing}: {txs} txs, {transfers} transfers", flush=True)
            next_block = max(next_block, number + 1)


async def main():
    while True:   # reconnect forever: the node or the database may restart
        try:
            with psycopg.connect(DATABASE_URL, autocommit=True) as db:
                db.execute(SCHEMA)
                await run(db)
        except Exception as err:
            print(f"[WARN] {type(err).__name__}: {str(err)[:150]}; reconnecting in 3 s", flush=True)
            await asyncio.sleep(3)


if __name__ == "__main__":
    asyncio.run(main())
