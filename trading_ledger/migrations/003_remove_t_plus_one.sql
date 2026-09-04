UPDATE position_lots
SET available_date = COALESCE(
    (
        SELECT substr(trade_time, 1, 10)
        FROM trade_records
        WHERE trade_records.trade_id = position_lots.source_trade_id
    ),
    substr(created_at, 1, 10)
);

UPDATE positions
SET available_quantity = quantity;
