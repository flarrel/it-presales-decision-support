CREATE TABLE IF NOT EXISTS tickets (
    ticket_id TEXT PRIMARY KEY,
    opened_at TEXT NOT NULL,
    resolved_at TEXT,
    queue TEXT NOT NULL,
    priority TEXT NOT NULL CHECK (priority IN ('critical', 'high', 'normal', 'low')),
    sla_minutes INTEGER NOT NULL CHECK (sla_minutes > 0),
    handling_minutes INTEGER CHECK (handling_minutes > 0),
    CHECK ((resolved_at IS NULL) = (handling_minutes IS NULL))
);

CREATE INDEX IF NOT EXISTS tickets_queue_opened ON tickets (queue, opened_at);

CREATE VIEW IF NOT EXISTS ticket_details AS
SELECT
    ticket_id,
    opened_at,
    resolved_at,
    queue,
    priority,
    sla_minutes,
    handling_minutes,
    date(substr(opened_at, 1, 10), 'weekday 0', '-6 days') AS week,
    CASE WHEN resolved_at IS NOT NULL THEN
        -- SQLite's date functions reduce fractional-second precision. Compare
        -- the whole seconds and fixed-width microseconds separately.
        (unixepoch(substr(resolved_at, 1, 19)) - unixepoch(substr(opened_at, 1, 19))) * 1000000
            + CAST(substr(resolved_at, 21, 6) AS INTEGER)
            - CAST(substr(opened_at, 21, 6) AS INTEGER) > sla_minutes * 60000000
    END AS sla_breached
FROM tickets;

CREATE VIEW IF NOT EXISTS weekly_metrics AS
SELECT
    queue,
    week,
    COUNT(*) AS tickets,
    COUNT(resolved_at) AS resolved_tickets,
    COUNT(*) - COUNT(resolved_at) AS open_tickets,
    SUM(COALESCE(sla_breached, 0)) AS sla_breaches,
    SUM(COALESCE(handling_minutes, 0)) AS total_handling_minutes
FROM ticket_details
GROUP BY queue, week;
