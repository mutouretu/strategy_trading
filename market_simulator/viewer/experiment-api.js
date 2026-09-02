(() => {
  "use strict";

  const Model = window.ExperimentResearchModel;
  const PAGE_META = {
    "strategy-overview": ["STRATEGIES", "策略总览"],
    "strategy-detail": ["STRATEGY DETAIL", "策略详情"],
    "strategy-runs": ["STRATEGY RUNS", "策略运行"],
    "rule-overview": ["TRADING RULES", "规则总览"],
    "rule-detail": ["RULE DETAIL", "规则详情"],
    "market-overview": ["MARKET ENVIRONMENTS", "市场环境"],
    "experiment-overview": ["STUDIES", "参数研究"],
    "experiment-detail": ["RUN DETAIL", "Run 详情"],
    playback: ["PLAYBACK", "K 线播放"],
  };

  const state = {
    experiments: [],
    records: [],
    strategies: [],
    strategyDefinitions: [],
    rules: [],
    markets: [],
    pathSets: [],
    page: "strategy-overview",
    strategyId: null,
    ruleId: null,
    marketId: null,
    marketRole: null,
    marketInterval: "1w",
    marketDocument: null,
    detailExperimentId: null,
    detailDatabaseName: null,
    detailScenarioId: null,
    detailRunId: null,
    overviewExperimentId: null,
    overviewDatabaseName: null,
    runDetail: null,
    runPerformance: null,
    runPerformanceAsset: null,
    runPerformanceError: null,
    playbackUrl: null,
  };

  const byId = (id) => document.getElementById(id);
  const elements = {
    navigation: [...document.querySelectorAll(".nav-item")],
    pages: [...document.querySelectorAll(".workspace-page")],
    pageEyebrow: byId("page-eyebrow"),
    pageTitle: byId("page-title"),
    catalogStatus: byId("catalog-status"),
    updatedAt: byId("data-updated-at"),
    refresh: byId("refresh-catalog"),
    message: byId("page-message"),
    strategyFacts: byId("strategy-overview-facts"),
    strategyBody: byId("strategy-overview-body"),
    strategySelect: byId("strategy-detail-select"),
    strategyHero: byId("strategy-hero"),
    strategyRuleBody: byId("strategy-rule-body"),
    strategyParameters: byId("strategy-parameters"),
    strategyCoordination: byId("strategy-coordination"),
    strategyConstraints: byId("strategy-constraints"),
    strategyLifecycle: byId("strategy-lifecycle"),
    strategyRunSelect: byId("strategy-run-select"),
    strategyRunFacts: byId("strategy-run-facts"),
    strategyRunHead: byId("strategy-run-head"),
    strategyRunBody: byId("strategy-run-body"),
    ruleFacts: byId("rule-overview-facts"),
    ruleBody: byId("rule-overview-body"),
    ruleSelect: byId("rule-detail-select"),
    ruleHero: byId("rule-hero"),
    ruleFormulae: byId("rule-formulae"),
    ruleConstraints: byId("rule-constraints"),
    ruleParameters: byId("rule-parameters"),
    ruleFlow: byId("rule-flow"),
    ruleNotes: byId("rule-research-notes"),
    ruleConfigBody: byId("rule-config-body"),
    marketList: byId("market-list"),
    marketIntervalSwitch: byId("market-interval-switch"),
    marketTitle: byId("market-chart-title"),
    marketDescription: byId("market-chart-description"),
    marketRoleSelect: byId("market-role-select"),
    marketPathSelect: byId("market-path-select"),
    marketChips: byId("market-parameter-chips"),
    marketProfileFacts: byId("market-profile-facts"),
    marketChart: byId("market-chart"),
    marketChartEmpty: byId("market-chart-empty"),
    experimentGroups: byId("experiment-groups"),
    detailExperimentSelect: byId("detail-experiment-select"),
    detailScenarioSelect: byId("detail-scenario-select"),
    detailSeedSelect: byId("detail-seed-select"),
    runDetailHero: byId("run-detail-hero"),
    runPerformanceEmpty: byId("run-performance-empty"),
    runPerformanceAssetSwitch: byId("run-performance-asset-switch"),
    runReturnChart: byId("run-return-chart"),
    runReturnChartSummary: byId("run-return-chart-summary"),
    runMarginRiskChart: byId("run-margin-risk-chart"),
    runMarginRiskChartSummary: byId("run-margin-risk-chart-summary"),
    runKeyMetrics: byId("run-key-metrics"),
    runConfiguration: byId("run-configuration"),
    allMetrics: byId("all-metrics"),
    detailPlayback: byId("open-playback-from-detail"),
    playbackContext: byId("playback-context"),
    playbackEmpty: byId("playback-empty"),
    playbackFrame: byId("playback-frame"),
    playerWindow: byId("open-player-window"),
  };

  async function request(path) {
    const response = await fetch(path, {
      headers: {Accept: "application/json"},
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(body.error?.message || `HTTP ${response.status}`);
    }
    return body;
  }

  function apiPath(...parts) {
    return `/api/${parts.map((part) => encodeURIComponent(part)).join("/")}`;
  }

  function experimentApiPath(experiment, parts = [], query = {}) {
    const search = new URLSearchParams(query);
    search.set("database", experiment.database_name);
    return `${apiPath(
      "experiments",
      experiment.experiment_id,
      ...parts,
    )}?${search.toString()}`;
  }

  function make(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = String(text);
    return element;
  }

  function option(value, label) {
    const item = make("option", "", label);
    item.value = value;
    return item;
  }

  function showMessage(message, isError = false) {
    elements.message.textContent = message;
    elements.message.classList.toggle("error", isError);
    elements.message.classList.add("visible");
    window.setTimeout(() => elements.message.classList.remove("visible"), 3200);
  }

  function humanize(value) {
    return String(value || "未命名")
      .replace(/\/v\d+$/i, "")
      .replaceAll("_", " ")
      .replaceAll("-", " ")
      .replace(/\b\w/g, (letter) => letter.toUpperCase());
  }

  function strategyName(strategy) {
    const composition = strategy?.descriptor?.rule_composition || [];
    if (composition.length) {
      return composition.map((rule) => strategyRuleName(rule)).join("&");
    }
    return strategy?.descriptor?.display_name || humanize(strategy?.id);
  }

  function ruleName(rule) {
    return rule?.descriptor?.display_name || humanize(rule?.id);
  }

  function strategyRuleName(compositionRule) {
    const rule = state.rules.find(
      (item) => item.type === compositionRule?.rule_type,
    );
    return rule
      ? ruleName(rule)
      : humanize(compositionRule?.rule_type || compositionRule?.rule_key);
  }

  function shortId(value, length = 12) {
    const text = String(value || "—");
    return text.length > length ? `${text.slice(0, length)}…` : text;
  }

  function dateText(value) {
    if (!value) return "—";
    return new Date(value).toLocaleString("zh-CN", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function formatNumber(value, digits = 2) {
    const numeric = Number(value);
    return Number.isFinite(numeric)
      ? numeric.toLocaleString("en-US", {maximumFractionDigits: digits})
      : String(value ?? "—");
  }

  function formatRatio(value) {
    const numeric = Number(value);
    return Number.isFinite(numeric) ? `${(numeric * 100).toFixed(2)}%` : "—";
  }

  function statusText(counts) {
    const succeeded = counts?.SUCCEEDED || 0;
    const failed = counts?.FAILED || 0;
    const other = Object.entries(counts || {})
      .filter(([name]) => !["SUCCEEDED", "FAILED"].includes(name))
      .reduce((sum, [, count]) => sum + count, 0);
    return `${succeeded} 成功${failed ? ` · ${failed} 失败` : ""}${
      other ? ` · ${other} 其他` : ""
    }`;
  }

  function primaryCell(title, subtitle) {
    const cell = make("td", "primary-cell");
    cell.append(make("strong", "", title), make("span", "", subtitle));
    return cell;
  }

  function experimentDetailHref(experiment) {
    const url = new URL(window.location.href);
    url.search = "";
    url.searchParams.set("page", "experiment-detail");
    url.searchParams.set("experiment", experiment.experiment_id);
    url.searchParams.set("database", experiment.database_name);
    return url.toString();
  }

  function experimentOverviewHref(experiment) {
    const url = new URL(window.location.href);
    url.search = "";
    url.searchParams.set("page", "experiment-overview");
    url.searchParams.set("experiment", experiment.experiment_id);
    url.searchParams.set("database", experiment.database_name);
    return url.toString();
  }

  function experimentCell(experiment) {
    const cell = make("td", "primary-cell");
    const title = make("strong");
    const link = make(
      "a",
      "experiment-detail-link",
      experiment.experiment_id,
    );
    link.href = experimentDetailHref(experiment);
    title.append(link);
    cell.append(
      title,
      make("span", "", experiment.database_name),
    );
    return cell;
  }

  function appendCell(row, value, className = "") {
    const cell = make("td", className, value ?? "—");
    row.append(cell);
    return cell;
  }

  function emptyRow(body, columns, message) {
    const row = document.createElement("tr");
    const cell = appendCell(row, message, "empty-cell");
    cell.colSpan = columns;
    body.replaceChildren(row);
  }

  function parameterName(path) {
    return String(path).split("/").filter(Boolean).at(-1) || path;
  }

  function simpleValue(value) {
    if (value === null || value === undefined) return "—";
    if (typeof value === "object") return JSON.stringify(value);
    if (typeof value === "boolean") return value ? "是" : "否";
    return String(value);
  }

  function parameterSummary(values, max = 8) {
    const entries = Object.entries(values || {});
    const wrapper = make("div", "parameter-summary");
    entries.slice(0, max).forEach(([key, value]) => {
      wrapper.append(
        make("span", "", `${parameterName(key)}=${simpleValue(value)}`),
      );
    });
    if (entries.length > max) {
      wrapper.append(make("span", "", `+${entries.length - max}`));
    }
    return wrapper;
  }

  function renderStrategyRunHeader() {
    const row = document.createElement("tr");
    [
      "实验 ID",
      "类型",
      "说明",
      "市场 / Seed",
      "Runs / 版本",
      "状态",
      "",
    ].forEach((label) => row.append(make("th", "", label)));
    elements.strategyRunHead.replaceChildren(row);
    return 7;
  }

  function replaceOptions(select, items, selected) {
    select.replaceChildren(...items);
    if (selected && [...select.options].some((item) => item.value === selected)) {
      select.value = selected;
    }
  }

  function recordById(experimentId, databaseName = null) {
    return state.records.find(
      (record) =>
        record.experiment.experiment_id === experimentId &&
        (!databaseName || record.experiment.database_name === databaseName),
    );
  }

  function selectedRule() {
    return state.rules.find((item) => item.id === state.ruleId);
  }

  function selectedStrategyDefinition() {
    return state.strategyDefinitions.find(
      (item) => item.id === state.strategyId,
    );
  }

  function selectedMarket() {
    return state.markets.find((item) => item.id === state.marketId);
  }

  function setPage(page, {updateUrl = true} = {}) {
    if (!PAGE_META[page]) return;
    state.page = page;
    elements.navigation.forEach((item) =>
      item.classList.toggle("active", item.dataset.page === page),
    );
    elements.pages.forEach((item) =>
      item.classList.toggle("active", item.dataset.pagePanel === page),
    );
    elements.pageEyebrow.textContent = PAGE_META[page][0];
    elements.pageTitle.textContent = PAGE_META[page][1];
    if (updateUrl) {
      const url = new URL(window.location.href);
      url.searchParams.set("page", page);
      window.history.replaceState({}, "", url);
    }
    if (page === "experiment-detail") {
      window.requestAnimationFrame(renderPerformanceCharts);
    }
  }

  function renderStrategyOverview() {
    const ruleTypes = new Set(
      state.strategyDefinitions.flatMap(
        (item) => (item.descriptor?.rule_composition || [])
          .map((rule) => rule.rule_type),
      ),
    );
    elements.strategyFacts.replaceChildren(
      make(
        "span",
        "mini-fact",
        `${state.strategyDefinitions.length} 类 Strategy`,
      ),
      make("span", "mini-fact", `${ruleTypes.size} 类 Rule`),
      make("span", "mini-fact", `${state.experiments.length} 个实验`),
    );
    elements.strategyBody.replaceChildren();
    if (!state.strategyDefinitions.length) {
      emptyRow(
        elements.strategyBody,
        7,
        "当前没有注册的 StrategyDefinition",
      );
      return;
    }
    state.strategyDefinitions.forEach((strategy) => {
      const descriptor = strategy.descriptor || {};
      const rules = descriptor.rule_composition || [];
      const row = document.createElement("tr");
      row.append(primaryCell(strategyName(strategy), strategy.type));
      appendCell(row, descriptor.family || "—");
      appendCell(
        row,
        rules.map(
          (rule) => `${rule.rule_key}: ${strategyRuleName(rule)}`,
        ).join("\n")
          || "未登记",
      );
      appendCell(
        row,
        (descriptor.supported_directions || []).join(" / ") || "—",
      );
      appendCell(
        row,
        (descriptor.supported_product_types || []).join(" / ") || "—",
      );
      appendCell(
        row,
        `${strategy.experiments.length} 实验 · ${strategy.runs.length} Runs`,
      );
      const actionCell = document.createElement("td");
      const detailAction = make("button", "text-button", "详情 →");
      detailAction.type = "button";
      detailAction.addEventListener("click", () => {
        state.strategyId = strategy.id;
        renderStrategyDetail();
        renderStrategyRuns();
        setPage("strategy-detail");
      });
      const runsAction = make("button", "text-button", "运行 →");
      runsAction.type = "button";
      runsAction.addEventListener("click", () => {
        state.strategyId = strategy.id;
        renderStrategyDetail();
        renderStrategyRuns();
        setPage("strategy-runs");
      });
      actionCell.append(detailAction, runsAction);
      row.append(actionCell);
      elements.strategyBody.append(row);
    });
  }

  function renderStrategyDetail() {
    const strategy = selectedStrategyDefinition()
      || state.strategyDefinitions[0];
    if (!strategy) {
      const emptyOption = option("", "暂无注册策略");
      emptyOption.disabled = true;
      replaceOptions(elements.strategySelect, [emptyOption], "");
      elements.strategyHero.replaceChildren(
        make("div", "empty-cell", "当前没有注册的 StrategyDefinition。"),
      );
      emptyRow(elements.strategyRuleBody, 6, "暂无 Rule 组合");
      elements.strategyParameters.replaceChildren(
        make("div", "empty-cell", "暂无 Strategy 参数"),
      );
      elements.strategyCoordination.replaceChildren();
      elements.strategyConstraints.replaceChildren();
      elements.strategyLifecycle.replaceChildren();
      return;
    }
    state.strategyId = strategy.id;
    replaceOptions(
      elements.strategySelect,
      state.strategyDefinitions.map(
        (item) => option(item.id, strategyName(item)),
      ),
      strategy.id,
    );
    const descriptor = strategy.descriptor || {};
    const content = make("div");
    content.append(
      make("div", "eyebrow", "STRATEGY DEFINITION"),
      make("h2", "", strategyName(strategy)),
      make("div", "hero-type", strategy.type),
      make(
        "p",
        "",
        descriptor.description
          || descriptor.summary
          || "该 Strategy 来自历史运行，尚未登记独立定义。",
      ),
    );
    const stats = make("div", "hero-stats");
    stats.append(
      heroStat("Rules", (descriptor.rule_composition || []).length),
      heroStat("Parameters", (descriptor.parameters || []).length),
      heroStat("Instances", strategy.instances.length),
    );
    elements.strategyHero.replaceChildren(content, stats);

    elements.strategyRuleBody.replaceChildren();
    const composition = descriptor.rule_composition || [];
    if (!composition.length) {
      emptyRow(elements.strategyRuleBody, 6, "尚未登记 Rule 组成");
    } else {
      composition.forEach((rule) => {
        const row = document.createElement("tr");
        row.append(primaryCell(rule.rule_key, rule.summary || "—"));
        appendCell(row, rule.role || "—");
        appendCell(row, rule.rule_type || "—");
        appendCell(row, (rule.permissions || []).join(" / ") || "—");
        appendCell(row, (rule.subscriptions || []).join(" / ") || "—");
        const mappings = document.createElement("td");
        mappings.append(parameterSummary(rule.parameter_mappings || {}, 12));
        row.append(mappings);
        elements.strategyRuleBody.append(row);
      });
    }

    elements.strategyParameters.replaceChildren();
    const parameters = descriptor.parameters || [];
    if (!parameters.length) {
      elements.strategyParameters.append(
        make("div", "empty-cell", "该 Strategy 没有登记外部参数"),
      );
    } else {
      parameters.forEach((parameter) => {
        const row = make("div", "strategy-parameter-item");
        const identity = make("div");
        identity.append(
          make("strong", "", parameter.name || parameter.key),
          make("code", "", parameter.key),
        );
        if (parameter.description) {
          identity.append(make("small", "", parameter.description));
        }
        const destinations = (parameter.maps_to || []).join(" · ");
        row.append(
          identity,
          make(
            "span",
            "",
            [
              parameter.required
                ? "必填"
                : `默认 ${simpleValue(parameter.default)}`,
              parameter.group ? `分组 ${parameter.group}` : "",
              destinations ? `→ ${destinations}` : "",
            ].filter(Boolean).join(" · "),
          ),
        );
        elements.strategyParameters.append(row);
      });
    }

    elements.strategyCoordination.replaceChildren();
    const policy = descriptor.coordination_policy || {};
    [
      `事件批次：${policy.proposal_batch || "未登记"}`,
      `冲突处理：${policy.conflict_resolution || "未登记"}`,
      `退出控制者：${policy.exit_controller_rule_key || "无"}`,
      `方向：${(descriptor.supported_directions || []).join("、") || "未登记"}`,
      `产品：${(descriptor.supported_product_types || []).join("、") || "未登记"}`,
    ].forEach((note) =>
      elements.strategyCoordination.append(make("div", "note-item", note)),
    );
    elements.strategyConstraints.replaceChildren();
    (descriptor.constraints || []).forEach((constraint) =>
      elements.strategyConstraints.append(
        make("span", "constraint-item", constraint),
      ),
    );

    elements.strategyLifecycle.replaceChildren();
    (descriptor.lifecycle || []).forEach((phase, index, phases) => {
      const node = make("div");
      node.append(
        make("span", "", String(index + 1).padStart(2, "0")),
        make("strong", "", phase),
        make("small", "", "Strategy phase"),
      );
      elements.strategyLifecycle.append(node);
      if (index < phases.length - 1) {
        elements.strategyLifecycle.append(make("i", "", "→"));
      }
    });
  }

  function experimentKindLabel(kind) {
    return {
      BASELINE: "基线",
      PARAMETER_STUDY: "参数研究",
      MARKET_VALIDATION: "市场验证",
      ROBUSTNESS: "稳健性验证",
    }[kind] || kind || "未分类";
  }

  function experimentMarketSeedSummary(record, runs) {
    const marketKeys = new Set(
      runs.map((run) => run.resolved_components.market.key),
    );
    const seeds = new Set(runs.map((run) => run.seed));
    if (!runs.length) {
      (record.detail?.spec?.scenario_groups || []).forEach((group) => {
        (group.markets || []).forEach((market) => marketKeys.add(market.key));
      });
      (record.detail?.spec?.seeds || []).forEach((seed) => seeds.add(seed));
    }
    return {
      markets: [...marketKeys],
      seeds: [...seeds].sort((left, right) => left - right),
    };
  }

  function openStrategyExperiment(record, runs) {
    if (record.experiment_kind === "PARAMETER_STUDY") {
      state.overviewExperimentId = record.experiment.experiment_id;
      state.overviewDatabaseName = record.experiment.database_name;
      renderExperimentOverview();
      setPage("experiment-overview");
      return;
    }
    if (runs.length) {
      openRunDetail(record, runs[0]);
      return;
    }
    state.detailExperimentId = record.experiment.experiment_id;
    state.detailDatabaseName = record.experiment.database_name;
    renderDetailSelectors();
    setPage("experiment-detail");
  }

  function renderStrategyRuns() {
    const strategy = selectedStrategyDefinition()
      || state.strategyDefinitions[0];
    if (!strategy) {
      const emptyOption = option("", "暂无注册策略");
      emptyOption.disabled = true;
      replaceOptions(elements.strategyRunSelect, [emptyOption], "");
      elements.strategyRunFacts.replaceChildren();
      const columnCount = renderStrategyRunHeader();
      emptyRow(elements.strategyRunBody, columnCount, "暂无策略实验");
      return;
    }
    state.strategyId = strategy.id;
    replaceOptions(
      elements.strategyRunSelect,
      state.strategyDefinitions.map(
        (item) => option(item.id, strategyName(item)),
      ),
      strategy.id,
    );
    const experimentGroups = Model.strategyExperimentGroups(strategy);
    elements.strategyRunFacts.replaceChildren(
      make("span", "mini-fact", `${experimentGroups.length} 个实验`),
      make("span", "mini-fact", `${strategy.runs.length} 个 Runs`),
      make(
        "span",
        "mini-fact",
        `${experimentGroups.filter(
          (group) => group.preferred.experiment_kind === "PARAMETER_STUDY",
        ).length} 个参数研究`,
      ),
    );
    const columnCount = renderStrategyRunHeader();
    elements.strategyRunBody.replaceChildren();
    if (!experimentGroups.length) {
      emptyRow(
        elements.strategyRunBody,
        columnCount,
        "策略定义已经注册，但尚无关联实验",
      );
    } else {
      experimentGroups.forEach((group) => {
        const record = group.preferred;
        const runs = group.runs;
        const scope = experimentMarketSeedSummary(record, runs);
        const row = document.createElement("tr");
        const experimentCell = document.createElement("td");
        const experimentLink = make(
          "a",
          "experiment-detail-link",
          record.experiment.experiment_id,
        );
        experimentLink.href = record.experiment_kind === "PARAMETER_STUDY"
          ? experimentOverviewHref(record.experiment)
          : experimentDetailHref(record.experiment);
        experimentCell.append(experimentLink);
        row.append(experimentCell);
        appendCell(row, experimentKindLabel(record.experiment_kind));
        row.append(primaryCell(
          record.experiment.description || "未填写实验说明",
          record.experiment.database_name,
        ));
        row.append(primaryCell(
          scope.markets.join(" / ") || "—",
          scope.seeds.length ? `Seed ${scope.seeds.join(", ")}` : "尚无 Seed",
        ));
        row.append(primaryCell(
          `${runs.length} Runs`,
          group.versions.length > 1
            ? `${group.versions.length} 个结果版本`
            : "1 个结果版本",
        ));
        appendCell(
          row,
          statusText(record.experiment.status_counts || {}),
        );
        const actionCell = document.createElement("td");
        const action = make(
          "button",
          "text-button",
          record.experiment_kind === "PARAMETER_STUDY"
            ? "查看参数研究 →"
            : runs.length === 1
              ? "查看 Run →"
              : "查看实验 →",
        );
        action.type = "button";
        action.addEventListener(
          "click",
          () => openStrategyExperiment(record, runs),
        );
        actionCell.append(action);
        row.append(actionCell);
        elements.strategyRunBody.append(row);
      });
    }
  }

  function renderRuleOverview() {
    elements.ruleFacts.replaceChildren(
      make("span", "mini-fact", `${state.rules.length} 条规则`),
      make(
        "span",
        "mini-fact",
        `${new Set(state.rules.flatMap((item) => item.strategies)).size} 类 Strategy`,
      ),
      make("span", "mini-fact", `${state.experiments.length} 个实验`),
    );
    elements.ruleBody.replaceChildren();
    if (!state.rules.length) {
      emptyRow(elements.ruleBody, 7, "当前没有注册的 TradingRuleDefinition");
      return;
    }
    state.rules.forEach((rule) => {
      const descriptor = rule.descriptor || {};
      const row = document.createElement("tr");
      row.append(primaryCell(ruleName(rule), rule.type));
      appendCell(row, descriptor.family || "—");
      appendCell(row, (descriptor.input_types || []).join(" / ") || "—");
      appendCell(row, (descriptor.output_types || []).join(" / ") || "—");
      appendCell(
        row,
        (descriptor.supported_product_types || []).join(" / ") || "—",
      );
      appendCell(
        row,
        `${rule.experiments.length} 实验 · ${rule.runs.length} Runs`,
      );
      const actionCell = document.createElement("td");
      const action = make("button", "text-button", "查看规则 →");
      action.type = "button";
      action.addEventListener("click", () => {
        state.ruleId = rule.id;
        renderRuleDetail();
        setPage("rule-detail");
      });
      actionCell.append(action);
      row.append(actionCell);
      elements.ruleBody.append(row);
    });
  }

  function heroStat(label, value) {
    const card = make("div", "hero-stat");
    card.append(make("span", "", label), make("strong", "", value));
    return card;
  }

  function renderRuleDetail() {
    const rule = selectedRule() || state.rules[0];
    if (!rule) {
      const emptyOption = option("", "暂无注册规则");
      emptyOption.disabled = true;
      replaceOptions(elements.ruleSelect, [emptyOption], "");
      elements.ruleHero.replaceChildren(
        make("div", "empty-cell", "当前没有注册的 TradingRuleDefinition。"),
      );
      elements.ruleFormulae.replaceChildren(
        make("div", "empty-cell", "暂无规则定义"),
      );
      elements.ruleConstraints.replaceChildren();
      elements.ruleParameters.replaceChildren(
        make("div", "empty-cell", "暂无规则配置字段"),
      );
      elements.ruleFlow.replaceChildren();
      elements.ruleNotes.replaceChildren(
        make("div", "empty-cell", "暂无能力声明"),
      );
      elements.ruleConfigBody.replaceChildren();
      emptyRow(elements.ruleConfigBody, 3, "尚未被 Strategy 使用");
      return;
    }
    state.ruleId = rule.id;
    replaceOptions(
      elements.ruleSelect,
      state.rules.map((item) => option(item.id, ruleName(item))),
      rule.id,
    );

    const descriptor = rule.descriptor || {};
    const summary = descriptor.description
      || descriptor.summary
      || "该规则来自历史结果，当前版本尚未注册独立定义。";
    const content = make("div");
    content.append(
      make("div", "eyebrow", "TRADING RULE"),
      make("h2", "", ruleName(rule)),
      make("div", "hero-type", rule.type),
      make("p", "", summary),
    );
    const stats = make("div", "hero-stats");
    stats.append(
      heroStat("Strategy", rule.strategies.length),
      heroStat("实验", rule.experiments.length),
      heroStat("Rule Instances", rule.instances.length),
    );
    elements.ruleHero.replaceChildren(content, stats);

    elements.ruleFormulae.replaceChildren();
    const formulae = descriptor.formulae || [];
    (formulae.length ? formulae : ["该规则尚未登记状态转移公式。"])
      .forEach((formula) =>
        elements.ruleFormulae.append(
          make("code", "formula-item", formula),
        ),
      );
    elements.ruleConstraints.replaceChildren();
    (descriptor.constraints || []).forEach((constraint) =>
      elements.ruleConstraints.append(
        make("span", "constraint-item", constraint),
      ),
    );
    elements.ruleParameters.replaceChildren();
    const descriptorParameters = descriptor.config_fields || [];
    if (!descriptorParameters.length) {
      elements.ruleParameters.append(
        make("div", "empty-cell", "该规则没有内部配置字段"),
      );
    } else {
      descriptorParameters.forEach((parameter) => {
        const row = make("div", "strategy-parameter-item");
        const identity = make("div");
        identity.append(
          make("strong", "", parameter.name || parameter.key),
          make("code", "", parameter.key),
        );
        row.append(
          identity,
          make(
            "span",
            "",
            parameter.required
              ? "必填"
              : `默认 ${simpleValue(parameter.default)}`,
          ),
        );
        elements.ruleParameters.append(row);
      });
    }

    const flow = (descriptor.lifecycle || []).map((phase) => ({
      title: phase,
      detail: "Rule state",
    }));
    elements.ruleFlow.replaceChildren();
    flow.forEach((step, index) => {
      const node = make("div");
      node.append(
        make("span", "", String(index + 1).padStart(2, "0")),
        make("strong", "", step.title),
        make("small", "", step.detail),
      );
      elements.ruleFlow.append(node);
      if (index < flow.length - 1) {
        elements.ruleFlow.append(make("i", "", "→"));
      }
    });

    elements.ruleNotes.replaceChildren();
    [
      `输入：${(descriptor.input_types || []).join("、") || "未登记"}`,
      `输出：${(descriptor.output_types || []).join("、") || "未登记"}`,
      `方向：${(descriptor.supported_directions || []).join("、") || "未登记"}`,
      `产品：${(descriptor.supported_product_types || []).join("、") || "未登记"}`,
    ].forEach((note) =>
      elements.ruleNotes.append(make("div", "note-item", note)),
    );

    elements.ruleConfigBody.replaceChildren();
    if (!rule.configurations.length && !rule.strategies.length) {
      emptyRow(
        elements.ruleConfigBody,
        3,
        "规则已经注册，但尚无 Strategy 组合或实验记录",
      );
      return;
    }
    const configurations = rule.configurations.length
      ? rule.configurations
      : rule.strategies.map((strategyType) => ({
          id: strategyType,
          key: strategyType,
          parameters: {},
          run_count: rule.runs.filter(({run}) =>
            run.resolved_components.strategy.type === strategyType,
          ).length,
        }));
    configurations.forEach((configuration) => {
      const row = document.createElement("tr");
      row.append(primaryCell(configuration.key, shortId(configuration.id, 20)));
      const parameterCell = document.createElement("td");
      parameterCell.append(parameterSummary(configuration.parameters, 12));
      row.append(parameterCell);
      appendCell(row, configuration.run_count);
      elements.ruleConfigBody.append(row);
    });
  }

  function marketDescription(market) {
    if (market.source === "PATH_SET") {
      const roles = market.role_counts || {};
      return [
        market.parameters?.instrument || "未指定标的",
        market.parameters?.interval || "原始周期",
        market.parameters?.model_type || "未指定模型",
        `TRAIN ${roles.TRAIN || 0}`,
        `VALIDATION ${roles.VALIDATION || 0}`,
        `HOLDOUT ${roles.HOLDOUT || 0}`,
      ].join(" · ");
    }
    const instrument = market.parameters?.instrument || "未指定标的";
    const interval = market.parameters?.interval || "原始周期";
    const anchors = Array.isArray(market.parameters?.anchors)
      ? `${market.parameters.anchors.length} 个趋势节点`
      : "无趋势节点";
    return `${instrument} · ${interval} · ${anchors} · ${market.paths.length} 条 Seed 路径`;
  }

  function renderMarketList() {
    elements.marketList.replaceChildren();
    Model.marketAssetGroups(state.markets).forEach((group) => {
      const details = make("details", "market-asset-group");
      details.open = group.markets.some((market) => market.id === state.marketId);
      const summary = make("summary", "market-asset-summary");
      const title = make("span", "market-asset-title", group.asset);
      title.append(make("small", "", "币种"));
      summary.append(
        title,
        make(
          "span",
          "market-asset-count",
          `${group.scenario_count} 类行情 · ${group.path_count} 条路径`,
        ),
      );
      const types = make("div", "market-type-list");
      group.markets.forEach((market) => {
        const assetPrefix = `${group.asset} `;
        const typeName = market.key.startsWith(assetPrefix)
          ? market.key.slice(assetPrefix.length)
          : market.key;
        const button = make("button", "selection-item market-type-item");
        button.type = "button";
        button.classList.toggle("active", market.id === state.marketId);
        button.append(
          make("span", "market-type-kicker", "行情类型"),
          make("strong", "", typeName),
          make(
            "span",
            market.source === "PATH_SET" ? "source-path-set" : "",
            market.type,
          ),
          make("span", "", marketDescription(market)),
        );
        button.addEventListener("click", () => selectMarket(market.id));
        types.append(button);
      });
      details.append(summary, types);
      elements.marketList.append(details);
    });
    if (!state.markets.length) {
      elements.marketList.append(
        make("div", "empty-cell", "尚未发现 PathSet 或实验市场环境"),
      );
    }
  }

  function renderMarketParameters(market) {
    elements.marketChips.replaceChildren();
    Object.entries(market.parameters || {}).forEach(([key, value]) => {
      if (Array.isArray(value) || (value && typeof value === "object")) return;
      elements.marketChips.append(
        make("span", "parameter-chip", `${key}: ${simpleValue(value)}`),
      );
    });
    if (market.source === "PATH_SET") {
      elements.marketChips.prepend(
        make(
          "span",
          "parameter-chip path-set-chip",
          `PathSet: ${market.path_set.path_set_id}`,
        ),
      );
    }
  }

  function roleLabel(role) {
    if (role === "TRAIN") return "TRAIN · 训练研究";
    if (role === "VALIDATION") return "VALIDATION · 参数验证";
    if (role === "HOLDOUT") return "HOLDOUT · 最终样本外锁定";
    return "EXPERIMENT · 已运行实验";
  }

  function marketRoles(market) {
    const order = ["TRAIN", "VALIDATION", "HOLDOUT", "EXPERIMENT"];
    return [...new Set((market.paths || []).map(
      (path) => path.role || "EXPERIMENT",
    ))].sort((left, right) => order.indexOf(left) - order.indexOf(right));
  }

  function pathsForSelectedRole(market) {
    return (market?.paths || []).filter(
      (path) => (path.role || "EXPERIMENT") === state.marketRole,
    );
  }

  function marketPathValue(path) {
    return path.source === "PATH_SET"
      ? path.market_path_id
      : `${path.experiment_id}:${path.run_id}`;
  }

  function selectedMarketPath() {
    return pathsForSelectedRole(selectedMarket()).find(
      (path) => marketPathValue(path) === elements.marketPathSelect.value,
    );
  }

  function renderMarketPathOptions(market, selectedValue = null) {
    const paths = pathsForSelectedRole(market);
    replaceOptions(
      elements.marketPathSelect,
      paths.map((path) => option(
        marketPathValue(path),
        path.availability === "LOCKED"
          ? `Seed ${path.seed} · 已锁定 · ${shortId(path.market_path_id)}`
          : `Seed ${path.seed} · ${shortId(path.market_path_id)}`,
      )),
      selectedValue,
    );
    elements.marketPathSelect.disabled = !paths.length;
  }

  function profileStat(label, value, detail = "") {
    const card = make("div", "market-profile-stat");
    card.append(make("span", "", label), make("strong", "", value));
    if (detail) card.append(make("small", "", detail));
    return card;
  }

  function renderMarketProfile(path) {
    elements.marketProfileFacts.replaceChildren();
    if (!path) return;
    if (path.availability === "LOCKED") {
      elements.marketProfileFacts.append(
        profileStat("数据角色", "HOLDOUT", "仅保留身份与数量"),
        profileStat("Seed", path.seed, shortId(path.market_path_id)),
        profileStat("价格走势", "已锁定", "最终样本外验收前不可读取"),
        profileStat("策略运行", "禁止", "不参与研究和调参"),
      );
      return;
    }
    if (!path.market_profile) {
      elements.marketProfileFacts.append(
        profileStat("Seed", path.seed, shortId(path.market_path_id)),
        profileStat("数据来源", "实验 Trace", path.experiment_id || "—"),
        profileStat("Trace", path.trace_state || "—", path.run_id || "—"),
      );
      return;
    }
    const profile = path.market_profile || {};
    elements.marketProfileFacts.append(
      profileStat("Seed", path.seed, shortId(path.market_path_id)),
      profileStat("期初价格", formatNumber(profile.initial_price, 1)),
      profileStat("期末价格", formatNumber(profile.final_price, 1)),
      profileStat("区间最低", formatNumber(profile.minimum_low, 1)),
      profileStat("区间最高", formatNumber(profile.maximum_high, 1)),
      profileStat("最大回撤", formatRatio(profile.max_drawdown_rate)),
      profileStat(
        "实现波动率",
        formatRatio(profile.annualized_realized_volatility),
      ),
    );
  }

  async function selectMarket(marketId, {load = true} = {}) {
    state.marketId = marketId;
    state.marketDocument = null;
    const market = selectedMarket();
    renderMarketList();
    if (!market) return;
    elements.marketTitle.textContent = market.key;
    elements.marketDescription.textContent = market.description
      || `${market.type} · ${marketDescription(market)}`;
    renderMarketParameters(market);
    const roles = marketRoles(market);
    state.marketRole = roles.includes(state.marketRole)
      ? state.marketRole
      : roles[0] || null;
    replaceOptions(
      elements.marketRoleSelect,
      roles.map((role) => option(role, roleLabel(role))),
      state.marketRole,
    );
    elements.marketRoleSelect.disabled = !roles.length;
    renderMarketPathOptions(market);
    renderMarketProfile(selectedMarketPath());
    if (load && market.paths.length) await loadMarketPath();
  }

  async function loadMarketPath() {
    const market = selectedMarket();
    if (!market) return;
    const path = selectedMarketPath();
    if (!path) return;
    renderMarketProfile(path);
    elements.marketChartEmpty.hidden = false;
    elements.marketChart.replaceChildren();
    if (path.availability === "LOCKED") {
      state.marketDocument = null;
      elements.marketChartEmpty.textContent =
        "HOLDOUT 路径已经物化并锁定；最终样本外验收前不展示价格走势，也不允许运行策略。";
      elements.marketDescription.textContent =
        `${market.key} · HOLDOUT · Seed ${path.seed} · 内容已锁定`;
      return;
    }
    elements.marketChartEmpty.textContent = "正在校验并载入价格路径…";
    try {
      if (path.source === "PATH_SET") {
        state.marketDocument = await request(
          `${apiPath(
            "market-path-sets",
            path.path_set_id,
            "paths",
            path.market_path_id,
          )}?interval=${encodeURIComponent(state.marketInterval)}`,
        );
      } else {
        state.marketDocument = await request(
          apiPath(
            "experiments",
            path.experiment_id,
            "runs",
            path.run_id,
            "viewer",
          ),
        );
      }
      renderMarketChart();
    } catch (error) {
      elements.marketChartEmpty.textContent =
        path.source === "PATH_SET" || path.trace_state === "STORED"
          ? `价格路径载入失败：${error.message}`
          : "该 Run 的 Trace 已清理，当前不能读取价格路径";
    }
  }

  function svgEscape(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function renderMarketChart() {
    if (!state.marketDocument?.market?.length) return;
    const preAggregated = Boolean(
      state.marketDocument.aggregation_interval,
    );
    const bars = preAggregated
      ? state.marketDocument.market
      : Model.aggregateBars(
          state.marketDocument.market,
          state.marketInterval,
        );
    const width = 1200;
    const height = 520;
    const margin = {left: 18, right: 82, top: 24, bottom: 42};
    const plotWidth = width - margin.left - margin.right;
    const plotHeight = height - margin.top - margin.bottom;
    const lows = bars.map((bar) => Number(bar.low));
    const highs = bars.map((bar) => Number(bar.high));
    let min = Math.min(...lows);
    let max = Math.max(...highs);
    const padding = (max - min) * 0.06 || max * 0.01;
    min -= padding;
    max += padding;
    const slot = plotWidth / bars.length;
    const x = (index) => margin.left + slot * (index + 0.5);
    const y = (price) => margin.top + ((max - price) / (max - min)) * plotHeight;
    const horizontal = Array.from({length: 6}, (_, index) => {
      const fraction = index / 5;
      const price = max - (max - min) * fraction;
      const py = margin.top + plotHeight * fraction;
      return `<line class="grid-line" x1="${margin.left}" x2="${width - margin.right}" y1="${py}" y2="${py}"></line><text class="axis-text" x="${width - margin.right + 9}" y="${py + 4}">${formatNumber(price, 0)}</text>`;
    }).join("");
    const labelIndexes = Array.from({length: Math.min(7, bars.length)}, (_, index) =>
      Math.round((index / Math.max(1, Math.min(7, bars.length) - 1)) * (bars.length - 1)),
    );
    const vertical = labelIndexes.map((index) => {
      const px = x(index);
      const date = bars[index].date;
      return `<line class="grid-line" x1="${px}" x2="${px}" y1="${margin.top}" y2="${margin.top + plotHeight}"></line><text class="axis-text" x="${px}" y="${height - 13}" text-anchor="middle">${svgEscape(date.slice(0, 7))}</text>`;
    }).join("");
    const candles = bars.map((bar, index) => {
      const up = Number(bar.close) >= Number(bar.open);
      const kind = up ? "up" : "down";
      const px = x(index);
      const bodyTop = y(Math.max(Number(bar.open), Number(bar.close)));
      const bodyBottom = y(Math.min(Number(bar.open), Number(bar.close)));
      const bodyWidth = Math.max(2.4, Math.min(10, slot * 0.62));
      return `<g><title>${svgEscape(bar.date)} O ${formatNumber(bar.open)} H ${formatNumber(bar.high)} L ${formatNumber(bar.low)} C ${formatNumber(bar.close)}</title><line class="wick-${kind}" x1="${px}" x2="${px}" y1="${y(Number(bar.high))}" y2="${y(Number(bar.low))}"></line><rect class="candle-${kind}" x="${px - bodyWidth / 2}" y="${bodyTop}" width="${bodyWidth}" height="${Math.max(1.5, bodyBottom - bodyTop)}"></rect></g>`;
    }).join("");
    elements.marketChart.innerHTML = horizontal + vertical + candles;
    elements.marketChartEmpty.hidden = true;
    const manifest = state.marketDocument.manifest || {};
    const sourceCount = state.marketDocument.source_frame_count
      || state.marketDocument.market.length;
    const sourceLabel = preAggregated
      ? `${sourceCount} 根小时 K 线`
      : `${sourceCount} 根日线`;
    elements.marketDescription.textContent = [
      manifest.instrument || bars[0].instrument,
      sourceLabel,
      `${bars.length} 根${state.marketInterval === "1w" ? "周线" : "月线"}`,
      `${bars[0].date} — ${bars.at(-1).date}`,
    ].filter(Boolean).join(" · ");
  }

  function metricValue(aggregates, metricKey, dimensions = {}) {
    for (const aggregate of aggregates || []) {
      for (const value of aggregate.values || []) {
        if (value.metric_key !== metricKey) continue;
        const matches = Object.entries(dimensions).every(
          ([key, expected]) => value.dimensions?.[key] === expected,
        );
        if (matches) return value;
      }
    }
    return null;
  }

  function scenarioMetricText(scenario, metricKey, dimensions, ratio = false) {
    const value = metricValue(scenario.aggregates, metricKey, dimensions);
    const mean = value?.statistics?.mean;
    if (mean === null || mean === undefined) return "—";
    return ratio ? formatRatio(mean) : formatNumber(mean, 4);
  }

  function scenarioLiquidationRate(scenario) {
    const core = (scenario.aggregates || []).find(
      (aggregate) => aggregate.metric_set_id === "core",
    );
    const rate = core?.counts?.liquidation_rate;
    return rate === null || rate === undefined ? "—" : formatRatio(rate);
  }

  function scenarioLabel(scenario) {
    const params = Object.entries(scenario.parameter_values || {})
      .map(([path, value]) => `${parameterName(path)}=${simpleValue(value)}`)
      .join(" · ");
    return params || scenario.strategy.key;
  }

  function openScenarioDetail(record, scenario, seed = null) {
    state.detailExperimentId = record.experiment.experiment_id;
    state.detailDatabaseName = record.experiment.database_name;
    state.detailScenarioId = scenario.scenario_id;
    state.detailRunId = scenario.runs.find(
      (run) => seed === null || run.seed === seed,
    )?.run_id;
    renderDetailSelectors();
    loadSelectedRun().catch((error) =>
      showMessage(`Run 载入失败：${error.message}`, true),
    );
    setPage("experiment-detail");
  }

  function scenarioTable(record) {
    const wrapper = make("div", "table-wrap scenario-table-wrap");
    const table = make("table", "research-table scenario-table");
    const head = document.createElement("thead");
    const header = document.createElement("tr");
    [
      "策略配置",
      "参数轴",
      "市场 / 执行 / 账户",
      "Seeds",
      "状态",
      "账户收益均值",
      "账户回撤均值",
      "强平率",
      "成交均值",
      "",
    ].forEach((label) => header.append(make("th", "", label)));
    head.append(header);
    const body = document.createElement("tbody");
    Model.scenarioRows(record).forEach((scenario) => {
      const row = document.createElement("tr");
      row.append(primaryCell(scenario.strategy.key, scenario.strategy.type));
      const params = document.createElement("td");
      params.append(parameterSummary(scenario.parameter_values));
      row.append(params);
      appendCell(
        row,
        `${scenario.market.key} / ${scenario.execution.key} / ${scenario.account.key}`,
      );
      appendCell(row, scenario.seeds.join(", "));
      appendCell(row, statusText(scenario.status_counts));
      appendCell(
        row,
        scenarioAccountMetricText(scenario, "return.total_rate"),
      );
      appendCell(
        row,
        scenarioAccountMetricText(scenario, "risk.max_drawdown_rate"),
      );
      appendCell(row, scenarioLiquidationRate(scenario));
      appendCell(
        row,
        scenarioMetricText(scenario, "execution.fill_count", {}),
      );
      const actionCell = document.createElement("td");
      const action = make("button", "text-button", "查看 Run →");
      action.type = "button";
      action.addEventListener("click", () => openScenarioDetail(record, scenario));
      actionCell.append(action);
      row.append(actionCell);
      body.append(row);
    });
    table.append(head, body);
    wrapper.append(table);
    return wrapper;
  }

  function scenarioAccountMetricText(scenario, metricKey) {
    for (const asset of ["BTC", "USDT"]) {
      const rendered = scenarioMetricText(
        scenario,
        metricKey,
        {scope: "account.total_equity", valuation_asset: asset},
        true,
      );
      if (rendered !== "—") return `${rendered} ${asset}`;
    }
    return "—";
  }

  function strategyParameterDescriptors(strategy) {
    return strategy?.descriptor?.parameters || [];
  }

  function researchParameterLabel(strategy, path) {
    if (path === "execution.funding_rate") return "日资金费率";
    const parts = String(path).split(".");
    const parameter = strategyParameterDescriptors(strategy).find(
      (item) => item.key === parts[0],
    );
    if (!parameter) return parts.at(-1) || parameterName(path);
    return parts.length === 1
      ? parameter.name
      : `${parameter.name} · ${parts.slice(1).join(".")}`;
  }

  function candidateParameterSummary(candidate, strategy) {
    const entries = Object.entries(candidate.parameter_values || {});
    const wrapper = make("div", "parameter-summary candidate-parameters");
    if (!entries.length) {
      wrapper.append(make("span", "parameter-empty", "无可调参数"));
      return wrapper;
    }
    entries.forEach(([path, value]) => {
      wrapper.append(
        make(
          "span",
          candidate.varying_parameter_paths.includes(path) ? "is-varying" : "",
          `${researchParameterLabel(strategy, path)}=${simpleValue(value)}`,
        ),
      );
    });
    return wrapper;
  }

  function optionalRatio(value) {
    return value === null || value === undefined ? "—" : formatRatio(value);
  }

  function optionalNumber(value, digits = 1) {
    return value === null || value === undefined
      ? "—"
      : formatNumber(value, digits);
  }

  function excessText(value) {
    if (value === null || value === undefined) return "—";
    const sign = Number(value) > 0 ? "+" : "";
    return `${sign}${formatRatio(value)}`;
  }

  function openRunDetail(record, run) {
    state.detailExperimentId = record.experiment.experiment_id;
    state.detailDatabaseName = record.experiment.database_name;
    state.detailScenarioId = run.scenario_id;
    state.detailRunId = run.run_id;
    renderDetailSelectors();
    loadSelectedRun().catch((error) =>
      showMessage(`Run 载入失败：${error.message}`, true),
    );
    setPage("experiment-detail");
  }

  function candidateSampleTable(candidate) {
    const wrapper = make("div", "table-wrap candidate-sample-wrap");
    const table = make("table", "research-table sample-table");
    const head = document.createElement("thead");
    const header = document.createElement("tr");
    [
      "数据角色",
      "市场路径",
      "Run Seed",
      "收益",
      "相对 HODL",
      "最大回撤",
      "成交 / 循环",
      "状态",
      "",
    ].forEach((label) => header.append(make("th", "", label)));
    head.append(header);
    const body = document.createElement("tbody");
    candidate.samples.forEach((sample) => {
      const row = document.createElement("tr");
      appendCell(row, sample.role);
      row.append(
        primaryCell(
          sample.run.resolved_components.market.key,
          sample.run.market_path_id || "未保存路径身份",
        ),
      );
      appendCell(row, sample.run.seed);
      appendCell(row, optionalRatio(sample.return_rate));
      appendCell(row, excessText(sample.excess_vs_hodl));
      appendCell(row, optionalRatio(sample.max_drawdown_rate));
      appendCell(
        row,
        `${optionalNumber(sample.fill_count, 0)} / ${optionalNumber(sample.completed_cycles, 0)}`,
      );
      appendCell(row, sample.liquidated ? "已强平" : sample.run.status);
      const actionCell = document.createElement("td");
      const action = make("button", "text-button", "查看 Run →");
      action.type = "button";
      action.addEventListener("click", () =>
        openRunDetail(sample.record, sample.run),
      );
      actionCell.append(action);
      row.append(actionCell);
      body.append(row);
    });
    table.append(head, body);
    wrapper.append(table);
    return wrapper;
  }

  function candidateComparisonTable(study, strategy) {
    const preferredPaths = strategyParameterDescriptors(strategy).map(
      (parameter) => parameter.key,
    );
    const candidates = Model.candidateRows(
      study.preferred_records,
      study.strategy_type,
      preferredPaths,
    );
    const wrapper = make("div", "table-wrap candidate-table-wrap");
    const table = make("table", "research-table candidate-table");
    const head = document.createElement("thead");
    const header = document.createElement("tr");
    [
      "参数组合",
      "本次变化参数",
      "配合策略",
      "样本",
      "TRAIN 收益中位数",
      "VALIDATION 收益中位数",
      "相对 HODL",
      "最差回撤",
      "强平率",
      "成交 / 循环中位数",
      "",
    ].forEach((label) => header.append(make("th", "", label)));
    head.append(header);
    const body = document.createElement("tbody");
    candidates.forEach((candidate) => {
      const row = make("tr", "candidate-row");
      row.append(
        primaryCell(
          candidate.label,
          `${candidate.asset} 计价 · ${statusText(candidate.status_counts)}`,
        ),
      );
      const parameters = document.createElement("td");
      parameters.append(candidateParameterSummary(candidate, strategy));
      row.append(parameters);
      appendCell(
        row,
        companionStrategyText(candidate.samples[0]?.run),
      );
      appendCell(row, candidate.summary?.sample_count || 0);
      appendCell(
        row,
        candidate.train
          ? `${optionalRatio(candidate.train.return_median)} ${candidate.asset}`
          : "—",
      );
      appendCell(
        row,
        candidate.validation
          ? `${optionalRatio(candidate.validation.return_median)} ${candidate.asset}`
          : "—",
      );
      appendCell(
        row,
        excessText(candidate.summary?.excess_median),
      );
      appendCell(row, optionalRatio(candidate.summary?.drawdown_worst));
      const liquidation = appendCell(
        row,
        optionalRatio(candidate.summary?.liquidation_rate),
      );
      if (candidate.summary?.liquidation_rate > 0) {
        liquidation.classList.add("risk-cell");
      }
      appendCell(
        row,
        `${optionalNumber(candidate.summary?.fill_median, 0)} / ${optionalNumber(candidate.summary?.cycle_median, 0)}`,
      );
      const actionCell = document.createElement("td");
      const expand = make("button", "text-button", "展开样本");
      expand.type = "button";
      actionCell.append(expand);
      row.append(actionCell);
      const detailRow = make("tr", "candidate-samples-row");
      detailRow.hidden = true;
      const detailCell = document.createElement("td");
      detailCell.colSpan = 11;
      detailCell.append(candidateSampleTable(candidate));
      detailRow.append(detailCell);
      expand.addEventListener("click", () => {
        detailRow.hidden = !detailRow.hidden;
        expand.textContent = detailRow.hidden ? "展开样本" : "收起样本";
      });
      body.append(row, detailRow);
    });
    table.append(head, body);
    wrapper.append(table);
    return {element: wrapper, candidates};
  }

  function svgNode(tag, attributes = {}, text = null) {
    const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    Object.entries(attributes).forEach(([name, value]) =>
      node.setAttribute(name, String(value)),
    );
    if (text !== null) node.textContent = String(text);
    return node;
  }

  function horizontalComparisonChart(
    candidates,
    seriesDefinitions,
    {ratio = false} = {},
  ) {
    const width = 760;
    const rowHeight = Math.max(62, 36 + seriesDefinitions.length * 17);
    const height = Math.max(210, 66 + candidates.length * rowHeight);
    const left = 145;
    const right = 84;
    const top = 24;
    const bottom = 30;
    const plotRight = width - right;
    const values = candidates.flatMap((candidate) =>
      seriesDefinitions.flatMap((series) => {
        const rawValue = series.value(candidate);
        if (rawValue === null || rawValue === undefined) return [];
        const value = Number(rawValue);
        return Number.isFinite(value) ? [value] : [];
      }),
    );
    if (!values.length) {
      return make(
        "div",
        "chart-empty",
        "指标尚未评估；完成 MetricSet 计算后才显示比较图。",
      );
    }
    let minimum = Math.min(0, ...values);
    let maximum = Math.max(0, ...values);
    if (minimum === maximum) {
      const padding = ratio ? 0.01 : Math.max(1, Math.abs(maximum));
      minimum -= padding;
      maximum += padding;
    } else {
      const padding = (maximum - minimum) * 0.08;
      minimum -= minimum < 0 ? padding : 0;
      maximum += maximum > 0 ? padding : 0;
    }
    const scale = (value) =>
      left + ((Number(value) - minimum) / (maximum - minimum))
        * (plotRight - left);
    const zeroX = scale(0);
    const svg = svgNode("svg", {
      class: "tearsheet-chart-svg",
      viewBox: `0 0 ${width} ${height}`,
      role: "img",
      "aria-label": "参数组合指标比较图",
    });
    [minimum, 0, maximum].forEach((value) => {
      const x = scale(value);
      svg.append(
        svgNode("line", {
          x1: x,
          y1: top,
          x2: x,
          y2: height - bottom,
          class: value === 0 ? "chart-zero" : "chart-grid",
        }),
        svgNode(
          "text",
          {
            x,
            y: height - 9,
            class: "chart-axis-label",
            "text-anchor": value === minimum
              ? "start"
              : value === maximum
                ? "end"
                : "middle",
          },
          ratio ? formatRatio(value) : formatNumber(value, 0),
        ),
      );
    });
    candidates.forEach((candidate, candidateIndex) => {
      const rowTop = top + candidateIndex * rowHeight;
      svg.append(
        svgNode(
          "text",
          {x: 8, y: rowTop + 17, class: "chart-candidate-label"},
          candidate.label,
        ),
        svgNode("line", {
          x1: 8,
          y1: rowTop + rowHeight - 4,
          x2: width - 8,
          y2: rowTop + rowHeight - 4,
          class: "chart-row-line",
        }),
      );
      seriesDefinitions.forEach((series, seriesIndex) => {
        const rawValue = series.value(candidate);
        if (rawValue === null || rawValue === undefined) return;
        const value = Number(rawValue);
        const y = rowTop + 25 + seriesIndex * 17;
        svg.append(
          svgNode(
            "text",
            {x: left - 10, y: y + 8, class: "chart-series-label", "text-anchor": "end"},
            series.label,
          ),
        );
        if (!Number.isFinite(value)) return;
        const valueX = scale(value);
        svg.append(
          svgNode("rect", {
            x: Math.min(zeroX, valueX),
            y,
            width: Math.max(1.5, Math.abs(valueX - zeroX)),
            height: 9,
            rx: 2,
            fill: series.color,
          }),
          svgNode(
            "text",
            {
              x: width - 8,
              y: y + 8,
              class: "chart-value-label",
              "text-anchor": "end",
            },
            ratio ? formatRatio(value) : formatNumber(value, 0),
          ),
        );
      });
    });
    return svg;
  }

  function tearSheetCharts(candidates) {
    const charts = make("div", "tearsheet-charts");
    const performance = make("section", "tearsheet-chart-block");
    performance.append(
      make("h4", "", "收益、最差回撤与保证金风险"),
      make(
        "p",
        "",
        "有 VALIDATION 时优先显示样本外收益；回撤以负方向表示。",
      ),
      horizontalComparisonChart(
        candidates,
        [
          {
            label: "验证收益",
            value: (candidate) => candidate.validation?.return_median
              ?? candidate.summary?.return_median,
            color: "#26a69a",
          },
          {
            label: "最差回撤",
            value: (candidate) => candidate.summary?.drawdown_worst === null
              || candidate.summary?.drawdown_worst === undefined
              ? null
              : -Number(candidate.summary.drawdown_worst),
            color: "#ef5350",
          },
          {
            label: "峰值初始保证金占用",
            value: (candidate) => (
              candidate.summary?.initial_margin_utilization_worst
            ),
            color: "#ab47bc",
          },
          {
            label: "峰值保证金风险",
            value: (candidate) => candidate.summary?.margin_risk_worst,
            color: "#f0a33e",
          },
        ],
        {ratio: true},
      ),
    );
    const activity = make("section", "tearsheet-chart-block");
    activity.append(
      make("h4", "", "成交与完整循环"),
      make("p", "", "使用每个参数组合在全部样本上的中位数。"),
      horizontalComparisonChart(candidates, [
        {
          label: "成交数",
          value: (candidate) => candidate.summary?.fill_median,
          color: "#5b8def",
        },
        {
          label: "完整循环",
          value: (candidate) => candidate.summary?.cycle_median,
          color: "#d99a42",
        },
      ]),
    );
    const funding = make("section", "tearsheet-chart-block");
    funding.append(
      make("h4", "", "资金费净变动"),
      make(
        "p",
        "",
        "正值表示收到资金费，负值表示支付；单位使用合约结算资产。",
      ),
      horizontalComparisonChart(candidates, [{
        label: "资金费",
        value: (candidate) => candidate.summary?.funding_median,
        color: "#5b8def",
      }]),
    );
    charts.append(performance, funding, activity);
    return charts;
  }

  function tearSheetMetrics(candidates) {
    const panel = make("aside", "tearsheet-metrics");
    panel.append(
      make("h4", "", "关键绩效指标"),
      make("p", "", "同一 Study 内按参数组合对齐，页面不重新计算指标。"),
    );
    const wrapper = make("div", "tearsheet-metric-table-wrap");
    const table = make("table", "tearsheet-metric-table");
    const head = document.createElement("thead");
    const header = document.createElement("tr");
    header.append(make("th", "", "指标"));
    candidates.forEach((candidate) =>
      header.append(make("th", "", candidate.label)),
    );
    head.append(header);
    const body = document.createElement("tbody");
    const rows = [
      ["样本数", (candidate) => candidate.summary?.sample_count ?? 0],
      ["TRAIN 收益中位数", (candidate) => optionalRatio(candidate.train?.return_median)],
      ["VALIDATION 收益中位数", (candidate) => optionalRatio(candidate.validation?.return_median)],
      ["全部收益中位数", (candidate) => optionalRatio(candidate.summary?.return_median)],
      ["相对 HODL", (candidate) => excessText(candidate.summary?.excess_median)],
      ["最差回撤", (candidate) => optionalRatio(candidate.summary?.drawdown_worst)],
      ["强平率", (candidate) => optionalRatio(candidate.summary?.liquidation_rate)],
      ["峰值保证金风险", (candidate) => optionalRatio(candidate.summary?.margin_risk_worst)],
      ["峰值初始保证金占用", (candidate) => optionalRatio(candidate.summary?.initial_margin_utilization_worst)],
      ["最大实际仓位倍率", (candidate) => {
        const value = candidate.summary?.max_effective_leverage;
        return value === null || value === undefined
          ? "—"
          : `${formatNumber(value, 3)}×`;
      }],
      ["建仓合约张数", (candidate) => optionalNumber(candidate.summary?.entry_contracts_median, 0)],
      ["入场预计强平价", (candidate) => optionalNumber(candidate.summary?.estimated_liquidation_price_median, 2)],
      ["成交数中位数", (candidate) => optionalNumber(candidate.summary?.fill_median, 0)],
      ["完整循环中位数", (candidate) => optionalNumber(candidate.summary?.cycle_median, 0)],
      ["资金费净变动", (candidate) => {
        const value = candidate.summary?.funding_median;
        const fundingAsset = candidate.samples[0]?.funding_asset
          || candidate.asset;
        return value === null || value === undefined
          ? "—"
          : `${formatNumber(value, 6)} ${fundingAsset}`;
      }],
      ["资金费结算次数", (candidate) => optionalNumber(candidate.summary?.funding_settlement_count_median, 0)],
      ["手续费中位数", (candidate) => {
        const value = candidate.summary?.fee_median;
        return value === null || value === undefined
          ? "—"
          : `${formatNumber(value, candidate.asset === "BTC" ? 6 : 2)} ${candidate.asset}`;
      }],
    ];
    rows.forEach(([label, formatter]) => {
      const row = document.createElement("tr");
      row.append(make("th", "", label));
      candidates.forEach((candidate) =>
        row.append(make("td", "", formatter(candidate))),
      );
      body.append(row);
    });
    table.append(head, body);
    wrapper.append(table);
    panel.append(wrapper);
    return panel;
  }

  function contextText(items, kind) {
    const values = items.map((item) => {
      const parameters = item.parameters || {};
      if (kind === "market" && parameters.path_set_id) {
        return [
          parameters.path_set_id,
          parameters.scenario_id || "全部场景",
          parameters.instrument,
          parameters.interval,
        ].filter(Boolean).join(" · ");
      }
      if (kind === "market" && parameters.content_sha256) {
        return [
          item.type,
          parameters.instrument,
          parameters.interval,
          shortId(parameters.content_sha256),
        ].filter(Boolean).join(" · ");
      }
      const parameterText = Object.entries(parameters)
        .filter(([key]) => ![
          "path",
          "content_sha256",
          "file_sha256",
        ].includes(key))
        .slice(0, 4)
        .map(([key, value]) => `${parameterName(key)}=${simpleValue(value)}`)
        .join(" · ");
      return parameterText ? `${item.type} · ${parameterText}` : item.type;
    });
    return [...new Set(values)].join("；") || "—";
  }

  function revisionHistory(study) {
    const block = make("details", "study-revisions");
    const summary = make(
      "summary",
      "",
      `执行版本 ${study.versions.length} 个（默认只比较推荐版本）`,
    );
    const list = make("div", "study-revision-list");
    study.versions.forEach((record) => {
      const revision = Model.revisionSummary(record);
      const selected = study.preferred_records.some(
        (preferred) => preferred.experiment.database_name
          === record.experiment.database_name,
      );
      const row = make("div", "study-revision-row");
      const identity = make("div");
      identity.append(
        make("strong", "", record.experiment.database_name),
        make(
          "span",
          "",
          `${record.experiment.experiment_id} · ${dateText(record.experiment.updated_at)}`,
        ),
      );
      row.append(
        identity,
        make(
          "span",
          selected ? "revision-badge recommended" : "revision-badge",
          selected ? `${revision.label} · 用于比较` : revision.label,
        ),
      );
      list.append(row);
    });
    block.append(summary, list);
    return block;
  }

  function companionStrategyText(run) {
    const companions = Model.companionStrategies(run);
    return companions.length
      ? companions.map((item) =>
          `${item.display_name} × ${item.count}`,
        ).join("、")
      : "无";
  }

  function studyCard(study, strategy) {
    const record = study.preferred;
    const card = make("details", "data-panel study-card");
    const requested = study.versions.some((version) =>
      version.experiment.experiment_id === state.overviewExperimentId
      && version.experiment.database_name === state.overviewDatabaseName,
    );
    card.open = requested;
    card.dataset.requested = requested ? "true" : "false";
    const header = make("summary", "study-card-heading");
    const copy = make("div", "study-card-copy");
    const title = make("h3");
    const titleLink = make(
      "a",
      "experiment-detail-link",
      record.experiment.experiment_id,
    );
    titleLink.href = experimentDetailHref(record.experiment);
    title.append(titleLink);
    copy.append(
      make("div", "eyebrow", "PARAMETER STUDY EXPERIMENT"),
      title,
      make(
        "p",
        "",
        `${strategyName(strategy)} · ${record.experiment.description || "无说明"} · ${study.versions.length} 个执行版本`,
      ),
    );
    const headerActions = make("div", "study-card-actions");
    const expandLabel = make(
      "span",
      "study-expand-label",
      requested ? "收起报告" : "展开报告",
    );
    headerActions.append(
      make(
        "span",
        "study-status-badge",
        `${study.versions.length} 个版本`,
      ),
      expandLabel,
    );
    header.append(copy, headerActions);
    card.append(header);
    card.addEventListener("toggle", () => {
      expandLabel.textContent = card.open ? "收起报告" : "展开报告";
    });

    const body = make("div", "study-card-body");
    const composition = strategy.descriptor?.rule_composition || [];
    const primaryRuleKey = strategy.descriptor?.primary_rule_key;
    const companionSummary = make("div", "study-guidance");
    companionSummary.append(
      make(
        "strong",
        "",
        `规则组成：${composition.length
          ? composition.map((item) =>
              `${item.rule_key}=${item.rule_type}${item.rule_key === primaryRuleKey ? "（主规则）" : ""}`,
            ).join("；")
          : "历史 Strategy，尚无 Rule 身份"}`,
      ),
      make(
        "span",
        "",
        `配合策略：${companionStrategyText(
          Model.strategyDefinitionRuns(
            study.preferred_records[0],
            study.strategy_type,
          )[0],
        )}`,
      ),
    );
    const context = make("div", "study-context-grid");
    [
      ["市场环境", contextText(study.context.market, "market")],
      ["执行与成本", contextText(study.context.execution, "execution")],
      ["账户模型", contextText(study.context.account, "account")],
    ].forEach(([label, value]) => {
      const item = make("div", "study-context-item");
      item.append(make("span", "", label), make("strong", "", value));
      context.append(item);
    });
    const axisGroups = make("div", "study-axis-groups");
    Model.strategyParameterAxisGroups(
      study.preferred,
      strategy.descriptor,
    ).forEach((group) => {
      const item = make("div", "study-axis-group");
      const groupLabel = group.scope === "RULE"
        ? group.rules.map((rule) =>
            `${rule.rule_key} · ${rule.rule_type}`,
          ).join(" + ")
        : group.scope === "STRATEGY"
          ? "Strategy / Application"
          : `${group.scope} Component`;
      item.append(
        make("strong", "", groupLabel),
        make(
          "span",
          "",
          group.axes.map((axis) =>
            `${axis.name}（${axis.values.length} 个候选值）`,
          ).join("；"),
        ),
      );
      axisGroups.append(item);
    });
    body.append(companionSummary, context, axisGroups);

    const comparison = candidateComparisonTable(study, strategy);
    const varying = [...new Set(
      comparison.candidates.flatMap((candidate) =>
        candidate.varying_parameter_paths,
      ),
    )];
    const guidance = make("div", "study-guidance");
    guidance.append(
      make(
        "strong",
        "",
        varying.length
          ? `正在比较：${varying.map((path) =>
              researchParameterLabel(strategy, path)).join("、")}`
          : "当前只有一组策略参数",
      ),
      make(
        "span",
        "",
        varying.length
          ? "报告按相同市场样本比较收益、回撤、强平和交易活跃度。"
          : "后续参数扫描产生的新组合会自动加入这份报告。",
      ),
    );
    const report = make("div", "tearsheet-layout");
    report.append(
      tearSheetCharts(comparison.candidates),
      tearSheetMetrics(comparison.candidates),
    );
    const rawDetails = make("details", "tearsheet-raw-details");
    rawDetails.append(
      make("summary", "", "参数组合与 Run 明细"),
      comparison.element,
    );
    body.append(
      guidance,
      report,
      rawDetails,
      revisionHistory(study),
    );
    card.append(body);
    return card;
  }

  function renderExperimentOverview() {
    elements.experimentGroups.replaceChildren();
    let renderedGroupCount = 0;
    state.strategyDefinitions.forEach((strategy) => {
      const studies = Model.studyGroups(state.records, strategy.type);
      if (!studies.length) return;
      const panel = make(
        "details",
        "data-panel experiment-strategy-group",
      );
      panel.open = studies.some((study) =>
        study.versions.some((version) =>
          version.experiment.experiment_id === state.overviewExperimentId
          && version.experiment.database_name === state.overviewDatabaseName,
        ),
      );
      const heading = make("summary", "experiment-group-heading");
      const identity = make("div", "experiment-group-identity");
      const runCount = strategy.runs.length;
      identity.append(
        make(
          "strong",
          "",
          strategyName(strategy),
        ),
        make(
          "span",
          "",
          `${studies.length} 个参数实验 · ${runCount} Runs`,
        ),
      );
      heading.append(
        identity,
        make("span", "experiment-group-action", "展开"),
      );
      panel.addEventListener("toggle", () => {
        heading.querySelector(".experiment-group-action").textContent =
          panel.open ? "收起" : "展开";
      });
      const studyList = make("div", "study-list");
      studies.forEach((study) =>
        studyList.append(studyCard(study, strategy)),
      );
      panel.append(heading, studyList);
      elements.experimentGroups.append(panel);
      renderedGroupCount += 1;
    });
    const requestedStudy = elements.experimentGroups.querySelector(
      '.study-card[data-requested="true"]',
    );
    if (requestedStudy) {
      window.requestAnimationFrame(() =>
        requestedStudy.scrollIntoView({block: "start"}),
      );
    }
    if (!renderedGroupCount) {
      elements.experimentGroups.append(
        make("div", "large-empty", "结果目录中还没有实验"),
      );
    }
  }

  function renderDetailSelectors() {
    const record = recordById(
      state.detailExperimentId,
      state.detailDatabaseName,
    ) || state.records[0];
    if (!record) return;
    state.detailExperimentId = record.experiment.experiment_id;
    state.detailDatabaseName = record.experiment.database_name;
    replaceOptions(
      elements.detailExperimentSelect,
      state.records.map((item) =>
        option(
          item.experiment.database_name,
          `${item.experiment.experiment_id} · ${item.experiment.database_name}`,
        ),
      ),
      state.detailDatabaseName,
    );
    const scenarios = Model.scenarioRows(record);
    const scenario = scenarios.find(
      (item) => item.scenario_id === state.detailScenarioId,
    ) || scenarios[0];
    if (!scenario) return;
    state.detailScenarioId = scenario.scenario_id;
    replaceOptions(
      elements.detailScenarioSelect,
      scenarios.map((item) =>
        option(item.scenario_id, `${scenarioLabel(item)} · ${item.market.key}`),
      ),
      scenario.scenario_id,
    );
    const run = scenario.runs.find((item) => item.run_id === state.detailRunId)
      || scenario.runs[0];
    state.detailRunId = run?.run_id || null;
    replaceOptions(
      elements.detailSeedSelect,
      scenario.runs.map((item) =>
        option(item.run_id, `Seed ${item.seed} · ${item.status}`),
      ),
      state.detailRunId,
    );
  }

  function metricDefinition(record, evaluation, value) {
    const release = `${evaluation.metric_set_id}/${evaluation.metric_set_version}`;
    const metricSet = (record.detail.metric_sets || []).find(
      (item) => `${item.metric_set_id}/${item.version}` === release,
    );
    return metricSet?.definitions?.find(
      (definition) => definition.metric_key === value.metric_key,
    );
  }

  function metricLabel(record, evaluation, value) {
    const definition = metricDefinition(record, evaluation, value);
    let label = definition?.display_name || value.metric_key;
    const dimensions = value.dimensions || {};
    if (value.metric_key === "run.liquidated") {
      label = "强平状态";
    } else if (
      value.metric_key === "return.total_rate" &&
      dimensions.scope === "account.total_equity" &&
      dimensions.valuation_asset
    ) {
      label = `${dimensions.valuation_asset} 总收益率`;
      if (dimensions.valuation_asset === "USDT") label += "（含行情）";
    } else if (
      value.metric_key === "risk.max_drawdown_rate" &&
      dimensions.scope === "account.total_equity" &&
      dimensions.valuation_asset
    ) {
      label = `${dimensions.valuation_asset} 最大回撤率`;
    } else if (
      value.metric_key === "grid.completed_cycle_annualized_on_initial_equity"
    ) {
      label = "完整网格循环简单年化（按初始权益）";
    } else if (value.metric_key === "grid.completed_cycle_net_pnl_total") {
      label = "完整网格循环累计净收益";
    } else if (value.metric_key === "grid.open_inventory_net_pnl") {
      label = "未完成网格期末净盈亏";
    } else {
      const qualifiers = [
        dimensions.valuation_asset,
        dimensions.scope,
        dimensions.instrument,
        dimensions.side,
        dimensions.role,
      ].filter(Boolean);
      if (qualifiers.length) label += ` · ${qualifiers.join(" / ")}`;
    }
    return label;
  }

  function metricRank(evaluation, value) {
    const dimensions = value.dimensions || {};
    if (
      value.metric_key === "return.total_rate" &&
      dimensions.scope === "account.total_equity"
    ) {
      return dimensions.valuation_asset === "BTC" ? 0 : 5;
    }
    if (
      value.metric_key === "return.total_rate" &&
      dimensions.scope === "account.futures_equity" &&
      dimensions.valuation_asset === "BTC"
    ) return Number.POSITIVE_INFINITY;
    if (
      value.metric_key === "risk.max_drawdown_rate" &&
      dimensions.scope === "account.total_equity"
    ) return dimensions.valuation_asset === "BTC" ? 10 : 14;
    if (value.metric_key === "run.liquidated") return 18;
    if (value.metric_key === "margin.max_maintenance_utilization") return 19;
    if (value.metric_key === "margin.minimum_buffer") return 20;
    if (value.metric_key === "margin.max_effective_leverage") return 21;
    if (value.metric_key === "execution.fill_count") return 24;
    if (value.metric_key === "grid.completed_cycles") return 25;
    if (
      value.metric_key
      === "grid.completed_cycle_annualized_on_initial_equity"
    ) return 8;
    if (value.metric_key === "grid.completed_cycle_net_pnl_total") return 26;
    if (value.metric_key === "grid.open_inventory_net_pnl") return 27;
    if (value.metric_key === "cost.total_fees") return 28;
    return Number.POSITIVE_INFINITY;
  }

  function formatMetric(record, evaluation, value) {
    if (value.status !== "AVAILABLE" || value.value === null) return "—";
    const definition = metricDefinition(record, evaluation, value);
    if (value.metric_key === "margin.max_effective_leverage") {
      return `${formatNumber(value.value, 2)}×`;
    }
    if (definition?.unit_kind === "ratio" || value.unit === "ratio") {
      return formatRatio(value.value);
    }
    if (definition?.value_type === "BOOLEAN" || typeof value.value === "boolean") {
      return value.value ? "是" : "否";
    }
    return formatNumber(value.value, 8);
  }

  function renderKeyMetrics(record, detail) {
    elements.runKeyMetrics.replaceChildren();
    const values = (detail.metrics || [])
      .flatMap((evaluation) =>
        (evaluation.values || []).map((value) => ({evaluation, value})),
      )
      .filter(({value}) => value.status === "AVAILABLE")
      .sort((left, right) =>
        metricRank(left.evaluation, left.value)
        - metricRank(right.evaluation, right.value),
      )
      .filter(({evaluation, value}) =>
        Number.isFinite(metricRank(evaluation, value)),
      )
      .slice(0, 10);
    if (!values.length) {
      elements.runKeyMetrics.append(
        make("div", "empty-cell", "本次 Run 尚未计算指标"),
      );
      return;
    }
    values.forEach(({evaluation, value}) => {
      const row = make("div", "run-metric-row");
      const label = make("div", "run-metric-label");
      label.append(
        make("span", "", metricLabel(record, evaluation, value)),
        make("small", "", `${evaluation.metric_set_id} · ${value.unit}`),
      );
      const formatted = make("strong", "", formatMetric(record, evaluation, value));
      const numeric = Number(value.value);
      if (Number.isFinite(numeric)) {
        if (value.metric_key === "return.total_rate" && numeric > 0) {
          formatted.classList.add("positive");
        } else if (
          value.metric_key === "risk.max_drawdown_rate" && numeric < 0
        ) {
          formatted.classList.add("negative");
        }
      }
      row.append(label, formatted);
      elements.runKeyMetrics.append(row);
    });
  }

  function keyValueList(values) {
    const list = make("dl", "key-value-list");
    Object.entries(values || {}).forEach(([key, value]) => {
      list.append(
        make("dt", "", key),
        make("dd", "", simpleValue(value)),
      );
    });
    return list;
  }

  function renderConfiguration(detail) {
    elements.runConfiguration.replaceChildren();
    ["strategy", "market", "execution", "account"].forEach((name) => {
      const component = detail.run_spec?.[name];
      if (!component) return;
      const block = make("details", "configuration-block");
      if (name === "strategy" || name === "market") block.open = true;
      const summary = make(
        "summary",
        "",
        `${humanize(name)} · ${component.key} · ${component.type}`,
      );
      block.append(summary, keyValueList(component.parameters));
      elements.runConfiguration.append(block);
    });
    if (Object.keys(detail.parameter_values || {}).length) {
      const block = make("details", "configuration-block");
      block.open = true;
      block.append(
        make("summary", "", "本次参数轴取值"),
        keyValueList(detail.parameter_values),
      );
      elements.runConfiguration.prepend(block);
    }
  }

  function renderAllMetrics(record, detail) {
    elements.allMetrics.replaceChildren();
    (detail.metrics || []).forEach((evaluation) => {
      const block = make("details", "metric-set-block");
      const available = (evaluation.values || []).filter(
        (value) => value.status === "AVAILABLE",
      ).length;
      block.append(
        make(
          "summary",
          "",
          `${evaluation.metric_set_id}/${evaluation.metric_set_version} · ${available} available`,
        ),
      );
      (evaluation.values || []).forEach((value) => {
        const row = make("div", "metric-value-row");
        row.append(
          make("span", "", metricLabel(record, evaluation, value)),
          make("strong", "", formatMetric(record, evaluation, value)),
        );
        block.append(row);
      });
      elements.allMetrics.append(block);
    });
    if (!detail.metrics?.length) {
      elements.allMetrics.append(
        make("div", "empty-cell", "尚未计算指标"),
      );
    }
  }

  function renderRunHero(detail) {
    const strategy = detail.run_spec?.strategy || {};
    const content = make("div");
    content.append(
      make("div", "eyebrow", "ONE STRATEGY · ONE CONFIG · ONE MARKET · ONE SEED"),
      make("h2", "", humanize(strategy.type || strategy.key)),
      make(
        "p",
        "",
        `${detail.run_spec?.market?.key || "—"} · Seed ${detail.seed} · Run ${detail.run_id}`,
      ),
    );
    const components = make("div", "component-line");
    Object.entries(detail.components || {}).forEach(([name, value]) =>
      components.append(make("span", "component-chip", `${name}: ${value}`)),
    );
    content.append(components);
    const stats = make("div", "hero-stats");
    stats.append(
      heroStat("状态", detail.status),
      heroStat("Seed", detail.seed),
      heroStat("Trace", detail.trace_state || "NONE"),
    );
    elements.runDetailHero.replaceChildren(content, stats);
  }

  function chartPercent(value) {
    const percent = Number(value) * 100;
    const digits = Math.abs(percent) >= 10 ? 1 : 2;
    return `${percent.toFixed(digits)}%`;
  }

  function chartDate(value) {
    const date = new Date(`${value}T00:00:00Z`);
    if (Number.isNaN(date.getTime())) return String(value || "");
    return date.toLocaleDateString("zh-CN", {
      year: "2-digit",
      month: "2-digit",
      timeZone: "UTC",
    });
  }

  function performanceSeries(asset, field) {
    return (state.runPerformance?.points || [])
      .map((point) => ({
        date: point.date,
        timestamp: Number(point.timestamp),
        value: Number(point.assets?.[asset]?.[field]),
      }))
      .filter((point) => Number.isFinite(point.value));
  }

  function liquidationDistanceSeries() {
    return (state.runPerformance?.points || [])
      .map((point) => {
        const rawValue = point.margin?.minimum_liquidation_distance_rate;
        return {
          date: point.date,
          timestamp: Number(point.timestamp),
          value: rawValue === null || rawValue === undefined
            ? Number.NaN
            : Number(rawValue),
        };
      })
      .filter((point) => Number.isFinite(point.value));
  }

  function marginPriceSeries(field) {
    return (state.runPerformance?.points || [])
      .map((point) => {
        const rawValue = point.margin?.[field];
        return {
          date: point.date,
          timestamp: Number(point.timestamp),
          value: rawValue === null || rawValue === undefined
            ? Number.NaN
            : Number(rawValue),
          liquidationTriggered: Boolean(point.margin?.liquidation_triggered),
        };
      })
      .filter((point) => Number.isFinite(point.value));
  }

  function clearChart(canvas) {
    if (!canvas) return;
    const context = canvas.getContext("2d");
    context.clearRect(0, 0, canvas.width, canvas.height);
  }

  function drawChartMessage(canvas, message) {
    if (!canvas) return;
    const width = Math.max(canvas.clientWidth || 620, 320);
    const height = Math.max(canvas.clientHeight || 224, 180);
    const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(width * pixelRatio);
    canvas.height = Math.round(height * pixelRatio);
    const context = canvas.getContext("2d");
    context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
    context.clearRect(0, 0, width, height);
    context.fillStyle = "#6f7785";
    context.font = "10px ui-monospace, SFMono-Regular, Menlo, monospace";
    context.textAlign = "center";
    context.textBaseline = "middle";
    context.fillText(message, width / 2, height / 2);
  }

  function drawPerformanceChart(
    canvas,
    series,
    {color, fill, liquidationDistance = false, timeline = []},
  ) {
    if (!canvas || !series.length) {
      clearChart(canvas);
      return;
    }
    const width = Math.max(canvas.clientWidth || 620, 320);
    const height = Math.max(
      canvas.clientHeight || 0,
      liquidationDistance ? 224 : 286,
    );
    const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(width * pixelRatio);
    canvas.height = Math.round(height * pixelRatio);
    const context = canvas.getContext("2d");
    context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
    context.clearRect(0, 0, width, height);

    const margin = {top: 14, right: 13, bottom: 28, left: 54};
    const chartWidth = width - margin.left - margin.right;
    const chartHeight = height - margin.top - margin.bottom;
    const values = series.map((point) => point.value);
    const rawMinimum = Math.min(0, ...values);
    const rawMaximum = Math.max(0, ...values);
    let minimum;
    let maximum;
    if (liquidationDistance) {
      const span = Math.max(rawMaximum - rawMinimum, 0.01);
      minimum = rawMinimum < 0 ? rawMinimum - span * 0.08 : 0;
      maximum = Math.max(0.01, rawMaximum + span * 0.08);
    } else {
      const span = Math.max(rawMaximum - rawMinimum, 0.02);
      minimum = rawMinimum - span * 0.08;
      maximum = rawMaximum + span * 0.08;
    }
    const range = Math.max(maximum - minimum, 0.000001);
    const domainSeries = timeline.length ? timeline : series;
    const domainTimestamps = domainSeries
      .map((point) => Number(point.timestamp))
      .filter(Number.isFinite);
    const firstTimestamp = Math.min(...domainTimestamps);
    const lastTimestamp = Math.max(...domainTimestamps);
    const timestampRange = Math.max(lastTimestamp - firstTimestamp, 1);
    const x = (point) =>
      margin.left
      + ((point.timestamp - firstTimestamp) / timestampRange) * chartWidth;
    const y = (value) => margin.top + ((maximum - value) / range) * chartHeight;

    context.font = "9px ui-monospace, SFMono-Regular, Menlo, monospace";
    context.textBaseline = "middle";
    context.textAlign = "right";
    for (let index = 0; index <= 4; index += 1) {
      const value = maximum - (range * index) / 4;
      const position = margin.top + (chartHeight * index) / 4;
      context.strokeStyle = "#262c38";
      context.lineWidth = 1;
      context.beginPath();
      context.moveTo(margin.left, Math.round(position) + 0.5);
      context.lineTo(width - margin.right, Math.round(position) + 0.5);
      context.stroke();
      context.fillStyle = "#68707e";
      context.fillText(chartPercent(value), margin.left - 8, position);
    }

    const zero = y(0);
    context.strokeStyle = liquidationDistance ? "#ef5350" : "#475164";
    context.setLineDash([4, 4]);
    context.beginPath();
    context.moveTo(margin.left, zero);
    context.lineTo(width - margin.right, zero);
    context.stroke();
    context.setLineDash([]);

    if (liquidationDistance) {
      context.fillStyle = "#ef7775";
      context.textAlign = "left";
      context.fillText("强平 0%", margin.left + 6, zero - 9);
    }

    const gradient = context.createLinearGradient(0, margin.top, 0, margin.top + chartHeight);
    gradient.addColorStop(0, fill);
    gradient.addColorStop(1, "rgb(15 18 26 / 0%)");
    context.beginPath();
    series.forEach((point, index) => {
      const pointX = x(point);
      const pointY = y(point.value);
      if (index === 0) context.moveTo(pointX, pointY);
      else context.lineTo(pointX, pointY);
    });
    context.lineTo(x(series.at(-1)), zero);
    context.lineTo(x(series[0]), zero);
    context.closePath();
    context.fillStyle = gradient;
    context.fill();

    context.beginPath();
    series.forEach((point, index) => {
      const pointX = x(point);
      const pointY = y(point.value);
      if (index === 0) context.moveTo(pointX, pointY);
      else context.lineTo(pointX, pointY);
    });
    context.strokeStyle = color;
    context.lineWidth = 1.6;
    context.lineJoin = "round";
    context.stroke();

    context.textAlign = "center";
    context.textBaseline = "alphabetic";
    context.fillStyle = "#68707e";
    const tickCount = Math.min(5, domainSeries.length);
    const used = new Set();
    for (let index = 0; index < tickCount; index += 1) {
      const pointIndex = tickCount === 1
        ? 0
        : Math.round((index * (domainSeries.length - 1)) / (tickCount - 1));
      if (used.has(pointIndex)) continue;
      used.add(pointIndex);
      context.fillText(
        chartDate(domainSeries[pointIndex].date),
        x(domainSeries[pointIndex]),
        height - 7,
      );
    }
  }

  function chartPrice(value) {
    return new Intl.NumberFormat("en-US", {
      maximumFractionDigits: Math.abs(value) < 100 ? 2 : 0,
    }).format(value);
  }

  function drawPriceReturnChart(
    canvas,
    marketSeries,
    liquidationSeries,
    returnSeries,
  ) {
    const allSeries = [marketSeries, liquidationSeries, returnSeries];
    const allPoints = allSeries.flat();
    if (!canvas || !allPoints.length) {
      clearChart(canvas);
      return;
    }
    const width = Math.max(canvas.clientWidth || 620, 320);
    const height = Math.max(canvas.clientHeight || 0, 286);
    const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(width * pixelRatio);
    canvas.height = Math.round(height * pixelRatio);
    const context = canvas.getContext("2d");
    context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
    context.clearRect(0, 0, width, height);

    const margin = {top: 38, right: 58, bottom: 28, left: 68};
    const chartWidth = width - margin.left - margin.right;
    const chartHeight = height - margin.top - margin.bottom;
    const timestamps = allPoints
      .map((point) => point.timestamp)
      .filter(Number.isFinite);
    const firstTimestamp = Math.min(...timestamps);
    const lastTimestamp = Math.max(...timestamps);
    const timestampRange = Math.max(lastTimestamp - firstTimestamp, 1);
    const x = (point) =>
      margin.left
      + ((point.timestamp - firstTimestamp) / timestampRange) * chartWidth;

    const priceValues = [...marketSeries, ...liquidationSeries]
      .map((point) => point.value)
      .filter(Number.isFinite);
    const hasPrice = priceValues.length > 0;
    const rawPriceMinimum = hasPrice ? Math.min(...priceValues) : 0;
    const rawPriceMaximum = hasPrice ? Math.max(...priceValues) : 1;
    const priceSpan = Math.max(rawPriceMaximum - rawPriceMinimum, rawPriceMaximum * 0.02, 1);
    const priceMinimum = rawPriceMinimum - priceSpan * 0.08;
    const priceMaximum = rawPriceMaximum + priceSpan * 0.08;
    const priceRange = Math.max(priceMaximum - priceMinimum, 1);
    const yPrice = (value) =>
      margin.top + ((priceMaximum - value) / priceRange) * chartHeight;

    const returnValues = returnSeries.map((point) => point.value);
    const rawReturnMinimum = Math.min(0, ...returnValues);
    const rawReturnMaximum = Math.max(0, ...returnValues);
    const returnSpan = Math.max(rawReturnMaximum - rawReturnMinimum, 0.02);
    const returnMinimum = rawReturnMinimum - returnSpan * 0.08;
    const returnMaximum = rawReturnMaximum + returnSpan * 0.08;
    const returnRange = Math.max(returnMaximum - returnMinimum, 0.000001);
    const yReturn = (value) =>
      margin.top + ((returnMaximum - value) / returnRange) * chartHeight;

    context.font = "9px ui-monospace, SFMono-Regular, Menlo, monospace";
    context.textBaseline = "middle";
    for (let index = 0; index <= 4; index += 1) {
      const position = margin.top + (chartHeight * index) / 4;
      context.strokeStyle = "#262c38";
      context.lineWidth = 1;
      context.beginPath();
      context.moveTo(margin.left, Math.round(position) + 0.5);
      context.lineTo(width - margin.right, Math.round(position) + 0.5);
      context.stroke();

      if (hasPrice) {
        const priceValue = priceMaximum - (priceRange * index) / 4;
        context.fillStyle = "#68707e";
        context.textAlign = "right";
        context.fillText(chartPrice(priceValue), margin.left - 8, position);
      }
      const returnValue = returnMaximum - (returnRange * index) / 4;
      context.fillStyle = "#68707e";
      context.textAlign = "left";
      context.fillText(chartPercent(returnValue), width - margin.right + 8, position);
    }

    const returnZero = yReturn(0);
    context.strokeStyle = "#475164";
    context.setLineDash([4, 4]);
    context.beginPath();
    context.moveTo(margin.left, returnZero);
    context.lineTo(width - margin.right, returnZero);
    context.stroke();
    context.setLineDash([]);

    const drawLine = (series, y, color, lineDash = [], lineWidth = 1.6) => {
      if (!series.length) return;
      context.beginPath();
      series.forEach((point, index) => {
        if (index === 0) context.moveTo(x(point), y(point.value));
        else context.lineTo(x(point), y(point.value));
      });
      context.strokeStyle = color;
      context.lineWidth = lineWidth;
      context.lineJoin = "round";
      context.setLineDash(lineDash);
      context.stroke();
      context.setLineDash([]);
    };
    drawLine(marketSeries, yPrice, "#4f7dff", [], 1.7);
    drawLine(liquidationSeries, yPrice, "#ef5350", [6, 4], 1.5);
    drawLine(returnSeries, yReturn, "#26a69a", [], 1.8);

    marketSeries
      .filter((point) => point.liquidationTriggered)
      .forEach((point) => {
        context.beginPath();
        context.arc(x(point), yPrice(point.value), 4, 0, Math.PI * 2);
        context.fillStyle = "#ef5350";
        context.fill();
        context.strokeStyle = "#f8b4b2";
        context.lineWidth = 1;
        context.stroke();
      });

    const legend = [
      ["市场价格", "#4f7dff", false],
      ["预计强平价", "#ef5350", true],
      [`${state.runPerformanceAsset || "权益"} 累计收益`, "#26a69a", false],
    ];
    let legendX = margin.left;
    legend.forEach(([label, color, dashed]) => {
      context.strokeStyle = color;
      context.lineWidth = 2;
      context.setLineDash(dashed ? [5, 3] : []);
      context.beginPath();
      context.moveTo(legendX, 17);
      context.lineTo(legendX + 18, 17);
      context.stroke();
      context.setLineDash([]);
      context.fillStyle = "#a8afbd";
      context.textAlign = "left";
      context.fillText(label, legendX + 24, 17);
      legendX += 32 + context.measureText(label).width + 24;
    });

    const dateSeries = [...allPoints].sort(
      (left, right) => left.timestamp - right.timestamp,
    );
    context.textAlign = "center";
    context.textBaseline = "alphabetic";
    context.fillStyle = "#68707e";
    const tickCount = Math.min(5, dateSeries.length);
    const used = new Set();
    for (let index = 0; index < tickCount; index += 1) {
      const pointIndex = tickCount === 1
        ? 0
        : Math.round((index * (dateSeries.length - 1)) / (tickCount - 1));
      const point = dateSeries[pointIndex];
      if (used.has(point.timestamp)) continue;
      used.add(point.timestamp);
      context.fillText(chartDate(point.date), x(point), height - 7);
    }
  }

  function renderPerformanceAssetSwitch() {
    elements.runPerformanceAssetSwitch.replaceChildren();
    (state.runPerformance?.assets || []).forEach((asset) => {
      const button = make("button", "run-asset-button", asset);
      button.type = "button";
      button.classList.toggle("active", asset === state.runPerformanceAsset);
      button.addEventListener("click", () => {
        state.runPerformanceAsset = asset;
        renderPerformanceCharts();
      });
      elements.runPerformanceAssetSwitch.append(button);
    });
  }

  function renderPerformanceCharts() {
    const performance = state.runPerformance;
    if (!performance) {
      elements.runPerformanceAssetSwitch.replaceChildren();
      elements.runPerformanceEmpty.hidden = !state.runPerformanceError;
      elements.runPerformanceEmpty.textContent = state.runPerformanceError || "";
      elements.runReturnChartSummary.textContent = "选择一个带 Trace 的 Run。";
      elements.runMarginRiskChartSummary.textContent =
        "当前价格距离预计强平价的百分比；0% 触发强平。";
      clearChart(elements.runReturnChart);
      clearChart(elements.runMarginRiskChart);
      return;
    }
    elements.runPerformanceEmpty.hidden = true;
    const assets = performance.assets || [];
    if (!assets.includes(state.runPerformanceAsset)) {
      state.runPerformanceAsset = performance.default_asset || assets[0] || null;
    }
    renderPerformanceAssetSwitch();
    const asset = state.runPerformanceAsset;
    const returns = performanceSeries(asset, "return_rate");
    const marketPrices = marginPriceSeries("mark_price");
    const liquidationPrices = marginPriceSeries("estimated_liquidation_price");
    const liquidationDistance = liquidationDistanceSeries();
    const statistics = performance.statistics?.[asset] || {};
    const marginStatistics = performance.margin_statistics;
    const latestMargin = [...(performance.points || [])]
      .reverse()
      .find((point) => point.margin)?.margin;
    const latestMarketPrice = latestMargin?.mark_price == null
      ? "—"
      : formatNumber(latestMargin.mark_price, 2);
    const latestLiquidationPrice = latestMargin?.estimated_liquidation_price == null
      ? "—"
      : formatNumber(latestMargin.estimated_liquidation_price, 2);
    elements.runReturnChartSummary.textContent =
      `${asset || "—"} · 期末收益 ${formatRatio(statistics.total_return_rate)}`
      + ` · 末期价格 ${latestMarketPrice}`
      + ` · 末期预计强平价 ${latestLiquidationPrice}`;
    drawPriceReturnChart(
      elements.runReturnChart,
      marketPrices,
      liquidationPrices,
      returns,
    );
    if (marginStatistics && liquidationDistance.length) {
      elements.runMarginRiskChartSummary.textContent =
        `全时点最小安全距离 ${formatRatio(marginStatistics.minimum_liquidation_distance_rate)}`
        + " · 0% 触发强平"
        + ` · 结算资产 ${marginStatistics.settlement_asset || "—"}`;
      drawPerformanceChart(elements.runMarginRiskChart, liquidationDistance, {
        color: "#f0a33e",
        fill: "rgb(240 163 62 / 31%)",
        liquidationDistance: true,
        timeline: performance.points || [],
      });
    } else {
      elements.runMarginRiskChartSummary.textContent =
        "当前 Run 没有可计算的持仓强平距离。";
      drawChartMessage(elements.runMarginRiskChart, "无持仓强平距离");
    }
  }

  async function loadSelectedRun() {
    const record = recordById(
      state.detailExperimentId,
      state.detailDatabaseName,
    );
    if (!record || !state.detailRunId) return;
    state.runPerformance = null;
    state.runPerformanceAsset = null;
    state.runPerformanceError = "正在读取收益与保证金风险序列…";
    renderPerformanceCharts();
    state.runDetail = await request(
      experimentApiPath(
        record.experiment,
        ["runs", state.detailRunId],
      ),
    );
    renderRunHero(state.runDetail);
    renderKeyMetrics(record, state.runDetail);
    renderConfiguration(state.runDetail);
    renderAllMetrics(record, state.runDetail);
    elements.detailPlayback.disabled = !(
      state.runDetail.status === "SUCCEEDED" &&
      state.runDetail.trace_state === "STORED"
    );
    if (!elements.detailPlayback.disabled) {
      try {
        state.runPerformance = await request(
          experimentApiPath(
            record.experiment,
            ["runs", state.detailRunId, "performance"],
          ),
        );
        state.runPerformanceError = null;
      } catch (error) {
        state.runPerformanceError = `收益与保证金风险序列载入失败：${error.message}`;
      }
    } else {
      state.runPerformanceError = "当前 Run 没有可读取的 Trace，无法绘制收益与保证金风险。";
    }
    renderPerformanceCharts();
  }

  function playbackUrls() {
    if (!state.detailExperimentId || !state.detailRunId) return null;
    const record = recordById(
      state.detailExperimentId,
      state.detailDatabaseName,
    );
    if (!record) return null;
    const runApi = experimentApiPath(
      record.experiment,
      ["runs", state.detailRunId, "viewer"],
    );
    return {
      embedded: `./index.html?embedded=1&run_api=${encodeURIComponent(runApi)}`,
      standalone: `./index.html?run_api=${encodeURIComponent(runApi)}`,
    };
  }

  function openPlayback() {
    const urls = playbackUrls();
    if (!urls || state.runDetail?.trace_state !== "STORED") {
      showMessage("当前 Run 没有可读取的 Trace", true);
      return;
    }
    state.playbackUrl = urls;
    elements.playbackEmpty.hidden = true;
    elements.playbackFrame.hidden = false;
    elements.playbackFrame.src = urls.embedded;
    elements.playbackContext.textContent =
      `${state.detailExperimentId} · Seed ${state.runDetail.seed} · ${state.detailRunId}`;
    setPage("playback");
  }

  async function loadCatalog() {
    elements.catalogStatus.textContent = "正在载入研究目录…";
    try {
      const [catalog, components, pathSets] = await Promise.all([
        request("/api/experiments"),
        request("/api/components"),
        request("/api/market-path-sets"),
      ]);
      const rawRecords = await Promise.all(
        catalog.items.map(async (experiment) => {
          const [detail, runs, metrics] = await Promise.all([
            request(
              experimentApiPath(experiment),
            ),
            request(
              experimentApiPath(experiment, ["runs"], {limit: "10000"}),
            ),
            request(
              experimentApiPath(experiment, ["metrics"]),
            ),
          ]);
          return {
            experiment,
            detail,
            runs: runs.items,
            metrics,
          };
        }),
      );
      const research = Model.buildCatalog(rawRecords, components.items || []);
      state.experiments = catalog.items;
      state.records = research.records;
      state.strategies = research.strategies;
      state.strategyDefinitions = research.strategyDefinitions;
      state.rules = research.rules;
      state.pathSets = pathSets.items || [];
      state.markets = [
        ...Model.pathSetMarkets(state.pathSets),
        ...research.markets,
      ];
      state.strategyId = state.strategyDefinitions.some(
        (item) => item.id === state.strategyId,
      ) ? state.strategyId : state.strategyDefinitions[0]?.id;
      state.ruleId = state.rules.some(
        (item) => item.id === state.ruleId,
      ) ? state.ruleId : state.rules[0]?.id;
      state.marketId = state.markets.some((item) => item.id === state.marketId)
        ? state.marketId
        : state.markets[0]?.id;
      const selectedRecord = recordById(
        state.detailExperimentId,
        state.detailDatabaseName,
      ) || state.records[0];
      state.detailExperimentId = selectedRecord?.experiment.experiment_id;
      state.detailDatabaseName = selectedRecord?.experiment.database_name;

      renderStrategyOverview();
      renderStrategyDetail();
      renderStrategyRuns();
      renderRuleOverview();
      renderRuleDetail();
      renderMarketList();
      if (state.marketId) await selectMarket(state.marketId);
      renderExperimentOverview();
      renderDetailSelectors();
      if (state.detailRunId) await loadSelectedRun();

      const runCount = state.records.reduce(
        (sum, record) => sum + record.runs.length,
        0,
      );
      elements.catalogStatus.textContent =
        `${state.strategyDefinitions.length} Strategy / ${state.rules.length} Rule · ${state.pathSets.length} PathSet / ${state.markets.length} 市场 · ${state.experiments.length} 实验 · ${runCount} Runs`;
      elements.updatedAt.textContent = `更新于 ${new Date().toLocaleTimeString("zh-CN")}`;
    } catch (error) {
      elements.catalogStatus.textContent = "研究目录载入失败";
      showMessage(`研究目录载入失败：${error.message}`, true);
    }
  }

  elements.navigation.forEach((item) =>
    item.addEventListener("click", () => setPage(item.dataset.page)),
  );
  elements.refresh.addEventListener("click", loadCatalog);
  elements.strategySelect.addEventListener("change", () => {
    state.strategyId = elements.strategySelect.value;
    renderStrategyDetail();
    renderStrategyRuns();
  });
  elements.strategyRunSelect.addEventListener("change", () => {
    state.strategyId = elements.strategyRunSelect.value;
    renderStrategyDetail();
    renderStrategyRuns();
  });
  elements.ruleSelect.addEventListener("change", () => {
    state.ruleId = elements.ruleSelect.value;
    renderRuleDetail();
  });
  elements.marketPathSelect.addEventListener("change", () => {
    loadMarketPath().catch((error) =>
      showMessage(`价格路径载入失败：${error.message}`, true),
    );
  });
  elements.marketRoleSelect.addEventListener("change", () => {
    state.marketRole = elements.marketRoleSelect.value;
    renderMarketPathOptions(selectedMarket());
    loadMarketPath().catch((error) =>
      showMessage(`价格路径载入失败：${error.message}`, true),
    );
  });
  elements.marketIntervalSwitch.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-interval]");
    if (!button) return;
    state.marketInterval = button.dataset.interval;
    elements.marketIntervalSwitch.querySelectorAll("button").forEach((item) =>
      item.classList.toggle("active", item === button),
    );
    const path = selectedMarketPath();
    if (path?.source === "PATH_SET" && path.availability !== "LOCKED") {
      loadMarketPath().catch((error) =>
        showMessage(`价格路径载入失败：${error.message}`, true),
      );
    } else {
      renderMarketChart();
    }
  });
  elements.detailExperimentSelect.addEventListener("change", () => {
    const record = state.records.find(
      (item) =>
        item.experiment.database_name === elements.detailExperimentSelect.value,
    );
    state.detailExperimentId = record?.experiment.experiment_id || null;
    state.detailDatabaseName = record?.experiment.database_name || null;
    state.detailScenarioId = null;
    state.detailRunId = null;
    renderDetailSelectors();
    loadSelectedRun().catch((error) =>
      showMessage(`Run 载入失败：${error.message}`, true),
    );
  });
  elements.detailScenarioSelect.addEventListener("change", () => {
    state.detailScenarioId = elements.detailScenarioSelect.value;
    state.detailRunId = null;
    renderDetailSelectors();
    loadSelectedRun().catch((error) =>
      showMessage(`Run 载入失败：${error.message}`, true),
    );
  });
  elements.detailSeedSelect.addEventListener("change", () => {
    state.detailRunId = elements.detailSeedSelect.value;
    loadSelectedRun().catch((error) =>
      showMessage(`Run 载入失败：${error.message}`, true),
    );
  });
  elements.detailPlayback.addEventListener("click", openPlayback);
  elements.playerWindow.addEventListener("click", () => {
    if (state.playbackUrl) window.open(state.playbackUrl.standalone, "_blank");
  });
  let runChartResizeFrame = null;
  window.addEventListener("resize", () => {
    if (state.page !== "experiment-detail") return;
    if (runChartResizeFrame !== null) {
      window.cancelAnimationFrame(runChartResizeFrame);
    }
    runChartResizeFrame = window.requestAnimationFrame(() => {
      runChartResizeFrame = null;
      renderPerformanceCharts();
    });
  });

  const requestedParams = new URLSearchParams(window.location.search);
  const requestedPage = requestedParams.get("page");
  state.detailExperimentId = requestedParams.get("experiment");
  state.detailDatabaseName = requestedParams.get("database");
  if (requestedPage === "experiment-overview") {
    state.overviewExperimentId = requestedParams.get("experiment");
    state.overviewDatabaseName = requestedParams.get("database");
  }
  setPage(PAGE_META[requestedPage] ? requestedPage : "strategy-overview", {
    updateUrl: false,
  });
  loadCatalog();
})();
