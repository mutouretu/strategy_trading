(() => {
  "use strict";

  const Model = window.ExperimentResearchModel;
  const PAGE_META = {
    "strategy-overview": ["STRATEGIES", "策略总览"],
    "strategy-detail": ["STRATEGY DETAIL", "策略详情"],
    "market-overview": ["MARKET ENVIRONMENTS", "市场环境"],
    "experiment-overview": ["EXPERIMENTS", "实验总览"],
    "experiment-detail": ["RUN DETAIL", "实验详情"],
    playback: ["PLAYBACK", "K 线播放"],
  };

  const state = {
    experiments: [],
    records: [],
    strategies: [],
    markets: [],
    page: "strategy-overview",
    strategyId: null,
    marketId: null,
    marketInterval: "1w",
    marketDocument: null,
    detailExperimentId: null,
    detailScenarioId: null,
    detailRunId: null,
    runDetail: null,
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
    strategyFlow: byId("strategy-flow"),
    strategyNotes: byId("strategy-research-notes"),
    strategyConfigBody: byId("strategy-config-body"),
    marketList: byId("market-list"),
    marketIntervalSwitch: byId("market-interval-switch"),
    marketTitle: byId("market-chart-title"),
    marketDescription: byId("market-chart-description"),
    marketPathSelect: byId("market-path-select"),
    marketChips: byId("market-parameter-chips"),
    marketChart: byId("market-chart"),
    marketChartEmpty: byId("market-chart-empty"),
    experimentGroups: byId("experiment-groups"),
    detailExperimentSelect: byId("detail-experiment-select"),
    detailScenarioSelect: byId("detail-scenario-select"),
    detailSeedSelect: byId("detail-seed-select"),
    runDetailHero: byId("run-detail-hero"),
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
    return strategy?.descriptor?.display_name || humanize(strategy?.id);
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

  function replaceOptions(select, items, selected) {
    select.replaceChildren(...items);
    if (selected && [...select.options].some((item) => item.value === selected)) {
      select.value = selected;
    }
  }

  function recordById(experimentId) {
    return state.records.find(
      (record) => record.experiment.experiment_id === experimentId,
    );
  }

  function selectedStrategy() {
    return state.strategies.find((item) => item.id === state.strategyId);
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
  }

  function renderStrategyOverview() {
    elements.strategyFacts.replaceChildren(
      make("span", "mini-fact", `${state.strategies.length} 个策略`),
      make(
        "span",
        "mini-fact",
        `${state.strategies.reduce((sum, item) => sum + item.configurations.length, 0)} 个配置`,
      ),
      make("span", "mini-fact", `${state.experiments.length} 个实验`),
    );
    elements.strategyBody.replaceChildren();
    if (!state.strategies.length) {
      emptyRow(elements.strategyBody, 7, "结果目录中还没有策略研究记录");
      return;
    }
    state.strategies.forEach((strategy) => {
      const row = document.createElement("tr");
      row.append(primaryCell(strategyName(strategy), strategy.type));
      appendCell(row, strategy.configurations.length);
      appendCell(row, strategy.experiments.length);
      appendCell(row, strategy.markets.length);
      const statusCell = document.createElement("td");
      const status = make("span", "status-inline");
      status.append(
        make("i", "status-dot succeeded"),
        document.createTextNode(statusText(strategy.status_counts)),
      );
      statusCell.append(status);
      row.append(statusCell);
      const latest = strategy.runs
        .map(({record}) => record.experiment.updated_at)
        .sort()
        .at(-1);
      appendCell(row, dateText(latest));
      const actionCell = document.createElement("td");
      const action = make("button", "text-button", "查看策略 →");
      action.type = "button";
      action.addEventListener("click", () => {
        state.strategyId = strategy.id;
        renderStrategyDetail();
        setPage("strategy-detail");
      });
      actionCell.append(action);
      row.append(actionCell);
      elements.strategyBody.append(row);
    });
  }

  function heroStat(label, value) {
    const card = make("div", "hero-stat");
    card.append(make("span", "", label), make("strong", "", value));
    return card;
  }

  function renderStrategyDetail() {
    const strategy = selectedStrategy() || state.strategies[0];
    if (!strategy) return;
    state.strategyId = strategy.id;
    replaceOptions(
      elements.strategySelect,
      state.strategies.map((item) => option(item.id, strategyName(item))),
      strategy.id,
    );

    const summary = strategy.descriptor?.description
      || strategy.descriptions[0]
      || "该策略尚未录入独立说明；当前页面根据已有实验整理配置、市场与运行记录。";
    const content = make("div");
    content.append(
      make("div", "eyebrow", "STRATEGY"),
      make("h2", "", strategyName(strategy)),
      make("div", "hero-type", strategy.type),
      make("p", "", summary),
    );
    const stats = make("div", "hero-stats");
    stats.append(
      heroStat("配置", strategy.configurations.length),
      heroStat("实验", strategy.experiments.length),
      heroStat("Runs", strategy.runs.length),
    );
    elements.strategyHero.replaceChildren(content, stats);

    const flow = strategy.descriptor?.flow || [
      {title: "市场数据", detail: "当前 K 线与历史状态"},
      {title: "策略判断", detail: "参数与内部状态"},
      {title: "交易意图", detail: "方向、数量与触发条件"},
      {title: "执行与账户", detail: "成交、记账与指标"},
    ];
    elements.strategyFlow.replaceChildren();
    flow.forEach((step, index) => {
      const node = make("div");
      node.append(
        make("span", "", String(index + 1).padStart(2, "0")),
        make("strong", "", step.title),
        make("small", "", step.detail),
      );
      elements.strategyFlow.append(node);
      if (index < flow.length - 1) {
        elements.strategyFlow.append(make("i", "", "→"));
      }
    });

    elements.strategyNotes.replaceChildren();
    (strategy.descriptions.length
      ? strategy.descriptions
      : ["暂无实验说明。后续可由策略应用提供公式、约束与适用市场说明。"]
    ).forEach((note) =>
      elements.strategyNotes.append(make("div", "note-item", note)),
    );

    elements.strategyConfigBody.replaceChildren();
    strategy.configurations.forEach((configuration) => {
      const row = document.createElement("tr");
      row.append(primaryCell(configuration.key, shortId(configuration.id, 20)));
      const parameterCell = document.createElement("td");
      parameterCell.append(parameterSummary(configuration.parameters, 12));
      row.append(parameterCell);
      appendCell(row, configuration.run_count);
      elements.strategyConfigBody.append(row);
    });
  }

  function marketDescription(market) {
    const instrument = market.parameters?.instrument || "未指定标的";
    const interval = market.parameters?.interval || "原始周期";
    const anchors = Array.isArray(market.parameters?.anchors)
      ? `${market.parameters.anchors.length} 个趋势节点`
      : "无趋势节点";
    return `${instrument} · ${interval} · ${anchors} · ${market.paths.length} 条 Seed 路径`;
  }

  function renderMarketList() {
    elements.marketList.replaceChildren();
    state.markets.forEach((market) => {
      const button = make("button", "selection-item");
      button.type = "button";
      button.classList.toggle("active", market.id === state.marketId);
      button.append(
        make("strong", "", market.key),
        make("span", "", market.type),
        make("span", "", marketDescription(market)),
      );
      button.addEventListener("click", () => selectMarket(market.id));
      elements.marketList.append(button);
    });
    if (!state.markets.length) {
      elements.marketList.append(
        make("div", "empty-cell", "结果目录中还没有市场环境"),
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
  }

  async function selectMarket(marketId, {load = true} = {}) {
    state.marketId = marketId;
    state.marketDocument = null;
    const market = selectedMarket();
    renderMarketList();
    if (!market) return;
    elements.marketTitle.textContent = market.key;
    elements.marketDescription.textContent = `${market.type} · ${marketDescription(market)}`;
    renderMarketParameters(market);
    replaceOptions(
      elements.marketPathSelect,
      market.paths.map((path) =>
        option(
          `${path.experiment_id}:${path.run_id}`,
          `Seed ${path.seed} · ${shortId(path.market_path_id)}`,
        ),
      ),
    );
    if (load && market.paths.length) await loadMarketPath();
  }

  async function loadMarketPath() {
    const market = selectedMarket();
    if (!market) return;
    const [experimentId, runId] = elements.marketPathSelect.value.split(":");
    const path = market.paths.find(
      (item) => item.experiment_id === experimentId && item.run_id === runId,
    );
    if (!path) return;
    elements.marketChartEmpty.hidden = false;
    elements.marketChartEmpty.textContent = "正在载入价格路径…";
    elements.marketChart.replaceChildren();
    try {
      state.marketDocument = await request(
        apiPath("experiments", experimentId, "runs", runId, "viewer"),
      );
      renderMarketChart();
    } catch (error) {
      elements.marketChartEmpty.textContent =
        path.trace_state === "STORED"
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
    const bars = Model.aggregateBars(
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
    elements.marketDescription.textContent = [
      manifest.instrument,
      `${state.marketDocument.market.length} 根日线`,
      `${bars.length} 根${state.marketInterval === "1w" ? "周线" : "月线"}`,
      `${state.marketDocument.market[0].date} — ${state.marketDocument.market.at(-1).date}`,
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
      "BTC 收益均值",
      "BTC 回撤均值",
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
        scenarioMetricText(
          scenario,
          "return.total_rate",
          {scope: "account.total_equity", valuation_asset: "BTC"},
          true,
        ),
      );
      appendCell(
        row,
        scenarioMetricText(
          scenario,
          "risk.max_drawdown_rate",
          {scope: "account.total_equity", valuation_asset: "BTC"},
          true,
        ),
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

  function renderExperimentOverview() {
    elements.experimentGroups.replaceChildren();
    state.strategies.forEach((strategy) => {
      const records = state.records.filter((record) =>
        record.runs.some(
          (run) => run.resolved_components.strategy.type === strategy.type,
        ),
      );
      const panel = make("section", "data-panel");
      const heading = make("div", "experiment-group-heading");
      heading.append(
        make("strong", "", strategyName(strategy)),
        make("span", "", `${records.length} 个实验 · ${strategy.runs.length} Runs`),
      );
      const wrapper = make("div", "table-wrap");
      const table = make("table", "research-table");
      const head = document.createElement("thead");
      const header = document.createElement("tr");
      ["实验", "说明", "场景", "Seeds", "Runs", "状态", "更新时间", ""].forEach(
        (label) => header.append(make("th", "", label)),
      );
      head.append(header);
      const body = document.createElement("tbody");
      records.forEach((record) => {
        const scenarios = Model.scenarioRows(record);
        const experiment = record.experiment;
        const row = make("tr", "experiment-row");
        row.append(
          primaryCell(experiment.experiment_id, experiment.database_name),
        );
        appendCell(row, experiment.description || "—");
        appendCell(row, scenarios.length);
        appendCell(
          row,
          [...new Set(record.runs.map((run) => run.seed))]
            .sort((a, b) => a - b)
            .join(", "),
        );
        appendCell(row, experiment.planned_run_count);
        appendCell(row, statusText(experiment.status_counts));
        appendCell(row, dateText(experiment.updated_at));
        const actionCell = document.createElement("td");
        const expand = make("button", "text-button", "展开配置");
        expand.type = "button";
        actionCell.append(expand);
        row.append(actionCell);
        const detailRow = make("tr", "scenario-row");
        detailRow.hidden = true;
        const detailCell = document.createElement("td");
        detailCell.colSpan = 8;
        detailCell.append(scenarioTable(record));
        detailRow.append(detailCell);
        expand.addEventListener("click", () => {
          detailRow.hidden = !detailRow.hidden;
          expand.textContent = detailRow.hidden ? "展开配置" : "收起配置";
        });
        body.append(row, detailRow);
      });
      table.append(head, body);
      wrapper.append(table);
      panel.append(heading, wrapper);
      elements.experimentGroups.append(panel);
    });
    if (!state.strategies.length) {
      elements.experimentGroups.append(
        make("div", "large-empty", "结果目录中还没有实验"),
      );
    }
  }

  function renderDetailSelectors() {
    const record = recordById(state.detailExperimentId) || state.records[0];
    if (!record) return;
    state.detailExperimentId = record.experiment.experiment_id;
    replaceOptions(
      elements.detailExperimentSelect,
      state.records.map((item) =>
        option(item.experiment.experiment_id, item.experiment.experiment_id),
      ),
      state.detailExperimentId,
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
      const card = make("div", "metric-card");
      card.append(
        make("span", "", metricLabel(record, evaluation, value)),
        make("strong", "", formatMetric(record, evaluation, value)),
        make(
          "small",
          "",
          `${evaluation.metric_set_id}/${evaluation.metric_set_version} · ${value.unit}`,
        ),
      );
      elements.runKeyMetrics.append(card);
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

  async function loadSelectedRun() {
    const record = recordById(state.detailExperimentId);
    if (!record || !state.detailRunId) return;
    state.runDetail = await request(
      apiPath(
        "experiments",
        state.detailExperimentId,
        "runs",
        state.detailRunId,
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
  }

  function playbackUrls() {
    if (!state.detailExperimentId || !state.detailRunId) return null;
    const runApi = apiPath(
      "experiments",
      state.detailExperimentId,
      "runs",
      state.detailRunId,
      "viewer",
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
      const [catalog, components] = await Promise.all([
        request("/api/experiments"),
        request("/api/components"),
      ]);
      const rawRecords = await Promise.all(
        catalog.items.map(async (experiment) => {
          const [detail, runs, metrics] = await Promise.all([
            request(apiPath("experiments", experiment.experiment_id)),
            request(
              `${apiPath("experiments", experiment.experiment_id, "runs")}?limit=10000`,
            ),
            request(apiPath("experiments", experiment.experiment_id, "metrics")),
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
      state.markets = research.markets;
      state.strategyId = state.strategies.some(
        (item) => item.id === state.strategyId,
      ) ? state.strategyId : state.strategies[0]?.id;
      state.marketId = state.markets.some((item) => item.id === state.marketId)
        ? state.marketId
        : state.markets[0]?.id;
      state.detailExperimentId = recordById(state.detailExperimentId)
        ? state.detailExperimentId
        : state.records[0]?.experiment.experiment_id;

      renderStrategyOverview();
      renderStrategyDetail();
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
        `${state.strategies.length} 策略 · ${state.markets.length} 市场 · ${state.experiments.length} 实验 · ${runCount} Runs`;
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
  });
  elements.marketPathSelect.addEventListener("change", () => {
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
    renderMarketChart();
  });
  elements.detailExperimentSelect.addEventListener("change", () => {
    state.detailExperimentId = elements.detailExperimentSelect.value;
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

  const requestedPage = new URLSearchParams(window.location.search).get("page");
  setPage(PAGE_META[requestedPage] ? requestedPage : "strategy-overview", {
    updateUrl: false,
  });
  loadCatalog();
})();
