#!/usr/bin/env python3
"""
Daily JobTread denials/approvals scrub for price point management.

Pulls yesterday's closed customerOrder documents (status approved/denied)
directly from the JobTread Pave API, aggregates by Job Type (price point)
and by payer Program, and writes a static HTML report to docs/index.html.

Required environment variables:
  JOBTREAD_GRANT_KEY       - JobTread API grant key (Settings > API in JobTread)

Optional environment variables (defaults match Phoenix Pro Management):
  JOBTREAD_ORG_ID          - JobTread organization id
  JOB_TYPE_FIELD_ID        - custom field id for "Job Type" on jobs
  PROGRAM_FIELD_NAME        - name of the customer custom field holding payer program (default "Program")
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

DOCS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs")
HISTORY_PATH = os.path.join(DOCS_DIR, "history.json")
OUTPUT_PATH = os.path.join(DOCS_DIR, "index.html")

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


def fetch_documents(start_iso, end_iso):
    all_nodes = []
    page = None
    while True:
        conn_args = {
            "where": {
                "and": [
                    ["type", "customerOrder"],
                    ["status", "in", ["approved", "denied"]],
                    ["closedAt", ">=", start_iso],
                    ["closedAt", "<", end_iso],
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
                        "closedAt": {},
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
            "closedAt": n["closedAt"],
            "jobType": job_type,
            "program": program,
        })
    return rows


def aggregate(rows, key_fn):
    groups = {}
    for r in rows:
        key = key_fn(r)
        g = groups.setdefault(key, {"key": key, "approved": 0, "denied": 0, "approvedAmt": 0.0, "deniedAmt": 0.0})
        if r["status"] == "approved":
            g["approved"] += 1
            g["approvedAmt"] += r["price"]
        else:
            g["denied"] += 1
            g["deniedAmt"] += r["price"]
    out = []
    for g in groups.values():
        total = g["approved"] + g["denied"]
        g["total"] = total
        g["denialRate"] = (g["denied"] / total) if total else 0
        g["totalAmt"] = g["approvedAmt"] + g["deniedAmt"]
        out.append(g)
    out.sort(key=lambda g: (-g["denialRate"], -g["total"]))
    return out


def money(n):
    return "${:,.0f}".format(n)


def risk_badge(rate, total):
    if total < 2:
        return '<span class="badge watch">N/A</span>'
    if rate >= 0.4:
        return '<span class="badge risk">At risk</span>'
    if rate >= 0.2:
        return '<span class="badge watch">Watch</span>'
    return '<span class="badge solid">Solid</span>'


def render_group_table(groups, label_header):
    if not groups:
        return f'<table><thead><tr><th>{label_header}</th></tr></thead><tbody><tr><td>No data</td></tr></tbody></table>'
    rows_html = []
    for g in groups:
        pct = round(g["denialRate"] * 100)
        rows_html.append(f"""<tr>
            <td>{g['key']}</td>
            <td class="num">{g['total']}</td>
            <td class="num">{g['approved']}</td>
            <td class="num">{g['denied']}</td>
            <td class="num">{money(g['deniedAmt'])}</td>
            <td><div class="gauge-row"><div class="gauge"><div class="gauge-fill" style="width:{pct}%"></div></div><div class="pct">{pct}%</div></div></td>
            <td>{risk_badge(g['denialRate'], g['total'])}</td>
        </tr>""")
    return f"""<table>
        <thead><tr><th>{label_header}</th><th class="num">Total</th><th class="num">Approved</th><th class="num">Denied</th><th class="num">Denied $</th><th>Denial Rate</th><th>Status</th></tr></thead>
        <tbody>{''.join(rows_html)}</tbody>
    </table>"""


def render_denials(rows):
    denials = sorted([r for r in rows if r["status"] == "denied"], key=lambda r: -r["price"])
    if not denials:
        return '<div class="empty">No denials in this window.</div>'
    cards = []
    for d in denials:
        cards.append(f"""<div class="denial-card">
            <div class="denial-main"><b>{d['jobName']}</b> — {d['jobType']} <span class="denial-meta">· {d['program']} · {d['fullName']}</span></div>
            <div class="denial-price">{money(d['price'])}</div>
        </div>""")
    return ''.join(cards)


def load_history():
    if os.path.exists(HISTORY_PATH):
        with open(HISTORY_PATH) as f:
            return json.load(f)
    return []


def save_history(history):
    with open(HISTORY_PATH, "w") as f:
        json.dump(history[-30:], f, indent=2)


def render_history(history):
    if not history:
        return '<div class="empty">No history yet.</div>'
    bars = []
    for e in history[-14:]:
        h = max(4, round(e["denialRate"] * 60))
        hi = "hi" if e["denialRate"] >= 0.3 else ""
        label = e["date"][5:]
        bars.append(f"""<div style="flex:1;text-align:center;">
            <div class="history" style="height:60px;"><div class="history-bar {hi}" style="height:{h}px;width:100%;" title="{label}: {round(e['denialRate']*100)}% denied"></div></div>
            <div class="history-label">{label}</div>
        </div>""")
    return f'<div style="display:flex;gap:6px;align-items:flex-end;background:#fff;border:1.5px solid var(--steel);border-radius:4px;padding:14px;">{"".join(bars)}</div>'


def main():
    now = datetime.datetime.now(datetime.timezone.utc)
    end = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - datetime.timedelta(days=1)
    start_iso, end_iso = start.isoformat(), end.isoformat()

    nodes = fetch_documents(start_iso, end_iso)
    rows = normalize(nodes)

    approved = [r for r in rows if r["status"] == "approved"]
    denied = [r for r in rows if r["status"] == "denied"]
    approved_amt = sum(r["price"] for r in approved)
    denied_amt = sum(r["price"] for r in denied)
    denial_rate = (len(denied) / len(rows)) if rows else 0

    by_job_type = aggregate(rows, lambda r: r["jobType"])
    by_program = aggregate(rows, lambda r: r["program"])

    history = load_history()
    history.append({
        "date": start.strftime("%Y-%m-%d"),
        "total": len(rows),
        "approved": len(approved),
        "denied": len(denied),
        "denialRate": denial_rate,
    })
    save_history(history)

    generated_at = now.strftime("%Y-%m-%d %H:%M UTC")
    report_date = start.strftime("%A, %B %d %Y")

    html = HTML_TEMPLATE.format(
        report_date=report_date,
        generated_at=generated_at,
        approved_count=len(approved),
        approved_amt=money(approved_amt),
        denied_count=len(denied),
        denied_amt=money(denied_amt),
        denial_rate_pct=round(denial_rate * 100),
        total_count=len(rows),
        job_type_table=render_group_table(by_job_type, "Job Type"),
        program_table=render_group_table(by_program, "Program"),
        denials_html=render_denials(rows),
        denial_count=len(denied),
        history_html=render_history(history),
    )

    os.makedirs(DOCS_DIR, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        f.write(html)

    print(f"Wrote report for {report_date}: {len(rows)} records, {len(denied)} denied ({round(denial_rate*100)}%).")


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Daily Scrub — {report_date}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Oswald:wght@500;600;700&family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">
<style>
  :root{{
    --paper:#F2EFE8; --steel:#1E2630; --steel-2:#2B3542; --navy:#16233B;
    --safety:#E85D04; --ok:#2F7A4D; --ok-bg:#E4F1E8; --bad:#B3261E; --bad-bg:#FBE6E4;
    --warn:#B7791F; --warn-bg:#FBF0DD; --line:#D8D2C4; --muted:#6B6459;
  }}
  *{{box-sizing:border-box;}}
  body{{margin:0;background:var(--paper);color:var(--steel);font-family:'Inter',sans-serif;padding:28px 20px 60px;}}
  .wrap{{max-width:1100px;margin:0 auto;}}
  header{{display:flex;justify-content:space-between;align-items:flex-end;flex-wrap:wrap;gap:16px;border-bottom:3px solid var(--steel);padding-bottom:16px;margin-bottom:22px;}}
  .brand-eyebrow{{font-family:'JetBrains Mono',monospace;font-size:11px;letter-spacing:2px;text-transform:uppercase;color:var(--safety);font-weight:700;margin-bottom:4px;}}
  h1{{font-family:'Oswald',sans-serif;font-size:30px;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;margin:0;}}
  .sub{{color:var(--muted);font-size:13.5px;margin-top:4px;}}
  .kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:18px 0 26px;}}
  .kpi{{background:#fff;border:1.5px solid var(--steel);border-radius:4px;padding:14px 16px;position:relative;overflow:hidden;}}
  .kpi::before{{content:'';position:absolute;top:0;left:0;bottom:0;width:5px;background:var(--safety);}}
  .kpi.bad::before{{background:var(--bad);}} .kpi.ok::before{{background:var(--ok);}}
  .kpi-label{{font-family:'JetBrains Mono',monospace;font-size:10.5px;text-transform:uppercase;letter-spacing:1px;color:var(--muted);}}
  .kpi-value{{font-family:'Oswald',sans-serif;font-size:28px;font-weight:700;margin-top:4px;}}
  .kpi-sub{{font-size:11.5px;color:var(--muted);margin-top:2px;}}
  h2{{font-family:'Oswald',sans-serif;font-size:16px;text-transform:uppercase;letter-spacing:1px;border-bottom:2px solid var(--steel);padding-bottom:6px;margin:32px 0 12px;display:flex;justify-content:space-between;align-items:baseline;}}
  h2 span.hint{{font-family:'JetBrains Mono',monospace;font-size:10.5px;color:var(--muted);text-transform:none;letter-spacing:0;}}
  table{{width:100%;border-collapse:collapse;background:#fff;}}
  th{{text-align:left;font-family:'JetBrains Mono',monospace;font-size:10.5px;text-transform:uppercase;letter-spacing:0.5px;color:var(--muted);padding:8px 10px;border-bottom:2px solid var(--steel);white-space:nowrap;}}
  td{{padding:9px 10px;border-bottom:1px solid var(--line);font-size:13px;vertical-align:middle;}}
  tr:hover td{{background:#FAF8F3;}}
  .num{{font-family:'JetBrains Mono',monospace;text-align:right;}}
  .gauge-row{{display:flex;align-items:center;gap:8px;}}
  .gauge{{flex:1;height:8px;background:var(--ok-bg);border-radius:4px;overflow:hidden;min-width:70px;}}
  .gauge-fill{{height:100%;background:var(--bad);}}
  .pct{{font-family:'JetBrains Mono',monospace;font-size:12px;width:42px;text-align:right;}}
  .badge{{font-family:'JetBrains Mono',monospace;font-size:10px;text-transform:uppercase;letter-spacing:0.5px;padding:2px 7px;border-radius:3px;font-weight:700;}}
  .badge.risk{{background:var(--bad-bg);color:var(--bad);}} .badge.watch{{background:var(--warn-bg);color:var(--warn);}} .badge.solid{{background:var(--ok-bg);color:var(--ok);}}
  .empty{{background:#fff;border:1.5px dashed var(--line);border-radius:4px;padding:30px;text-align:center;color:var(--muted);font-size:13.5px;}}
  .denials-list{{display:flex;flex-direction:column;gap:8px;}}
  .denial-card{{background:#fff;border:1.5px solid var(--line);border-left:4px solid var(--bad);border-radius:3px;padding:10px 14px;display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;}}
  .denial-main{{font-size:13.5px;}} .denial-main b{{font-weight:600;}}
  .denial-meta{{font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--muted);}}
  .denial-price{{font-family:'JetBrains Mono',monospace;font-weight:700;font-size:14px;color:var(--bad);}}
  footer{{margin-top:34px;font-size:11.5px;color:var(--muted);border-top:1px solid var(--line);padding-top:12px;}}
  .history{{display:flex;gap:4px;align-items:flex-end;height:60px;margin-top:10px;}}
  .history-bar{{flex:1;background:var(--steel-2);border-radius:2px 2px 0 0;position:relative;min-height:2px;}}
  .history-bar.hi{{background:var(--bad);}}
  .history-label{{font-family:'JetBrains Mono',monospace;font-size:9px;color:var(--muted);text-align:center;margin-top:3px;}}
  @media(max-width:720px){{ .kpis{{grid-template-columns:repeat(2,1fr);}} table{{font-size:11.5px;}} }}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div>
      <div class="brand-eyebrow">Phoenix Pro · Price Point Management</div>
      <h1>Daily Scrub</h1>
      <div class="sub">{report_date} · generated {generated_at} · JobTread proposal approvals &amp; denials</div>
    </div>
  </header>

  <div class="kpis">
    <div class="kpi ok"><div class="kpi-label">Approved</div><div class="kpi-value">{approved_count}</div><div class="kpi-sub">{approved_amt}</div></div>
    <div class="kpi bad"><div class="kpi-label">Denied</div><div class="kpi-value">{denied_count}</div><div class="kpi-sub">{denied_amt}</div></div>
    <div class="kpi"><div class="kpi-label">Denial Rate</div><div class="kpi-value">{denial_rate_pct}%</div><div class="kpi-sub">of {total_count} closed</div></div>
    <div class="kpi"><div class="kpi-label">$ At Risk</div><div class="kpi-value">{denied_amt}</div><div class="kpi-sub">denied, resubmit candidates</div></div>
  </div>

  <h2>By Job Type (Price Point) <span class="hint">sorted by denial rate</span></h2>
  {job_type_table}

  <h2>By Payer Program</h2>
  {program_table}

  <h2>Denials Needing Follow-Up <span class="hint">({denial_count})</span></h2>
  <div class="denials-list">{denials_html}</div>

  <h2 style="margin-top:40px;">Trend — Denial Rate by Day</h2>
  {history_html}

  <footer>Auto-generated daily from JobTread. This page is overwritten each morning — bookmark it, don't screenshot it.</footer>
</div>
</body>
</html>
"""

if __name__ == "__main__":
    main()
