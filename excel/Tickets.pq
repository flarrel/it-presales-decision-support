let
    CsvRows = Csv.Document(
        File.Contents(TicketCsvPath),
        [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]
    ),
    Headers = Table.PromoteHeaders(CsvRows, [PromoteAllScalars = true]),
    ExportedFlag = if Table.HasColumns(Headers, {"sla_breach"}) then "sla_breach" else "sla_breached",
    Fields = Table.RenameColumns(
        Table.SelectColumns(Headers, {
            "ticket_id", "queue", "week", "opened_at", "resolved_at", "sla_minutes", "handling_minutes", ExportedFlag
        }),
        {{"week", "sql_week"}, {ExportedFlag, "sql_ticket_sla_breach"}}
    ),
    Numbers = Table.TransformColumns(Fields, {
        {"sla_minutes", each Number.FromText(_, "en-US"), Int64.Type},
        {"handling_minutes", each if _ = null or Text.Trim(_) = "" then 0 else Number.FromText(_, "en-US"), type number},
        {"sql_ticket_sla_breach", each
            let Flag = if _ = null then "" else Text.Lower(Text.Trim(_))
            in
                if List.Contains({"1", "true"}, Flag) then 1
                else if List.Contains({"0", "false"}, Flag) then 0
                else if Flag = "" then null
                else error "Expected the exported SLA flag to be blank, 0, 1, true, or false.",
            Int64.Type}
    }),
    Timestamp = (Value as text) as record =>
        let
            Whole = DateTime.FromText(Text.Start(Value, 19), [Format = "yyyy-MM-dd'T'HH:mm:ss", Culture = "en-US"]),
            Fraction = if Text.Length(Value) = 19 then 0
                else Number.FromText(Text.PadEnd(Text.Range(Value, 20), 6, "0"), "en-US")
        in
            [whole = Whole, microseconds = Fraction],
    Opened = Table.AddColumn(Numbers, "opened_instant", each Timestamp([opened_at]), type record),
    Resolved = Table.AddColumn(Opened, "resolved_instant", each
        if [resolved_at] = null or Text.Trim([resolved_at]) = "" then null
        else Timestamp([resolved_at]),
        type nullable record
    ),
    Flagged = Table.AddColumn(Resolved,
        "resolved_ticket",
        each if [resolved_instant] = null then 0 else 1,
        Int64.Type
    ),
    WithWeek = Table.AddColumn(Flagged, "week", each
        Date.ToText(Date.StartOfWeek(Date.From([opened_instant][whole]), Day.Monday), "yyyy-MM-dd", "en-US"),
        type text
    ),
    WithBreach = Table.AddColumn(WithWeek, "sla_breach", each
        if [resolved_ticket] = 0 then 0
        else
            let
                Start = [opened_instant],
                End = [resolved_instant],
                Seconds = Number.Round(Duration.TotalSeconds(End[whole] - Start[whole])),
                Elapsed = Seconds * 1000000 + End[microseconds] - Start[microseconds]
            in
                if Elapsed > [sla_minutes] * 60000000 then 1 else 0,
        Int64.Type
    ),
    Checked = Table.AddColumn(WithBreach, "row_mismatch", each
        let
            Exported = if [sql_ticket_sla_breach] = null and [resolved_ticket] = 0
                then 0 else [sql_ticket_sla_breach]
        in
            if [week] <> [sql_week] or [sla_breach] <> Exported then 1 else 0,
        Int64.Type
    ),
    WithKey = Table.AddColumn(Checked, "queue_key", each
        Binary.ToText(Text.ToBinary([queue], TextEncoding.Utf8), BinaryEncoding.Hex),
        type text
    )
in
    Table.RemoveColumns(WithKey, {"opened_instant", "resolved_instant"})
