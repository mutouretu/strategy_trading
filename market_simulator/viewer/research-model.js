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

  function applicationSummary(run) {
    const summary = run?.provider_summary || {};
    if (summary.application) return summary;
    const providers = summary.provider_summary;
    if (!providers || typeof providers !== "object") return null;
    return Object.values(providers).find(
      (item) => item && typeof item === "object" && item.application,
    ) || null;
  }

  function companionStrategies(run) {
    const summary = applicationSummary(run);
    const strategies = summary?.application?.strategies || [];
    if (strategies.length <= 1) return [];
    const primaryId = summary?.research_focus?.primary_strategy_instance_id
      || summary?.strategy_instance_id
      || strategies[0]?.strategy_instance_id;
    const grouped = new Map();
    strategies
      .filter((item) => item.strategy_instance_id !== primaryId)
      .forEach((item) => {
        const type = item.strategy_type || "unknown-strategy";
        const name = item.display_name || type;
        const key = `${type}\u0000${name}`;
        if (!grouped.has(key)) {
          grouped.set(key, {strategy_type: type, display_name: name, count: 0});
        }
        grouped.get(key).count += 1;
      });
    return [...grouped.values()].sort((left, right) =>
      left.display_name.localeCompare(right.display_name)
      || left.strategy_type.localeCompare(right.strategy_type),
    );
  }

  function buildCatalog(rawRecords, descriptors = []) {
    const strategyMap = new Map();
    const marketMap = new Map();
    const strategyDefinitionItems = descriptors.filter(
      (item) => item.kind === "strategy-definition",
    );
    const strategyDefinitionAliases = new Map();
    const strategyDefinitionMap = new Map(
      strategyDefinitionItems.map((descriptor) => {
        (descriptor.aliases || []).forEach((alias) => {
          strategyDefinitionAliases.set(alias, descriptor.type);
        });
        return [
          descriptor.type,
          {
            id: descriptor.type,
            type: descriptor.type,
            descriptor,
            experiments: new Set(),
            instances: new Set(),
            configurations: new Map(),
            run_instances: [],
            runs: [],
          },
        ];
      }),
    );
    const ruleDescriptorItems = descriptors.filter(
      (item) => item.kind === "trading-rule",
    );
    const ruleMap = new Map(ruleDescriptorItems.map((descriptor) => [
      descriptor.type,
      {
        id: descriptor.type,
        type: descriptor.type,
        descriptor,
        strategies: new Set(),
        experiments: new Set(),
        instances: new Set(),
        configurations: new Map(),
        runs: [],
      },
    ]));
    const strategyDescriptorItems = descriptors.filter(
      (item) => item.kind === "strategy",
    );
    const strategyDescriptors = new Map(
      strategyDescriptorItems.map((item) => [item.type, item]),
    );
    const strategyTypeAliases = new Map();
    strategyDescriptorItems.forEach((descriptor) => {
      (descriptor.aliases || []).forEach((alias) => {
        strategyTypeAliases.set(alias, descriptor.type);
      });
    });
    const records = rawRecords.map(decorateRecord).map((record) => ({
      ...record,
      runs: record.runs.map((run) => {
        const strategy = run.resolved_components.strategy;
        const canonicalType = strategyTypeAliases.get(strategy.type)
          || strategy.type;
        if (canonicalType === strategy.type) return run;
        return {
          ...run,
          resolved_components: {
            ...run.resolved_components,
            strategy: {
              ...strategy,
              source_type: strategy.type,
              type: canonicalType,
            },
          },
        };
      }),
    }));

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

        const application = applicationSummary(run)?.application;
        (application?.strategies || []).forEach((applicationStrategy) => {
          const sourceStrategyType = applicationStrategy.strategy_type
            || "unknown-strategy";
          const applicationStrategyType = strategyDefinitionAliases.get(
            sourceStrategyType,
          ) || sourceStrategyType;
          if (!strategyDefinitionMap.has(applicationStrategyType)) {
            strategyDefinitionMap.set(applicationStrategyType, {
              id: applicationStrategyType,
              type: applicationStrategyType,
              descriptor: null,
              experiments: new Set(),
              instances: new Set(),
              configurations: new Map(),
              run_instances: [],
              runs: [],
            });
          }
          const definitionEntry = strategyDefinitionMap.get(
            applicationStrategyType,
          );
          definitionEntry.experiments.add(record.experiment.experiment_id);
          if (applicationStrategy.strategy_instance_id) {
            definitionEntry.instances.add(
              applicationStrategy.strategy_instance_id,
            );
          }
          definitionEntry.runs.push({record, run});
          definitionEntry.run_instances.push({
            record,
            run,
            strategy: applicationStrategy,
          });
          const strategyParameters = applicationStrategy.parameters || {};
          const strategyConfigId = stableJson(strategyParameters);
          if (!definitionEntry.configurations.has(strategyConfigId)) {
            definitionEntry.configurations.set(strategyConfigId, {
              id: strategyConfigId,
              key: applicationStrategy.strategy_spec_id
                || applicationStrategy.strategy_instance_id
                || applicationStrategyType,
              parameters: strategyParameters,
              binding: applicationStrategy.binding || {},
              allocation: applicationStrategy.allocation || null,
              run_count: 0,
            });
          }
          definitionEntry.configurations.get(strategyConfigId).run_count += 1;
          (applicationStrategy.rules || []).forEach((rule) => {
            const ruleType = rule.rule_type || "unknown-rule";
            if (!ruleMap.has(ruleType)) {
              ruleMap.set(ruleType, {
                id: ruleType,
                type: ruleType,
                descriptor: null,
                strategies: new Set(),
                experiments: new Set(),
                instances: new Set(),
                configurations: new Map(),
                runs: [],
              });
            }
            const entry = ruleMap.get(ruleType);
            entry.strategies.add(applicationStrategyType || strategyId);
            entry.experiments.add(record.experiment.experiment_id);
            if (rule.rule_instance_id) entry.instances.add(rule.rule_instance_id);
            entry.runs.push({record, run});
            const configId = stableJson(rule.config || {});
            if (!entry.configurations.has(configId)) {
              entry.configurations.set(configId, {
                id: configId,
                key: rule.rule_key,
                parameters: rule.config || {},
                run_count: 0,
              });
            }
            entry.configurations.get(configId).run_count += 1;
          });
        });

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
            source: "EXPERIMENT",
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
            source: "EXPERIMENT",
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

    strategyDefinitionItems.forEach((descriptor) => {
      (descriptor.rule_composition || []).forEach((rule) => {
        const entry = ruleMap.get(rule.rule_type);
        if (entry) entry.strategies.add(descriptor.type);
      });
    });

    const strategyDefinitions = [...strategyDefinitionMap.values()].map(
      (item) => ({
        ...item,
        experiments: [...item.experiments].sort(),
        instances: [...item.instances].sort(),
        configurations: [...item.configurations.values()],
        status_counts: statusCounts(item.runs.map(({run}) => run)),
      }),
    ).sort((left, right) => left.id.localeCompare(right.id));

    strategies.forEach((strategy) => {
      (strategy.descriptor?.rule_composition || []).forEach((rule) => {
        const entry = ruleMap.get(rule.rule_type);
        if (entry) {
          entry.strategies.add(strategy.type);
          strategy.runs.forEach(({record, run}) => {
            if (!entry.runs.some((item) => item.run.run_id === run.run_id)) {
              entry.runs.push({record, run});
              entry.experiments.add(record.experiment.experiment_id);
            }
          });
        }
      });
    });

    const rules = [...ruleMap.values()].map((item) => ({
      ...item,
      strategies: [...item.strategies].sort(),
      experiments: [...item.experiments].sort(),
      instances: [...item.instances].sort(),
      configurations: [...item.configurations.values()],
      status_counts: statusCounts(item.runs.map(({run}) => run)),
    })).sort((left, right) => left.id.localeCompare(right.id));

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
    return {records, strategies, strategyDefinitions, rules, markets};
  }

  function researchFocusGroups(strategies) {
    const groups = new Map();
    strategies.forEach((strategy) => {
      const focus = strategy.descriptor?.research_focus || {};
      const ruleType = focus.primary_rule_type || null;
      const id = ruleType || `legacy-strategy:${strategy.type}`;
      if (!groups.has(id)) {
        groups.set(id, {
          id,
          rule_type: ruleType,
          primary_rule_key: focus.primary_rule_key || null,
          strategies: [],
        });
      }
      groups.get(id).strategies.push(strategy);
    });
    return [...groups.values()].sort((left, right) =>
      left.id.localeCompare(right.id),
    );
  }

  function pathSetMarkets(pathSets) {
    return (pathSets || []).flatMap((pathSet) =>
      (pathSet.scenarios || []).map((scenario) => ({
        id: `path-set:${pathSet.path_set_id}:${scenario.scenario_id}`,
        source: "PATH_SET",
        key: scenario.name || scenario.scenario_id,
        type: `PathSet · ${pathSet.path_set_id}`,
        description: scenario.description || pathSet.description || "",
        parameters: {
          instrument: scenario.instrument,
          interval: scenario.interval,
          model_type: scenario.model?.type,
          horizon_start: scenario.horizon?.start,
          horizon_end: scenario.horizon?.end,
          status: pathSet.status,
        },
        role_counts: scenario.role_counts || {},
        definition: scenario,
        path_set: {
          path_set_id: pathSet.path_set_id,
          status: pathSet.status,
          reproducible: pathSet.reproducible,
          lock_fingerprint: pathSet.lock_fingerprint,
          holdout_policy: pathSet.holdout_policy,
        },
        paths: (scenario.paths || []).map((path) => ({
          ...path,
          source: "PATH_SET",
          seed: path.market_seed,
          path_set_id: pathSet.path_set_id,
        })),
        experiments: [],
        strategies: [],
        runs: [],
      })),
    );
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

  const IDENTITY_PARAMETER_KEYS = new Set([
    "strategy_id",
    "grid_id",
  ]);

  function researchParameters(value) {
    if (Array.isArray(value)) return value.map(researchParameters);
    if (value && typeof value === "object") {
      return Object.fromEntries(
        Object.entries(value)
          .filter(([key]) => !IDENTITY_PARAMETER_KEYS.has(key))
          .map(([key, item]) => [key, researchParameters(item)]),
      );
    }
    return value;
  }

  function flattenParameters(value, prefix = "") {
    const result = {};
    Object.entries(value || {}).forEach(([key, item]) => {
      const path = prefix ? `${prefix}.${key}` : key;
      if (
        item &&
        typeof item === "object" &&
        !Array.isArray(item)
      ) {
        Object.assign(result, flattenParameters(item, path));
      } else {
        result[path] = item;
      }
    });
    return result;
  }

  function strategyRuns(record, strategyType) {
    return record.runs.filter(
      (run) => run.resolved_components.strategy.type === strategyType,
    );
  }

  function revisionSummary(record) {
    const revisions = Object.values(record.detail?.code_revisions || {});
    const tag = revisions.map((revision) => revision.tag).find(Boolean) || null;
    const dirty = revisions.some((revision) => revision.dirty);
    const reproducible = Boolean(record.experiment.reproducible) && !dirty;
    return {
      tag,
      dirty,
      reproducible,
      rank: tag ? 3 : reproducible ? 2 : dirty ? 0 : 1,
      label: tag
        ? `标签 ${tag}`
        : reproducible
          ? "Clean 可复现"
          : dirty
            ? "探索版本"
            : "Clean 版本",
    };
  }

  function componentContext(component, componentName) {
    const parameters = clone(component.parameters || {});
    if (componentName === "market") {
      if (component.type === "locked-market-path/v1") {
        return {
          type: component.type,
          parameters: Object.fromEntries(
            [
              "path_set_id",
              "scenario_id",
              "origin",
              "instrument",
              "interval",
            ]
              .filter((key) => parameters[key] !== undefined)
              .map((key) => [key, parameters[key]]),
          ),
        };
      }
      if (component.type === "historical-parquet/v1") {
        delete parameters.path;
      }
    }
    return {type: component.type, parameters};
  }

  function studyContext(record, strategyType) {
    const runs = strategyRuns(record, strategyType);
    return Object.fromEntries(
      ["market", "execution", "account"].map((componentName) => [
        componentName,
        [...new Map(
          runs.map((run) => {
            const context = componentContext(
              run.resolved_components[componentName],
              componentName,
            );
            return [stableJson(context), context];
          }),
        ).values()].sort((left, right) =>
          stableJson(left).localeCompare(stableJson(right)),
        ),
      ]),
    );
  }

  function studyGroups(records, strategyType) {
    const experimentVersions = new Map();
    records.forEach((record) => {
      if (!strategyRuns(record, strategyType).length) return;
      const id = record.experiment.experiment_id;
      if (!experimentVersions.has(id)) experimentVersions.set(id, []);
      experimentVersions.get(id).push(record);
    });
    const logicalExperiments = [...experimentVersions.entries()].map(
      ([experimentId, versions]) => {
        versions.sort((left, right) => {
          const rank = revisionSummary(right).rank - revisionSummary(left).rank;
          if (rank) return rank;
          return String(right.experiment.updated_at).localeCompare(
            String(left.experiment.updated_at),
          );
        });
        return {
          experiment_id: experimentId,
          preferred: versions[0],
          versions,
        };
      },
    );
    const contextGroups = new Map();
    logicalExperiments.forEach((experiment) => {
      const context = studyContext(experiment.preferred, strategyType);
      const contextId = stableJson(context);
      if (!contextGroups.has(contextId)) {
        contextGroups.set(contextId, {context, experiments: []});
      }
      contextGroups.get(contextId).experiments.push(experiment);
    });
    return [...contextGroups.entries()].map(([id, group]) => {
      const preferredRecords = group.experiments.map((item) => item.preferred);
      preferredRecords.sort((left, right) =>
        String(right.experiment.updated_at).localeCompare(
          String(left.experiment.updated_at),
        ),
      );
      const versions = group.experiments.flatMap((item) => item.versions);
      return {
        id,
        strategy_type: strategyType,
        context: group.context,
        preferred: preferredRecords[0],
        preferred_records: preferredRecords,
        experiments: group.experiments,
        versions,
        revision: revisionSummary(preferredRecords[0]),
      };
    }).sort((left, right) =>
      String(right.preferred.experiment.updated_at).localeCompare(
        String(left.preferred.experiment.updated_at),
      ),
    );
  }

  function metricScalar(run, metricKey, dimensions = {}) {
    const marker = `:${metricKey}`;
    for (const [key, rawValue] of Object.entries(run.metric_scalars || {})) {
      const markerIndex = key.indexOf(marker);
      if (markerIndex < 0) continue;
      const suffix = key.slice(markerIndex + marker.length);
      if (suffix && !suffix.startsWith("{")) continue;
      const matches = Object.entries(dimensions).every(
        ([name, expected]) => suffix.includes(`${name}=${expected}`),
      );
      if (!matches) continue;
      if (typeof rawValue === "boolean") return rawValue;
      const numeric = Number(rawValue);
      return Number.isFinite(numeric) ? numeric : rawValue;
    }
    return null;
  }

  function median(values) {
    const sorted = values
      .map(Number)
      .filter(Number.isFinite)
      .sort((left, right) => left - right);
    if (!sorted.length) return null;
    const middle = Math.floor(sorted.length / 2);
    return sorted.length % 2
      ? sorted[middle]
      : (sorted[middle - 1] + sorted[middle]) / 2;
  }

  function marketRole(run) {
    const parameters = run.resolved_components.market.parameters || {};
    if (parameters.role) return String(parameters.role).toUpperCase();
    const key = String(run.resolved_components.market.key || "").toUpperCase();
    if (key.includes("VALIDATION")) return "VALIDATION";
    if (key.includes("TRAIN")) return "TRAIN";
    return "ALL";
  }

  function valuationAsset(runs) {
    const hasBtc = runs.some(
      (run) => metricScalar(
        run,
        "return.total_rate",
        {scope: "account.total_equity", valuation_asset: "BTC"},
      ) !== null,
    );
    return hasBtc ? "BTC" : "USDT";
  }

  function runReturn(run, asset) {
    return metricScalar(
      run,
      "return.total_rate",
      {scope: "account.total_equity", valuation_asset: asset},
    );
  }

  function runDrawdown(run, asset) {
    return metricScalar(
      run,
      "risk.max_drawdown_rate",
      {scope: "account.total_equity", valuation_asset: asset},
    );
  }

  function matchingBaseline(record, run) {
    return record.runs.find((candidate) =>
      candidate.resolved_components.strategy.type === "hold-btc/v1" &&
      candidate.market_path_id === run.market_path_id &&
      candidate.seed === run.seed,
    );
  }

  function runSummary(record, run, asset) {
    const baseline = matchingBaseline(record, run);
    const totalReturn = runReturn(run, asset);
    const baselineReturn = baseline ? runReturn(baseline, asset) : null;
    return {
      record,
      run,
      role: marketRole(run),
      return_rate: totalReturn,
      excess_vs_hodl: (
        totalReturn !== null && baselineReturn !== null
          ? Number(totalReturn) - Number(baselineReturn)
          : null
      ),
      max_drawdown_rate: runDrawdown(run, asset),
      liquidated: [true, 1, "true"].includes(
        metricScalar(run, "run.liquidated"),
      ),
      fill_count: metricScalar(run, "execution.fill_count"),
      completed_cycles: metricScalar(run, "grid.completed_cycles"),
      fees: metricScalar(run, "cost.total_fees", {valuation_asset: asset}),
      entry_contracts: metricScalar(run, "strategy.entry_contracts"),
      estimated_liquidation_price: metricScalar(
        run,
        "strategy.estimated_liquidation_price_after_entry",
      ),
      peak_margin_risk: metricScalar(
        run,
        "margin.max_maintenance_utilization",
      ),
      peak_initial_margin_utilization: metricScalar(
        run,
        "margin.max_initial_utilization",
      ),
      max_effective_leverage: metricScalar(
        run,
        "margin.max_effective_leverage",
      ),
    };
  }

  function summarizeSamples(samples, role = null) {
    const selected = role
      ? samples.filter((sample) => sample.role === role)
      : samples;
    if (!selected.length) return null;
    const drawdowns = selected
      .map((sample) => Number(sample.max_drawdown_rate))
      .filter(Number.isFinite);
    const marginRisks = selected
      .map((sample) => Number(sample.peak_margin_risk))
      .filter(Number.isFinite);
    const effectiveLeverages = selected
      .map((sample) => Number(sample.max_effective_leverage))
      .filter(Number.isFinite);
    const initialMarginUtilizations = selected
      .map((sample) => Number(sample.peak_initial_margin_utilization))
      .filter(Number.isFinite);
    return {
      sample_count: selected.length,
      return_median: median(selected.map((sample) => sample.return_rate)),
      excess_median: median(selected.map((sample) => sample.excess_vs_hodl)),
      drawdown_worst: drawdowns.length ? Math.max(...drawdowns) : null,
      liquidation_rate: selected.filter((sample) => sample.liquidated).length
        / selected.length,
      fill_median: median(selected.map((sample) => sample.fill_count)),
      cycle_median: median(selected.map((sample) => sample.completed_cycles)),
      fee_median: median(selected.map((sample) => sample.fees)),
      entry_contracts_median: median(
        selected.map((sample) => sample.entry_contracts),
      ),
      estimated_liquidation_price_median: median(
        selected.map((sample) => sample.estimated_liquidation_price),
      ),
      margin_risk_worst: marginRisks.length
        ? Math.max(...marginRisks)
        : null,
      initial_margin_utilization_worst: initialMarginUtilizations.length
        ? Math.max(...initialMarginUtilizations)
        : null,
      max_effective_leverage: effectiveLeverages.length
        ? Math.max(...effectiveLeverages)
        : null,
    };
  }

  function candidateRows(recordOrRecords, strategyType, preferredPaths = []) {
    const records = Array.isArray(recordOrRecords)
      ? recordOrRecords
      : [recordOrRecords];
    const grouped = new Map();
    records.forEach((record) => {
      strategyRuns(record, strategyType).forEach((run) => {
        const parameters = researchParameters(
          run.resolved_components.strategy.parameters,
        );
        const id = stableJson(parameters);
        if (!grouped.has(id)) {
          grouped.set(id, {id, parameters, samples_with_records: []});
        }
        grouped.get(id).samples_with_records.push({record, run});
      });
    });
    const candidates = [...grouped.values()].sort((left, right) =>
      left.id.localeCompare(right.id),
    );
    const flattened = candidates.map((candidate) =>
      flattenParameters(candidate.parameters),
    );
    const allPaths = [...new Set(flattened.flatMap(Object.keys))].sort();
    const varyingPaths = allPaths.filter((path) =>
      new Set(flattened.map((parameters) => stableJson(parameters[path]))).size > 1,
    );
    const displayPaths = varyingPaths.length
      ? varyingPaths
      : [
          ...preferredPaths.filter((path) => allPaths.includes(path)),
          ...allPaths.filter((path) => !preferredPaths.includes(path)),
        ].slice(0, 6);

    return candidates.map((candidate, index) => {
      const runs = candidate.samples_with_records.map((item) => item.run);
      const asset = valuationAsset(runs);
      const samples = candidate.samples_with_records.map(({record, run}) =>
        runSummary(record, run, asset),
      );
      const flat = flattenParameters(candidate.parameters);
      const label = displayPaths
        .map((path) => `${path.split(".").at(-1)}=${flat[path]}`)
        .join(" · ");
      return {
        ...candidate,
        runs,
        label: candidates.length === 1
          ? "当前配置"
          : label || `参数组合 ${String(index + 1).padStart(2, "0")}`,
        asset,
        parameter_values: Object.fromEntries(
          displayPaths.map((path) => [path, flat[path]]),
        ),
        varying_parameter_paths: varyingPaths,
        samples,
        summary: summarizeSamples(samples),
        train: summarizeSamples(samples, "TRAIN"),
        validation: summarizeSamples(samples, "VALIDATION"),
        status_counts: statusCounts(runs),
      };
    });
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
    applicationSummary,
    buildCatalog,
    candidateRows,
    companionStrategies,
    decorateRecord,
    flattenParameters,
    metricScalar,
    pathSetMarkets,
    revisionSummary,
    resolvedParameters,
    researchFocusGroups,
    scenarioRows,
    stableJson,
    strategyRuns,
    studyContext,
    studyGroups,
  };
});
