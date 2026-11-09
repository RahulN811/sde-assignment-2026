#!/usr/bin/env python3
"""Answer "why did workflow X change around time T, who approved it, and what happened next?" from the
controller's own SQLite store (never from simulator logs).

    python scripts/why.py --db runs/controller_state.sqlite3 --workflow ks_cancel --at 2027-01-06T19:00
    python scripts/why.py --db runs/controller_state.sqlite3 --workflow ks_cancel        # whole history

Prints, in time order, every audit row for that workflow from 24h before T to 48h after T:
the detection and its evidence, the proposal, the approval request and decision, the apply, and the
post-change verification or later edits.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timedelta

SKIP_KEYS = {"baseline_config", "proposed_config", "previous_config"}


def short(details: dict) -> str:
    out = {}
    for key, value in details.items():
        if key in SKIP_KEYS:
            continue
        if key == "metadata" and isinstance(value, dict):
            value = {k: v for k, v in value.items() if k not in SKIP_KEYS}
        out[key] = value
    text = json.dumps(out, separators=(", ", ": "))
    return text if len(text) <= 400 else text[:397] + "..."


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="runs/controller_state.sqlite3")
    ap.add_argument("--workflow", required=True)
    ap.add_argument("--at", help="ISO time, e.g. 2027-01-06T19:00 (IST assumed)")
    a = ap.parse_args()

    db = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    query = "SELECT ts, action, details FROM audit WHERE workflow_id = ?"
    args: list = [a.workflow]
    if a.at:
        at = datetime.fromisoformat(a.at if "+" in a.at else a.at + "+05:30")
        query += " AND ts >= ? AND ts <= ?"
        args += [(at - timedelta(hours=24)).isoformat(), (at + timedelta(hours=48)).isoformat()]
    query += " ORDER BY ts, audit_id"

    rows = db.execute(query, args).fetchall()
    if not rows:
        print("no audit rows found")
        return
    for ts, action, details in rows:
        print(f"{ts[:16]}  {action:<34} {short(json.loads(details))}")

    print("\nconfig versions recorded for this workflow:")
    for pid, kind, status, approval_id, created in db.execute(
        "SELECT proposal_id, kind, status, approval_id, created_at FROM proposals WHERE workflow_id = ? ORDER BY rowid",
        (a.workflow,),
    ):
        print(f"  {created[:16]}  {kind:<17} {status:<20} approval={approval_id}  proposal={pid}")


if __name__ == "__main__":
    main()
