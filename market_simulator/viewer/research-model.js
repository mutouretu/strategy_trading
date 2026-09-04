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

  function canonicalStrategyDefinitionType(strategyType, aliases) {
    if (!strategyType || typeof strategyType !== "string") return null;
    return aliases.get(strategyType) || strategyType;
  }

  function explicitStrategyDefinitionTypes(detail, aliases = new Map()) {
    const types = new Set();
    (detail?.spec?.scenario_groups || []).forEach((group) => {
      (group.strategies || []).forEach((strategy) => {
        const strategyType = canonicalStrategyDefinitionType(
          strategy.parameters?.strategy_definition_type,
          aliases,
        );
        if (strategyType) types.add(strategyType);
      });
    });
    return [...types].sort();
  }

  function summaryStrategyDefinitionTypes(runs, aliases = new Map()) {
    const types = new Set();
    (runs || []).forEach((run) => {
      const application = applicationSummary(run)?.application;
      (application?.strategies || []).forEach((strategy) => {
        const strategyType = canonicalStrategyDefinitionType(
          strategy.strategy_type,
          aliases,
        );
        if (strategyType) types.add(strategyType);
      });
    });
    return [...types].sort();
  }

  function experimentKind(record) {
    const declared = record?.detail?.spec?.metadata?.experiment_kind;
    if (typeof declared === "string" && declared.trim()) {
      return declared.trim().toUpperCase();
    }
    const hasAxes = (record?.detail?.spec?.scenario_groups || []).some(
      (group) => (group.parameter_axes || []).length > 0,
    );
    return hasAxes ? "PARAMETER_STUDY" : "UNCLASSIFIED";
  }

  function ensureStrategyDefinitionEntry(
    strategyDefinitionMap,
    strategyDefinitionType,
    descriptors,
  ) {
    if (!strategyDefinitionMap.has(strategyDefinitionType)) {
      strategyDefinitionMap.set(strategyDefinitionType, {
        id: strategyDefinitionType,
        type: strategyDefinitionType,
        descriptor: descriptors.get(strategyDefinitionType) || null,
        experiments: new Set(),
        experiment_records: [],
        instances: new Set(),
        configurations: new Map(),
        run_instances: [],
        runs: [],
      });
    }
    return strategyDefinitionMap.get(strategyDefinitionType);
  }

  function buildCatalog(rawRecords, descriptors = []) {
    const strategyMap = new Map();
    const marketMap = new Map();
    const strategyDefinitionItems = descriptors.filter(
      (item) => item.kind === "strategy-definition",
    );
    const strategyDefinitionAliases = new Map();
    const strategyDefinitionDescriptors = new Map(
      strategyDefinitionItems.map((descriptor) => [descriptor.type, descriptor]),
    );
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
            experiment_records: [],
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
    const records = rawRecords.map(decorateRecord).map((record) => {
      const runs = record.runs.map((run) => {
        const strategy = run.resolved_components.strategy;
        const canonicalType = strategyTypeAliases.get(strategy.type)
          || strategy.type;
        const resolvedStrategy = canonicalType === strategy.type
          ? strategy
          : {
              ...strategy,
              source_type: strategy.type,
              type: canonicalType,
            };
        const definitionType = canonicalStrategyDefinitionType(
          resolvedStrategy.parameters?.strategy_definition_type,
          strategyDefinitionAliases,
        );
        const fallbackDefinitionTypes = definitionType
          ? []
          : summaryStrategyDefinitionTypes(
              [run],
              strategyDefinitionAliases,
            );
        return {
          ...run,
          strategy_definition_type: definitionType,
          strategy_definition_types: definitionType
            ? [definitionType]
            : fallbackDefinitionTypes,
          resolved_components: {
            ...run.resolved_components,
            strategy: resolvedStrategy,
          },
        };
      });
      const explicitTypes = explicitStrategyDefinitionTypes(
        record.detail,
        strategyDefinitionAliases,
      );
      const fallbackTypes = explicitTypes.length
        ? []
        : summaryStrategyDefinitionTypes(runs, strategyDefinitionAliases);
      const associationSource = explicitTypes.length
        ? "EXPERIMENT_SPEC"
        : fallbackTypes.length
          ? "PROVIDER_SUMMARY"
          : null;
      return {
        ...record,
        runs,
        experiment_kind: experimentKind(record),
        strategy_definition_types: explicitTypes.length
          ? explicitTypes
          : fallbackTypes,
        strategy_definition_associations: (
          explicitTypes.length ? explicitTypes : fallbackTypes
        ).map((strategyDefinitionType) => ({
          strategy_definition_type: strategyDefinitionType,
          source: associationSource,
        })),
      };
    });

    records.forEach((record) => {
      record.strategy_definition_associations.forEach((association) => {
        const definitionEntry = ensureStrategyDefinitionEntry(
          strategyDefinitionMap,
          association.strategy_definition_type,
          strategyDefinitionDescriptors,
        );
        definitionEntry.experiments.add(record.experiment.experiment_id);
        if (!definitionEntry.experiment_records.includes(record)) {
          definitionEntry.experiment_records.push(record);
        }
      });
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

        if (run.strategy_definition_type) {
          const definitionEntry = ensureStrategyDefinitionEntry(
            strategyDefinitionMap,
            run.strategy_definition_type,
            strategyDefinitionDescriptors,
          );
          definitionEntry.experiments.add(record.experiment.experiment_id);
          if (!definitionEntry.experiment_records.includes(record)) {
            definitionEntry.experiment_records.push(record);
          }
          if (!definitionEntry.runs.some(
            (item) => item.run.run_id === run.run_id,
          )) {
            definitionEntry.runs.push({record, run});
          }
        }

        const application = applicationSummary(run)?.application;
        (application?.strategies || []).forEach((applicationStrategy) => {
          const sourceStrategyType = applicationStrategy.strategy_type
            || "unknown-strategy";
          const applicationStrategyType = strategyDefinitionAliases.get(
            sourceStrategyType,
          ) || sourceStrategyType;
          const definitionEntry = ensureStrategyDefinitionEntry(
            strategyDefinitionMap,
            applicationStrategyType,
            strategyDefinitionDescriptors,
          );
          definitionEntry.experiments.add(record.experiment.experiment_id);
          if (!definitionEntry.experiment_records.includes(record)) {
            definitionEntry.experiment_records.push(record);
          }
          if (applicationStrategy.strategy_instance_id) {
            definitionEntry.instances.add(
              applicationStrategy.strategy_instance_id,
            );
          }
          if (!definitionEntry.runs.some(
            (item) => item.run.run_id === run.run_id,
          )) {
            definitionEntry.runs.push({record, run});
          }
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

  function marketAssetFromInstrument(instrument) {
    const normalized = String(instrument || "").trim().toUpperCase();
    const match = normalized.match(
      /^([A-Z0-9]+?)(?:USDT|USDC|USD)(?:_|$)/,
    );
    return match?.[1] || normalized.split(/[-_/]/)[0] || "OTHER";
  }

  function marketAsset(market) {
    const explicit = market?.asset
      || market?.definition?.metadata?.asset
      || market?.parameters?.asset;
    if (explicit) return String(explicit).trim().toUpperCase();
    return marketAssetFromInstrument(
      market?.parameters?.instrument || market?.definition?.instrument,
    );
  }

  function marketAssetGroups(markets) {
    const groups = new Map();
    (markets || []).forEach((market) => {
      const asset = marketAsset(market);
      if (!groups.has(asset)) {
        groups.set(asset, {
          asset,
          markets: [],
          scenario_count: 0,
          path_count: 0,
        });
      }
      const group = groups.get(asset);
      group.markets.push(market);
      group.scenario_count += 1;
      group.path_count += (market.paths || []).length;
    });
    const preferredOrder = new Map(
      ["BTC", "ETH", "AAVE"].map((asset, index) => [asset, index]),
    );
    return [...groups.values()].map((group) => ({
      ...group,
      markets: group.markets.sort((left, right) =>
        left.key.localeCompare(right.key),
      ),
    })).sort((left, right) => {
      const leftOrder = preferredOrder.get(left.asset) ?? 100;
      const rightOrder = preferredOrder.get(right.asset) ?? 100;
      return leftOrder - rightOrder || left.asset.localeCompare(right.asset);
    });
  }

  function pathSetMarkets(pathSets) {
    return (pathSets || []).flatMap((pathSet) =>
      (pathSet.scenarios || []).map((scenario) => ({
        id: `path-set:${pathSet.path_set_id}:${scenario.scenario_id}`,
        source: "PATH_SET",
        asset: marketAssetFromInstrument(scenario.instrument),
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
    "strategy_definition_type",
  ]);

  function researchParameters(value) {
    if (
      value?.strategy_parameters
      && typeof value.strategy_parameters === "object"
      && !Array.isArray(value.strategy_parameters)
    ) {
      return researchParameters(value.strategy_parameters);
    }
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

  function runStrategyDefinitionTypes(run) {
    if (Array.isArray(run.strategy_definition_types)) {
      return run.strategy_definition_types;
    }
    const types = new Set();
    if (run.strategy_definition_type) {
      types.add(run.strategy_definition_type);
    }
    const application = applicationSummary(run)?.application;
    (application?.strategies || []).forEach((strategy) => {
      if (strategy.strategy_type) types.add(strategy.strategy_type);
    });
    return [...types];
  }

  function strategyDefinitionRuns(record, strategyDefinitionType) {
    return record.runs.filter((run) =>
      runStrategyDefinitionTypes(run).includes(strategyDefinitionType),
    );
  }

  function strategyExperimentGroups(strategyDefinition) {
    const experimentVersions = new Map();
    (strategyDefinition?.experiment_records || []).forEach((record) => {
      const id = record.experiment.experiment_id;
      if (!experimentVersions.has(id)) experimentVersions.set(id, []);
      experimentVersions.get(id).push(record);
    });
    return [...experimentVersions.entries()].map(
      ([experimentId, versions]) => {
        versions.sort((left, right) => {
          const rank = revisionSummary(right).rank - revisionSummary(left).rank;
          if (rank) return rank;
          return String(right.experiment.updated_at || "").localeCompare(
            String(left.experiment.updated_at || ""),
          );
        });
        const preferred = versions[0];
        return {
          experiment_id: experimentId,
          preferred,
          versions,
          runs: strategyDefinitionRuns(
            preferred,
            strategyDefinition.type,
          ),
        };
      },
    ).sort((left, right) =>
      String(right.preferred.experiment.updated_at || "").localeCompare(
        String(left.preferred.experiment.updated_at || ""),
      ),
    );
  }

  function isParameterStudy(record) {
    return record.experiment_kind === "PARAMETER_STUDY"
      || (record.detail?.spec?.scenario_groups || []).some(
        (group) => (group.parameter_axes || []).length > 0,
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

  function studyContext(record, strategyDefinitionType) {
    const runs = strategyDefinitionRuns(record, strategyDefinitionType);
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

  function studyGroups(records, strategyDefinitionType) {
    const experimentVersions = new Map();
    records.forEach((record) => {
      if (!isParameterStudy(record)) return;
      if (!record.strategy_definition_types.includes(strategyDefinitionType)) {
        return;
      }
      const id = record.experiment.experiment_id;
      if (!experimentVersions.has(id)) experimentVersions.set(id, []);
      experimentVersions.get(id).push(record);
    });
    return [...experimentVersions.entries()].map(
      ([experimentId, versions]) => {
        versions.sort((left, right) => {
          const rank = revisionSummary(right).rank - revisionSummary(left).rank;
          if (rank) return rank;
          return String(right.experiment.updated_at).localeCompare(
            String(left.experiment.updated_at),
          );
        });
        return {
          id: experimentId,
          experiment_id: experimentId,
          strategy_definition_type: strategyDefinitionType,
          strategy_type: strategyDefinitionType,
          context: studyContext(versions[0], strategyDefinitionType),
          preferred: versions[0],
          preferred_records: [versions[0]],
          experiments: [{
            experiment_id: experimentId,
            preferred: versions[0],
            versions,
          }],
          versions,
          revision: revisionSummary(versions[0]),
        };
      },
    ).sort((left, right) =>
      String(right.preferred.experiment.updated_at).localeCompare(
        String(left.preferred.experiment.updated_at),
      ),
    );
  }

  function strategyParameterPath(axisPath) {
    const parts = String(axisPath || "").split("/").filter(Boolean);
    if (parts[0] !== "strategy" || parts[1] !== "parameters") {
      return null;
    }
    const parameterParts = parts.slice(2);
    if (parameterParts[0] === "strategy_parameters") {
      parameterParts.shift();
    }
    return parameterParts.length ? parameterParts.join(".") : null;
  }

  function strategyParameterAxisGroups(record, strategyDescriptor) {
    const composition = strategyDescriptor?.rule_composition || [];
    const parameterDefinitions = new Map(
      (strategyDescriptor?.parameters || []).map((item) => [item.key, item]),
    );
    const groups = new Map();
    (record?.detail?.spec?.scenario_groups || []).forEach((scenarioGroup) => {
      (scenarioGroup.parameter_axes || []).forEach((axis) => {
        const parameterPath = strategyParameterPath(axis.path);
        if (!parameterPath) {
          const parts = String(axis.path || "").split("/").filter(Boolean);
          const componentName = parts[0] || "component";
          const componentParts = parts.slice(
            parts[1] === "parameters" ? 2 : 1,
          );
          const componentPath = componentParts.join(".") || axis.path;
          const groupId = `component:${componentName}`;
          if (!groups.has(groupId)) {
            groups.set(groupId, {
              id: groupId,
              scope: componentName.toUpperCase(),
              component_name: componentName,
              rules: [],
              axes: [],
            });
          }
          groups.get(groupId).axes.push({
            path: axis.path,
            parameter_path: `${componentName}.${componentPath}`,
            parameter_key: componentParts[0] || componentPath,
            name: `${componentName}.${componentPath}`,
            values: axis.values || [],
            maps_to: [],
          });
          return;
        }
        const parameterKey = parameterPath.split(".")[0];
        const definition = parameterDefinitions.get(parameterKey) || null;
        const mappedRuleKeys = [...new Set(
          (definition?.maps_to || []).flatMap((target) =>
            composition
              .filter((rule) => target.startsWith(`${rule.rule_key}.`))
              .map((rule) => rule.rule_key),
          ),
        )];
        const groupId = mappedRuleKeys.length
          ? mappedRuleKeys.join("+")
          : "strategy";
        if (!groups.has(groupId)) {
          const rules = composition.filter((rule) =>
            mappedRuleKeys.includes(rule.rule_key),
          );
          groups.set(groupId, {
            id: groupId,
            scope: mappedRuleKeys.length ? "RULE" : "STRATEGY",
            rules,
            axes: [],
          });
        }
        const suffix = parameterPath.split(".").slice(1).join(".");
        groups.get(groupId).axes.push({
          path: axis.path,
          parameter_path: parameterPath,
          parameter_key: parameterKey,
          name: definition?.name
            ? `${definition.name}${suffix ? ` · ${suffix}` : ""}`
            : parameterPath,
          values: axis.values || [],
          maps_to: definition?.maps_to || [],
        });
      });
    });
    return [...groups.values()];
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
      .filter((value) => value !== null && value !== undefined)
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
    const configuredBase = runs.map((run) =>
      run.resolved_components.account.parameters?.base_asset
      || run.resolved_components.account.parameters?.settlement_asset,
    ).find(Boolean);
    if (
      configuredBase
      && runs.some((run) =>
        runReturn(run, String(configuredBase).toUpperCase()) !== null,
      )
    ) {
      return String(configuredBase).toUpperCase();
    }
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
    const liquidationValue = metricScalar(run, "run.liquidated");
    const fundingAsset = String(
      run.resolved_components.account.parameters?.base_asset
      || run.resolved_components.account.parameters?.settlement_asset
      || asset,
    ).toUpperCase();
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
      liquidated: liquidationValue === null
        ? null
        : [true, 1, "true"].includes(liquidationValue),
      fill_count: metricScalar(run, "execution.fill_count"),
      completed_cycles: metricScalar(run, "grid.completed_cycles"),
      fees: metricScalar(run, "cost.total_fees", {valuation_asset: asset}),
      funding: metricScalar(
        run,
        "funding.net_wallet_delta",
        {valuation_asset: fundingAsset},
      ),
      funding_asset: fundingAsset,
      funding_settlement_count: metricScalar(
        run,
        "funding.settlement_count",
      ),
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
    const liquidationObservations = selected
      .map((sample) => sample.liquidated)
      .filter((value) => typeof value === "boolean");
    return {
      sample_count: selected.length,
      return_median: median(selected.map((sample) => sample.return_rate)),
      excess_median: median(selected.map((sample) => sample.excess_vs_hodl)),
      drawdown_worst: drawdowns.length ? Math.max(...drawdowns) : null,
      liquidation_rate: liquidationObservations.length
        ? liquidationObservations.filter(Boolean).length
          / liquidationObservations.length
        : null,
      fill_median: median(selected.map((sample) => sample.fill_count)),
      cycle_median: median(selected.map((sample) => sample.completed_cycles)),
      fee_median: median(selected.map((sample) => sample.fees)),
      funding_median: median(selected.map((sample) => sample.funding)),
      funding_settlement_count_median: median(
        selected.map((sample) => sample.funding_settlement_count),
      ),
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

  function candidateRows(
    recordOrRecords,
    strategyDefinitionType,
    preferredPaths = [],
  ) {
    const records = Array.isArray(recordOrRecords)
      ? recordOrRecords
      : [recordOrRecords];
    const grouped = new Map();
    records.forEach((record) => {
      strategyDefinitionRuns(record, strategyDefinitionType).forEach((run) => {
        const parameters = researchParameters(
          run.resolved_components.strategy.parameters,
        );
        const axisParameters = Object.fromEntries(
          Object.entries(run.parameter_values || {}).map(([path, value]) => {
            const parts = String(path).split("/").filter(Boolean);
            const componentName = parts[0] || "component";
            const parameterParts = parts.slice(
              parts[1] === "parameters" ? 2 : 1,
            );
            if (
              componentName === "strategy"
              && parameterParts[0] === "strategy_parameters"
            ) {
              parameterParts.shift();
            }
            const suffix = parameterParts.join(".") || path;
            const key = componentName === "strategy"
              ? suffix
              : `${componentName}.${suffix}`;
            return [key, value];
          }),
        );
        const id = stableJson({parameters, axis_parameters: axisParameters});
        if (!grouped.has(id)) {
          grouped.set(id, {
            id,
            parameters,
            axis_parameters: axisParameters,
            samples_with_records: [],
          });
        }
        grouped.get(id).samples_with_records.push({record, run});
      });
    });
    const candidates = [...grouped.values()].sort((left, right) =>
      left.id.localeCompare(right.id),
    );
    const flattened = candidates.map((candidate) => ({
      ...flattenParameters(candidate.parameters),
      ...candidate.axis_parameters,
    }));
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
      const flat = {
        ...flattenParameters(candidate.parameters),
        ...candidate.axis_parameters,
      };
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
    experimentKind,
    explicitStrategyDefinitionTypes,
    flattenParameters,
    metricScalar,
    marketAsset,
    marketAssetFromInstrument,
    marketAssetGroups,
    pathSetMarkets,
    revisionSummary,
    resolvedParameters,
    researchFocusGroups,
    scenarioRows,
    stableJson,
    strategyDefinitionRuns,
    strategyExperimentGroups,
    strategyParameterAxisGroups,
    strategyRuns,
    studyContext,
    studyGroups,
  };
});
