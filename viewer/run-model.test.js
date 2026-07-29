"use strict";

const assert = require("node:assert/strict");
const {normalizeRun} = require("./run-model.js");

function baseRun(schemaVersion) {
  return {
    schema_version: schemaVersion,
    manifest: {
      run_id: `viewer-v${schemaVersion}`,
      instrument: "BTCUSD",
      interval: "1d",
    },
    market: [
      {
        sequence: 0,
        timestamp: 1767225600000,
        date: "2026-01-01",
        instrument: "BTCUSD",
        open: "100",
        high: "105",
        low: "95",
        close: "102",
      },
    ],
    fills: [],
    equity: [],
    summary: {},
  };
}

const v1 = baseRun(1);
v1.orders = [
  {
    order_key: "legacy-buy",
    instrument: "BTCUSD",
    side: "BUY",
    order_type: "LIMIT",
    quantity: "1",
    price: "98",
    reduce_only: false,
    active_from_sequence: 0,
    active_to_sequence: null,
    status: "ACTIVE",
    tags: {},
  },
];
const normalizedV1 = normalizeRun(v1);
assert.equal(normalizedV1.intents.length, 1);
assert.equal(normalizedV1.intents[0].intent_key, "legacy-buy");
assert.equal(normalizedV1.intents[0].intent_mode, "PASSIVE");
assert.equal(normalizedV1.intents[0].target_price, 98);
assert.equal(normalizedV1.intents[0].status, "WAITING");
assert.deepEqual(normalizedV1.instructions, []);

const v2 = baseRun(2);
v2.intents = [
  {
    intent_key: "rsi-entry",
    instrument: "BTCUSD",
    intent_mode: "ACTIVE",
    side: "BUY",
    quantity: "1",
    target_price: null,
    reduce_only: false,
    active_from_sequence: 0,
    active_to_sequence: null,
    status: "WAITING",
    tags: {},
  },
];
v2.instructions = [
  {
    instruction_key: "rsi-entry:frame:0",
    source_intent_key: "rsi-entry",
    instrument: "BTCUSD",
    frame_sequence: 0,
    timestamp: 1767225600000,
    date: "2026-01-01",
    intent_mode: "ACTIVE",
    side: "BUY",
    price: "100",
    quantity: "1",
    reduce_only: false,
    tags: {},
  },
];
v2.fills = [
  {
    fill_id: "rsi-entry:frame:0@0",
    source_intent_key: "rsi-entry",
    instruction_key: "rsi-entry:frame:0",
    intent_mode: "ACTIVE",
    sequence: 0,
    timestamp: 1767225600000,
    side: "BUY",
    price: "100",
    quantity: "1",
    liquidity_role: "TAKER",
    fee_rate: "0.0005",
    fee_amount: "0.05",
    fee_asset: "USDT",
    reduce_only: false,
    tags: {},
  },
];
v2.funding_events = [
  {
    event_type: "FUNDING_SETTLEMENT",
    settlement_id: "funding:fixed:BTCUSD:1767225600000",
    sequence: 0,
    timestamp: 1767225600000,
    date: "2026-01-01",
    instrument: "BTCUSD",
    source: "FIXED",
    funding_rate: "0.0001",
    position_quantity: "1",
    mark_price: "100",
    mark_price_source: "market_frame_close",
    position_notional: "100",
    notional_asset: "USDT",
    position_value: "100",
    settlement_asset: "USDT",
    wallet_delta: "-0.01",
  },
];
const normalizedV2 = normalizeRun(v2);
assert.equal(normalizedV2.intents[0].target_price, null);
assert.equal(normalizedV2.instructions[0].price, 100);
assert.equal(normalizedV2.fills[0].source_intent_key, "rsi-entry");
assert.equal(normalizedV2.fills[0].liquidity_role, "TAKER");
assert.equal(normalizedV2.fills[0].fee_amount, 0.05);
assert.equal(normalizedV2.fills[0].reference_price, 100);
assert.equal(normalizedV2.fills[0].slippage_amount, 0);
assert.equal(normalizedV2.fills[0].slippage_bps, 0);
assert.equal(normalizedV2.funding_events.length, 1);
assert.equal(normalizedV2.funding_events[0].wallet_delta, -0.01);
assert.equal(normalizedV2.run_status.completed, true);
assert.deepEqual(normalizedV2.margin, []);
assert.deepEqual(normalizedV2.account_events, []);

const liquidatedV2 = baseRun(2);
liquidatedV2.intents = [];
liquidatedV2.instructions = [];
liquidatedV2.run_status = {
  completed: false,
  liquidated: true,
  bankrupt: false,
  termination_reason: "LIQUIDATION",
  termination_sequence: 0,
};
const liquidationSnapshot = {
  sequence: 0,
  timestamp: 1767225600000,
  date: "2026-01-01",
  instrument: "BTCUSD",
  settlement_asset: "BTC",
  notional_asset: "USD",
  mark_price: "95",
  mark_price_source: "market_ohlc_proxy",
  leverage: "5",
  position_quantity: "10",
  position_unit: "contracts",
  average_entry_price: "100",
  position_notional: "1000",
  wallet_balance: "0.003",
  unrealized_pnl: "-0.002",
  margin_balance: "0.001",
  position_initial_margin: "0.002",
  maintenance_margin: "0.0011",
  available_balance: "-0.001",
  margin_buffer: "-0.0001",
  initial_margin_utilization: "2",
  maintenance_margin_utilization: "1.1",
  effective_leverage: "10",
  estimated_liquidation_price: "95.5",
  liquidation_triggered: true,
  bankrupt: false,
};
liquidatedV2.margin = [liquidationSnapshot];
liquidatedV2.account_events = [
  {
    event_type: "LIQUIDATION",
    sequence: 0,
    timestamp: 1767225600000,
    date: "2026-01-01",
    instrument: "BTCUSD",
    mark_price_sampling: "ADVERSE_EXTREME",
    maintenance_schedule_version: "fixture-v1",
    intrabar_ordering_ambiguous: true,
    bankrupt: false,
    snapshot: liquidationSnapshot,
  },
];
const normalizedLiquidatedV2 = normalizeRun(liquidatedV2);
assert.equal(normalizedLiquidatedV2.run_status.liquidated, true);
assert.equal(normalizedLiquidatedV2.margin[0].mark_price, 95);
assert.equal(
  normalizedLiquidatedV2.account_events[0].mark_price_sampling,
  "ADVERSE_EXTREME",
);
assert.equal(
  normalizedLiquidatedV2.account_events[0]
    .intrabar_ordering_ambiguous,
  true,
);

const invalidV2 = baseRun(2);
assert.throws(
  () => normalizeRun(invalidV2),
  /schema v2 缺少 intents 数组/,
);
