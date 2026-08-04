(function exposeSimulationRunModel(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  } else {
    root.SimulationRunModel = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this, () => {
  "use strict";

  function numericMap(values) {
    return Object.fromEntries(
      Object.entries(values || {}).map(([key, value]) => [
        key,
        Number(value),
      ]),
    );
  }

  function optionalNumber(value) {
    return value === null || value === undefined
      ? null
      : Number(value);
  }

  function normalizeMarket(raw) {
    return raw.market.map((bar, index) => {
      const timestamp = Number(bar.timestamp);
      const normalized = {
        sequence: Number(bar.sequence ?? index),
        timestamp,
        date:
          bar.date ||
          new Date(timestamp).toISOString().slice(0, 10),
        instrument: String(
          bar.instrument || raw.manifest.instrument || "",
        ),
        open: Number(bar.open),
        high: Number(bar.high),
        low: Number(bar.low),
        close: Number(bar.close),
      };
      if (
        !normalized.instrument ||
        !Number.isFinite(normalized.sequence) ||
        !Number.isFinite(normalized.timestamp) ||
        ![
          normalized.open,
          normalized.high,
          normalized.low,
          normalized.close,
        ].every(Number.isFinite) ||
        normalized.low > Math.min(normalized.open, normalized.close) ||
        normalized.high < Math.max(normalized.open, normalized.close)
      ) {
        throw new Error(`第 ${index + 1} 根 K 线无效`);
      }
      return normalized;
    });
  }

  function normalizeOrders(raw) {
    return (Array.isArray(raw.orders) ? raw.orders : []).map(
      (order, index) => {
        const normalized = {
          order_key: String(order.order_key || ""),
          instrument: String(
            order.instrument || raw.manifest.instrument || "",
          ),
          side: String(order.side || "").toUpperCase(),
          order_type: String(
            order.order_type || "LIMIT",
          ).toUpperCase(),
          quantity: Number(order.quantity),
          price: optionalNumber(order.price),
          reduce_only: Boolean(order.reduce_only),
          active_from_sequence: Number(
            order.active_from_sequence ?? order.sequence ?? 0,
          ),
          active_to_sequence: optionalNumber(
            order.active_to_sequence,
          ),
          status: String(order.status || "ACTIVE").toUpperCase(),
          tags: {...(order.tags || {})},
        };
        if (
          !normalized.order_key ||
          !["BUY", "SELL"].includes(normalized.side) ||
          !["LIMIT", "MARKET"].includes(normalized.order_type) ||
          !["ACTIVE", "FILLED", "CANCELLED"].includes(
            normalized.status,
          ) ||
          !Number.isFinite(normalized.quantity) ||
          normalized.quantity <= 0 ||
          !Number.isFinite(normalized.active_from_sequence) ||
          (normalized.active_to_sequence !== null &&
            !Number.isFinite(normalized.active_to_sequence)) ||
          (normalized.price !== null &&
            !Number.isFinite(normalized.price))
        ) {
          throw new Error(`第 ${index + 1} 条订单记录无效`);
        }
        return normalized;
      },
    );
  }

  function projectOrdersToIntents(orders) {
    return orders.map((order) => ({
      intent_key: order.order_key,
      instrument: order.instrument,
      intent_mode:
        order.order_type === "MARKET" ? "ACTIVE" : "PASSIVE",
      side: order.side,
      quantity: order.quantity,
      target_price: order.price,
      reduce_only: order.reduce_only,
      active_from_sequence: order.active_from_sequence,
      active_to_sequence: order.active_to_sequence,
      status: order.status === "ACTIVE" ? "WAITING" : order.status,
      tags: {
        ...order.tags,
        legacy_order_type: order.order_type,
      },
    }));
  }

  function normalizeIntents(raw, schemaVersion, orders) {
    if (schemaVersion === 1) return projectOrdersToIntents(orders);
    if (!Array.isArray(raw.intents)) {
      throw new Error("schema v2 缺少 intents 数组");
    }
    return raw.intents.map((intent, index) => {
      const normalized = {
        intent_key: String(intent.intent_key || ""),
        instrument: String(
          intent.instrument || raw.manifest.instrument || "",
        ),
        intent_mode: String(
          intent.intent_mode || "",
        ).toUpperCase(),
        side: String(intent.side || "").toUpperCase(),
        quantity: Number(intent.quantity),
        target_price: optionalNumber(intent.target_price),
        reduce_only: Boolean(intent.reduce_only),
        active_from_sequence: Number(intent.active_from_sequence),
        active_to_sequence: optionalNumber(
          intent.active_to_sequence,
        ),
        status: String(intent.status || "WAITING").toUpperCase(),
        tags: {...(intent.tags || {})},
      };
      if (
        !normalized.intent_key ||
        !["PASSIVE", "ACTIVE"].includes(normalized.intent_mode) ||
        !["BUY", "SELL"].includes(normalized.side) ||
        !["WAITING", "FILLED", "CANCELLED"].includes(
          normalized.status,
        ) ||
        !Number.isFinite(normalized.quantity) ||
        normalized.quantity <= 0 ||
        !Number.isFinite(normalized.active_from_sequence) ||
        (normalized.active_to_sequence !== null &&
          !Number.isFinite(normalized.active_to_sequence)) ||
        (normalized.intent_mode === "PASSIVE" &&
          (!Number.isFinite(normalized.target_price) ||
            normalized.target_price <= 0)) ||
        (normalized.intent_mode === "ACTIVE" &&
          normalized.target_price !== null)
      ) {
        throw new Error(`第 ${index + 1} 条意图记录无效`);
      }
      return normalized;
    });
  }

  function normalizeInstructions(raw, schemaVersion) {
    if (schemaVersion === 1) return [];
    if (!Array.isArray(raw.instructions)) {
      throw new Error("schema v2 缺少 instructions 数组");
    }
    return raw.instructions.map((instruction, index) => {
      const normalized = {
        instruction_key: String(
          instruction.instruction_key || "",
        ),
        source_intent_key: String(
          instruction.source_intent_key || "",
        ),
        instrument: String(
          instruction.instrument || raw.manifest.instrument || "",
        ),
        frame_sequence: Number(instruction.frame_sequence),
        timestamp: Number(instruction.timestamp),
        date: String(instruction.date || ""),
        intent_mode: String(
          instruction.intent_mode || "",
        ).toUpperCase(),
        side: String(instruction.side || "").toUpperCase(),
        price: Number(instruction.price),
        quantity: Number(instruction.quantity),
        reduce_only: Boolean(instruction.reduce_only),
        tags: {...(instruction.tags || {})},
      };
      if (
        !normalized.instruction_key ||
        !normalized.source_intent_key ||
        !["PASSIVE", "ACTIVE"].includes(normalized.intent_mode) ||
        !["BUY", "SELL"].includes(normalized.side) ||
        ![
          normalized.frame_sequence,
          normalized.timestamp,
          normalized.price,
          normalized.quantity,
        ].every(Number.isFinite) ||
        normalized.price <= 0 ||
        normalized.quantity <= 0
      ) {
        throw new Error(`第 ${index + 1} 条交易指令无效`);
      }
      return normalized;
    });
  }

  function normalizeFills(raw, schemaVersion) {
    return (Array.isArray(raw.fills) ? raw.fills : []).map(
      (fill, index) => {
        const tags = {...(fill.tags || {})};
        const orderKey = String(fill.order_key || "");
        const normalized = {
          fill_id: String(fill.fill_id || ""),
          source_intent_key: String(
            fill.source_intent_key ||
              tags.source_intent_key ||
              orderKey,
          ),
          instruction_key: String(
            fill.instruction_key || tags.instruction_key || "",
          ),
          intent_mode: String(
            fill.intent_mode || tags.intent_mode || "",
          ).toUpperCase(),
          sequence: Number(fill.sequence),
          timestamp: Number(fill.timestamp),
          date: String(fill.date || ""),
          side: String(fill.side || "").toUpperCase(),
          reference_price: optionalNumber(fill.reference_price),
          price: Number(fill.price),
          slippage_amount: optionalNumber(fill.slippage_amount),
          slippage_bps: optionalNumber(fill.slippage_bps),
          quantity: Number(fill.quantity),
          liquidity_role: String(
            fill.liquidity_role || "",
          ).toUpperCase(),
          fee_rate: optionalNumber(fill.fee_rate),
          fee_amount: optionalNumber(fill.fee_amount),
          fee_asset: String(fill.fee_asset || "").toUpperCase(),
          reduce_only: Boolean(fill.reduce_only),
          tags,
        };
        if (!Number.isFinite(normalized.reference_price)) {
          normalized.reference_price = normalized.price;
        }
        if (!Number.isFinite(normalized.slippage_amount)) {
          normalized.slippage_amount =
            normalized.price - normalized.reference_price;
        }
        if (!Number.isFinite(normalized.slippage_bps)) {
          normalized.slippage_bps =
            normalized.slippage_amount /
            normalized.reference_price *
            10000;
        }
        if (
          !normalized.fill_id ||
          !["BUY", "SELL"].includes(normalized.side) ||
          ![
            normalized.sequence,
            normalized.timestamp,
            normalized.reference_price,
            normalized.price,
            normalized.slippage_amount,
            normalized.slippage_bps,
            normalized.quantity,
          ].every(Number.isFinite) ||
          normalized.reference_price <= 0 ||
          (schemaVersion === 2 &&
            (
              !["MAKER", "TAKER"].includes(
                normalized.liquidity_role,
              ) ||
              !Number.isFinite(normalized.fee_rate) ||
              normalized.fee_rate < 0 ||
              !Number.isFinite(normalized.fee_amount) ||
              normalized.fee_amount < 0 ||
              !normalized.fee_asset
            )
          )
        ) {
          throw new Error(`第 ${index + 1} 条成交记录无效`);
        }
        return normalized;
      },
    );
  }

  function normalizeEquity(raw) {
    return (Array.isArray(raw.equity) ? raw.equity : []).map(
      (snapshot, index) => {
        const normalized = {
          sequence: Number(snapshot.sequence),
          timestamp: Number(snapshot.timestamp),
          date: String(snapshot.date || ""),
          cash: Number(snapshot.cash),
          positions: numericMap(snapshot.positions),
          average_costs: numericMap(snapshot.average_costs),
          marks: numericMap(snapshot.marks),
          gross_realized_pnl: Number(
            snapshot.gross_realized_pnl ??
              snapshot.realized_pnl,
          ),
          total_fees: Number(snapshot.total_fees ?? 0),
          net_realized_pnl: Number(
            snapshot.net_realized_pnl ??
              snapshot.realized_pnl,
          ),
          total_funding: Number(snapshot.total_funding ?? 0),
          net_pnl_after_fees_and_funding: Number(
            snapshot.net_pnl_after_fees_and_funding ??
              Number(
                snapshot.net_realized_pnl ??
                  snapshot.realized_pnl,
              ) +
                Number(snapshot.total_funding ?? 0),
          ),
          realized_pnl: Number(
            snapshot.realized_pnl ??
              snapshot.net_realized_pnl,
          ),
          equity: Number(snapshot.equity),
          equity_asset: String(
            snapshot.equity_asset || "USDT",
          ).toUpperCase(),
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
            normalized.gross_realized_pnl,
            normalized.total_fees,
            normalized.net_realized_pnl,
            normalized.total_funding,
            normalized.net_pnl_after_fees_and_funding,
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
  }

  function normalizeRunStatus(raw) {
    const source = raw.run_status || raw.summary || {};
    const liquidated = Boolean(source.liquidated ?? false);
    const normalized = {
      completed: Boolean(source.completed ?? !liquidated),
      liquidated,
      bankrupt: Boolean(source.bankrupt ?? false),
      termination_reason:
        source.termination_reason === null ||
        source.termination_reason === undefined
          ? null
          : String(source.termination_reason).toUpperCase(),
      termination_sequence: optionalNumber(
        source.termination_sequence,
      ),
    };
    if (
      (normalized.liquidated &&
        (normalized.completed ||
          normalized.termination_reason !== "LIQUIDATION" ||
          !Number.isFinite(normalized.termination_sequence))) ||
      (!normalized.liquidated &&
        (!normalized.completed ||
          normalized.bankrupt ||
          normalized.termination_reason !== null ||
          normalized.termination_sequence !== null))
    ) {
      throw new Error("run_status 终止状态无效");
    }
    return normalized;
  }

  function normalizeMarginSnapshot(snapshot, index, label = "保证金") {
    const normalized = {
      sequence: Number(snapshot.sequence),
      timestamp: Number(snapshot.timestamp),
      date: String(snapshot.date || ""),
      instrument: String(snapshot.instrument || ""),
      settlement_asset: String(
        snapshot.settlement_asset || "",
      ).toUpperCase(),
      notional_asset: String(
        snapshot.notional_asset || "",
      ).toUpperCase(),
      mark_price: Number(snapshot.mark_price),
      mark_price_source: String(snapshot.mark_price_source || ""),
      leverage: Number(snapshot.leverage),
      position_quantity: Number(snapshot.position_quantity),
      position_unit: String(snapshot.position_unit || ""),
      average_entry_price: Number(snapshot.average_entry_price),
      position_notional: Number(snapshot.position_notional),
      wallet_balance: Number(snapshot.wallet_balance),
      unrealized_pnl: Number(snapshot.unrealized_pnl),
      margin_balance: Number(snapshot.margin_balance),
      position_initial_margin: Number(
        snapshot.position_initial_margin,
      ),
      maintenance_margin: Number(snapshot.maintenance_margin),
      available_balance: Number(snapshot.available_balance),
      margin_buffer: Number(snapshot.margin_buffer),
      initial_margin_utilization: optionalNumber(
        snapshot.initial_margin_utilization,
      ),
      maintenance_margin_utilization: optionalNumber(
        snapshot.maintenance_margin_utilization,
      ),
      effective_leverage: optionalNumber(
        snapshot.effective_leverage,
      ),
      estimated_liquidation_price: optionalNumber(
        snapshot.estimated_liquidation_price,
      ),
      liquidation_triggered: Boolean(
        snapshot.liquidation_triggered,
      ),
      bankrupt: Boolean(snapshot.bankrupt),
    };
    const optionalValues = [
      normalized.initial_margin_utilization,
      normalized.maintenance_margin_utilization,
      normalized.effective_leverage,
      normalized.estimated_liquidation_price,
    ].filter((value) => value !== null);
    if (
      !normalized.instrument ||
      !normalized.settlement_asset ||
      !normalized.notional_asset ||
      !normalized.mark_price_source ||
      !normalized.position_unit ||
      ![
        normalized.sequence,
        normalized.timestamp,
        normalized.mark_price,
        normalized.leverage,
        normalized.position_quantity,
        normalized.average_entry_price,
        normalized.position_notional,
        normalized.wallet_balance,
        normalized.unrealized_pnl,
        normalized.margin_balance,
        normalized.position_initial_margin,
        normalized.maintenance_margin,
        normalized.available_balance,
        normalized.margin_buffer,
        ...optionalValues,
      ].every(Number.isFinite) ||
      normalized.sequence < 0 ||
      normalized.mark_price <= 0 ||
      normalized.leverage <= 0 ||
      normalized.position_notional < 0 ||
      normalized.position_initial_margin < 0 ||
      normalized.maintenance_margin < 0
    ) {
      throw new Error(`第 ${index + 1} 条${label}快照无效`);
    }
    return normalized;
  }

  function normalizeMargin(raw) {
    return (Array.isArray(raw.margin) ? raw.margin : []).map(
      (snapshot, index) =>
        normalizeMarginSnapshot(snapshot, index),
    );
  }

  function normalizeAccountEvents(raw) {
    return (
      Array.isArray(raw.account_events) ? raw.account_events : []
    ).map((event, index) => {
      const normalized = {
        event_type: String(event.event_type || "").toUpperCase(),
        sequence: Number(event.sequence),
        timestamp: Number(event.timestamp),
        date: String(event.date || ""),
        instrument: String(event.instrument || ""),
        mark_price_sampling: String(
          event.mark_price_sampling || "",
        ).toUpperCase(),
        maintenance_schedule_version: String(
          event.maintenance_schedule_version || "",
        ),
        intrabar_ordering_ambiguous: Boolean(
          event.intrabar_ordering_ambiguous,
        ),
        bankrupt: Boolean(event.bankrupt),
        snapshot: normalizeMarginSnapshot(
          event.snapshot || {},
          index,
          "强平事件",
        ),
      };
      if (
        normalized.event_type !== "LIQUIDATION" ||
        !["CLOSE_ONLY", "ADVERSE_EXTREME"].includes(
          normalized.mark_price_sampling,
        ) ||
        !normalized.maintenance_schedule_version ||
        !normalized.instrument ||
        ![normalized.sequence, normalized.timestamp].every(
          Number.isFinite,
        ) ||
        normalized.sequence !== normalized.snapshot.sequence ||
        normalized.timestamp !== normalized.snapshot.timestamp ||
        normalized.instrument !== normalized.snapshot.instrument ||
        normalized.bankrupt !== normalized.snapshot.bankrupt ||
        !normalized.snapshot.liquidation_triggered
      ) {
        throw new Error(`第 ${index + 1} 条账户事件无效`);
      }
      return normalized;
    });
  }

  function normalizeFundingEvents(raw) {
    return (
      Array.isArray(raw.funding_events) ? raw.funding_events : []
    ).map((event, index) => {
      const normalized = {
        event_type: String(event.event_type || "").toUpperCase(),
        settlement_id: String(event.settlement_id || ""),
        sequence: Number(event.sequence),
        timestamp: Number(event.timestamp),
        date: String(event.date || ""),
        instrument: String(
          event.instrument || raw.manifest.instrument || "",
        ),
        source: String(event.source || ""),
        funding_rate: Number(event.funding_rate),
        position_quantity: Number(event.position_quantity),
        mark_price: Number(event.mark_price),
        mark_price_source: String(event.mark_price_source || ""),
        position_notional: Number(event.position_notional),
        notional_asset: String(
          event.notional_asset || "",
        ).toUpperCase(),
        position_value: Number(event.position_value),
        settlement_asset: String(
          event.settlement_asset || "",
        ).toUpperCase(),
        wallet_delta: Number(event.wallet_delta),
      };
      if (
        normalized.event_type !== "FUNDING_SETTLEMENT" ||
        !normalized.settlement_id ||
        !normalized.instrument ||
        !normalized.source ||
        !normalized.mark_price_source ||
        !normalized.notional_asset ||
        !normalized.settlement_asset ||
        ![
          normalized.sequence,
          normalized.timestamp,
          normalized.funding_rate,
          normalized.position_quantity,
          normalized.mark_price,
          normalized.position_notional,
          normalized.position_value,
          normalized.wallet_delta,
        ].every(Number.isFinite) ||
        normalized.sequence < 0 ||
        normalized.funding_rate === 0 ||
        normalized.position_quantity === 0 ||
        normalized.mark_price <= 0 ||
        normalized.position_notional <= 0 ||
        normalized.position_value <= 0 ||
        normalized.wallet_delta === 0
      ) {
        throw new Error(`第 ${index + 1} 条资金费事件无效`);
      }
      return normalized;
    });
  }

  function normalizeRun(raw) {
    if (!raw || !raw.manifest || !Array.isArray(raw.market)) {
      throw new Error("缺少 manifest 或 market 数组");
    }
    if (!raw.market.length) {
      throw new Error("market 数组为空");
    }
    const schemaVersion = Number(raw.schema_version ?? 1);
    if (![1, 2].includes(schemaVersion)) {
      throw new Error(`不支持 schema v${raw.schema_version}`);
    }
    const market = normalizeMarket(raw);
    const orders = normalizeOrders(raw);
    const runStatus = normalizeRunStatus(raw);
    const margin = normalizeMargin(raw);
    const accountEvents = normalizeAccountEvents(raw);
    const fundingEvents = normalizeFundingEvents(raw);
    if (
      runStatus.liquidated &&
      (!margin.length ||
        !accountEvents.length ||
        accountEvents.at(-1).sequence !==
          runStatus.termination_sequence ||
        margin.at(-1).sequence !== runStatus.termination_sequence)
    ) {
      throw new Error("强平 run 缺少终止保证金快照或账户事件");
    }
    return {
      schema_version: schemaVersion,
      manifest: raw.manifest,
      run_status: runStatus,
      market,
      orders,
      intents: normalizeIntents(raw, schemaVersion, orders),
      instructions: normalizeInstructions(raw, schemaVersion),
      fills: normalizeFills(raw, schemaVersion),
      equity: normalizeEquity(raw),
      margin,
      account_events: accountEvents,
      funding_events: fundingEvents,
      summary: raw.summary || {},
    };
  }

  return {normalizeRun};
});
