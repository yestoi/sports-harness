#!/usr/bin/env python3
"""Deterministic Layer 3 for verify.md: the dashboard's /api/summary checked against SQL, over ssh.

Usage (Mac, repo root):  scripts/verify_summary.py DEPLOY_SHA [--evidence PATH]
Prints one PASS/FAIL line per cross-check; exit 0 only when every check passes.
SQL runs first and the page second, so an in-flight tick can only make the page newer; an out-of-band
candidate count is re-checked once after 20 s before it is scored FAIL.
"""
import json
import subprocess
import sys
import time
from datetime import datetime, timezone

SQL = """\
select 'run_id', id from runs order by started_at desc limit 1;
select 'ws_age_s', round(extract(epoch from now()-ts)) from orderbook_events order by id desc limit 1;
select 'cand', v.name, count(s.id) from strategy_variants v
  left join signals s on s.variant_id = v.variant_id and s.decision = 'candidate' and s.replay = false
                     and s.created_at > now() - interval '24 hours'
  where v.active group by v.name order by v.name;
"""


def nas() -> tuple[str, str]:
    vals = {}
    for line in open(".env.nas"):
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            vals[k] = v
    return f"{vals['NAS_USER']}@{vals['NAS_IP']}", vals["NAS_STACK_DIR"]


def ssh(host: str, cmd: str, stdin: str | None = None) -> str:
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", host, cmd], input=stdin, capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        sys.exit(f"ssh failed ({cmd[:50]}...): {r.stderr.strip()[:200]}")
    return r.stdout


def sql(host: str, stack: str) -> dict:
    out = ssh(host, f"cd {stack} && docker compose exec -T postgres psql -U harness -d harness -At -F '|'", stdin=SQL)
    d: dict = {"cand": {}}
    for line in out.splitlines():
        parts = line.split("|")
        if parts[0] == "cand":
            d["cand"][parts[1]] = int(parts[2])
        elif len(parts) == 2:
            d[parts[0]] = float(parts[1])
    return d


def fetch(host: str) -> tuple[dict, float, float]:
    out = ssh(host, "curl -s --max-time 30 -w '\\n%{time_total}' http://127.0.0.1:8180/api/summary")
    body, secs = out.rsplit("\n", 1)
    page = float(ssh(host, "curl -s -o /dev/null --max-time 30 -w '%{time_total}' http://127.0.0.1:8180/").strip())
    return json.loads(body), float(secs), page


def candidates_ok(page: dict, db: dict) -> list[str]:
    sbv = (page.get("funnel") or {}).get("signals_by_variant") or {}
    bad = []
    for name, n in db["cand"].items():
        p = (sbv.get(name) or {}).get("candidate")
        if p is None or abs(p - n) > max(20, 0.05 * max(p, n)):
            bad.append(f"{name}: page {p} vs sql {n}")
    return bad


def main() -> int:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    deploy_sha = sys.argv[1]
    evidence = sys.argv[sys.argv.index("--evidence") + 1] if "--evidence" in sys.argv else None
    host, stack = nas()
    lines: list[str] = []
    failed = False

    def rec(name: str, ok: bool, detail: str) -> None:
        nonlocal failed
        failed |= not ok
        line = f"{'PASS' if ok else 'FAIL'} {name}: {detail}"
        lines.append(line)
        print(line)

    db = sql(host, stack)
    s, api_secs, page_secs = fetch(host)
    bad = candidates_ok(s, db)
    if bad:  # an in-flight tick: measure again from scratch
        time.sleep(20)
        db = sql(host, stack)
        s, api_secs, page_secs = fetch(host)
        bad = candidates_ok(s, db)

    build = (s.get("build") or {}).get("sha")
    rec("build", build == deploy_sha, f"page {build} vs deploy {deploy_sha}")
    errs = {k: v.get("error") for k, v in s.items() if isinstance(v, dict) and "error" in v}
    rec("sections", not errs, "no section errors" if not errs else f"section errors: {errs}")
    rec("page_time", page_secs < 10 and api_secs < 10, f"/ {page_secs:.1f}s, /api/summary {api_secs:.1f}s")
    health = s.get("health") or {}
    run_id = health.get("run_id")
    rec("run_id", run_id is not None and abs(run_id - db["run_id"]) <= 10, f"page {run_id} vs sql {int(db['run_id'])}")
    last = (s.get("websocket") or {}).get("last_event_at")
    if last:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds()
        rec("ws_last_event", abs(age - db["ws_age_s"]) <= 120, f"page age {age:.0f}s vs sql {db['ws_age_s']:.0f}s")
    else:
        rec("ws_last_event", False, "page has no last_event_at")
    rec("candidates_24h", not bad, "every active variant within 5 % (or 20 rows) of SQL" if not bad else "; ".join(bad))
    rec("kill_switch", (s.get("kill_switch") or {}).get("active") is False, f"active={ (s.get('kill_switch') or {}).get('active') }")
    credits = health.get("odds_remaining", health.get("credits_remaining"))
    rec("credits_numeric", isinstance(credits, (int, float)), f"credits={credits}")
    dq = s.get("data_quality") or {}
    rec("data_quality_shape", isinstance(dq, dict) and "error" not in dq, "section rendered (empty is allowed in quiet hours)")

    if evidence:
        with open(evidence, "w") as f:
            f.write(f"# verify_summary {datetime.now(timezone.utc).isoformat()} deploy {deploy_sha}\n")
            f.write("\n".join(lines) + "\n\n# /api/summary\n")
            json.dump(s, f, indent=1, default=str)
            f.write("\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
