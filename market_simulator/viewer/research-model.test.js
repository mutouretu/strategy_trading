"use strict";

const assert = require("node:assert/strict");
const {
  aggregateBars,
  buildCatalog,
  resolvedParameters,
  scenarioRows,
} = require("./research-model.js");

const detail = {
  spec: {
    scenario_groups: [
      {
        strategies: [
          {key: "candidate-a", type: "strategy/v1", parameters: {size: "1"}},
        ],
        markets: [
          {key: "market-a", type: "market/v1", parameters: {symbol: "BTC"}},
        ],
        executions: [
          {key: "execution-a", type: "execution/v1", parameters: {}},
        ],
        accounts: [
          {key: "account-a", type: "account/v1", parameters: {}},
        ],
      },
    ],
  },
};

const baseRun = {
  scenario_id: "scenario-a",
  status: "SUCCEEDED",
  components: {
    strategy: "candidate-a",
    market: "market-a",
    execution: "execution-a",
    account: "account-a",
  },
  parameter_values: {"/strategy/parameters/size": "2"},
  market_path_id: "path-a",
  trace_state: "STORED",
};

const catalog = buildCatalog(
  [
    {
      experiment: {
        experiment_id: "experiment-a",
        description: "research",
      },
      detail,
      metrics: {aggregates: []},
      runs: [
        {...baseRun, run_id: "run-42", seed: 42},
        {...baseRun, run_id: "run-43", seed: 43, market_path_id: "path-b"},
      ],
    },
  ],
  [
    {
      kind: "strategy",
      type: "strategy/v1",
      display_name: "Strategy A",
    },
  ],
);

assert.equal(catalog.strategies.length, 1);
assert.equal(catalog.strategies[0].id, "strategy/v1");
assert.equal(catalog.strategies[0].descriptor.display_name, "Strategy A");
assert.equal(catalog.strategies[0].configurations.length, 1);
assert.equal(catalog.strategies[0].configurations[0].parameters.size, "2");
assert.equal(catalog.markets.length, 1);
assert.deepEqual(catalog.markets[0].paths.map((path) => path.seed), [42, 43]);
assert.deepEqual(
  scenarioRows(catalog.records[0])[0].seeds,
  [42, 43],
);
assert.equal(
  resolvedParameters(
    "strategy",
    {parameters: {nested: {value: 1}}},
    {"/strategy/parameters/nested/value": 2},
  ).nested.value,
  2,
);

const descriptorOnly = buildCatalog([], [
  {
    kind: "strategy",
    type: "registered-only/v1",
    display_name: "Registered Only",
    formulae: ["x = 1"],
  },
]);
assert.equal(descriptorOnly.strategies.length, 1);
assert.equal(descriptorOnly.strategies[0].runs.length, 0);
assert.equal(descriptorOnly.strategies[0].configurations.length, 0);
assert.equal(descriptorOnly.strategies[0].descriptor.formulae[0], "x = 1");

const daily = [
  {date: "2026-01-30", open: 100, high: 110, low: 90, close: 105},
  {date: "2026-01-31", open: 105, high: 115, low: 95, close: 110},
  {date: "2026-02-01", open: 110, high: 120, low: 100, close: 115},
];
const monthly = aggregateBars(daily, "1m");
assert.equal(monthly.length, 2);
assert.deepEqual(
  [monthly[0].open, monthly[0].high, monthly[0].low, monthly[0].close],
  [100, 115, 90, 110],
);

const intraday = [
  {date: "2026-01-30", open: 100, high: 102, low: 99, close: 101},
  {date: "2026-01-30", open: 101, high: 105, low: 100, close: 104},
  {date: "2026-01-31", open: 104, high: 106, low: 103, close: 105},
];
const aggregatedDaily = aggregateBars(intraday, "1d");
assert.equal(aggregatedDaily.length, 2);
assert.deepEqual(
  [
    aggregatedDaily[0].open,
    aggregatedDaily[0].high,
    aggregatedDaily[0].low,
    aggregatedDaily[0].close,
  ],
  [100, 105, 99, 104],
);
