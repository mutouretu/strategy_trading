"use strict";

const assert = require("node:assert/strict");
const {
  aggregateBars,
  applicationSummary,
  buildCatalog,
  candidateRows,
  companionStrategies,
  explicitStrategyDefinitionTypes,
  marketAsset,
  marketAssetFromInstrument,
  marketAssetGroups,
  pathSetMarkets,
  researchFocusGroups,
  resolvedParameters,
  scenarioRows,
  strategyDefinitionRuns,
  strategyExperimentGroups,
  strategyParameterAxisGroups,
  studyGroups,
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

assert.equal(marketAssetFromInstrument("AAVEUSD_PERP"), "AAVE");
assert.equal(marketAssetFromInstrument("BTCUSDT"), "BTC");
const pathSetMarketItems = pathSetMarkets([
  {
    path_set_id: "aave-set-v1",
    description: "AAVE paths",
    status: "CONTENT_LOCKED",
    scenarios: [{
      scenario_id: "aave-range-v1",
      name: "AAVE 宽幅震荡",
      instrument: "AAVEUSD_PERP",
      paths: [{market_seed: 1}, {market_seed: 2}],
    }],
  },
  {
    path_set_id: "btc-set-v1",
    description: "BTC paths",
    status: "CONTENT_LOCKED",
    scenarios: [{
      scenario_id: "btc-bull-v1",
      name: "BTC 牛市",
      instrument: "BTCUSD_PERP",
      paths: [{market_seed: 3}],
    }],
  },
]);
assert.equal(marketAsset(pathSetMarketItems[0]), "AAVE");
assert.deepEqual(
  marketAssetGroups(pathSetMarketItems).map((group) => [
    group.asset,
    group.scenario_count,
    group.path_count,
  ]),
  [["BTC", 1, 1], ["AAVE", 1, 2]],
);

const legacyDetail = JSON.parse(JSON.stringify(detail));
legacyDetail.spec.scenario_groups[0].strategies[0].type = "legacy-strategy/v1";
const aliasedCatalog = buildCatalog(
  [
    {
      experiment: {experiment_id: "legacy-experiment"},
      detail: legacyDetail,
      metrics: {aggregates: []},
      runs: [{...baseRun, run_id: "legacy-run", seed: 1}],
    },
  ],
  [
    {
      kind: "strategy",
      type: "canonical-strategy/v1",
      aliases: ["legacy-strategy/v1"],
      display_name: "Canonical Strategy",
    },
  ],
);
assert.equal(aliasedCatalog.strategies.length, 1);
assert.equal(aliasedCatalog.strategies[0].type, "canonical-strategy/v1");
assert.equal(
  aliasedCatalog.records[0].runs[0].resolved_components.strategy.source_type,
  "legacy-strategy/v1",
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
assert.equal(descriptorOnly.strategies.length, 0);

const strategyDefinitionOnly = buildCatalog([], [
  {
    kind: "strategy-definition",
    type: "entry-then-ladder-exit/v1",
    display_name: "Entry Then Ladder Exit",
    aliases: ["legacy-core-strategy/v1"],
    rule_composition: [
      {rule_key: "entry", rule_type: "initial-entry/v1"},
      {rule_key: "take-profit", rule_type: "ladder-take-profit/v1"},
    ],
  },
  {
    kind: "trading-rule",
    type: "initial-entry/v1",
    display_name: "Initial Entry",
  },
  {
    kind: "trading-rule",
    type: "ladder-take-profit/v1",
    display_name: "Ladder Exit",
  },
]);
assert.equal(strategyDefinitionOnly.strategyDefinitions.length, 1);
assert.equal(
  strategyDefinitionOnly.strategyDefinitions[0].id,
  "entry-then-ladder-exit/v1",
);
assert.deepEqual(
  strategyDefinitionOnly.rules.map((item) => item.strategies),
  [
    ["entry-then-ladder-exit/v1"],
    ["entry-then-ladder-exit/v1"],
  ],
);

const explicitDefinitionRecord = {
  experiment: {experiment_id: "failed-baseline"},
  detail: {
    spec: {
      metadata: {experiment_kind: "BASELINE"},
      scenario_groups: [{
        strategies: [{
          key: "failed-strategy",
          type: "simulation-plugin/v1",
          parameters: {
            strategy_definition_type: "entry-then-ladder-exit/v1",
          },
        }],
        markets: [],
        executions: [],
        accounts: [],
      }],
    },
  },
  metrics: {aggregates: []},
  runs: [],
};
const explicitDefinitionCatalog = buildCatalog(
  [explicitDefinitionRecord],
  [{
    kind: "strategy-definition",
    type: "entry-then-ladder-exit/v1",
    display_name: "Entry Then Ladder Exit",
    rule_composition: [],
  }],
);
assert.deepEqual(
  explicitStrategyDefinitionTypes(explicitDefinitionRecord.detail),
  ["entry-then-ladder-exit/v1"],
);
assert.deepEqual(
  explicitDefinitionCatalog.strategyDefinitions[0].experiments,
  ["failed-baseline"],
);
assert.equal(
  explicitDefinitionCatalog.strategyDefinitions[0]
    .experiment_records[0].strategy_definition_associations[0].source,
  "EXPERIMENT_SPEC",
);
assert.equal(
  studyGroups(
    explicitDefinitionCatalog.records,
    "entry-then-ladder-exit/v1",
  ).length,
  0,
);
assert.equal(
  strategyExperimentGroups(
    explicitDefinitionCatalog.strategyDefinitions[0],
  ).length,
  1,
);

const applicationCatalog = buildCatalog(
  [
    {
      experiment: {experiment_id: "application-experiment"},
      detail,
      metrics: {aggregates: []},
      runs: [{
        ...baseRun,
        run_id: "application-run",
        seed: 1,
        provider_summary: {
          provider_summary: {
            "strategies-simulation/v1": {
              research_focus: {
                primary_rule_type: "ladder-take-profit/v1",
                primary_strategy_instance_id: "strategy-1",
              },
              application: {
                strategy_count: 3,
                strategies: [
                  {
                    strategy_type: "strategy/v1",
                    strategy_instance_id: "strategy-1",
                    rules: [{
                      rule_key: "take-profit",
                      rule_type: "ladder-take-profit/v1",
                      rule_instance_id: "strategy-1:take-profit",
                      config: {level_count: 10},
                    }],
                  },
                  {
                    strategy_type: "helper/v1",
                    display_name: "Helper",
                    strategy_instance_id: "helper-1",
                    rules: [],
                  },
                  {
                    strategy_type: "helper/v1",
                    display_name: "Helper",
                    strategy_instance_id: "helper-2",
                    rules: [],
                  },
                ],
              },
            },
          },
        },
      }],
    },
  ],
  [
    {
      kind: "strategy",
      type: "strategy/v1",
      display_name: "Strategy A",
      research_focus: {primary_rule_type: "ladder-take-profit/v1"},
    },
    {
      kind: "trading-rule",
      type: "ladder-take-profit/v1",
      display_name: "Ladder Take Profit",
    },
  ],
);
assert.equal(applicationCatalog.rules.length, 1);
assert.equal(applicationCatalog.strategyDefinitions.length, 2);
assert.equal(
  applicationCatalog.strategyDefinitions.find(
    (item) => item.id === "strategy/v1",
  ).instances.length,
  1,
);
assert.equal(
  applicationCatalog.strategyDefinitions.find(
    (item) => item.id === "strategy/v1",
  ).run_instances.length,
  1,
);
assert.equal(
  applicationCatalog.strategyDefinitions.find(
    (item) => item.id === "helper/v1",
  ).instances.length,
  2,
);
assert.equal(applicationCatalog.rules[0].runs.length, 1);
assert.equal(applicationCatalog.rules[0].instances.length, 1);
assert.equal(applicationCatalog.rules[0].configurations.length, 1);
assert.equal(
  applicationSummary(applicationCatalog.records[0].runs[0])
    .research_focus.primary_rule_type,
  "ladder-take-profit/v1",
);
assert.deepEqual(
  companionStrategies(applicationCatalog.records[0].runs[0]),
  [{strategy_type: "helper/v1", display_name: "Helper", count: 2}],
);
assert.equal(researchFocusGroups(applicationCatalog.strategies).length, 1);
assert.equal(
  researchFocusGroups(applicationCatalog.strategies)[0].rule_type,
  "ladder-take-profit/v1",
);

function researchRecord({
  experimentId,
  databaseName,
  size,
  returnRate,
  tag = null,
}) {
  return {
    experiment: {
      experiment_id: experimentId,
      database_name: databaseName,
      description: `Research ${size}`,
      updated_at: `2026-08-0${size}T00:00:00Z`,
      reproducible: Boolean(tag),
    },
    detail: {
      code_revisions: {
        repository: {dirty: !tag, tag},
      },
      spec: {
        metadata: {experiment_kind: "PARAMETER_STUDY"},
        scenario_groups: [
          {
            strategies: [
              {
                key: `candidate-${size}`,
                type: "strategy/v1",
                parameters: {
                  strategy_definition_type: "strategy-definition/v1",
                  strategy_parameters: {
                    strategy_id: `identity-${size}`,
                    size,
                  },
                },
              },
            ],
            markets: [
              {
                key: "market-train",
                type: "locked-market-path/v1",
                parameters: {
                  path_set_id: "path-set-a",
                  scenario_id: "sideways",
                  role: "TRAIN",
                  market_seed: size,
                  market_path_id: `path-${size}`,
                  content_sha256: String(size).repeat(64),
                  instrument: "BTCUSDT",
                  interval: "1d",
                },
              },
            ],
            executions: [
              {key: "execution-a", type: "execution/v1", parameters: {}},
            ],
            accounts: [
              {key: "account-a", type: "account/v1", parameters: {}},
            ],
            parameter_axes: [{
              path: "/strategy/parameters/strategy_parameters/size",
              values: [size],
            }],
          },
        ],
      },
    },
    metrics: {aggregates: []},
    runs: [
      {
        run_id: `run-${experimentId}-${size}`,
        scenario_id: `scenario-${size}`,
        seed: size,
        status: "SUCCEEDED",
        components: {
          strategy: `candidate-${size}`,
          market: "market-train",
          execution: "execution-a",
          account: "account-a",
        },
        parameter_values: {},
        market_path_id: `path-${size}`,
        trace_state: "NOT_STORED",
        metric_scalars: {
          "core:return.total_rate{scope=account.total_equity,valuation_asset=USDT}": returnRate,
          "core:risk.max_drawdown_rate{scope=account.total_equity,valuation_asset=USDT}": "0.08",
          "core:run.liquidated": false,
          "core:execution.fill_count": size * 10,
          "core:margin.max_maintenance_utilization": String(size * 0.01),
          "core:margin.max_initial_utilization": String(size * 0.1),
          "core:margin.max_effective_leverage": String(size * 0.5),
          "btc-accumulation:strategy.entry_contracts": String(size * 100),
          "btc-accumulation:strategy.estimated_liquidation_price_after_entry": String(20000 + size * 1000),
        },
      },
    ],
  };
}

const studyCatalog = buildCatalog([
  researchRecord({
    experimentId: "parameter-study-a",
    databaseName: "parameter-study-a.sqlite3",
    size: 1,
    returnRate: "0.10",
  }),
  researchRecord({
    experimentId: "parameter-study-b",
    databaseName: "parameter-study-b.sqlite3",
    size: 2,
    returnRate: "0.20",
  }),
]);
const groupedStudies = studyGroups(
  studyCatalog.records,
  "strategy-definition/v1",
);
assert.equal(groupedStudies.length, 2);
assert.deepEqual(
  groupedStudies.map((study) => study.experiment_id).sort(),
  ["parameter-study-a", "parameter-study-b"],
);
const candidates = candidateRows(
  studyCatalog.records,
  "strategy-definition/v1",
  ["size"],
);
assert.equal(candidates.length, 2);
assert.deepEqual(candidates.map((candidate) => candidate.parameter_values.size), [1, 2]);
assert.deepEqual(candidates[0].varying_parameter_paths, ["size"]);
assert.equal(candidates[0].summary.return_median, 0.1);
assert.equal(candidates[0].summary.margin_risk_worst, 0.01);
assert.equal(candidates[0].summary.initial_margin_utilization_worst, 0.1);
assert.equal(candidates[0].summary.max_effective_leverage, 0.5);
assert.equal(candidates[0].summary.entry_contracts_median, 100);
assert.equal(candidates[0].summary.estimated_liquidation_price_median, 21000);
assert.equal(candidates[0].label, "size=1");

const zeroFundingRecord = researchRecord({
  experimentId: "funding-study",
  databaseName: "funding-study.sqlite3",
  size: 1,
  returnRate: "0.10",
});
zeroFundingRecord.runs[0].parameter_values = {
  "/execution/parameters/funding_rate": "0",
};
zeroFundingRecord.runs[0].metric_scalars[
  "core:funding.net_wallet_delta{valuation_asset=BTC}"
] = "0";
const paidFundingRecord = researchRecord({
  experimentId: "funding-study",
  databaseName: "funding-study.sqlite3",
  size: 1,
  returnRate: "0.08",
});
paidFundingRecord.runs[0].run_id = "run-funding-paid";
paidFundingRecord.runs[0].parameter_values = {
  "/execution/parameters/funding_rate": "0.0003",
};
paidFundingRecord.runs[0].metric_scalars[
  "core:funding.net_wallet_delta{valuation_asset=BTC}"
] = "-1.5";
const fundingCatalog = buildCatalog([
  zeroFundingRecord,
  paidFundingRecord,
]);
const fundingCandidates = candidateRows(
  fundingCatalog.records,
  "strategy-definition/v1",
  [],
);
assert.equal(fundingCandidates.length, 2);
assert.deepEqual(
  fundingCandidates.map(
    (candidate) => candidate.parameter_values["execution.funding_rate"],
  ).sort(),
  ["0", "0.0003"],
);
assert.deepEqual(
  fundingCandidates.map((candidate) => candidate.summary.funding_median).sort(),
  [-1.5, 0],
);

const unevaluatedRecord = researchRecord({
  experimentId: "unevaluated-study",
  databaseName: "unevaluated-study.sqlite3",
  size: 3,
  returnRate: "0.30",
});
unevaluatedRecord.runs[0].metric_scalars = {};
const unevaluatedCatalog = buildCatalog([unevaluatedRecord]);
const unevaluatedCandidate = candidateRows(
  unevaluatedCatalog.records,
  "strategy-definition/v1",
  ["size"],
)[0];
assert.equal(unevaluatedCandidate.summary.return_median, null);
assert.equal(unevaluatedCandidate.summary.liquidation_rate, null);

assert.equal(
  strategyDefinitionRuns(
    studyCatalog.records[0],
    "strategy-definition/v1",
  ).length,
  1,
);
const axisGroups = strategyParameterAxisGroups(
  studyCatalog.records[0],
  {
    parameters: [{
      key: "size",
      name: "仓位规模",
      maps_to: ["entry.quantity"],
    }],
    rule_composition: [{rule_key: "entry", rule_type: "entry/v1"}],
  },
);
assert.equal(axisGroups.length, 1);
assert.equal(axisGroups[0].scope, "RULE");
assert.equal(axisGroups[0].rules[0].rule_key, "entry");
assert.equal(axisGroups[0].axes[0].name, "仓位规模");

const executionAxisRecord = researchRecord({
  experimentId: "funding-axis-study",
  databaseName: "funding-axis-study.sqlite3",
  size: 1,
  returnRate: "0.10",
});
executionAxisRecord.detail.spec.scenario_groups[0].parameter_axes = [{
  path: "/execution/parameters/funding_rate",
  values: ["0", "0.0003"],
}];
const executionAxisGroups = strategyParameterAxisGroups(
  executionAxisRecord,
  {parameters: [], rule_composition: []},
);
assert.equal(executionAxisGroups.length, 1);
assert.equal(executionAxisGroups[0].scope, "EXECUTION");
assert.equal(
  executionAxisGroups[0].axes[0].parameter_path,
  "execution.funding_rate",
);

const versionCatalog = buildCatalog([
  researchRecord({
    experimentId: "same-experiment",
    databaseName: "dirty.sqlite3",
    size: 1,
    returnRate: "0.10",
  }),
  researchRecord({
    experimentId: "same-experiment",
    databaseName: "tagged.sqlite3",
    size: 1,
    returnRate: "0.10",
    tag: "research-v1.0.0",
  }),
]);
const versionStudy = studyGroups(
  versionCatalog.records,
  "strategy-definition/v1",
)[0];
assert.equal(versionStudy.preferred.experiment.database_name, "tagged.sqlite3");
assert.equal(versionStudy.versions.length, 2);
const versionExperiment = strategyExperimentGroups(
  versionCatalog.strategyDefinitions[0],
)[0];
assert.equal(versionExperiment.experiment_id, "same-experiment");
assert.equal(
  versionExperiment.preferred.experiment.database_name,
  "tagged.sqlite3",
);
assert.equal(versionExperiment.versions.length, 2);
assert.equal(versionExperiment.runs.length, 1);

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
