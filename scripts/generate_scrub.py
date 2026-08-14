#!/usr/bin/env python3
"""
Daily JobTread denials/approvals scrub for price point management.

Pulls the last ROLLING_WINDOW_DAYS of closed customerOrder documents
(status approved/denied) directly from the JobTread Pave API and writes
the raw, normalized rows to docs/data.json. The page itself (docs/index.html)
is a static app that loads data.json and lets the user pick any date range
client-side — no live API calls happen in the browser.

Required environment variables:
  JOBTREAD_GRANT_KEY       - JobTread API grant key (Settings > API in JobTread)

Optional environment variables (defaults match Phoenix Pro Management):
  JOBTREAD_ORG_ID          - JobTread organization id
  JOB_TYPE_FIELD_ID        - custom field id for "Job Type" on jobs
  PROGRAM_FIELD_NAME        - name of the customer custom field holding payer program (default "Program")
  ROLLING_WINDOW_DAYS      - how many days back to pull each run (default 90)
"""
import os
import json
import datetime
import urllib.request
import urllib.error

JOBTREAD_URL = "https://api.jobtread.com/pave"
GRANT_KEY = os.environ.get("JOBTREAD_GRANT_KEY")
ORG_ID = os.environ.get("JOBTREAD_ORG_ID", "22P479WZqKdw")
JOB_TYPE_FIELD_ID = os.environ.get("JOB_TYPE_FIELD_ID", "22P47EsnYCmj")
PROGRAM_FIELD_NAME = os.environ.get("PROGRAM_FIELD_NAME", "Program")
ROLLING_WINDOW_DAYS = int(os.environ.get("ROLLING_WINDOW_DAYS", "90"))

DOCS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs")
DATA_PATH = os.path.join(DOCS_DIR, "data.json")

if not GRANT_KEY:
    raise SystemExit("Missing JOBTREAD_GRANT_KEY environment variable / secret.")


def jt_query(query_body, grant_key):
    payload = {"query": {"$": {"grantKey": grant_key}, **query_body}}
    req = urllib.request.Request(
        JOBTREAD_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise SystemExit(f"JobTread API error {e.code}: {e.read().decode('utf-8', 'ignore')}")


def fetch_documents(start_date, end_date):
    """start_date/end_date are 'YYYY-MM-DD' strings, filtering on issueDate
    (the submission date) rather than closedAt, so pending proposals that
    haven't been decided yet are included too."""
    all_nodes = []
    page = None
    while True:
        conn_args = {
            "where": {
                "and": [
                    ["type", "customerOrder"],
                    ["status", "in", ["pending", "approved", "denied"]],
                    ["issueDate", ">=", start_date],
                    ["issueDate", "<", end_date],
                ]
            },
            "size": 100,
        }
        if page:
            conn_args["page"] = page

        body = {
            "organization": {
                "$": {"id": ORG_ID},
                "documents": {
                    "$": conn_args,
                    "nextPage": {},
                    "nodes": {
                        "id": {},
                        "fullName": {},
                        "status": {},
                        "price": {},
                        "issueDate": {},
                        "closedAt": {},
                        "closeMessage": {},
                        "job": {
                            "id": {},
                            "name": {},
                            "customFieldValues": {
                                "$": {"where": [["customField", "id"], JOB_TYPE_FIELD_ID]},
                                "nodes": {"value": {}},
                            },
                        },
                        "account": {
                            "id": {},
                            "name": {},
                            "customFieldValues": {
                                "$": {"where": [["customField", "name"], PROGRAM_FIELD_NAME]},
                                "nodes": {"value": {}},
                            },
                        },
                    },
                },
            }
        }
        result = jt_query(body, GRANT_KEY)
        docs = result["organization"]["documents"]
        all_nodes.extend(docs["nodes"])
        page = docs.get("nextPage")
        if not page:
            break
    return all_nodes


def normalize(nodes):
    rows = []
    for n in nodes:
        job_type_vals = [v["value"] for v in n["job"]["customFieldValues"]["nodes"]]
        job_type = " / ".join(job_type_vals) if job_type_vals else "Unspecified"
        program_vals = [v["value"] for v in n["account"]["customFieldValues"]["nodes"]]
        program = program_vals[0] if program_vals else "Unspecified"
        rows.append({
            "id": n["id"],
            "fullName": n["fullName"],
            "jobName": n["job"]["name"],
            "status": n["status"],
            "price": n["price"],
            "issueDate": n.get("issueDate"),
            "closedAt": n.get("closedAt"),
            "jobType": job_type,
            "program": program,
            "reason": n.get("closeMessage") or "",
        })
    return rows


def money(n):
    return "${:,.0f}".format(n)


def main():
    now = datetime.datetime.now(datetime.timezone.utc)
    end = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - datetime.timedelta(days=ROLLING_WINDOW_DAYS)
    start_date, end_date = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")

    nodes = fetch_documents(start_date, end_date)
    rows = normalize(nodes)

    payload = {
        "generatedAt": now.isoformat(),
        "windowStart": start_date,
        "windowEnd": end_date,
        "rows": rows,
    }

    os.makedirs(DOCS_DIR, exist_ok=True)
    with open(DATA_PATH, "w") as f:
        json.dump(payload, f, indent=2)

    approved = [r for r in rows if r["status"] == "approved"]
    denied = [r for r in rows if r["status"] == "denied"]
    pending = [r for r in rows if r["status"] == "pending"]
    print(f"Wrote {len(rows)} records (submitted) — {len(approved)} approved, "
          f"{len(denied)} denied, {len(pending)} pending — covering "
          f"{start_date} to {end_date} to {DATA_PATH}")


if __name__ == "__main__":
    main()
