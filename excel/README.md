# Reconcile a ticket export in Microsoft Excel

Use Excel’s Power Query to calculate reporting weeks from the opening timestamps and recompute SLA breaches from elapsed time and each ticket’s SLA threshold. The reconciliation checks individual flags, aggregates the recomputed details by queue and week, and compares them to the SQLite totals. Excel with Power Query’s Advanced Editor and local CSV access is required.

1. In Excel’s Power Query Editor, create two text parameters named `TicketCsvPath` and `WeeklyCsvPath`. Set them to the absolute paths of `run/tickets.csv` and `run/weekly.csv`. If needed, define each parameter in a blank query’s Advanced Editor using `"/absolute/path/tickets.csv" meta [IsParameterQuery = true, Type = "Text", IsParameterQueryRequired = true]`, with the appropriate path.
2. Add blank queries named `Tickets`, `SqlWeekly`, and `Reconciliation`. Paste the corresponding `.pq` file into each query’s Advanced Editor. Keep the query names exactly as shown because the M code references them.
3. Refresh `Reconciliation`. Each queue/week group should have status `PASS` and matching detail and SQL metrics. To inspect a difference, filter by status and use its queue and week in the `Tickets` query.
4. Load `Tickets` and `Reconciliation` into Excel tables for a worksheet-level check. Name the first table `TicketDetail`. In the reconciliation table, add a formula column that independently totals SLA breaches. Excel installations that use a localized list separator may require switching commas.

```excel
=SUMIFS(TicketDetail[sla_breach],TicketDetail[queue_key],[@queue_key],TicketDetail[week],[@week])
```

Compare that result with `sql_sla_breaches`:

```excel
=IF([@FormulaBreaches]=[@sql_sla_breaches],"PASS","MISMATCH")
```

Replace `FormulaBreaches` with the name you gave the first formula column. To verify that the expected ticket count and breach count use the same grouping, count the matching ticket rows:

```excel
=COUNTIFS(TicketDetail[queue_key],[@queue_key],TicketDetail[week],[@week])
```

## Fixed check

Point the path parameters at the two CSV files in `excel/fixture`. Expect four `PASS` group rows. For Network during the week of January 6, 2025, expect **3 tickets, 2 resolved tickets, 1 breach, and 55 handling minutes**. Across all groups, expect **6 tickets, 5 resolved tickets, 3 breaches, and 185 handling minutes**. Ticket `EX-002` breaches its 60-minute SLA because it took 90 minutes to resolve and required 35 minutes of handling. Ticket `EX-004` meets its SLA at exactly 30 minutes. Ticket `EX-005` breaches by one microsecond.

Temporarily filter `EX-002` out of the `Tickets` query and refresh. The Network row for January 6 should show `MISMATCH`. Restore the filter and confirm it returns to `PASS`.
