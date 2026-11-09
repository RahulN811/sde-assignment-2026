#!/usr/bin/env python3
"""Crash-recovery test: hard-kill the controller mid-run, restart it, and check the run finishes correctly.

What it does:
  1. starts a fresh simulator for the scenario (its own port and run dir)
  2. starts the controller with a fresh SQLite DB
  3. when the sim clock passes each kill point, force-kills the controller (no cleanup, like a power cut)
     and prints what was in flight (open proposals) at that moment
  4. restarts the controller against the same DB and simulator
  5. after the final run, checks:
       - controller exited 0 and the scenario finished
       - no proposal was applied twice (config history) or requested twice (approvals)
       - no proposal is stuck half-sent in the DB
       - every event the simulator produced was ingested exactly once
  6. grades the run against the baseline

    python scripts/crash_test.py --scenario scenarios/public_02_incidents.yaml \
        --baseline runs/base_public02 --kill-at pending 3720 pending 9300

This script only drives the simulator CLI and HTTP API; the controller itself never imports `sim`.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
OPEN_STATES = ("submitting_auto", "submitting_approval", "pending", "approved")


def log(msg: str) -> None:
    print(f"[crash-test {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def hard_kill(proc: subprocess.Popen) -> None:
    if os.name == "nt":
        # the venv python.exe on Windows is a launcher with a child process; kill the whole tree
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
    else:
        proc.kill()  # SIGKILL: no finally blocks, no DB close
    proc.wait()


def open_proposals(db_path: Path) -> list[tuple]:
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        q = "SELECT workflow_id, kind, status, approval_id FROM proposals WHERE status IN (%s)" % ",".join("?" * len(OPEN_STATES))
        return db.execute(q, OPEN_STATES).fetchall()
    finally:
        db.close()


def wait_ready(client: httpx.Client, timeout: float = 300) -> dict:
    start = time.time()
    while time.time() - start < timeout:
        try:
            return client.get("/sim/state").json()
        except httpx.HTTPError:
            time.sleep(1)
    raise SystemExit("simulator did not come up")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="scenarios/public_02_incidents.yaml")
    ap.add_argument("--baseline", default="runs/base_public02")
    ap.add_argument("--port", type=int, default=8701)
    ap.add_argument("--name", default="crash02")
    ap.add_argument("--kill-at", nargs="+", default=["pending", "3720", "pending", "9300"],
                    help="kill points, in order: a sim minute, or 'pending' = the first moment a proposal is in "
                         "flight (sent or waiting for approval). Default: pending, D2 14:00, pending, D6 11:00")
    a = ap.parse_args()

    url = f"http://127.0.0.1:{a.port}"
    run_dir = ROOT / "runs" / f"mine_{a.name}"
    db_path = ROOT / "runs" / f"{a.name}.sqlite3"
    for p in (db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm")):
        p.unlink(missing_ok=True)
    py = sys.executable
    ctl_cmd = [py, "-u", str(ROOT / "controller.py"), "--url", url, "--db", str(db_path)]
    ctl_log = open(ROOT / "runs" / f"{a.name}_controller.log", "w", encoding="utf-8")

    log(f"starting simulator for {a.scenario} on {url}")
    server = subprocess.Popen([py, "-m", "sim", "serve", "--scenario", a.scenario, "--run-dir", str(run_dir),
                               "--port", str(a.port), "--force"], cwd=ROOT,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    client = httpx.Client(base_url=url, timeout=120)
    failures: list[str] = []
    try:
        wait_ready(client)
        exit_code = None
        for attempt, kill_minute in enumerate(a.kill_at + [None], start=1):
            log(f"run {attempt}: starting controller" + (f" (will kill at sim minute {kill_minute})" if kill_minute else " (run to the end)"))
            ctl_log.write(f"\n===== controller start #{attempt} =====\n")
            ctl_log.flush()
            proc = subprocess.Popen(ctl_cmd, cwd=ROOT, stdout=ctl_log, stderr=subprocess.STDOUT)
            if kill_minute is None:
                exit_code = proc.wait()
                break
            while proc.poll() is None:
                if kill_minute == "pending":
                    if db_path.exists() and open_proposals(db_path):
                        break
                    time.sleep(0.05)
                    continue
                state = client.get("/sim/state").json()
                if state["sim_minute"] >= int(kill_minute):
                    break
                time.sleep(0.2)
            if proc.poll() is not None:
                failures.append(f"controller exited on its own (code {proc.returncode}) before kill point {kill_minute}")
                break
            hard_kill(proc)
            state = client.get("/sim/state").json()
            inflight = open_proposals(db_path)
            log(f"KILLED controller at {state['ts']} (sim minute {state['sim_minute']}); "
                f"{len(inflight)} proposal(s) in flight:")
            for row in inflight:
                log(f"    {row}")
            ctl_log.write(f"===== KILLED at {state['ts']} =====\n")
            ctl_log.flush()

        # ---- checks -------------------------------------------------------------
        state = client.get("/sim/state").json()
        log(f"final controller exit code: {exit_code}; scenario finished: {state['finished']}")
        if exit_code != 0:
            failures.append(f"controller exit code {exit_code}")
        if not state["finished"]:
            failures.append("scenario did not finish")

        workflows = [w["workflow_id"] for w in client.get("/workflows").json()]
        applied = Counter()
        for wid in workflows:
            for item in client.get(f"/workflows/{wid}/config/history").json():
                if item.get("proposal_id"):
                    applied[item["proposal_id"]] += 1
        requested = Counter(item["proposal_id"] for item in client.get("/approvals").json() if item.get("proposal_id"))
        double_applied = {k: v for k, v in applied.items() if v > 1}
        double_requested = {k: v for k, v in requested.items() if v > 1}
        log(f"changes applied: {sum(applied.values())}; approvals requested: {sum(requested.values())}")
        log(f"double-applied proposals: {double_applied or 'none'}")
        log(f"double-requested proposals: {double_requested or 'none'}")
        if double_applied:
            failures.append(f"double-applied: {double_applied}")
        if double_requested:
            failures.append(f"double-requested: {double_requested}")

        db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        stuck = db.execute("SELECT proposal_id, status FROM proposals WHERE status LIKE 'submitting_%'").fetchall()
        n_events, max_id = db.execute("SELECT COUNT(*), MAX(event_id) FROM events").fetchone()
        n_audit = db.execute("SELECT COUNT(*) FROM audit").fetchone()[0]
        db.close()
        log(f"proposals stuck half-sent: {stuck or 'none'}")
        log(f"events ingested: {n_events} (simulator last_event_id: {state['last_event_id']}, max stored id: {max_id})")
        log(f"audit rows: {n_audit}")
        if stuck:
            failures.append(f"stuck proposals: {stuck}")
        if max_id != state["last_event_id"] or n_events != state["last_event_id"]:
            failures.append("event count does not match the simulator (lost or duplicated events)")
    finally:
        client.close()
        server.terminate()
        server.wait()
        ctl_log.close()

    report = ROOT / "reports" / f"{a.name}.md"
    env = dict(os.environ, PYTHONUTF8="1")
    subprocess.run([py, "-m", "sim", "grade", "--run", str(run_dir), "--baseline", a.baseline, "--out", str(report)],
                   cwd=ROOT, env=env, check=False, stdout=subprocess.DEVNULL)
    log(f"grade report written to {report.relative_to(ROOT)}")

    if failures:
        log("RESULT: FAIL")
        for f in failures:
            log(f"  - {f}")
        return 1
    log("RESULT: PASS - killed and restarted, finished correctly, nothing lost or done twice")
    return 0


if __name__ == "__main__":
    sys.exit(main())
