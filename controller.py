#!/usr/bin/env python3
"""A restartable, API-only controller for the assignment simulator."""

from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx


MIN_BASELINE_CALLS = 100
MIN_ARM_CALLS = 100
MIN_ARM_CONNECTIONS = 60
CONFIDENCE_Z = 3.0
DEFAULT_STEP_MINUTES = 60


def encode(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def decode(value: str | None, default: Any = None) -> Any:
    return default if value is None else json.loads(value)


class Store:
    def __init__(self, path: str):
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(target)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                event_id INTEGER PRIMARY KEY,
                chunk_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                workflow_id TEXT,
                event_type TEXT,
                ts TEXT,
                payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS events_workflow_idx ON events(workflow_id, event_id);
            CREATE TABLE IF NOT EXISTS calls (
                event_id INTEGER PRIMARY KEY,
                workflow_id TEXT NOT NULL,
                ts TEXT,
                cohort_id TEXT,
                config_version INTEGER,
                variant TEXT NOT NULL,
                connected INTEGER NOT NULL,
                engaged INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS calls_group_idx
                ON calls(workflow_id, cohort_id, event_id, config_version);
            CREATE TABLE IF NOT EXISTS workflows (
                workflow_id TEXT PRIMARY KEY,
                public_data TEXT NOT NULL,
                config TEXT NOT NULL,
                config_version INTEGER NOT NULL,
                experiment TEXT
            );
            CREATE TABLE IF NOT EXISTS proposals (
                proposal_id TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                approval_id TEXT,
                base_version INTEGER NOT NULL,
                proposed_config TEXT NOT NULL,
                previous_config TEXT NOT NULL,
                metadata TEXT NOT NULL,
                created_at TEXT
            );
            CREATE INDEX IF NOT EXISTS proposals_workflow_idx ON proposals(workflow_id, status);
            CREATE TABLE IF NOT EXISTS audit (
                audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT,
                workflow_id TEXT,
                action TEXT NOT NULL,
                details TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS audit_workflow_idx ON audit(workflow_id, audit_id);
            """
        )
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return default if row is None else row["value"]

    def set_meta(self, key: str, value: Any) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(value)),
            )

    def begin_run(self, scenario: str, sim_minute: int) -> None:
        previous_scenario = self.get_meta("scenario")
        previous_minute = self.get_meta("sim_minute")
        is_new_world = previous_scenario != scenario
        if previous_minute is not None and sim_minute < int(previous_minute):
            is_new_world = True
        if is_new_world:
            with self.db:
                for table in ("events", "calls", "workflows", "proposals", "audit", "meta"):
                    self.db.execute(f"DELETE FROM {table}")
            self.set_meta("run_id", str(uuid.uuid4()))
        elif self.get_meta("run_id") is None:
            self.set_meta("run_id", str(uuid.uuid4()))
        self.set_meta("scenario", scenario)
        self.set_meta("sim_minute", sim_minute)
        if self.get_meta("last_chunk") is None:
            self.set_meta("last_chunk", -1)

    def last_chunk(self) -> int:
        return int(self.get_meta("last_chunk", "-1") or -1)

    def max_event_id(self) -> int:
        row = self.db.execute("SELECT COALESCE(MAX(event_id), 0) AS n FROM events").fetchone()
        return int(row["n"])

    def insert_event(self, event: dict[str, Any], chunk_id: int, kind: str) -> bool:
        event_id = int(event["event_id"])
        cursor = self.db.execute(
            "INSERT OR IGNORE INTO events(event_id, chunk_id, kind, workflow_id, event_type, ts, payload) "
            "VALUES(?, ?, ?, ?, ?, ?, ?)",
            (event_id, chunk_id, kind, event.get("workflow_id"), event.get("type"), event.get("ts"), encode(event)),
        )
        if cursor.rowcount == 0:
            return False
        if event.get("type") == "call":
            self.db.execute(
                "INSERT INTO calls(event_id, workflow_id, ts, cohort_id, config_version, variant, connected, engaged) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event_id,
                    event["workflow_id"],
                    event.get("ts"),
                    event.get("cohort_id", "default"),
                    int(event.get("config_version", 0)),
                    encode(event.get("variant") or {}),
                    int(bool(event.get("connected"))),
                    int(bool(event.get("engaged"))),
                ),
            )
        elif event.get("type") == "config_changed":
            self.audit(
                event.get("ts"),
                event.get("workflow_id"),
                "config_changed_event",
                {key: event.get(key) for key in ("actor", "version", "changed_dims", "proposal_id", "approval_id")},
            )
        elif event.get("type") == "approval_decided":
            self.audit(
                event.get("ts"),
                event.get("workflow_id"),
                "approval_decided_event",
                {key: event.get(key) for key in ("approval_id", "status", "reasons")},
            )
        return True

    def audit(self, ts: str | None, workflow_id: str | None, action: str, details: dict[str, Any]) -> None:
        transaction_open = self.db.in_transaction
        self.db.execute(
            "INSERT INTO audit(ts, workflow_id, action, details) VALUES(?, ?, ?, ?)",
            (ts, workflow_id, action, encode(details)),
        )
        if not transaction_open:
            self.db.commit()

    def get_workflow(self, workflow_id: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT * FROM workflows WHERE workflow_id = ?", (workflow_id,)).fetchone()
        if row is None:
            return None
        return {
            "workflow": decode(row["public_data"], {}),
            "config": decode(row["config"], {}),
            "version": int(row["config_version"]),
            "experiment": decode(row["experiment"]),
        }

    def save_workflow(
        self,
        workflow_id: str,
        public_data: dict[str, Any],
        config: dict[str, Any],
        version: int,
        experiment: dict[str, Any] | None = None,
    ) -> None:
        existing = self.get_workflow(workflow_id)
        if experiment is None and existing is not None:
            experiment = existing["experiment"]
        with self.db:
            self.db.execute(
                "INSERT INTO workflows(workflow_id, public_data, config, config_version, experiment) "
                "VALUES(?, ?, ?, ?, ?) ON CONFLICT(workflow_id) DO UPDATE SET "
                "public_data = excluded.public_data, config = excluded.config, "
                "config_version = excluded.config_version, experiment = excluded.experiment",
                (workflow_id, encode(public_data), encode(config), version, None if experiment is None else encode(experiment)),
            )

    def save_experiment(self, workflow_id: str, experiment: dict[str, Any] | None) -> None:
        with self.db:
            self.db.execute(
                "UPDATE workflows SET experiment = ? WHERE workflow_id = ?",
                (None if experiment is None else encode(experiment), workflow_id),
            )

    def save_proposal(self, proposal: dict[str, Any]) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO proposals(proposal_id, workflow_id, kind, status, approval_id, base_version, "
                "proposed_config, previous_config, metadata, created_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    proposal["proposal_id"],
                    proposal["workflow_id"],
                    proposal["kind"],
                    proposal["status"],
                    proposal.get("approval_id"),
                    proposal["base_version"],
                    encode(proposal["proposed_config"]),
                    encode(proposal["previous_config"]),
                    encode(proposal["metadata"]),
                    proposal.get("created_at"),
                ),
            )

    def get_proposal(self, proposal_id: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT * FROM proposals WHERE proposal_id = ?", (proposal_id,)).fetchone()
        return None if row is None else self._proposal_from_row(row)

    def proposals(self, workflow_id: str | None = None, statuses: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM proposals WHERE 1 = 1"
        args: list[Any] = []
        if workflow_id is not None:
            query += " AND workflow_id = ?"
            args.append(workflow_id)
        if statuses:
            query += " AND status IN (" + ",".join("?" for _ in statuses) + ")"
            args.extend(statuses)
        query += " ORDER BY rowid"
        return [self._proposal_from_row(row) for row in self.db.execute(query, args).fetchall()]

    @staticmethod
    def _proposal_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "proposal_id": row["proposal_id"],
            "workflow_id": row["workflow_id"],
            "kind": row["kind"],
            "status": row["status"],
            "approval_id": row["approval_id"],
            "base_version": int(row["base_version"]),
            "proposed_config": decode(row["proposed_config"], {}),
            "previous_config": decode(row["previous_config"], {}),
            "metadata": decode(row["metadata"], {}),
            "created_at": row["created_at"],
        }

    def update_proposal(self, proposal_id: str, **fields: Any) -> None:
        allowed = {"status", "approval_id", "base_version"}
        updates = [(key, value) for key, value in fields.items() if key in allowed]
        if not updates:
            return
        statement = ", ".join(f"{key} = ?" for key, _ in updates)
        with self.db:
            self.db.execute(
                f"UPDATE proposals SET {statement} WHERE proposal_id = ?",
                [value for _, value in updates] + [proposal_id],
            )

    def has_open_proposal(self, workflow_id: str) -> bool:
        row = self.db.execute(
            "SELECT 1 FROM proposals WHERE workflow_id = ? AND status IN "
            "('submitting_auto', 'submitting_approval', 'pending', 'approved') LIMIT 1",
            (workflow_id,),
        ).fetchone()
        return row is not None

    def stats(
        self,
        workflow_id: str,
        dimension: str,
        event_after: int = 0,
        version_at_least: int = 0,
    ) -> dict[str, dict[str, int]]:
        rows = self.db.execute(
            "SELECT variant, connected, engaged FROM calls WHERE workflow_id = ? "
            "AND COALESCE(cohort_id, 'default') = 'default' AND event_id > ? AND config_version >= ?",
            (workflow_id, event_after, version_at_least),
        ).fetchall()
        result: dict[str, dict[str, int]] = {}
        for row in rows:
            variant = decode(row["variant"], {})
            value = variant.get(dimension)
            if value is None:
                continue
            key = str(value)
            counts = result.setdefault(key, {"calls": 0, "connected": 0, "engaged": 0})
            counts["calls"] += 1
            counts["connected"] += int(row["connected"])
            counts["engaged"] += int(row["engaged"])
        return result

    def caller_id_stats(
        self,
        workflow_id: str,
        cutoff_ts: str,
        version_at_least: int,
        event_after: int = 0,
    ) -> dict[str, dict[str, int]]:
        rows = self.db.execute(
            "SELECT variant, connected, engaged FROM calls WHERE workflow_id = ? AND ts >= ? "
            "AND config_version >= ? AND event_id > ? AND COALESCE(cohort_id, 'default') = 'default'",
            (workflow_id, cutoff_ts, version_at_least, event_after),
        ).fetchall()
        result: dict[str, dict[str, int]] = {}
        for row in rows:
            caller_id = decode(row["variant"], {}).get("caller_id")
            if caller_id is None:
                continue
            counts = result.setdefault(str(caller_id), {"calls": 0, "connected": 0, "engaged": 0})
            counts["calls"] += 1
            counts["connected"] += int(row["connected"])
            counts["engaged"] += int(row["engaged"])
        return result


def wilson(successes: int, trials: int, z: float = CONFIDENCE_Z) -> tuple[float, float]:
    if trials <= 0:
        return 0.0, 1.0
    rate = successes / trials
    z2 = z * z
    denominator = 1 + z2 / trials
    center = (rate + z2 / (2 * trials)) / denominator
    margin = z * math.sqrt((rate * (1 - rate) + z2 / (4 * trials)) / trials) / denominator
    return max(0.0, center - margin), min(1.0, center + margin)


def metric_counts(counts: dict[str, int], metric: str) -> tuple[int, int]:
    if metric == "engagement_rate":
        return counts["engaged"], counts["connected"]
    return counts["connected"], counts["calls"]


def metric_rate(counts: dict[str, int], metric: str) -> float:
    successes, trials = metric_counts(counts, metric)
    return successes / trials if trials else 0.0


def sufficient(counts: dict[str, int], objective: dict[str, Any]) -> bool:
    primary = objective.get("primary", "engagement_rate")
    primary_trials = counts["connected"] if primary == "engagement_rate" else counts["calls"]
    guardrail = objective.get("guardrail", {})
    guardrail_metric = next((key.removesuffix("_max_drop") for key in guardrail), None)
    guardrail_trials = counts["connected"] if guardrail_metric == "engagement_rate" else counts["calls"]
    return primary_trials >= MIN_ARM_CONNECTIONS and guardrail_trials >= MIN_ARM_CALLS


def winner_for(
    stats: dict[str, dict[str, int]],
    incumbent: str,
    arms: list[str],
    objective: dict[str, Any],
) -> str | None:
    control = stats.get(incumbent)
    if not control or not sufficient(control, objective):
        return None
    primary = objective.get("primary", "engagement_rate")
    guardrail = objective.get("guardrail", {})
    guardrail_key = next(iter(guardrail), None)
    guardrail_metric = guardrail_key.removesuffix("_max_drop") if guardrail_key else None
    allowed_drop = float(guardrail.get(guardrail_key, 0.0)) if guardrail_key else 0.0
    control_success, control_trials = metric_counts(control, primary)
    control_low, control_high = wilson(control_success, control_trials)
    control_guard_low = control_guard_high = 0.0
    if guardrail_metric:
        successes, trials = metric_counts(control, guardrail_metric)
        control_guard_low, control_guard_high = wilson(successes, trials)
    candidates = []
    for arm in arms:
        if arm == incumbent:
            continue
        candidate = stats.get(arm)
        if not candidate or not sufficient(candidate, objective):
            continue
        successes, trials = metric_counts(candidate, primary)
        candidate_low, _ = wilson(successes, trials)
        if candidate_low <= control_high:
            continue
        if guardrail_metric:
            successes, trials = metric_counts(candidate, guardrail_metric)
            candidate_guard_low, _ = wilson(successes, trials)
            if candidate_guard_low - control_guard_high < -allowed_drop:
                continue
        candidates.append((metric_rate(candidate, primary), arm))
    return max(candidates)[1] if candidates else None


class Controller:
    def __init__(self, url: str, db_path: str, step_minutes: int):
        self.client = httpx.Client(base_url=url.rstrip("/"), timeout=60)
        self.store = Store(db_path)
        self.step_minutes = step_minutes

    def close(self) -> None:
        self.client.close()
        self.store.close()

    def api(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        response = self.client.request(method, path, **kwargs)
        response.raise_for_status()
        return response

    def ingest_chunks(self, chunks: list[dict[str, Any]]) -> None:
        for chunk in sorted(chunks, key=lambda item: int(item["chunk"])):
            chunk_id = int(chunk["chunk"])
            if chunk_id <= self.store.last_chunk():
                continue
            response = self.api("GET", f"/events/chunks/{chunk_id}")
            with self.store.db:
                for line in response.text.splitlines():
                    if line.strip():
                        self.store.insert_event(json.loads(line), chunk_id, chunk.get("kind", "events"))
                self.store.db.execute(
                    "INSERT INTO meta(key, value) VALUES('last_chunk', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (str(chunk_id),),
                )

    def ingest_missing_chunks(self) -> None:
        cursor = self.store.last_chunk()
        if cursor < 0:
            history = self.api("GET", "/events/chunks", params={"kind": "history", "after": -1}).json()
            self.ingest_chunks(history)
            cursor = self.store.last_chunk()
        events = self.api("GET", "/events/chunks", params={"kind": "events", "after": cursor}).json()
        self.ingest_chunks(events)

    def run(self) -> None:
        state = self.api("GET", "/sim/state").json()
        self.store.begin_run(state["scenario"], int(state["sim_minute"]))
        self.ingest_missing_chunks()
        self.sync_workflows()
        self.process_approvals(state["ts"])
        self.evaluate_workflows(state["ts"])
        print(f"Controller attached to {state['scenario']} at {state['ts']}")

        while not state["finished"]:
            remaining = int(state["end_minute"]) - int(state["sim_minute"])
            minutes = min(self.step_minutes, remaining)
            tick = int(state["tick_minutes"])
            minutes = max(tick, (minutes // tick) * tick)
            result = self.api("POST", "/sim/advance", json={"minutes": minutes}).json()
            self.ingest_chunks(result.get("new_chunks", []))
            state = self.api("GET", "/sim/state").json()
            self.store.set_meta("sim_minute", state["sim_minute"])
            self.sync_workflows()
            self.process_approvals(state["ts"])
            self.evaluate_workflows(state["ts"])
            print(f"{state['ts']}  chunks={self.store.last_chunk()}  finished={state['finished']}")

        self.store.audit(state["ts"], None, "scenario_finished", {"scenario": state["scenario"]})
        print(f"Controller finished {state['scenario']}; audit database: {self.store.get_meta('scenario')}")

    def sync_workflows(self) -> None:
        for public_data in self.api("GET", "/workflows").json():
            workflow_id = public_data["workflow_id"]
            config_response = self.api("GET", f"/workflows/{workflow_id}/config").json()
            history = self.api("GET", f"/workflows/{workflow_id}/config/history").json()
            self.reconcile_submissions(workflow_id, history)
            previous = self.store.get_workflow(workflow_id)
            version = int(config_response["version"])
            if previous is not None and previous["version"] != version:
                latest = next((item for item in reversed(history) if int(item["version"]) == version), {})
                actor = latest.get("actor")
                if actor == "supervisor":
                    self.store.audit(
                        config_response.get("applied_at"),
                        workflow_id,
                        "supervisor_config_change_detected",
                        {"version": version, "changed_dims": latest.get("changed_dims", [])},
                    )
                    experiment = previous["experiment"]
                    if experiment and experiment.get("status") in ("exploring", "verifying"):
                        experiment["status"] = "paused_supervisor_edit"
                        self.store.save_experiment(workflow_id, experiment)
                elif actor not in ("controller_auto", "controller_approved"):
                    self.store.audit(
                        config_response.get("applied_at"),
                        workflow_id,
                        "external_config_change_detected",
                        {"version": version, "actor": actor},
                    )
            public_data["current_version"] = version
            self.store.save_workflow(workflow_id, public_data, config_response["config"], version)

    def reconcile_submissions(self, workflow_id: str, history: list[dict[str, Any]]) -> None:
        proposals = self.store.proposals(
            workflow_id,
            ("submitting_auto", "submitting_approval", "pending", "approved"),
        )
        history_by_id = {item.get("proposal_id"): item for item in history if item.get("proposal_id")}
        approvals: list[dict[str, Any]] | None = None
        for proposal in proposals:
            applied = history_by_id.get(proposal["proposal_id"])
            if applied is not None:
                self.complete_application(proposal, int(applied["version"]), applied.get("applied_at"))
                continue
            if proposal["status"] in ("submitting_approval", "pending", "approved"):
                if approvals is None:
                    approvals = self.api("GET", "/approvals", params={"workflow_id": workflow_id}).json()
                match = next(
                    (item for item in approvals if item.get("proposal_id") == proposal["proposal_id"]),
                    None,
                )
                if match is not None:
                    self.store.update_proposal(
                        proposal["proposal_id"],
                        status=match["status"],
                        approval_id=match["approval_id"],
                    )
                    if match["status"] in ("rejected", "expired", "lapsed"):
                        self.finish_proposal(proposal, match["status"], {"reasons": match.get("reasons", [])})
                    continue
            if proposal["status"] == "submitting_auto":
                self.send_auto(proposal)
            elif proposal["status"] == "submitting_approval":
                self.send_approval(proposal)

    def evaluate_workflows(self, ts: str) -> None:
        for workflow_id in [item["workflow_id"] for item in self.api("GET", "/workflows").json()]:
            if self.store.has_open_proposal(workflow_id):
                continue
            row = self.store.get_workflow(workflow_id)
            if row is None:
                continue
            if (row["experiment"] or {}).get("caller_id_incident_status") == "applied":
                self.verify_caller_id_rotation(workflow_id, row, ts)
            if self.detect_caller_id_degradation(workflow_id, row, ts):
                continue
            experiment = row["experiment"]
            if not experiment or not experiment.get("status"):
                self.maybe_start_experiment(workflow_id, row, ts)
            elif experiment.get("status") == "exploring":
                self.evaluate_exploration(workflow_id, row, ts)
            elif experiment.get("status") == "verifying":
                self.evaluate_verification(workflow_id, row, ts)

    def detect_caller_id_degradation(self, workflow_id: str, row: dict[str, Any], ts: str) -> bool:
        experiment = row["experiment"] or {}
        if experiment.get("caller_id_incident_status"):
            return False
        allocation = row["config"].get("allocations", {}).get("default", {}).get("caller_id", {})
        active_ids = [str(caller_id) for caller_id, weight in allocation.items() if float(weight) > 0]
        if len(active_ids) < 2:
            return False
        cutoff = (datetime.fromisoformat(ts) - timedelta(hours=24)).isoformat()
        stats = self.store.caller_id_stats(workflow_id, cutoff, row["version"])
        if any(stats.get(caller_id, {}).get("calls", 0) < MIN_ARM_CALLS for caller_id in active_ids):
            return False
        best_id = max(active_ids, key=lambda caller_id: metric_rate(stats[caller_id], "pickup_rate"))
        best_low, _ = wilson(stats[best_id]["connected"], stats[best_id]["calls"])
        unhealthy = []
        for caller_id in active_ids:
            if caller_id == best_id:
                continue
            _, low_performer_high = wilson(stats[caller_id]["connected"], stats[caller_id]["calls"])
            if best_low - low_performer_high >= 0.05:
                unhealthy.append(caller_id)
        if not unhealthy:
            return False
        healthy_ids = [caller_id for caller_id in active_ids if caller_id not in unhealthy]
        if not healthy_ids:
            return False
        healthy_weight = sum(float(allocation[caller_id]) for caller_id in healthy_ids)
        proposed = json.loads(encode(row["config"]))
        proposed_distribution = {
            caller_id: float(allocation[caller_id]) / healthy_weight for caller_id in healthy_ids
        }
        proposed.setdefault("allocations", {}).setdefault("default", {})["caller_id"] = proposed_distribution
        best_rate = metric_rate(stats[best_id], "pickup_rate")
        worst_rate = min(metric_rate(stats[caller_id], "pickup_rate") for caller_id in unhealthy)
        metadata = {
            "dimension": "caller_id",
            "unhealthy_ids": unhealthy,
            "healthy_ids": healthy_ids,
            "baseline_stats": stats,
            "impact_score": min(1.0, max(0.01, best_rate - worst_rate)),
        }
        self.store.audit(ts, workflow_id, "caller_id_degradation_detected", metadata)
        self.submit_candidate(workflow_id, "caller_id_rotate", proposed, metadata, ts)
        return True

    def verify_caller_id_rotation(self, workflow_id: str, row: dict[str, Any], ts: str) -> None:
        experiment = row["experiment"] or {}
        metadata = experiment.get("caller_id_evidence", {})
        active_ids = metadata.get("healthy_ids", [])
        if not active_ids:
            return
        cutoff = (datetime.fromisoformat(ts) - timedelta(hours=24)).isoformat()
        after = self.store.caller_id_stats(
            workflow_id,
            cutoff,
            int(experiment.get("caller_id_version", row["version"])),
            int(experiment.get("caller_id_verification_start_event_id", 0)),
        )
        after_counts = {"calls": 0, "connected": 0, "engaged": 0}
        for caller_id in active_ids:
            for key in after_counts:
                after_counts[key] += after.get(caller_id, {}).get(key, 0)
        if after_counts["calls"] < MIN_BASELINE_CALLS:
            return
        baseline = metadata.get("baseline_stats", {})
        reference = {"calls": 0, "connected": 0, "engaged": 0}
        for caller_id in active_ids:
            for key in reference:
                reference[key] += baseline.get(caller_id, {}).get(key, 0)
        objective = row["workflow"].get("objective", {})
        primary = objective.get("primary", "engagement_rate")
        primary_change = metric_rate(after_counts, primary) - metric_rate(reference, primary)
        guardrail = objective.get("guardrail", {})
        guardrail_key = next(iter(guardrail), None)
        guardrail_change = None
        if guardrail_key:
            guardrail_metric = guardrail_key.removesuffix("_max_drop")
            guardrail_change = metric_rate(after_counts, guardrail_metric) - metric_rate(reference, guardrail_metric)
        result = {
            "checked_at": ts,
            "post_change_stats": after_counts,
            "reference_stats": reference,
            "primary_metric": primary,
            "primary_change": primary_change,
            "guardrail_change": guardrail_change,
            "removed_id_calls": {caller_id: after.get(caller_id, {}).get("calls", 0) for caller_id in metadata.get("unhealthy_ids", [])},
        }
        experiment["caller_id_verification"] = result
        if guardrail_key and guardrail_change is not None:
            allowed_drop = float(guardrail[guardrail_key])
            if guardrail_change < -allowed_drop:
                experiment["caller_id_incident_status"] = "guardrail_breach_review"
            elif primary_change > 0:
                experiment["caller_id_incident_status"] = "verified_helpful"
            else:
                experiment["caller_id_incident_status"] = "verified_no_clear_lift"
        else:
            experiment["caller_id_incident_status"] = "verified_helpful" if primary_change > 0 else "verified_no_clear_lift"
        self.store.save_experiment(workflow_id, experiment)
        self.store.audit(ts, workflow_id, "caller_id_rotation_verified", {"status": experiment["caller_id_incident_status"], **result})

    def maybe_start_experiment(self, workflow_id: str, row: dict[str, Any], ts: str) -> None:
        workflow = row["workflow"]
        options = workflow.get("options", {})
        dimension = "voice" if len(options.get("voice", [])) > 1 else "welcome_message"
        arms = [str(value) for value in options.get(dimension, [])]
        if len(arms) < 2:
            return
        allocation = row["config"].get("allocations", {}).get("default", {}).get(dimension, {})
        incumbent = max(arms, key=lambda arm: float(allocation.get(arm, 0)))
        baseline = self.store.stats(workflow_id, dimension, version_at_least=row["version"])
        incumbent_stats = baseline.get(incumbent, {"calls": 0, "connected": 0, "engaged": 0})
        if incumbent_stats["calls"] < MIN_BASELINE_CALLS or not sufficient(incumbent_stats, workflow["objective"]):
            return
        positive_arms = [arm for arm in arms if float(allocation.get(arm, 0)) > 0]
        if len(positive_arms) > 1:
            experiment = {
                "status": "exploring",
                "dimension": dimension,
                "incumbent": incumbent,
                "arms": positive_arms,
                "applied_version": row["version"],
                "start_event_id": self.store.max_event_id(),
                "baseline_config": row["config"],
                "baseline_stats": baseline,
            }
            self.store.save_experiment(workflow_id, experiment)
            self.store.audit(ts, workflow_id, "existing_experiment_observed", experiment)
            return
        proposed = json.loads(encode(row["config"]))
        distribution = {incumbent: 0.82}
        share = 0.18 / (len(arms) - 1)
        for arm in arms:
            if arm != incumbent:
                distribution[arm] = share
        proposed.setdefault("allocations", {}).setdefault("default", {})[dimension] = distribution
        metadata = {
            "dimension": dimension,
            "incumbent": incumbent,
            "arms": arms,
            "baseline_stats": baseline,
            "baseline_config": row["config"],
            "impact_score": 0.05,
        }
        self.submit_candidate(workflow_id, "explore", proposed, metadata, ts)

    def evaluate_exploration(self, workflow_id: str, row: dict[str, Any], ts: str) -> None:
        experiment = row["experiment"]
        stats = self.store.stats(
            workflow_id,
            experiment["dimension"],
            event_after=int(experiment["start_event_id"]),
            version_at_least=int(experiment["applied_version"]),
        )
        winner = winner_for(stats, experiment["incumbent"], experiment["arms"], row["workflow"]["objective"])
        if winner is None:
            return
        incumbent_rate = metric_rate(stats[winner], row["workflow"]["objective"]["primary"])
        control_rate = metric_rate(stats[experiment["incumbent"]], row["workflow"]["objective"]["primary"])
        proposed = json.loads(encode(row["config"]))
        proposed.setdefault("allocations", {}).setdefault("default", {})[experiment["dimension"]] = {winner: 1.0}
        metadata = {
            "dimension": experiment["dimension"],
            "winner": winner,
            "winner_reference": stats[winner],
            "experiment_stats": stats,
            "impact_score": min(1.0, max(0.01, incumbent_rate - control_rate)),
        }
        self.store.audit(ts, workflow_id, "experiment_winner_selected", metadata)
        self.submit_candidate(workflow_id, "promote", proposed, metadata, ts)

    def evaluate_verification(self, workflow_id: str, row: dict[str, Any], ts: str) -> None:
        experiment = row["experiment"]
        stats = self.store.stats(
            workflow_id,
            experiment["dimension"],
            event_after=int(experiment["verification_start_event_id"]),
            version_at_least=int(experiment["promoted_version"]),
        )
        winner_stats = stats.get(experiment["winner"], {"calls": 0, "connected": 0, "engaged": 0})
        if not sufficient(winner_stats, row["workflow"]["objective"]):
            return
        objective = row["workflow"]["objective"]
        primary = objective.get("primary", "engagement_rate")
        ref = experiment["winner_reference"]
        ref_success, ref_trials = metric_counts(ref, primary)
        ref_low, _ = wilson(ref_success, ref_trials)
        after_success, after_trials = metric_counts(winner_stats, primary)
        _, after_high = wilson(after_success, after_trials)
        guardrail = objective.get("guardrail", {})
        guardrail_key = next(iter(guardrail), None)
        degraded = after_high < ref_low
        if guardrail_key:
            guardrail_metric = guardrail_key.removesuffix("_max_drop")
            ref_success, ref_trials = metric_counts(ref, guardrail_metric)
            ref_guard_low, _ = wilson(ref_success, ref_trials)
            after_success, after_trials = metric_counts(winner_stats, guardrail_metric)
            _, after_guard_high = wilson(after_success, after_trials)
            allowed_drop = float(guardrail[guardrail_key])
            degraded = degraded or after_guard_high < ref_guard_low - allowed_drop
        if degraded:
            proposed = experiment["baseline_config"]
            metadata = {
                "dimension": experiment["dimension"],
                "winner": experiment["winner"],
                "impact_score": 0.0,
                "verification_stats": winner_stats,
            }
            self.submit_candidate(workflow_id, "rollback", proposed, metadata, ts)
            return
        experiment["status"] = "complete"
        experiment["verification_stats"] = winner_stats
        experiment["verified_at"] = ts
        self.store.save_experiment(workflow_id, experiment)
        self.store.audit(ts, workflow_id, "promotion_verified", {"winner": experiment["winner"], "stats": winner_stats})

    def submit_candidate(
        self,
        workflow_id: str,
        kind: str,
        proposed_config: dict[str, Any],
        metadata: dict[str, Any],
        ts: str,
    ) -> None:
        row = self.store.get_workflow(workflow_id)
        if row is None or self.store.has_open_proposal(workflow_id):
            return
        previous = row["config"]
        dimension = metadata["dimension"]
        proposal_id = str(uuid.uuid4())
        proposal = {
            "proposal_id": proposal_id,
            "workflow_id": workflow_id,
            "kind": kind,
            "status": "submitting_approval",
            "approval_id": None,
            "base_version": row["version"],
            "proposed_config": proposed_config,
            "previous_config": previous,
            "metadata": metadata,
            "created_at": ts,
        }
        self.store.save_proposal(proposal)
        experiment = row["experiment"] or {}
        if kind == "caller_id_rotate":
            experiment["caller_id_incident_status"] = "proposal_pending"
        else:
            experiment.update({"proposal_id": proposal_id, "status": f"{kind}_proposed"})
            if kind == "explore":
                experiment.update(metadata)
                experiment["baseline_config"] = previous
        self.store.save_experiment(workflow_id, experiment)
        self.store.audit(ts, workflow_id, "proposal_prepared", {"proposal_id": proposal_id, "kind": kind, "metadata": metadata})
        policy = row["workflow"].get("governance", {}).get("auto_apply", {})
        shift = self.allocation_shift(previous, proposed_config, dimension)
        if (
            policy.get("enabled")
            and dimension in policy.get("allowed_dims", [])
            and shift <= float(policy.get("max_shift", 0)) + 1e-9
        ):
            proposal["status"] = "submitting_auto"
            self.store.update_proposal(proposal_id, status="submitting_auto")
            self.send_auto(proposal)
        else:
            self.send_approval(proposal)

    @staticmethod
    def allocation_shift(old: dict[str, Any], new: dict[str, Any], dimension: str) -> float:
        old_dist = old.get("allocations", {}).get("default", {}).get(dimension, {})
        new_dist = new.get("allocations", {}).get("default", {}).get(dimension, {})
        keys = set(old_dist) | set(new_dist)
        return 0.5 * sum(abs(float(old_dist.get(key, 0)) - float(new_dist.get(key, 0))) for key in keys)

    def approval_body(self, proposal: dict[str, Any]) -> dict[str, Any]:
        metadata = proposal["metadata"]
        dimension = metadata["dimension"]
        old_value = proposal["previous_config"].get("allocations", {}).get("default", {}).get(dimension, {})
        new_value = proposal["proposed_config"].get("allocations", {}).get("default", {}).get(dimension, {})
        row = self.store.get_workflow(proposal["workflow_id"])
        workflow = row["workflow"] if row else {}
        daily_batch = workflow.get("daily_batch", {}).get("size", 0)
        summaries = {
            "explore": f"Run a controlled {dimension} test while keeping the current option as the majority",
            "promote": f"Promote {metadata.get('winner')} after it passed the KPI and guardrail evidence checks",
            "rollback": f"Restore the prior {dimension} setting after post-change results degraded",
            "caller_id_rotate": "Reduce calls from caller IDs with a sustained pickup-rate drop",
        }
        return {
            "proposal_id": proposal["proposal_id"],
            "workflow_id": proposal["workflow_id"],
            "base_version": proposal["base_version"],
            "proposed_config": proposal["proposed_config"],
            "change_summary": summaries.get(proposal["kind"], "Adjust workflow configuration based on measured results"),
            "diff": {dimension: {"from": old_value, "to": new_value}},
            "affected_leads": int(daily_batch or 0),
            "dry_run": {"method": "replay of recorded default-cohort calls", "observed_arms": metadata.get("baseline_stats", metadata.get("experiment_stats", {}))},
            "impact_score": float(metadata.get("impact_score", 0.05)),
            "rationale": self.rationale(proposal),
        }

    @staticmethod
    def rationale(proposal: dict[str, Any]) -> str:
        if proposal["kind"] == "explore":
            return "Historical calls provide a baseline. This proposal is a limited experiment, not a claim that a new option has won. The incumbent retains 82 percent of traffic; other options share 18 percent."
        if proposal["kind"] == "promote":
            return "The candidate cleared conservative confidence-interval checks against the incumbent for the primary KPI and did not exceed the configured guardrail drop."
        if proposal["kind"] == "rollback":
            return "Post-application measurements indicate degradation against the experiment reference; restore the recorded pre-experiment configuration."
        if proposal["kind"] == "caller_id_rotate":
            return "The active caller ID showed a sustained pickup-rate deficit over a 24-hour population with enough samples and a conservative confidence interval. Remove that ID from the default allocation and redistribute its traffic across the remaining active IDs."
        return "The change is based on measured workflow results and follows the workflow's configured governance policy."

    def send_auto(self, proposal: dict[str, Any]) -> None:
        proposal_id = proposal["proposal_id"]
        try:
            response = self.api(
                "PUT",
                f"/workflows/{proposal['workflow_id']}/config",
                json={
                    "proposal_id": proposal_id,
                    "base_version": proposal["base_version"],
                    "proposed_config": proposal["proposed_config"],
                    "rationale": self.rationale(proposal),
                },
            ).json()
        except httpx.HTTPStatusError as error:
            status_code = error.response.status_code
            if status_code == 403:
                self.store.audit(proposal["created_at"], proposal["workflow_id"], "auto_apply_denied", {"proposal_id": proposal_id})
                self.send_approval(proposal)
            elif status_code == 409:
                self.finish_proposal(proposal, "stale_base_version", {"status_code": status_code})
            else:
                self.finish_proposal(proposal, "failed", {"status_code": status_code, "response": error.response.text[:500]})
            return
        self.complete_application(proposal, int(response["version"]), proposal["created_at"])

    def send_approval(self, proposal: dict[str, Any]) -> None:
        proposal_id = proposal["proposal_id"]
        self.store.update_proposal(proposal_id, status="submitting_approval")
        try:
            approval = self.api("POST", "/approvals", json=self.approval_body(proposal)).json()
        except httpx.HTTPStatusError as error:
            status_code = error.response.status_code
            final_status = "stale_base_version" if status_code == 409 else "failed"
            self.finish_proposal(
                proposal,
                final_status,
                {"status_code": status_code, "response": error.response.text[:500]},
            )
            return
        self.store.update_proposal(proposal_id, status=approval["status"], approval_id=approval["approval_id"])
        self.store.audit(
            proposal["created_at"],
            proposal["workflow_id"],
            "approval_requested",
            {"proposal_id": proposal_id, "approval_id": approval["approval_id"], "status": approval["status"]},
        )

    def process_approvals(self, ts: str) -> None:
        pending = self.store.proposals(statuses=("pending", "approved"))
        for proposal in pending:
            approval_id = proposal.get("approval_id")
            if not approval_id:
                continue
            try:
                approval = self.api("GET", f"/approvals/{approval_id}").json()
            except httpx.HTTPStatusError as error:
                self.store.audit(ts, proposal["workflow_id"], "approval_poll_error", {"approval_id": approval_id, "status_code": error.response.status_code})
                continue
            status = approval["status"]
            self.store.update_proposal(proposal["proposal_id"], status=status)
            if status == "approved":
                current = self.api("GET", f"/workflows/{proposal['workflow_id']}/config").json()
                if int(current["version"]) != proposal["base_version"]:
                    self.finish_proposal(proposal, "stale_base_version", {"current_version": current["version"]})
                    continue
                try:
                    result = self.api(
                        "POST",
                        f"/workflows/{proposal['workflow_id']}/apply",
                        json={"approval_id": approval_id},
                    ).json()
                except httpx.HTTPStatusError as error:
                    status_code = error.response.status_code
                    final_status = "lapsed" if status_code == 410 else "stale_base_version" if status_code == 409 else "failed"
                    self.finish_proposal(proposal, final_status, {"status_code": status_code, "response": error.response.text[:500]})
                    continue
                self.complete_application(proposal, int(result["version"]), ts)
            elif status in ("rejected", "expired", "lapsed", "applied"):
                if status == "applied":
                    history = self.api("GET", f"/workflows/{proposal['workflow_id']}/config/history").json()
                    applied = next((item for item in history if item.get("proposal_id") == proposal["proposal_id"]), {})
                    self.complete_application(proposal, int(applied.get("version", proposal["base_version"] + 1)), ts)
                else:
                    self.finish_proposal(proposal, status, {"reasons": approval.get("reasons", [])})

    def complete_application(self, proposal: dict[str, Any], version: int, ts: str | None) -> None:
        self.store.update_proposal(proposal["proposal_id"], status="applied")
        row = self.store.get_workflow(proposal["workflow_id"])
        if row is None:
            return
        experiment = row["experiment"] or {}
        kind = proposal["kind"]
        if kind == "explore":
            experiment.update(proposal["metadata"])
            experiment.update(
                {
                    "status": "exploring",
                    "baseline_config": proposal["previous_config"],
                    "applied_version": version,
                    "start_event_id": self.store.max_event_id(),
                }
            )
        elif kind == "promote":
            experiment.update(
                {
                    "status": "verifying",
                    "winner": proposal["metadata"]["winner"],
                    "winner_reference": proposal["metadata"]["winner_reference"],
                    "promoted_version": version,
                    "verification_start_event_id": self.store.max_event_id(),
                }
            )
        elif kind == "rollback":
            experiment.update({"status": "rolled_back", "rollback_version": version})
        elif kind == "caller_id_rotate":
            experiment.update(
                {
                    "caller_id_incident_status": "applied",
                    "caller_id_version": version,
                    "caller_id_verification_start_event_id": self.store.max_event_id(),
                    "caller_id_evidence": proposal["metadata"],
                }
            )
        self.store.save_workflow(
            proposal["workflow_id"],
            row["workflow"],
            proposal["proposed_config"],
            version,
            experiment,
        )
        self.store.audit(
            ts,
            proposal["workflow_id"],
            "config_applied",
            {"proposal_id": proposal["proposal_id"], "kind": kind, "version": version, "approval_id": proposal.get("approval_id")},
        )

    def finish_proposal(self, proposal: dict[str, Any], status: str, details: dict[str, Any]) -> None:
        self.store.update_proposal(proposal["proposal_id"], status=status)
        row = self.store.get_workflow(proposal["workflow_id"])
        if row and row["experiment"]:
            experiment = row["experiment"]
            if proposal["kind"] == "caller_id_rotate":
                experiment["caller_id_incident_status"] = status
            else:
                experiment["status"] = status
            self.store.save_experiment(proposal["workflow_id"], experiment)
        self.store.audit(proposal.get("created_at"), proposal["workflow_id"], "proposal_finished", {"proposal_id": proposal["proposal_id"], "status": status, **details})


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the restartable assignment controller.")
    parser.add_argument("--url", default="http://127.0.0.1:8700", help="Simulator base URL")
    parser.add_argument(
        "--db",
        default=os.environ.get("CONTROLLER_DB", "runs/controller_state.sqlite3"),
        help="SQLite state and audit database path",
    )
    parser.add_argument("--step-minutes", type=int, default=DEFAULT_STEP_MINUTES)
    args = parser.parse_args()
    if args.step_minutes <= 0:
        parser.error("--step-minutes must be positive")
    controller = Controller(args.url, args.db, args.step_minutes)
    try:
        controller.run()
    finally:
        controller.close()


if __name__ == "__main__":
    main()