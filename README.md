# IT Presales Decision Support

Local reporting and staffing analysis for service-desk leads and solutions engineers. The application turns ticket history into weekly demand and SLA metrics, forecasts the next four weeks, and compares staffing options under budget and coverage constraints.

## Run

Python 3.10 or newer and base R are required. Start the simulation and browser report from the repository root:

```sh
python3 -m presales demo
```

Open [the local report](http://127.0.0.1:8765). The pipeline looks for `Rscript` on PATH or in `runtime/r/bin`. Use `--rscript /absolute/path/to/Rscript` to select another executable.

To generate the files without starting the server, use `python3 -m presales build --synthetic`. To view existing results, use `python3 -m presales serve`. Generated data, SQLite, and CSV reports live under `run/`. Shut down the server before rebuilding the report.

### Metabase

The local Metabase dashboard uses the same SQLite report and includes a queue filter, demand and SLA trends, staffing comparisons, and the R sensitivity results.

1. Generate the report with `python3 -m presales build --synthetic`.
2. Put the [Metabase OSS 58.40 JAR](https://downloads.metabase.com/v0.58.40.x/metabase.jar) in `runtime/metabase.jar` and make Java 25 available. The launcher accepts `JAVA_BIN` and `METABASE_JAR` overrides.
3. Start `scripts/run-metabase`. In another terminal, run `python3 -m presales.metabase` once Metabase is ready.
4. Open the dashboard URL printed by the setup command. The generated local credentials are kept in `runtime/metabase/operator.json`.

The setup command checks all dashboard queries against SQLite with and without the queue filter. Metabase listens on localhost, so its local application database and credentials can stay on the same machine.

### Excel reconciliation

The [Excel reconciliation workflow](excel/README.md) reprocesses the ticket export with Power Query and checks it against the SQL totals. It recalculates Monday reporting weeks and per-ticket SLA flags from timestamps, then checks aggregate ticket counts, resolutions, breaches, and handling minutes. The checked-in M queries and worksheet formulas are plain text. A fixed fixture includes an exact SLA boundary and a ticket that exceeds the threshold by one microsecond.

## Data and method

The built-in simulation uses a reproducible seed over 96 weeks and three support queues. It deliberately includes four invalid rows to exercise rejection handling. Import a different CSV from the repository root with:

```sh
python3 -m presales build --csv ./tickets.csv --scenarios ./scenarios.json
```

The source CSV fields are `ticket_id,opened_at,resolved_at,queue,priority,sla_minutes,handling_minutes`. Timestamps use ISO 8601. Naive timestamps are interpreted as UTC. Open tickets have blank resolution and handling fields. Supported priorities are `critical`, `high`, `normal`, and `low`. The reporting week starts Monday UTC. An SLA breach means a resolved ticket’s elapsed time strictly exceeds its per-ticket threshold. Breach rates use resolved tickets as the denominator.

For an imported CSV, supply a JSON list of scenarios. Each scenario includes a unique `name`, a total four-week `budget`, an `hourly_rate`, a `minimum_coverage` fraction, and maps named `baseline_hours`, `max_extra_hours`, and `minutes_per_ticket` with one value per forecasted queue. Hours in those maps apply to each week. For a single forecasted `Cloud` queue, one example is:

```json
[
  {
    "name": "Current budget",
    "budget": 2000,
    "hourly_rate": 25,
    "minimum_coverage": 0.85,
    "baseline_hours": {"Cloud": 32},
    "max_extra_hours": {"Cloud": 20},
    "minutes_per_ticket": {"Cloud": 35}
  }
]
```

The forecast compares a 52-week seasonal-naive model with a trailing four-week average. Model selection uses earlier targets and evaluation uses a separate chronological holdout. Unobserved weeks within an observed queue's history count as zero. Use complete weekly extracts. Gaps from an incomplete export can understate demand.

The staffing model first reserves the extra hours needed to meet minimum workload coverage by queue and week, then allocates remaining budget to reduce unmet workload. It reports infeasibility when a queue cap or the total budget prevents coverage. The base R step independently estimates sensitivity using the 25th, 50th, and 75th percentiles of observed handling time. It reports full coverage and shortfall under each assumption, with pooled durations marked when a queue has too little resolved-ticket history.

## Walk through a staffing decision

1. Filter to a queue, inspect a busy week, and trace the weekly totals to the underlying tickets.
2. Compare forecast errors. In the seeded simulation, the models picked on earlier data had holdout mean absolute errors of 6.94, 7.50, and 8.54 tickets for Cloud, Endpoints, and Network. The other baseline scored better on that holdout in all three queues. This supports testing both models again on more weeks before using either to staff a live queue.
3. Compare the two modeled client scenarios. The fixed budget funded 80 additional hours and reduced estimated unmet workload from 126.4 to 46.4 hours over four weeks. The higher service target failed the Endpoints capacity constraint in its final week despite a larger budget.
4. Review the R results before agreeing to a plan. Under the lower handling-time assumption, the fixed-budget plan covers all 12 queue-week combinations. At the median handling duration it has shortfalls in all 12. The plan is sensitive to handling time even though it meets the minimum coverage requirement under the base model.

These results use simulated tickets and costs. Workload coverage is a planning proxy. Actual SLA results also depend on ticket arrival patterns, triage, staffing schedules, and queue dependencies.

## Checks

```sh
python3 -m unittest discover -s tests -v
Rscript tests/staffing_sensitivity.R
```
