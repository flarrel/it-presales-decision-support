let
    FromTickets = Table.Group(Tickets, {"queue", "week"}, {
        {"excel_tickets", each Table.RowCount(_), Int64.Type},
        {"excel_resolved_tickets", each List.Sum([resolved_ticket]), Int64.Type},
        {"excel_sla_breaches", each List.Sum([sla_breach]), Int64.Type},
        {"excel_handling_minutes", each List.Sum([handling_minutes]), type number},
        {"ticket_mismatches", each List.Sum([row_mismatch]), Int64.Type}
    }),
    Keys = Table.Distinct(Table.Combine({
        Table.SelectColumns(FromTickets, {"queue", "week"}),
        Table.SelectColumns(SqlWeekly, {"queue", "week"})
    })),
    TicketJoin = Table.NestedJoin(Keys, {"queue", "week"}, FromTickets, {"queue", "week"}, "detail", JoinKind.LeftOuter),
    SqlJoin = Table.NestedJoin(TicketJoin, {"queue", "week"}, SqlWeekly, {"queue", "week"}, "sql", JoinKind.LeftOuter),
    Checked = Table.AddColumn(SqlJoin, "status", each
        let
            DetailRows = [detail],
            SqlRows = [sql]
        in
            if Table.IsEmpty(DetailRows) then "MISSING TICKETS"
            else if Table.IsEmpty(SqlRows) then "MISSING SQL GROUP"
            else if Table.RowCount(SqlRows) <> 1 then "DUPLICATE SQL GROUP"
            else
                let
                    Detail = DetailRows{0},
                    Reference = SqlRows{0},
                    SqlMinutes = if Reference[total_handling_minutes] = null then 0 else Reference[total_handling_minutes]
                in
                    if Detail[ticket_mismatches] > 0 then "TICKET FLAG OR WEEK MISMATCH"
                    else if Detail[excel_tickets] = Reference[tickets]
                        and Detail[excel_resolved_tickets] = Reference[resolved_tickets]
                        and Detail[excel_sla_breaches] = Reference[sla_breaches]
                        and Number.Abs(Detail[excel_handling_minutes] - SqlMinutes) < 0.000001
                    then "PASS"
                    else "MISMATCH",
        type text
    ),
    Detail = Table.ExpandTableColumn(Checked, "detail", {
        "excel_tickets", "excel_resolved_tickets", "excel_sla_breaches", "excel_handling_minutes", "ticket_mismatches"
    }),
    Sql = Table.ExpandTableColumn(Detail, "sql", {
        "tickets", "resolved_tickets", "sla_breaches", "total_handling_minutes"
    }, {"sql_tickets", "sql_resolved_tickets", "sql_sla_breaches", "sql_handling_minutes"}),
    WithKey = Table.AddColumn(Sql, "queue_key", each
        Binary.ToText(Text.ToBinary([queue], TextEncoding.Utf8), BinaryEncoding.Hex),
        type text
    ),
    Ordered = Table.Sort(WithKey, {{"week", Order.Ascending}, {"queue", Order.Ascending}})
in
    Ordered
