(() => {
  "use strict";

  const state = {
    experiments: [],
    selectedExperiment: null,
    detail: null,
    runs: [],
    summaryFields: [],
  };

  const elements = {
    experimentCount: document.getElementById("experiment-count"),
    experimentList: document.getElementById("experiment-list"),
    refresh: document.getElementById("refresh-experiments"),
    empty: document.getElementById("empty-state"),
    view: document.getElementById("experiment-view"),
    title: document.getElementById("experiment-title"),
    description: document.getElementById("experiment-description"),
    status: document.getElementById("experiment-status"),
    facts: document.getElementById("experiment-facts"),
    spec: document.getElementById("experiment-spec"),
    revisions: document.getElementById("code-revisions"),
    downloadComparison: document.getElementById("download-comparison"),
    runResultCount: document.getElementById("run-result-count"),
    filters: document.getElementById("run-filters"),
    search: document.getElementById("filter-search"),
    filterStatus: document.getElementById("filter-status"),
    seed: document.getElementById("filter-seed"),
    sort: document.getElementById("filter-sort"),
    order: document.getElementById("filter-order"),
    summaryFields: document.getElementById("summary-fields"),
    runsHead: document.getElementById("runs-head"),
    runsBody: document.getElementById("runs-body"),
    dialog: document.getElementById("run-dialog"),
    dialogRunId: document.getElementById("dialog-run-id"),
    dialogActions: document.getElementById("dialog-actions"),
    dialogRunSpec: document.getElementById("dialog-run-spec"),
    dialogRunSummary: document.getElementById("dialog-run-summary"),
    closeDialog: document.getElementById("close-run-dialog"),
    message: document.getElementById("page-message"),
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

  function showMessage(message, isError = false) {
    elements.message.textContent = message;
    elements.message.classList.toggle("error", isError);
    elements.message.classList.add("visible");
    window.setTimeout(() => {
      elements.message.classList.remove("visible");
    }, 3200);
  }

  function statusClass(status) {
    return String(status || "").toLowerCase();
  }

  function make(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = String(text);
    return element;
  }

  function pretty(value) {
    return JSON.stringify(value, null, 2);
  }

  function shortId(value) {
    const text = String(value || "—");
    return text.length > 18 ? `${text.slice(0, 16)}…` : text;
  }

  function compactJson(value) {
    const text = JSON.stringify(value || {});
    return text === "{}" ? "—" : text;
  }

  function renderExperimentList() {
    elements.experimentCount.textContent =
      `${state.experiments.length} 个数据库`;
    elements.experimentList.replaceChildren();
    if (!state.experiments.length) {
      elements.experimentList.append(
        make("div", "empty-cell", "结果目录中没有实验数据库"),
      );
      return;
    }
    state.experiments.forEach((experiment) => {
      const button = make("button", "experiment-card");
      button.type = "button";
      button.classList.toggle(
        "active",
        experiment.experiment_id === state.selectedExperiment,
      );
      button.append(
        make("strong", "", experiment.experiment_id),
        make(
          "span",
          "",
          `${experiment.status} · ${experiment.planned_run_count} Runs`,
        ),
        make("span", "", experiment.database_name),
      );
      button.addEventListener("click", () =>
        selectExperiment(experiment.experiment_id),
      );
      elements.experimentList.append(button);
    });
  }

  function fact(label, value) {
    const card = make("div", "fact-card");
    card.append(make("span", "", label), make("strong", "", value));
    return card;
  }

  function renderExperimentDetail() {
    const detail = state.detail;
    elements.empty.hidden = true;
    elements.view.hidden = false;
    elements.title.textContent = detail.experiment_id;
    elements.description.textContent =
      detail.description || "没有实验说明";
    elements.status.textContent = detail.status;
    elements.status.className =
      `status-pill ${statusClass(detail.status)}`;
    elements.facts.replaceChildren(
      fact("计划 Run", detail.planned_run_count),
      fact("成功", detail.status_counts.SUCCEEDED || 0),
      fact("失败", detail.status_counts.FAILED || 0),
      fact("运行中", detail.status_counts.RUNNING || 0),
      fact("可复现", detail.reproducible ? "YES" : "NO"),
    );
    elements.spec.textContent = pretty(detail.spec);
    elements.revisions.textContent = pretty(detail.code_revisions);
    const id = encodeURIComponent(detail.experiment_id);
    elements.downloadComparison.href =
      `/api/experiments/${id}/comparison.csv`;
  }

  function filterQuery() {
    const params = new URLSearchParams({
      sort: elements.sort.value,
      order: elements.order.value,
      limit: "10000",
    });
    if (elements.search.value.trim()) {
      params.set("q", elements.search.value.trim());
    }
    if (elements.filterStatus.value) {
      params.set("status", elements.filterStatus.value);
    }
    if (elements.seed.value !== "") {
      params.set("seed", elements.seed.value);
    }
    return params;
  }

  function availableSummaryFields(runs) {
    return [...new Set(
      runs.flatMap((run) => Object.keys(run.summary_scalars || {})),
    )].sort();
  }

  function renderSummaryFieldOptions() {
    const previous = new Set(state.summaryFields);
    const fields = availableSummaryFields(state.runs);
    const matchingPrevious = fields.filter((field) =>
      previous.has(field),
    );
    const selected = new Set(
      matchingPrevious.length ? matchingPrevious : fields.slice(0, 5),
    );
    elements.summaryFields.replaceChildren();
    fields.forEach((field) => {
      const option = make("option", "", field);
      option.value = field;
      option.selected = selected.has(field);
      elements.summaryFields.append(option);
    });
    state.summaryFields = [...elements.summaryFields.selectedOptions]
      .map((option) => option.value);
  }

  function appendCell(row, value, className = "") {
    const cell = make("td", className, value ?? "—");
    row.append(cell);
    return cell;
  }

  function renderRuns() {
    const header = document.createElement("tr");
    [
      "Run",
      "Seed / 状态",
      "组件",
      "参数轴",
      "Trace / 保留",
      "耗时",
      ...state.summaryFields,
    ].forEach((label) => header.append(make("th", "", label)));
    elements.runsHead.replaceChildren(header);
    elements.runsBody.replaceChildren();
    if (!state.runs.length) {
      const row = document.createElement("tr");
      const cell = appendCell(row, "没有符合条件的 Run", "empty-cell");
      cell.colSpan = 6 + state.summaryFields.length;
      elements.runsBody.append(row);
      return;
    }
    state.runs.forEach((run) => {
      const row = document.createElement("tr");
      const runCell = document.createElement("td");
      const button = make("button", "run-id-button", shortId(run.run_id));
      button.type = "button";
      button.title = run.run_id;
      button.addEventListener("click", () => openRun(run.run_id));
      runCell.append(button);
      runCell.append(
        make("div", "json-compact", shortId(run.scenario_id)),
      );
      row.append(runCell);

      const stateCell = make("td", "cell-stack");
      stateCell.append(
        make("span", "", `Seed ${run.seed}`),
        make("span", `status-pill ${statusClass(run.status)}`, run.status),
      );
      row.append(stateCell);

      const componentCell = make("td", "cell-stack");
      Object.entries(run.components).forEach(([name, value]) => {
        componentCell.append(make("span", "", `${name}: ${value || "—"}`));
      });
      row.append(componentCell);
      appendCell(row, compactJson(run.parameter_values), "json-compact");
      appendCell(
        row,
        `${run.trace_state || "NONE"} / ${run.retention_class}`,
      );
      appendCell(
        row,
        run.duration_seconds === null
          ? "—"
          : `${Number(run.duration_seconds).toFixed(3)}s`,
      );
      state.summaryFields.forEach((field) => {
        appendCell(row, run.summary_scalars?.[field]);
      });
      elements.runsBody.append(row);
    });
  }

  async function loadRuns() {
    const id = state.selectedExperiment;
    if (!id) return;
    const result = await request(
      `${apiPath("experiments", id, "runs")}?${filterQuery()}`,
    );
    state.runs = result.items;
    elements.runResultCount.textContent =
      `显示 ${result.items.length} / ${result.total} 个 Run`;
    elements.downloadComparison.href =
      `${apiPath("experiments", id, "comparison.csv")}?${filterQuery()}`;
    renderSummaryFieldOptions();
    renderRuns();
  }

  async function selectExperiment(experimentId) {
    state.selectedExperiment = experimentId;
    renderExperimentList();
    const url = new URL(window.location.href);
    url.searchParams.set("experiment", experimentId);
    window.history.replaceState({}, "", url);
    try {
      state.detail = await request(
        apiPath("experiments", experimentId),
      );
      renderExperimentDetail();
      await loadRuns();
    } catch (error) {
      showMessage(`实验载入失败：${error.message}`, true);
    }
  }

  function actionLink(label, href, download = false) {
    const link = make("a", "button", label);
    link.href = href;
    if (download) link.download = "";
    return link;
  }

  async function openRun(runId) {
    try {
      const experimentId = state.selectedExperiment;
      const detail = await request(
        apiPath("experiments", experimentId, "runs", runId),
      );
      elements.dialogRunId.textContent = runId;
      elements.dialogRunSpec.textContent = pretty(detail.run_spec);
      elements.dialogRunSummary.textContent = pretty(
        detail.summary || detail.error || {status: detail.status},
      );
      elements.dialogActions.replaceChildren();
      if (detail.status === "SUCCEEDED" && detail.trace_state === "STORED") {
        const viewerApi = apiPath(
          "experiments",
          experimentId,
          "runs",
          runId,
          "viewer",
        );
        elements.dialogActions.append(
          actionLink(
            "打开 K 线回放",
            `./index.html?run_api=${encodeURIComponent(viewerApi)}`,
          ),
          actionLink(
            "导出 Viewer JSON",
            `${viewerApi}?download=1`,
            true,
          ),
        );
      } else {
        elements.dialogActions.append(
          make(
            "span",
            "page-subtitle",
            "当前 Run 没有可读取的 STORED Trace",
          ),
        );
      }
      elements.dialog.showModal();
    } catch (error) {
      showMessage(`Run 载入失败：${error.message}`, true);
    }
  }

  async function loadExperiments() {
    try {
      const result = await request("/api/experiments");
      state.experiments = result.items;
      renderExperimentList();
      const requested = new URLSearchParams(window.location.search)
        .get("experiment");
      const target = state.experiments.find(
        (item) => item.experiment_id === requested,
      ) || state.experiments[0];
      if (target) await selectExperiment(target.experiment_id);
    } catch (error) {
      showMessage(`实验目录载入失败：${error.message}`, true);
      elements.experimentCount.textContent = "载入失败";
    }
  }

  elements.refresh.addEventListener("click", loadExperiments);
  elements.filters.addEventListener("submit", (event) => {
    event.preventDefault();
    loadRuns().catch((error) =>
      showMessage(`Run 查询失败：${error.message}`, true),
    );
  });
  elements.summaryFields.addEventListener("change", () => {
    state.summaryFields = [...elements.summaryFields.selectedOptions]
      .map((option) => option.value);
    renderRuns();
  });
  elements.closeDialog.addEventListener("click", () =>
    elements.dialog.close(),
  );

  loadExperiments();
})();
