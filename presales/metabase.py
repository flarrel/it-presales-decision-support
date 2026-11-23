"""Provision and check the service desk dashboard on a local Metabase instance."""

import argparse
from http.client import HTTPException
import json
import os
from pathlib import Path
import secrets
import sqlite3
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "runtime" / "metabase"
DATABASE = "Service desk reporting"
DASHBOARD = "Service desk demand and staffing"
EMAIL = "operator@localhost.test"
QUESTIONS = (
    (
        "Weekly ticket demand and SLA breaches",
        "line",
        """SELECT week, SUM(tickets) AS tickets, SUM(sla_breaches) AS sla_breaches
           FROM reporting_weekly WHERE 1 = 1 [[AND queue = {{queue}}]]
           GROUP BY week ORDER BY week""",
        {"graph.dimensions": ["week"], "graph.metrics": ["tickets", "sla_breaches"]},
    ),
    (
        "Staffed hours and predicted workload",
        "bar",
        """SELECT scenario, SUM(scheduled_hours) AS scheduled_hours,
                  SUM(workload_hours) AS workload_hours
           FROM reporting_staffing WHERE 1 = 1 [[AND queue = {{queue}}]]
           GROUP BY scenario ORDER BY scenario""",
        {"graph.dimensions": ["scenario"], "graph.metrics": ["scheduled_hours", "workload_hours"]},
    ),
    (
        "Handling-time sensitivity",
        "table",
        """SELECT scenario, assumption, SUM(shortfall_hours) AS shortfall_hours,
                  SUM(CASE WHEN covered THEN 1 ELSE 0 END) AS covered_queue_weeks,
                  COUNT(*) AS queue_weeks
           FROM reporting_sensitivity WHERE 1 = 1 [[AND queue = {{queue}}]]
           GROUP BY scenario, assumption
           ORDER BY scenario, CASE assumption WHEN 'low' THEN 1 WHEN 'typical' THEN 2 ELSE 3 END""",
        {},
    ),
)


class Metabase:
    def __init__(self, port):
        self.base = f"http://127.0.0.1:{port}"
        self.session = None

    def request(self, method, path, body=None):
        headers = {"Accept": "application/json"}
        payload = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            payload = json.dumps(body).encode()
        if self.session:
            headers["X-Metabase-Session"] = self.session
        request = Request(f"{self.base}/api{path}", payload, headers, method=method)
        try:
            with urlopen(request, timeout=45) as response:
                raw = response.read()
                return json.loads(raw) if raw else None
        except HTTPError as error:
            message = error.read(2500).decode(errors="replace")
            raise ValueError(f"Metabase {method} {path} returned {error.code}: {message}") from error

    def login(self):
        properties = self.request("GET", "/session/properties")
        token = properties.get("setup-token")
        credentials = STATE / "operator.json"
        if token and not credentials.exists():
            STATE.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(credentials, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump({"email": EMAIL, "password": secrets.token_urlsafe(28) + "Aa0!"}, stream)
        if not credentials.is_file():
            raise ValueError("This Metabase instance is already configured without project credentials.")
        operator = json.loads(credentials.read_text(encoding="utf-8"))
        login = {"username": operator["email"], "password": operator["password"]}
        try:
            self.session = self.request("POST", "/session", login)["id"]
        except ValueError:
            if not token:
                raise
            self.request("POST", "/setup", {
                "token": token,
                "user": operator | {"first_name": "Local", "last_name": "Operator"},
                "prefs": {"site_name": "Service Desk Analytics", "site_locale": "en"},
            })
            self.session = self.request("POST", "/session", login)["id"]


def configure(api, database):
    databases = api.request("GET", "/database")["data"]
    existing = next((item for item in databases if item["name"] == DATABASE), None)
    if existing:
        source = api.request("GET", f"/database/{existing['id']}")
        if source["details"]["db"] != str(database):
            raise ValueError("The saved Metabase connection belongs to a different SQLite file.")
        return existing["id"]
    return api.request("POST", "/database", {
        "name": DATABASE, "engine": "sqlite", "details": {"db": str(database)},
        "is_full_sync": True, "is_on_demand": False, "connection_source": "admin",
    })["id"]


def setup_question(api, database_id, question, cards):
    name, display, query, settings = question
    definition = {
        "name": name,
        "display": display,
        "visualization_settings": settings,
        "dataset_query": {
            "database": database_id, "type": "native",
            "native": {
                "query": query,
                "template-tags": {
                    "queue": {"id": "service-desk-queue", "name": "queue",
                              "display-name": "Queue", "type": "text", "required": False},
                },
            },
        },
    }
    existing = next((item for item in cards if item["name"] == name), None)
    if existing:
        return api.request("PUT", f"/card/{existing['id']}", definition)["id"]
    return api.request("POST", "/card", definition)["id"]


def setup_dashboard(api, card_ids, queues):
    id_ = "support-queue"
    parameter = {
        "id": id_, "name": "Queue", "slug": "queue", "type": "string/=",
        "sectionId": "string", "values_source_type": "static-list",
        "values_source_config": {"values": [[queue] for queue in queues]},
    }
    dashboard = next((item for item in api.request("GET", "/dashboard")
                      if item["name"] == DASHBOARD), None)
    if dashboard is None:
        dashboard = api.request("POST", "/dashboard", {"name": DASHBOARD})
    dashboard_id = dashboard["id"]
    previous = api.request("GET", f"/dashboard/{dashboard_id}")["dashcards"]
    layout = []
    for index, card_id in enumerate(card_ids):
        placement = next((item for item in previous if item["card_id"] == card_id), None)
        layout.append({
            "id": placement["id"] if placement else -(index + 1),
            "card_id": card_id,
            "row": 0 if index < 2 else 6, "col": 9 if index == 1 else 0,
            "size_x": 9 if index < 2 else 18, "size_y": 6,
            "parameter_mappings": [
                {"parameter_id": id_, "card_id": card_id,
                 "target": ["variable", ["template-tag", "queue"]]},
            ],
        })
    api.request("PUT", f"/dashboard/{dashboard_id}", {
        "dashcards": layout, "parameters": [parameter],
        "description": "Compare weekly arrivals and SLA breaches with staffing options and handling-time assumptions.",
    })
    return dashboard_id


def verify(api, database, dashboard_id, ids, queues):
    dashboard = api.request("GET", f"/dashboard/{dashboard_id}")
    placements = {row["card_id"]: row["id"] for row in dashboard["dashcards"]}
    checks = [None, *queues]
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
        for queue in checks:
            params = []
            where = ""
            bindings = {}
            if queue is not None:
                params = [{
                    "id": "support-queue", "type": "string/=", "value": [queue],
                }]
                where = "AND queue = :queue"
                bindings = {"queue": queue}
            for card_id, question in zip(ids, QUESTIONS, strict=True):
                expected_sql = question[2].replace("[[AND queue = {{queue}}]]", where)
                expected = connection.execute(expected_sql, bindings).fetchall()
                dashcard = placements[card_id]
                endpoint = f"/dashboard/{dashboard_id}/dashcard/{dashcard}/card/{card_id}/query"
                response = api.request(
                    "POST", endpoint, {"ignore_cache": True, "parameters": params},
                )
                data = response["data"]
                columns = [column["name"] for column in data["cols"]]
                actual = [
                    [value.removesuffix("T00:00:00Z") if column == "week" else value
                     for column, value in zip(columns, row, strict=True)]
                    for row in data["rows"]
                ]
                if actual != [list(row) for row in expected]:
                    raise ValueError(f"Metabase query does not match SQLite: {question[0]}")
    print("Metabase questions matched SQLite with and without the queue filter.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=3000)
    parser.add_argument("--database", type=Path, default=ROOT / "run" / "service-desk.sqlite")
    args = parser.parse_args()
    database = args.database.resolve()
    if not database.is_file():
        parser.error("Generate the reporting database before configuring Metabase.")
    api = Metabase(args.port)
    try:
        api.login()
        database_id = configure(api, database)
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
            queues = [row[0] for row in connection.execute(
                "SELECT DISTINCT queue FROM reporting_weekly ORDER BY queue"
            )]
        cards = api.request("GET", "/card")
        ids = [setup_question(api, database_id, question, cards) for question in QUESTIONS]
        dashboard = setup_dashboard(api, ids, queues)
        verify(api, database, dashboard, ids, queues)
    except (HTTPException, OSError, ValueError, KeyError, sqlite3.Error) as error:
        parser.exit(1, f"{error}\n")
    print(f"Dashboard: {api.base}/dashboard/{dashboard}")
    print(f"Local sign-in details: {STATE / 'operator.json'}")


if __name__ == "__main__":
    main()
