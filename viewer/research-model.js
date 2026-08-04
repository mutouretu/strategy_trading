(function exposeExperimentResearchModel(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  } else {
    root.ExperimentResearchModel = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this, () => {
  "use strict";

  function canonical(value) {
    if (Array.isArray(value)) return value.map(canonical);
    if (value && typeof value === "object") {
      return Object.fromEntries(
        Object.keys(value)
          .sort()
          .map((key) => [key, canonical(value[key])]),
      );
    }
    return value;
  }

  function stableJson(value) {
    return JSON.stringify(canonical(value));
  }

  function clone(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function componentIndex(detail, componentName) {
    const result = new Map();
    const collectionName = componentName === "strategy"
      ? "strategies"
      : `${componentName}s`;
    (detail?.spec?.scenario_groups || []).forEach((group) => {
      (group[collectionName] || []).forEach((component) => {
        result.set(component.key, component);
      });
    });
    return result;
  }

  function resolvedParameters(componentName, component, parameterValues) {
    const result = clone(component?.parameters || {});
    Object.entries(parameterValues || {}).forEach(([path, value]) => {
      const parts = path.split("/").filter(Boolean);
      if (parts[0] !== componentName || parts[1] !== "parameters") return;
      let cursor = result;
      parts.slice(2, -1).forEach((part) => {
        if (!cursor[part] || typeof cursor[part] !== "object") {
          cursor[part] = {};
        }
        cursor = cursor[part];
      });
      if (parts.length > 2) cursor[parts.at(-1)] = value;
    });
    return result;
  }

  function decorateRecord(record) {
    const indexes = Object.fromEntries(
      ["market", "strategy", "execution", "account"].map((name) => [
        name,
        componentIndex(record.detail, name),
      ]),
    );
    const runs = record.runs.map((run) => {
      const components = {};
      Object.entries(indexes).forEach(([name, index]) => {
        const component = index.get(run.components?.[name]);
        components[name] = component
          ? {
              ...component,
              parameters: resolvedParameters(
                name,
                component,
                run.parameter_values,
              ),
            }
          : {
              key: run.components?.[name] || "unknown",
              type: "unknown",
              parameters: {},
            };
      });
      return {...run, resolved_components: components};
    });
    return {...record, runs};
  }

  function statusCounts(runs) {
    return runs.reduce((counts, run) => {
      counts[run.status] = (counts[run.status] || 0) + 1;
      return counts;
    }, {});
  }

  function buildCatalog(rawRecords, descriptors = []) {
    const records = rawRecords.map(decorateRecord);
    const strategyMap = new Map();
    const marketMap = new Map();
    const strategyDescriptors = new Map(
      descriptors
        .filter((item) => item.kind === "strategy")
        .map((item) => [item.type, item]),
    );

    strategyDescriptors.forEach((descriptor, strategyId) => {
      strategyMap.set(strategyId, {
        id: strategyId,
        type: strategyId,
        descriptor,
        keys: new Set(),
        experiments: new Set(),
        markets: new Set(),
        configurations: new Map(),
        runs: [],
        descriptions: new Set(),
      });
    });

    records.forEach((record) => {
      record.runs.forEach((run) => {
        const strategy = run.resolved_components.strategy;
        const strategyId = strategy.type || strategy.key;
        if (!strategyMap.has(strategyId)) {
          strategyMap.set(strategyId, {
            id: strategyId,
            type: strategy.type,
            descriptor: strategyDescriptors.get(strategyId) || null,
            keys: new Set(),
            experiments: new Set(),
            markets: new Set(),
            configurations: new Map(),
            runs: [],
            descriptions: new Set(),
          });
        }
        const strategyEntry = strategyMap.get(strategyId);
        strategyEntry.keys.add(strategy.key);
        strategyEntry.experiments.add(record.experiment.experiment_id);
        strategyEntry.runs.push({record, run});
        if (record.experiment.description) {
          strategyEntry.descriptions.add(record.experiment.description);
        }
        const configurationId = stableJson(strategy.parameters);
        if (!strategyEntry.configurations.has(configurationId)) {
          strategyEntry.configurations.set(configurationId, {
            id: configurationId,
            key: strategy.key,
            parameters: strategy.parameters,
            run_count: 0,
          });
        }
        strategyEntry.configurations.get(configurationId).run_count += 1;

        const market = run.resolved_components.market;
        const marketId = stableJson({
          key: market.key,
          type: market.type,
          parameters: market.parameters,
        });
        strategyEntry.markets.add(marketId);
        if (!marketMap.has(marketId)) {
          marketMap.set(marketId, {
            id: marketId,
            key: market.key,
            type: market.type,
            parameters: market.parameters,
            experiments: new Set(),
            strategies: new Set(),
            paths: new Map(),
            runs: [],
          });
        }
        const marketEntry = marketMap.get(marketId);
        marketEntry.experiments.add(record.experiment.experiment_id);
        marketEntry.strategies.add(strategyId);
        marketEntry.runs.push({record, run});
        const pathKey = run.market_path_id || `${run.seed}:${run.run_id}`;
        if (!marketEntry.paths.has(pathKey)) {
          marketEntry.paths.set(pathKey, {
            market_path_id: run.market_path_id,
            seed: run.seed,
            experiment_id: record.experiment.experiment_id,
            run_id: run.run_id,
            trace_state: run.trace_state,
          });
        }
      });
    });

    const strategies = [...strategyMap.values()].map((item) => ({
      ...item,
      keys: [...item.keys].sort(),
      experiments: [...item.experiments].sort(),
      markets: [...item.markets],
      configurations: [...item.configurations.values()],
      descriptions: [...item.descriptions],
      status_counts: statusCounts(item.runs.map(({run}) => run)),
    }));
    strategies.sort((left, right) => left.id.localeCompare(right.id));

    const markets = [...marketMap.values()].map((item) => ({
      ...item,
      experiments: [...item.experiments].sort(),
      strategies: [...item.strategies].sort(),
      paths: [...item.paths.values()].sort(
        (left, right) => left.seed - right.seed,
      ),
      status_counts: statusCounts(item.runs.map(({run}) => run)),
    }));
    markets.sort((left, right) =>
      left.key.localeCompare(right.key) || left.type.localeCompare(right.type),
    );
    return {records, strategies, markets};
  }

  function scenarioRows(record) {
    const groups = new Map();
    record.runs.forEach((run) => {
      if (!groups.has(run.scenario_id)) {
        groups.set(run.scenario_id, {
          scenario_id: run.scenario_id,
          strategy: run.resolved_components.strategy,
          market: run.resolved_components.market,
          execution: run.resolved_components.execution,
          account: run.resolved_components.account,
          parameter_values: run.parameter_values,
          runs: [],
        });
      }
      groups.get(run.scenario_id).runs.push(run);
    });
    return [...groups.values()].map((group) => ({
      ...group,
      seeds: group.runs.map((run) => run.seed).sort((a, b) => a - b),
      status_counts: statusCounts(group.runs),
      aggregates: (record.metrics?.aggregates || []).filter(
        (item) => item.scenario_id === group.scenario_id,
      ),
    }));
  }

  function weekStart(dateText) {
    const date = new Date(`${dateText}T00:00:00Z`);
    const day = date.getUTCDay() || 7;
    date.setUTCDate(date.getUTCDate() - day + 1);
    return date.toISOString().slice(0, 10);
  }

  function aggregateBars(bars, interval) {
    const groups = new Map();
    bars.forEach((bar) => {
      const key = interval === "1d"
        ? String(bar.date).slice(0, 10)
        : interval === "1w"
          ? weekStart(bar.date)
          : String(bar.date).slice(0, 7);
      if (!groups.has(key)) {
        groups.set(key, {
          sequence: bar.sequence,
          timestamp: bar.timestamp,
          date: interval === "1d" || interval === "1w" ? key : `${key}-01`,
          instrument: bar.instrument,
          open: Number(bar.open),
          high: Number(bar.high),
          low: Number(bar.low),
          close: Number(bar.close),
        });
        return;
      }
      const aggregate = groups.get(key);
      aggregate.high = Math.max(aggregate.high, Number(bar.high));
      aggregate.low = Math.min(aggregate.low, Number(bar.low));
      aggregate.close = Number(bar.close);
    });
    return [...groups.values()];
  }

  return {
    aggregateBars,
    buildCatalog,
    decorateRecord,
    resolvedParameters,
    scenarioRows,
    stableJson,
  };
});
