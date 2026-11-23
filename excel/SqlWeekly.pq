let
    CsvRows = Csv.Document(
        File.Contents(WeeklyCsvPath),
        [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]
    ),
    Headers = Table.PromoteHeaders(CsvRows, [PromoteAllScalars = true]),
    Fields = Table.SelectColumns(Headers, {
        "queue", "week", "tickets", "resolved_tickets", "sla_breaches", "total_handling_minutes"
    }),
    Counts = Table.TransformColumnTypes(Fields, {
        {"queue", type text}, {"week", type text},
        {"tickets", Int64.Type}, {"resolved_tickets", Int64.Type},
        {"sla_breaches", Int64.Type}, {"total_handling_minutes", type number}
    }, "en-US")
in
    Counts
