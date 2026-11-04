-- Schema of the arbitrage monitor. Runs once, the first time the postgres volume is created.

-- One row per block: the context needed to price gas.
CREATE TABLE blocks (
    block_number  BIGINT PRIMARY KEY,
    block_time    TIMESTAMPTZ NOT NULL,
    gas_price_gwei NUMERIC NOT NULL,   -- eth_gasPrice at collection time (gwei)
    pol_price_usd  NUMERIC NOT NULL    -- WPOL price in USD, from the QuickSwap WPOL/USDC.e pool
);

-- One row per pool per block. Raw units (wei-like integers), NOT token units.
CREATE TABLE pool_states (
    block_number BIGINT NOT NULL,
    block_time   TIMESTAMPTZ NOT NULL,
    dex          TEXT NOT NULL,        -- SushiSwap | QuickSwap | UniV3-0.05% | UniV3-0.3%
    pair         TEXT NOT NULL,        -- WETH/USDC.e | WPOL/USDC.e | WETH/WPOL
    pool         TEXT NOT NULL,        -- pool contract address
    kind         TEXT NOT NULL CHECK (kind IN ('v2', 'v3')),
    token0       TEXT NOT NULL,        -- symbol of token0 (the pool sorts tokens by address)
    token1       TEXT NOT NULL,
    fee_ppm      INTEGER NOT NULL,     -- fee in parts per million: 3000 = 0.3%, 500 = 0.05%
    reserve0     NUMERIC NOT NULL,     -- v2: real reserve. v3: VIRTUAL reserve x = L / sqrtP
    reserve1     NUMERIC NOT NULL,     -- v2: real reserve. v3: VIRTUAL reserve y = L * sqrtP
    sqrt_price   NUMERIC,              -- v3 only: sqrtPriceX96
    liquidity    NUMERIC,              -- v3 only: in-range liquidity L
    tick         INTEGER,              -- v3 only: current tick
    price_usd    NUMERIC,              -- mid price in USD of the non-USDC.e token (WETH/WPOL pool: WETH)
    PRIMARY KEY (block_number, pool)
);
CREATE INDEX pool_states_time ON pool_states (block_time);

-- One row per candidate cycle per block. Every cycle starts and ends in USDC.e.
CREATE TABLE opportunities (
    block_number     BIGINT NOT NULL,
    block_time       TIMESTAMPTZ NOT NULL,
    cycle            TEXT NOT NULL,     -- e.g. 'USDC.e→WETH (SushiSwap) → USDC.e (UniV3-0.05%)'
    n_legs           INTEGER NOT NULL,
    marginal_product DOUBLE PRECISION NOT NULL,  -- g1*...*gn * prod(r_out/r_in); > 1 means an opportunity
    gap_bps          DOUBLE PRECISION NOT NULL,  -- (marginal_product - 1) * 10000
    dx_star_usdc     DOUBLE PRECISION NOT NULL,  -- optimal size, 0 if no opportunity. V3 legs: in-range approximation!
    gross_usdc       DOUBLE PRECISION NOT NULL,  -- cycle output - dx*
    flash_fee_usdc   DOUBLE PRECISION NOT NULL,  -- 0.05% of dx*
    gas_usdc         DOUBLE PRECISION NOT NULL,  -- gas units * gas price * POL price
    net_usdc         DOUBLE PRECISION NOT NULL,  -- gross - flash fee - gas (0 if no opportunity)
    profitable       BOOLEAN NOT NULL,
    PRIMARY KEY (block_number, cycle)
);
CREATE INDEX opportunities_time ON opportunities (block_time);
