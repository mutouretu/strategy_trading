(() => {
  "use strict";

  const DEFAULT_RUN =
    "./data/layered-following-grid-coinm-long-3y-seed-42.json";
  const VISIBLE_CANDLES = 80;
  const layout = {
    width: 1200,
    height: 680,
    left: 16,
    right: 82,
    top: 22,
    bottom: 42,
  };

  const elements = {
    chart: document.getElementById("chart"),
    symbol: document.getElementById("symbol"),
    interval: document.getElementById("interval"),
    ohlc: document.getElementById("ohlc"),
    play: document.getElementById("play"),
    step: document.getElementById("step"),
    reset: document.getElementById("reset"),
    speed: document.getElementById("speed"),
    runMeta: document.getElementById("run-meta"),
    timeline: document.getElementById("timeline"),
    progress: document.getElementById("progress"),
    message: document.getElementById("message"),
    tooltip: document.getElementById("tooltip"),
    file: document.getElementById("run-file"),
    accountEquityLabel: document.getElementById("account-equity-label"),
    accountEquity: document.getElementById("account-equity"),
    accountEquityQuoteLabel: document.getElementById(
      "account-equity-quote-label",
    ),
    accountEquityQuote: document.getElementById("account-equity-quote"),
    accountSpotLabel: document.getElementById("account-spot-label"),
    accountSpot: document.getElementById("account-spot"),
    accountCashLabel: document.getElementById("account-cash-label"),
    accountCash: document.getElementById("account-cash"),
    accountUnrealizedLabel: document.getElementById(
      "account-unrealized-label",
    ),
    accountUnrealized: document.getElementById("account-unrealized"),
    accountPositionLabel: document.getElementById("account-position-label"),
    accountPosition: document.getElementById("account-position"),
    accountAverage: document.getElementById("account-average"),
    accountRealizedLabel: document.getElementById("account-realized-label"),
    accountRealized: document.getElementById("account-realized"),
    activeOrderCount: document.getElementById("active-order-count"),
    equityChart: document.getElementById("equity-chart"),
    equityUnit: document.getElementById("equity-unit"),
    equityNote: document.getElementById("equity-note"),
    fillCount: document.getElementById("fill-count"),
    fillsBody: document.getElementById("fills-body"),
  };

  let run = null;
  let market = [];
  let cursor = 0;
  let timer = null;
  let sequenceIndex = new Map();

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function normalizeRun(raw) {
    if (!raw || !raw.manifest || !Array.isArray(raw.market)) {
      throw new Error("缺少 manifest 或 market 数组");
    }
    if (!raw.market.length) {
      throw new Error("market 数组为空");
    }
    const normalizedMarket = raw.market.map((bar, index) => {
      const normalized = {
        sequence: Number(bar.sequence ?? index),
        timestamp: Number(bar.timestamp),
        date: bar.date || new Date(Number(bar.timestamp)).toISOString().slice(0, 10),
        instrument: String(bar.instrument || raw.manifest.instrument || ""),
        open: Number(bar.open),
        high: Number(bar.high),
        low: Number(bar.low),
        close: Number(bar.close),
      };
      if (
        !normalized.instrument ||
        !Number.isFinite(normalized.timestamp) ||
        ![normalized.open, normalized.high, normalized.low, normalized.close].every(
          Number.isFinite,
        ) ||
        normalized.low > Math.min(normalized.open, normalized.close) ||
        normalized.high < Math.max(normalized.open, normalized.close)
      ) {
        throw new Error(`第 ${index + 1} 根 K 线无效`);
      }
      return normalized;
    });
    const orders = (Array.isArray(raw.orders) ? raw.orders : []).map(
      (order, index) => {
        const normalized = {
          order_key: String(order.order_key || ""),
          instrument: String(order.instrument || raw.manifest.instrument || ""),
          side: String(order.side || "").toUpperCase(),
          order_type: String(order.order_type || "LIMIT").toUpperCase(),
          quantity: Number(order.quantity),
          price:
            order.price === null || order.price === undefined
              ? null
              : Number(order.price),
          active_from_sequence: Number(
            order.active_from_sequence ?? order.sequence ?? 0,
          ),
          active_to_sequence:
            order.active_to_sequence === null ||
            order.active_to_sequence === undefined
              ? null
              : Number(order.active_to_sequence),
          status: String(order.status || "ACTIVE").toUpperCase(),
        };
        if (
          !normalized.order_key ||
          !["BUY", "SELL"].includes(normalized.side) ||
          !["LIMIT", "MARKET"].includes(normalized.order_type) ||
          !Number.isFinite(normalized.quantity) ||
          normalized.quantity <= 0 ||
          !Number.isFinite(normalized.active_from_sequence) ||
          (normalized.active_to_sequence !== null &&
            !Number.isFinite(normalized.active_to_sequence)) ||
          (normalized.price !== null && !Number.isFinite(normalized.price))
        ) {
          throw new Error(`第 ${index + 1} 条订单记录无效`);
        }
        return normalized;
      },
    );
    const fills = (Array.isArray(raw.fills) ? raw.fills : []).map(
      (fill, index) => {
        const normalized = {
          fill_id: String(fill.fill_id || ""),
          order_key: String(fill.order_key || ""),
          sequence: Number(fill.sequence),
          timestamp: Number(fill.timestamp),
          date: fill.date || "",
          side: String(fill.side || "").toUpperCase(),
          price: Number(fill.price),
          quantity: Number(fill.quantity),
        };
        if (
          !["BUY", "SELL"].includes(normalized.side) ||
          ![
            normalized.sequence,
            normalized.timestamp,
            normalized.price,
            normalized.quantity,
          ].every(Number.isFinite)
        ) {
          throw new Error(`第 ${index + 1} 条成交记录无效`);
        }
        return normalized;
      },
    );
    const equity = (Array.isArray(raw.equity) ? raw.equity : []).map(
      (snapshot, index) => {
        const numericMap = (values) =>
          Object.fromEntries(
            Object.entries(values || {}).map(([key, value]) => [
              key,
              Number(value),
            ]),
          );
        const normalized = {
          sequence: Number(snapshot.sequence),
          timestamp: Number(snapshot.timestamp),
          date: String(snapshot.date || ""),
          cash: Number(snapshot.cash),
          positions: numericMap(snapshot.positions),
          average_costs: numericMap(snapshot.average_costs),
          marks: numericMap(snapshot.marks),
          realized_pnl: Number(snapshot.realized_pnl),
          equity: Number(snapshot.equity),
          equity_asset: String(snapshot.equity_asset || "USDT").toUpperCase(),
          account_metrics: numericMap(snapshot.account_metrics),
        };
        const mapValues = [
          ...Object.values(normalized.positions),
          ...Object.values(normalized.average_costs),
          ...Object.values(normalized.marks),
          ...Object.values(normalized.account_metrics),
        ];
        if (
          ![
            normalized.sequence,
            normalized.cash,
            normalized.realized_pnl,
            normalized.equity,
            ...mapValues,
          ].every(Number.isFinite)
        ) {
          throw new Error(`第 ${index + 1} 条权益记录无效`);
        }
        return normalized;
      },
    );
    return {
      schema_version: raw.schema_version || 1,
      manifest: raw.manifest,
      market: normalizedMarket,
      orders,
      fills,
      equity,
      summary: raw.summary || {},
    };
  }

  async function loadDefault() {
    const response = await fetch(DEFAULT_RUN);
    if (!response.ok) {
      throw new Error(`无法读取默认 run：HTTP ${response.status}`);
    }
    applyRun(normalizeRun(await response.json()));
  }

  function applyRun(nextRun) {
    stop();
    run = nextRun;
    market = nextRun.market;
    sequenceIndex = new Map(
      market.map((bar, index) => [Number(bar.sequence), index]),
    );
    cursor = 0;
    elements.timeline.max = String(market.length - 1);
    elements.timeline.value = String(cursor);
    elements.symbol.textContent =
      nextRun.manifest.instrument || market[0].instrument;
    elements.interval.textContent =
      String(nextRun.manifest.interval || "1d").toUpperCase();
    elements.runMeta.textContent = [
      nextRun.manifest.run_id,
      nextRun.manifest.source,
      nextRun.manifest.seed !== undefined
        ? `seed ${nextRun.manifest.seed}`
        : null,
    ]
      .filter(Boolean)
      .join(" · ");
    const riskDate =
      nextRun.summary.first_nonpositive_futures_equity_date;
    elements.message.classList.toggle("risk", Boolean(riskDate));
    elements.message.textContent = riskDate
      ? `风险提示：${riskDate} 合约子账户权益已不大于 0；当前模型未执行强平，此日期之后的结果仅供公式调试`
      : nextRun.fills.length
        ? `${nextRun.orders.length} 条订单生命周期 · ${nextRun.fills.length} 条成交 · ${nextRun.equity.length} 个账户快照`
        : "市场数据已载入；当前 run 尚无策略成交";
    const settlementAsset = String(
      nextRun.summary.equity_asset ||
        nextRun.equity[0]?.equity_asset ||
        "USDT",
    ).toUpperCase();
    const hasQuoteEquity = nextRun.equity.some((snapshot) =>
      Number.isFinite(snapshot.account_metrics.total_equity_usdt),
    );
    elements.equityUnit.options[0].textContent = settlementAsset;
    elements.equityUnit.options[1].textContent = "USDT";
    elements.equityUnit.disabled =
      settlementAsset === "USDT" || !hasQuoteEquity;
    elements.equityUnit.value = "settlement";
    render();
  }

  function money(value) {
    return Number(value).toLocaleString("en-US", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
  }

  function assetAmount(value, asset, digits = 8) {
    return `${Number(value).toLocaleString("en-US", {
      minimumFractionDigits: Math.min(2, digits),
      maximumFractionDigits: digits,
    })} ${asset}`;
  }

  function shortMoney(value) {
    if (Math.abs(value) >= 1000) return `${(value / 1000).toFixed(1)}k`;
    return value.toFixed(0);
  }

  function render() {
    if (!run) return;
    const start = Math.max(0, cursor - VISIBLE_CANDLES + 1);
    const bars = market.slice(start, cursor + 1);
    const currentSequence = market[cursor].sequence;
    const plotWidth = layout.width - layout.left - layout.right;
    const plotHeight = layout.height - layout.top - layout.bottom;
    const activeOrders = run.orders.filter((order) => {
      const from = Number(order.active_from_sequence ?? order.sequence ?? 0);
      const to =
        order.active_to_sequence === null ||
        order.active_to_sequence === undefined
          ? Number.POSITIVE_INFINITY
          : Number(order.active_to_sequence);
      return (
        from <= currentSequence &&
        currentSequence < to &&
        order.price !== null &&
        Number.isFinite(Number(order.price))
      );
    });
    const visibleFills = run.fills.filter((fill) => {
      const index = sequenceIndex.get(Number(fill.sequence));
      return index !== undefined && index >= start && index <= cursor;
    });
    const priceValues = bars.flatMap((bar) => [bar.high, bar.low]);
    activeOrders.forEach((order) => priceValues.push(Number(order.price)));
    visibleFills.forEach((fill) => priceValues.push(Number(fill.price)));
    const minPrice = Math.min(...priceValues);
    const maxPrice = Math.max(...priceValues);
    const padding = (maxPrice - minPrice) * 0.08 || maxPrice * 0.02;
    const yMin = minPrice - padding;
    const yMax = maxPrice + padding;
    const slot = plotWidth / Math.max(6, bars.length);
    const x = (localIndex) => layout.left + slot * (localIndex + 0.5);
    const y = (price) =>
      layout.top + ((yMax - price) / (yMax - yMin)) * plotHeight;

    const horizontalGrid = Array.from({ length: 7 }, (_, index) => {
      const fraction = index / 6;
      const price = yMax - fraction * (yMax - yMin);
      const py = layout.top + fraction * plotHeight;
      return `
        <line class="grid-line" x1="${layout.left}" x2="${layout.width - layout.right}" y1="${py}" y2="${py}"></line>
        <text class="axis-text" x="${layout.width - layout.right + 9}" y="${py + 4}">${shortMoney(price)}</text>
      `;
    }).join("");

    const verticalGridCount = Math.min(7, bars.length);
    const verticalGrid = Array.from({ length: verticalGridCount }, (_, index) => {
      const localIndex = Math.min(
        bars.length - 1,
        Math.round(
          (index / Math.max(1, verticalGridCount - 1)) *
            Math.max(0, bars.length - 1),
        ),
      );
      const px = x(localIndex);
      const label = bars[localIndex]?.date || "";
      return `
        <line class="grid-line" x1="${px}" x2="${px}" y1="${layout.top}" y2="${layout.top + plotHeight}"></line>
        <text class="axis-text" x="${px}" y="${layout.top + plotHeight + 24}" text-anchor="middle">${escapeHtml(label.slice(5))}</text>
      `;
    }).join("");

    const candles = bars
      .map((bar, localIndex) => {
        const globalIndex = start + localIndex;
        const px = x(localIndex);
        const up = bar.close >= bar.open;
        const kind = up ? "up" : "down";
        const bodyTop = y(Math.max(bar.open, bar.close));
        const bodyBottom = y(Math.min(bar.open, bar.close));
        const bodyHeight = Math.max(2, bodyBottom - bodyTop);
        const bodyWidth = Math.max(3, slot * 0.62);
        return `
          <line class="wick-${kind}" x1="${px}" x2="${px}" y1="${y(bar.high)}" y2="${y(bar.low)}"></line>
          <rect class="candle-${kind}" x="${px - bodyWidth / 2}" y="${bodyTop}" width="${bodyWidth}" height="${bodyHeight}"></rect>
          <rect class="hit-area" data-index="${globalIndex}" x="${px - slot / 2}" y="${layout.top}" width="${slot}" height="${plotHeight}" fill="transparent"></rect>
        `;
      })
      .join("");

    const orderLines = activeOrders
      .map((order) => {
        const py = y(Number(order.price));
        const sideClass = String(order.side).toLowerCase();
        return `
          <line class="order-line ${sideClass}" x1="${layout.left}" x2="${layout.width - layout.right}" y1="${py}" y2="${py}"></line>
          <text class="order-label ${sideClass}" x="${layout.left + 6}" y="${py - 5}">${escapeHtml(order.side || "")} ${money(order.price)}</text>
        `;
      })
      .join("");

    const fillMarkers = visibleFills
      .map((fill) => {
        const fillIndex = sequenceIndex.get(Number(fill.sequence));
        const localIndex = fillIndex - start;
        const px = x(localIndex);
        const py = y(Number(fill.price));
        const buy = String(fill.side).toUpperCase() === "BUY";
        return `
          <g aria-label="${buy ? "买入成交" : "卖出成交"} ${money(fill.price)}">
            <circle class="${buy ? "fill-buy" : "fill-sell"}" cx="${px}" cy="${py}" r="8"></circle>
            <text class="fill-label" x="${px}" y="${py + 3}">${buy ? "B" : "S"}</text>
          </g>
        `;
      })
      .join("");

    elements.chart.innerHTML = `
      <title id="chart-title">仿真日线 K 线</title>
      <desc id="chart-description">显示最近 ${VISIBLE_CANDLES} 根日线、活动订单和策略成交。</desc>
      ${horizontalGrid}
      ${verticalGrid}
      ${orderLines}
      ${candles}
      ${fillMarkers}
      <line class="crosshair" id="crosshair-x" x1="0" x2="0" y1="${layout.top}" y2="${layout.top + plotHeight}" visibility="hidden"></line>
      <line class="crosshair" id="crosshair-y" x1="${layout.left}" x2="${layout.width - layout.right}" y1="0" y2="0" visibility="hidden"></line>
    `;

    elements.timeline.value = String(cursor);
    elements.progress.textContent = `${market[cursor].date} · ${cursor + 1}/${market.length}`;
    showOHLC(market[cursor]);
    bindChartInteractions(start, slot, y);
    renderAccount(activeOrders, currentSequence);
    renderEquity(start);
    renderFills(currentSequence);
  }

  function currentSnapshot() {
    const currentSequence = market[cursor].sequence;
    return [...run.equity]
      .reverse()
      .find((snapshot) => snapshot.sequence <= currentSequence);
  }

  function setMetric(element, value, tone = "") {
    element.textContent = value;
    element.classList.remove("positive", "negative");
    if (tone) element.classList.add(tone);
  }

  function signedNumber(value, digits = 2) {
    const numeric = Number(value);
    return `${numeric > 0 ? "+" : ""}${numeric.toLocaleString("en-US", {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    })}`;
  }

  function renderAccount(activeOrders, currentSequence) {
    const snapshot = currentSnapshot();
    if (!snapshot) {
      [
        elements.accountEquity,
        elements.accountEquityQuote,
        elements.accountSpot,
        elements.accountCash,
        elements.accountUnrealized,
        elements.accountPosition,
        elements.accountAverage,
        elements.accountRealized,
      ].forEach((element) => setMetric(element, "—"));
      setMetric(elements.activeOrderCount, String(activeOrders.length));
      return;
    }

    const instrument = run.manifest.instrument || market[0].instrument;
    const position = Number(snapshot.positions[instrument] || 0);
    const average = snapshot.average_costs[instrument];
    const realized = Number(snapshot.realized_pnl);
    const metrics = snapshot.account_metrics || {};
    const settlementAsset = String(
      snapshot.equity_asset || run.summary.equity_asset || "USDT",
    ).toUpperCase();
    const settlementKey = `total_equity_${settlementAsset.toLowerCase()}`;
    const settlementEquity = Number(
      metrics[settlementKey] ?? snapshot.equity,
    );
    const quoteEquity = Number(metrics.total_equity_usdt);
    const firstSnapshot = run.equity[0];
    const initial = Number(
      firstSnapshot?.account_metrics?.[settlementKey] ??
        run.summary.initial_equity ??
        firstSnapshot?.equity,
    );
    const equityChange = settlementEquity - initial;
    const base = settlementAsset.toLowerCase();
    const spot = Number(metrics[`spot_${base}`]);
    const unrealized = Number(metrics[`futures_unrealized_pnl_${base}`]);
    const isCoinM = run.manifest.market_type === "coinm";
    elements.accountEquityLabel.textContent = `总权益 ${settlementAsset}`;
    elements.accountEquityQuoteLabel.textContent = "总权益 USDT";
    elements.accountSpotLabel.textContent = isCoinM
      ? `长期底仓 ${settlementAsset}`
      : "长期底仓";
    elements.accountCashLabel.textContent = isCoinM
      ? `合约钱包 ${settlementAsset}`
      : "现金";
    elements.accountUnrealizedLabel.textContent = isCoinM
      ? `合约未实现 ${settlementAsset}`
      : "未实现盈亏";
    elements.accountPositionLabel.textContent = isCoinM
      ? "合约持仓（张）"
      : "持仓";
    elements.accountRealizedLabel.textContent = isCoinM
      ? `合约已实现 ${settlementAsset}`
      : "已实现盈亏";
    setMetric(
      elements.accountEquity,
      `${assetAmount(settlementEquity, settlementAsset)} (${signedNumber(
        equityChange,
        settlementAsset === "BTC" ? 8 : 2,
      )})`,
      equityChange > 0 ? "positive" : equityChange < 0 ? "negative" : "",
    );
    setMetric(
      elements.accountEquityQuote,
      isCoinM && Number.isFinite(quoteEquity)
        ? assetAmount(quoteEquity, "USDT", 2)
        : "—",
    );
    setMetric(
      elements.accountSpot,
      Number.isFinite(spot)
        ? assetAmount(spot, settlementAsset)
        : "—",
    );
    setMetric(
      elements.accountCash,
      assetAmount(
        snapshot.cash,
        settlementAsset,
        settlementAsset === "BTC" ? 8 : 2,
      ),
    );
    setMetric(
      elements.accountUnrealized,
      Number.isFinite(unrealized)
        ? signedNumber(unrealized, settlementAsset === "BTC" ? 8 : 2)
        : "—",
      unrealized > 0 ? "positive" : unrealized < 0 ? "negative" : "",
    );
    setMetric(
      elements.accountPosition,
      signedNumber(position, isCoinM ? 0 : 4),
      position > 0 ? "positive" : position < 0 ? "negative" : "",
    );
    setMetric(
      elements.accountAverage,
      average === undefined ? "—" : money(average),
    );
    setMetric(
      elements.accountRealized,
      signedNumber(realized, settlementAsset === "BTC" ? 8 : 2),
      realized > 0 ? "positive" : realized < 0 ? "negative" : "",
    );
    const allActiveOrders = run.orders.filter((order) => {
      const to =
        order.active_to_sequence === null
          ? Number.POSITIVE_INFINITY
          : order.active_to_sequence;
      return order.active_from_sequence <= currentSequence && currentSequence < to;
    });
    setMetric(elements.activeOrderCount, String(allActiveOrders.length));
  }

  function renderEquity(start) {
    const currentSequence = market[cursor].sequence;
    const points = run.equity.filter((snapshot) => {
      const index = sequenceIndex.get(snapshot.sequence);
      return (
        index !== undefined &&
        index >= start &&
        index <= cursor &&
        snapshot.sequence <= currentSequence
      );
    });
    if (!points.length) {
      elements.equityChart.innerHTML = `
        <text class="axis-text" x="600" y="118" text-anchor="middle">
          当前 run 没有权益快照
        </text>
      `;
      return;
    }

    const chart = { width: 1200, height: 230, left: 16, right: 82, top: 18, bottom: 34 };
    const width = chart.width - chart.left - chart.right;
    const height = chart.height - chart.top - chart.bottom;
    const settlementAsset = String(
      run.summary.equity_asset || points[0].equity_asset || "USDT",
    ).toUpperCase();
    const settlementKey = `total_equity_${settlementAsset.toLowerCase()}`;
    const useQuote =
      elements.equityUnit.value === "quote" &&
      settlementAsset !== "USDT";
    const valueFor = (point) =>
      Number(
        useQuote
          ? point.account_metrics.total_equity_usdt
          : point.account_metrics[settlementKey] ?? point.equity,
      );
    const unit = useQuote ? "USDT" : settlementAsset;
    elements.equityNote.textContent =
      unit === "BTC"
        ? "BTC 计价：底仓 + 合约钱包 + 合约浮动盈亏"
        : "USDT 计价：BTC 总权益按当日收盘价折算";
    const values = points.map(valueFor);
    const base = valueFor(run.equity[0]);
    const rawMin = Math.min(...values, base);
    const rawMax = Math.max(...values, base);
    const padding = (rawMax - rawMin) * 0.16 || Math.max(Math.abs(rawMax) * 0.002, 1);
    const minimum = rawMin - padding;
    const maximum = rawMax + padding;
    const visibleBarCount = cursor - start + 1;
    const visibleSlots = Math.max(6, visibleBarCount);
    const x = (point) => {
      const index = sequenceIndex.get(point.sequence) - start;
      return chart.left + ((index + 0.5) / visibleSlots) * width;
    };
    const y = (value) =>
      chart.top + ((maximum - value) / (maximum - minimum)) * height;
    const horizontalGrid = Array.from({ length: 4 }, (_, index) => {
      const fraction = index / 3;
      const value = maximum - fraction * (maximum - minimum);
      const py = chart.top + fraction * height;
      return `
        <line class="grid-line" x1="${chart.left}" x2="${chart.width - chart.right}" y1="${py}" y2="${py}"></line>
        <text class="axis-text" x="${chart.width - chart.right + 9}" y="${py + 4}">${
          unit === "BTC" ? Number(value).toFixed(4) : money(value)
        }</text>
      `;
    }).join("");
    const polyline = points
      .map((point) => `${x(point)},${y(valueFor(point))}`)
      .join(" ");
    const last = points.at(-1);
    elements.equityChart.innerHTML = `
      <title>账户权益（${escapeHtml(unit)}）</title>
      ${horizontalGrid}
      <line class="equity-base" x1="${chart.left}" x2="${chart.width - chart.right}" y1="${y(base)}" y2="${y(base)}"></line>
      <polyline class="equity-line" points="${polyline}"></polyline>
      <circle class="equity-point" cx="${x(last)}" cy="${y(valueFor(last))}" r="4"></circle>
    `;
  }

  function renderFills(currentSequence) {
    const fills = run.fills.filter(
      (fill) => Number(fill.sequence) <= currentSequence,
    );
    elements.fillCount.textContent = `截至当前日期 ${fills.length} 笔`;
    if (!fills.length) {
      elements.fillsBody.innerHTML =
        '<tr><td colspan="5" class="empty-cell">暂无成交</td></tr>';
      return;
    }
    elements.fillsBody.innerHTML = [...fills]
      .reverse()
      .slice(0, 8)
      .map((fill) => {
        const index = sequenceIndex.get(Number(fill.sequence));
        const date = fill.date || market[index]?.date || `#${fill.sequence}`;
        const side = fill.side.toLowerCase();
        return `
          <tr>
            <td>${escapeHtml(date)}</td>
            <td><span class="side ${side}">${escapeHtml(fill.side)}</span></td>
            <td>${money(fill.price)}</td>
            <td>${escapeHtml(String(fill.quantity))}</td>
            <td><span class="order-key" title="${escapeHtml(fill.order_key)}">${escapeHtml(fill.order_key)}</span></td>
          </tr>
        `;
      })
      .join("");
  }

  function showOHLC(bar) {
    const direction = bar.close >= bar.open ? "up" : "down";
    elements.ohlc.innerHTML = `
      <span>${escapeHtml(bar.date)}</span>
      <span> O ${money(bar.open)}</span>
      <span> H ${money(bar.high)}</span>
      <span> L ${money(bar.low)}</span>
      <span class="${direction}"> C ${money(bar.close)}</span>
    `;
  }

  function bindChartInteractions(start, slot, y) {
    const crosshairX = document.getElementById("crosshair-x");
    const crosshairY = document.getElementById("crosshair-y");
    elements.chart.querySelectorAll(".hit-area").forEach((target) => {
      target.addEventListener("pointermove", (event) => {
        const index = Number(target.dataset.index);
        const bar = market[index];
        showOHLC(bar);
        const localIndex = index - start;
        const px = layout.left + slot * (localIndex + 0.5);
        const py = y(bar.close);
        crosshairX.setAttribute("x1", px);
        crosshairX.setAttribute("x2", px);
        crosshairX.setAttribute("visibility", "visible");
        crosshairY.setAttribute("y1", py);
        crosshairY.setAttribute("y2", py);
        crosshairY.setAttribute("visibility", "visible");
        elements.tooltip.textContent =
          `${bar.date} · O ${money(bar.open)} · H ${money(bar.high)} · ` +
          `L ${money(bar.low)} · C ${money(bar.close)}`;
        const bounds = elements.chart.getBoundingClientRect();
        const wrapper = elements.chart.parentElement.getBoundingClientRect();
        const candleX = ((px / layout.width) * bounds.width) + bounds.left - wrapper.left;
        const candleY = ((py / layout.height) * bounds.height) + bounds.top - wrapper.top;
        elements.tooltip.style.left =
          Math.max(120, Math.min(wrapper.width - 120, candleX)) + "px";
        elements.tooltip.style.top = Math.max(44, candleY) + "px";
        elements.tooltip.style.visibility = "visible";
      });
      target.addEventListener("pointerleave", () => {
        crosshairX.setAttribute("visibility", "hidden");
        crosshairY.setAttribute("visibility", "hidden");
        elements.tooltip.style.visibility = "hidden";
        showOHLC(market[cursor]);
      });
    });
  }

  function stop() {
    if (timer !== null) window.clearInterval(timer);
    timer = null;
    elements.play.textContent = "播放";
    elements.play.setAttribute("aria-pressed", "false");
  }

  function advance() {
    if (cursor >= market.length - 1) {
      stop();
      return;
    }
    cursor += 1;
    render();
  }

  function togglePlay() {
    if (timer !== null) {
      stop();
      return;
    }
    if (cursor >= market.length - 1) cursor = 0;
    elements.play.textContent = "暂停";
    elements.play.setAttribute("aria-pressed", "true");
    timer = window.setInterval(advance, Number(elements.speed.value));
  }

  elements.play.addEventListener("click", togglePlay);
  elements.step.addEventListener("click", () => {
    stop();
    advance();
  });
  elements.reset.addEventListener("click", () => {
    stop();
    cursor = 0;
    render();
  });
  elements.speed.addEventListener("change", () => {
    if (timer !== null) {
      stop();
      togglePlay();
    }
  });
  elements.timeline.addEventListener("input", () => {
    stop();
    cursor = Number(elements.timeline.value);
    render();
  });
  elements.equityUnit.addEventListener("change", () => {
    if (run) renderEquity(Math.max(0, cursor - VISIBLE_CANDLES + 1));
  });
  elements.file.addEventListener("change", async () => {
    const [file] = elements.file.files;
    if (!file) return;
    try {
      applyRun(normalizeRun(JSON.parse(await file.text())));
    } catch (error) {
      elements.message.textContent = `文件载入失败：${error.message}`;
    } finally {
      elements.file.value = "";
    }
  });

  loadDefault().catch((error) => {
    elements.message.textContent =
      `${error.message}。请通过本地 HTTP 服务打开页面，或手动载入 run JSON。`;
  });
})();
