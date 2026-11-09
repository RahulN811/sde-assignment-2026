# Outcome-First Agent Loop — Submission

**Name:** _TODO_ · **Date:** _TODO_ · **Hours actually spent (be honest):** _TODO_ · **Language / storage:** Python 3.11 · SQLite (stdlib, WAL mode), `httpx` for HTTP. No new services.

---

## 0. TL;DR

- An always-on, restartable controller (`controller.py`, ~1,050 lines) that ingests every event into SQLite, detects caller-ID degradation, runs one conservative voice experiment per workflow (82/18 split, Wilson intervals at z = 3), and routes every change through `/approvals` or policy-permitted auto-apply.
- **public_01:** conclusion rate **+1.8%** vs baseline, 3 changes applied (1 beneficial, 2 neutral, **0 harmful**).
- **public_02:** conclusion rate **+2.9%**, the flagged caller ID was removed 9 h after it went bad (flagged share 42% → 0%), **0 harmful** changes, but ks_cancel's primary KPI (engagement) ended **−3.0%** vs baseline.
- Crash-safe (killed 4× mid-run, including twice while approvals were pending: nothing lost, nothing doubled) and auditable (`scripts/why.py`).
- **Deliberately not built:** cohorts, call-time optimisation, hallucination response (0 credits spent), supervisor view, loop observability.

## 1. Assumptions

1. Thresholds are fixed constants in code (`MIN_ARM_CALLS = 100`, `MIN_ARM_CONNECTIONS = 60`, `CONFIDENCE_Z = 3.0`, caller-ID gap ≥ 5 pts). They are not fitted to any scenario's hidden truth, because hidden scenarios differ.
2. The controller only knows what the HTTP API returns. It never reads `runs/`, imports `sim`, or parses scenario files.
3. A supervisor edit is intentional. The controller pauses its own experiment rather than reverting or overriding it.
4. `affected_leads` is approximated by the workflow's daily batch size, an upper bound for one day of exposure.
5. A 60-simulated-minute decision step is fresh enough. Detection windows are 24 h, so finer steps would add round-trips without changing decisions much.
6. SQLite is acceptable for this exercise because it is an embedded file, not a service someone operates at 2 a.m. In production the same schema moves to the Postgres SRE already runs (§8, §14).
7. Voice and welcome message are heard *after* pickup, so they mainly move engagement. Pickup is driven by caller ID and call time. (The controller does not yet exploit this; see §13.)
8. The `default` cohort is large enough to act on alone. Per-cohort effects are not modelled (§2, C2).

## 2. Scope decisions

| # | Capability | Built / Partial / Cut | Why | What it would take to finish |
|---|---|---|---|---|
| C1 | Event ingestion | **Built** | Floor requirement; everything depends on it | Store aggregates instead of raw JSON twice (7 GB on public_03) |
| C2 | Shared cohort object | **Cut** | High design cost; needs min-size rules first | Cohort registry table keyed by ID, used by stats, proposals and view |
| C3 | Bootstrapping from history | **Partial** | History is ingested and used as the incumbent baseline | Use history for caller-ID health and hour-of-day priors |
| C4 | Call-time optimisation | **Cut** | Biggest design surface (per-person, 200k map); can't do it properly in the time | Hour-of-day pickup per cohort from history → `schedule.hour_weights` |
| C5 | Caller-ID health | **Built** | Clear incident, clear evidence, cheap (no credits) | Re-propose after expiry; re-admit a recovered ID; explore unused IDs |
| C6 | Variant experiments | **Partial** | One dimension (voice), conservative split | Welcome × voice, sequential testing, per-cohort arms |
| C7 | Promotion, decay, rollback | **Partial** | Promote + one post-promotion check + rollback | Continuous decay monitoring on a rolling window |
| C8 | Hallucination response | **Cut** | Needs LLM + validation + credit policy; risky to do half-way | Rate detector → `/llm/suggest-fixes` → `/validation/runs` → propose with evidence |
| C9 | Governance client | **Partial** | Lifecycle, idempotent `proposal_id`, stale check, auto-apply within policy | Approver-hours timing, batching, re-proposal after expiry/stale |
| C10 | Honest impact / leads / dry run | **Partial** | Fields filled, but roughly | Impact from the CI lower bound × exposure; real replay dry run |
| C11 | Supervisor view | **Cut** | A new surface; the audit store answers the same questions for now | Read-only endpoint over `proposals` + `workflows.experiment` |
| C12 | Audit store | **Built** | Non-negotiable constraint #6 | — (`scripts/why.py`) |
| C13 | Crash recovery, demonstrated | **Built** | Non-negotiable constraint #8 | — (`scripts/crash_test.py`) |
| C14 | Loop observability | **Cut** | Only a stdout progress line today | Metrics table + `/healthz` with ingest lag, decisions/h, credits |
| C15 | Fair credit / attention allocation | **Cut** | No credits are spent; one open proposal per workflow caps attention | Per-tenant credit budget and request quota |

**Principle:** first the things that can't be wrong (governance boundary, crash safety, idempotency, audit), then one incident with unambiguous evidence and zero credit cost (caller ID), then one conservative experiment loop. Anything that needs credits, a new UI surface, or per-cohort statistics was cut rather than sketched.

## 3. Stakeholders

Each memo was checked against the workflow policies the simulator actually enforces (`GET /workflows/{id}` → `governance`, `objective`).

### 01 — Nikhil Rao, VP Sales

- **Accepted:** a per-client lift number. The grader output gives one per workflow.
- **Declined:** a single blended score (Product and the README forbid blending pickup and engagement); "winning by lunch, live for everyone by evening" (needs ≥100 calls and non-overlapping z = 3 intervals, plus approval latency of 20–480 min inside approver hours); rolling out the "persuasive" prompt everywhere (prompt changes need validation evidence, `prompt_version` is not auto-appliable on any workflow, and winners don't cross clients).
- **Wrong claims:** *"Kestrel has turned on auto-apply for everything."* False: `ks_cancel` allows only `schedule, caller_id, welcome_message, voice`, with `max_shift 0.2`. Prompt changes and any shift above 20% still need approval. *"Northwind is fine with auto-apply on KYC."* False: `nw_kyc` has `auto_apply.enabled: false`, and Northwind's Compliance head requires a named human approval for anything customer-facing.
- **Reply:**
  > Nikhil — you'll have a per-client lift number for the summit, with pickup and engagement shown separately (blending them is something Product and our regulated clients have ruled out). I can't push winners straight through: Kestrel's policy only auto-applies small voice/welcome/caller-ID shifts (≤20%), and Northwind KYC has auto-apply switched off entirely, so every change there needs their approver. "Fast" here means hours, not minutes, and that's what lets their compliance teams sign off. The persuasive prompt needs validation evidence per workflow before anyone can approve it. Happy to walk you through a real change timeline you can show on stage.

### 02 — Meera Iyer, Head of Compliance, Northwind (client)

- **Accepted:** no customer-facing change without human approval. The controller sends every voice change on `nw_renewal`/`nw_kyc` through `/approvals`, because voice is not in their `allowed_dims`. "Slow and right": z = 3 intervals, an 82/18 split, a guardrail check. Auditability: every call event stores `config_version` and `variant`, and the audit table links each version to its proposal and approval.
- **Not yet met:** the controller sent Northwind requests at D0 00:00. They sat in the queue until 10:00. It should hold requests until approver hours.
- **Wrong claim:** *"operational settings such as caller-ID rotation are covered by the policy we signed."* True for `nw_renewal` (`caller_id` and `schedule` are auto-appliable, `max_shift 0.5`), **false for `nw_kyc`**, where auto-apply is disabled for every dimension, so caller-ID rotation there needs approval too.
- **Reply:**
  > Meera — confirmed: nothing a customer hears on Northwind changes without your approver. Every voice change so far went through approval, and for any customer on any day we can show the exact variant, config version, and the approval behind it from our own store, well inside 24 hours. One correction: on KYC your policy disables auto-apply for every setting, including caller-ID rotation, so those will come to your approver too. We currently send some requests outside your 10:00–19:00 (KYC 10:00–18:00) hours. That's on our list to fix, and requests will be held until your approvers are in.

### 03 — Kabir Menon, Platform & SRE

- **Accepted:** kill/restart test (§9: killed 4×, including twice with approvals pending; no double apply, no lost approval, no lost/duplicated event). Idempotency (event-id primary key, `proposal_id` written before sending, reconciliation by `proposal_id` on restart). Speed target: ~190 s per simulated day of `public_03_load` including the simulator, against his 10-minute target (measured on a Windows laptop, not the 4 vCPU box).
- **Pushed back:** SQLite is new state, but it's a single file inside the process, with nothing to operate. In production I'd move the schema to his Postgres rather than add anything.
- **Not met:** loop health without logs (C14 cut), and actionable alerts (none emitted yet; §10 lists what I'd page on).
- **Wrong claim:** none found.
- **Reply:**
  > Kabir — kill/restart is demonstrated, not promised: `scripts/crash_test.py` hard-kills the controller four times (twice with approvals pending) and verifies no double-apply, no lost approval and no lost event. One public_03 day takes ~3 minutes end to end. What breaks first at 10× is disk (raw events stored twice, ~70 GB for 5 days) and bootstrap memory (2.5 GB peak), not throughput. State is a SQLite file in-process today, with no new service; for production I'd move it to your Postgres. Loop health metrics aren't built yet. That's the honest gap.

### 04 — Anita Desai, Ops Supervisor, Kestrel (client)

- **Accepted:** "don't undo my changes." Supervisor edits are detected via `config/history` actor, audited, and the controller's experiment is paused, never reverted. "Don't make it robotic": no prompt changes are made at all, and every promotion checks the guardrail KPI.
- **Pushed back / not met:** "tell me before anything changes": Kestrel's own policy allows auto-applying small voice shifts, and the controller did auto-apply the 82/18 voice test at D0 without notifying her. "Two requests a day, never before 11:00": stricter than the policy (`max_requests_per_24h: 4`, approver hours 10–19). The controller currently keeps at most one open request per workflow, but does not hold requests until 11:00. One screen: not built (C11).
- **Wrong claim:** none against the policy. Her limits are stricter than the policy, which is her right, so I'd treat them as voluntary limits.
- **Reply:**
  > Anita — your prompt edits are never undone: when you change something, we log it and pause our own test on that workflow. We never change prompts ourselves. Two honest gaps: your policy lets us auto-apply small voice tests, and we did that without telling you first; and requests can arrive before 11:00. I'll hold requests until 11:00 and keep to two a day, even though the policy allows four. The single screen isn't built yet. For now I can send you a daily summary of what's being tested and what changed.

### 05 — Tara Kulkarni, Head of Product

- **Accepted:** separate KPIs (`metric_counts` never blends; primary and guardrail are evaluated separately). Evidence = only what happened: stats only count calls actually dialled under the experiment's config version, after its start event. Winners update only their workflow.
- **Pushed back:** "all three P0s": caller-ID health is built; per-person call-time and the supervisor view are cut (§2). Min cohort size: chosen as ≥100 calls and ≥60 connections per arm at z = 3, which costs speed (most experiments never reach a winner in 5–10 days) in exchange for a very low false-winner rate.
- **Not met:** shared cohort object (C2 cut). "Expired = rejection, re-queue with fresh data": the controller treats expiry as final and never re-queues, which is a real bug (§13). Impact score is not yet meaningful enough to rank a queue by (fixed 0.05 for explore proposals).
- **Wrong claim:** none; her definitions match the contract.
- **Reply:**
  > Tara — pickup and engagement are never blended, and learning only counts calls that actually ran under the experiment's config. Of the three P0s, I built caller-ID health; per-person call-time and the supervisor view are cut, because doing them half-way would hurt more than help. Minimum sample: 100 calls / 60 connections per arm with 99.7% intervals. Slow, but false winners are rare. Two gaps I own: expired proposals aren't re-queued yet, and impact scores are too rough to rank a queue by. Both are next on the list.

### 06 — Aryamann, CTO

- **Accepted:** explainability over size of lift (every change traces through `scripts/why.py`); "no compute": 0 credits spent; choosing: §2 lists what was cut; honesty about scale (§8).
- **Wrong claims found in other memos:** Kestrel "auto-apply for everything" (01), Northwind KYC auto-apply (01), KYC caller-ID covered by policy (02).
- **Reply:**
  > I chose governance, crash safety, audit, caller-ID health and one conservative experiment loop, and cut cohorts, call-time, hallucination response, the supervisor view and observability. Zero credits were spent. What breaks first at scale is storage and bootstrap memory, not throughput. The biggest behavioural gap is that the controller gives up after an expired or rejected proposal; that's the first fix.

## 4. Architecture

```
             Simulator (HTTP only)
 ┌───────────────────────────────────────────────────┐
 │ /sim/advance  /events/chunks  /workflows/*        │
 │ /approvals    /workflows/{id}/apply  PUT config   │
 └───────┬───────────────────────────────▲───────────┘
         │ events (NDJSON chunks)        │ proposals / applies
         ▼                               │
 ┌───────────────────────────────────────┴───────────┐
 │ Controller.run()  — loop every 60 sim-minutes      │
 │  1 ingest_chunks()        events → events, calls   │
 │  2 sync_workflows()       config + history,        │
 │      reconcile_submissions()  crash fix-up         │
 │      supervisor edit? → pause experiment           │
 │  3 process_approvals()    approved → apply         │
 │  4 evaluate_workflows()   caller-ID check,         │
 │      explore → promote → verify / rollback         │
 │  submit_candidate()  save proposal → auto | approval│
 └───────────────────────┬───────────────────────────┘
                         ▼
 ┌───────────────────────────────────────────────────┐
 │ SQLite (WAL, synchronous=FULL)                     │
 │ meta(last_chunk, scenario, sim_minute)             │
 │ events · calls · workflows(config, experiment)     │
 │ proposals(status, approval_id, base_version)       │
 │ audit(ts, workflow, action, details)               │
 └───────────────────────────────────────────────────┘
```

**On restart:** `begin_run()` keeps the DB if the scenario name matches and the sim clock hasn't gone backwards. `ingest_missing_chunks()` resumes after `meta.last_chunk`. `reconcile_submissions()` looks up every open `proposal_id` in config history and `/approvals`, marks it applied, copies its status, or sends it if it never left.

## 5. The learning loop

- **Cohorts:** not modelled. All statistics use `cohort_id = 'default'`. This avoids fragmenting traffic, at the cost of missing per-cohort winners.
- **Experiment design:** one dimension per workflow (voice, or welcome message if there's only one voice). The incumbent keeps 82% of traffic, and the others share 18% equally. The shift is 0.18, which is ≤ Kestrel's `max_shift 0.2`, so it can auto-apply there. No welcome × voice interaction is modelled.
- **Deciding a winner:** deterministic, no LLM. A candidate wins only if the **lower** bound of its Wilson interval (z = 3) on the primary KPI is above the incumbent's **upper** bound, each arm has ≥100 calls and ≥60 connections, and the guardrail KPI's lower bound hasn't fallen more than the policy's `*_max_drop` below the incumbent's upper bound. This is a conservative, repeated-look test with no formal alpha spending. z = 3 on both sides is the crude multiple-comparisons guard: per look and per arm the false-winner probability is well below 0.1%, but it is checked hourly, and the total rate is not measured.
- **Decay and rollback:** after promotion, the winner's post-promotion upper bound is compared with its in-experiment lower bound (primary and guardrail). Degraded → rollback proposal to the recorded pre-experiment config. This check runs once; there is no continuous decay monitoring (C7 partial).
- **Call time and caller ID:** call time is cut. Caller ID: a rolling 24 h window, ≥100 calls per active ID; an ID is unhealthy if the best ID's lower bound exceeds its upper bound by ≥5 pts. The unhealthy ID's weight is redistributed to the healthy IDs. No exploration of unused IDs.
- **Guardrail KPI:** checked at promotion (above), at verification (rollback trigger), and after caller-ID rotation (`guardrail_breach_review` status).

## 6. Governance

```
                submit_candidate()
                        │ saved to DB first
         ┌──────────────┴──────────────┐
         ▼                             ▼
  submitting_auto               submitting_approval
   PUT config                    POST /approvals
   ├ 200 → applied               ├ 409 → stale_base_version
   ├ 403 → submitting_approval   └ 200 → pending
   └ 409 → stale_base_version             │
                         ┌────────────────┼────────────────┐
                         ▼                ▼                ▼
                     approved          rejected          expired
                         │
          version still == base_version?
             ├ no  → stale_base_version
             └ yes → POST apply
                     ├ 200 → applied
                     ├ 410 → lapsed
                     └ 409 → stale_base_version
```

- **Re-proposal:** not implemented. Rejected, expired, lapsed and stale proposals are terminal, and that workflow's experiment stays stopped (§13).
- **Batching and timing:** at most one open proposal per workflow. No approver-hours timing (requests can be sent at 00:00), no batching across dimensions.
- **Auto-apply:** used whenever the policy permits it (dimension in `allowed_dims` and shift ≤ `max_shift`). The controller never voluntarily declines auto-apply; Anita's memo argues it should notify first.
- **Impact / affected leads / dry run:** impact = observed rate difference (promote, caller ID) or a fixed 0.05 (explore). Affected leads = daily batch size. Dry run = the observed per-arm counts that justify the change, not a true replay. These are honest about their inputs but too rough to rank a queue by.
- **Supervisor edit mid-experiment:** detected via the config-history actor, audited as `supervisor_config_change_detected`, experiment set to `paused_supervisor_edit`. It never resumes (§13).

## 7. Incidents

**Caller-ID degradation:** 24 h window, ≥100 calls per ID, Wilson z = 3, ≥5 pt gap. Expected false positives are low because the intervals must be separated by 5 pts at 99.7% each, but this is not measured. No credits are used.

**public_02 timeline (normal run):**

| Time | Event |
|---|---|
| D2 10:00 | Caller ID `D2` on `ks_cancel` gets spam-flagged (grader truth) |
| D2 18:00 | Controller detects it: D1 585/2398 (24.4%) vs D2 327/2402 (13.6%) pickup |
| D2 18:00 | Proposal `caller_id_rotate` → `apr_00003` (not auto-appliable: shift 0.5 > `max_shift` 0.2) |
| D2 18:42 | Approved |
| D2 19:00 | Applied, v3: `{"D1": 1.0}`. Flagged-ID share: 42% on D2, 0% every day after |
| D2 20:00 | Verified on 400 post-change calls: engagement +1.5 pts → `verified_helpful` (a premature check: one hour of data) |
| D3 11:30 | Supervisor ships a new `prompt_version` on `ks_cancel` → detected and audited, experiment paused, **no hallucination response** (C8 cut; grader MTTR = none) |
| D5 15:00 | Supervisor edits voice on `nw_renewal` → detected, experiment paused |

Detection lag of ~8 h comes from the 24 h window diluting the post-flag calls. A shorter window, or a change-point test, would catch it sooner.

## 8. Scale

Measured on `public_03_load` (Windows 11 laptop, simulator and controller on the same machine). Details: [`reports/public_03_load_numbers.md`](reports/public_03_load_numbers.md).

| Measure | Value |
|---|---|
| History bootstrap | 5.54 M events in ~224 s → **~24,700 events/s** |
| Live run | 4.2 M events over 5 sim days in ~946 s → **~190 s per sim day** (incl. simulator) |
| Decision lag | ≤ 60 sim-minutes (one step), evaluated right after ingest |
| Peak memory | **~2.5 GB** during bootstrap; ~80–100 MB during live days |
| Disk | ~7.0 GB SQLite at the end |
| Outcome | +0.8% conclusion rate, 12 applied (4 beneficial, 8 neutral, 0 harmful) |

**What breaks first at 10×:** (1) disk, ~70 GB, because raw events are stored twice; (2) bootstrap memory and time (~37 min before the first decision); (3) `stats()` re-reading every call row into Python each hour. **Partitioning:** workflows are independent, so shard by tenant/workflow: one ingest consumer writes per-workflow hourly aggregates (`workflow, cohort, variant, hour → calls, connected, engaged`), and decision workers own disjoint workflow sets and read only aggregates.

## 9. Reliability

- **Method:** [`scripts/crash_test.py`](scripts/crash_test.py) starts a fresh simulator, runs the controller, and **force-kills** it (TerminateProcess / SIGKILL) four times: when the first proposal is in flight, at D2 14:00, when the next proposal is in flight, and at D6 11:00. It restarts each time against the same DB and then verifies the outcome.
- **Result** ([`reports/crash_recovery_log.txt`](reports/crash_recovery_log.txt)): exit 0, scenario finished; 0 double-applied, 0 double-requested; 0 proposals stuck half-sent; events 324,312 = simulator's 324,312; pending approvals `apr_00001`, `apr_00002` and `apr_00003` were all picked up after restart.
- **State lost:** none observed. At most one hour of ingestion is redone; it is idempotent.
- **Idempotency keys:** `event_id` (primary key, `INSERT OR IGNORE`); `proposal_id` (UUID saved before sending; the simulator rejects duplicates with 409); events and `last_chunk` are committed in one transaction.
- **4xx/5xx:** 403 auto-apply → falls back to approval; 409 stale → `stale_base_version`; 410 → `lapsed`. **Other errors (5xx, timeouts) are not retried.** They raise and stop the process, and `run.sh` does not restart it. That's a known gap (§13).
- **Note:** outcomes vary between runs because approvals are random (`base_reject_rate`, latency). In one crash run, the caller-ID approval expired; that was the approver, not the crash, but it exposed the no-re-proposal bug.

## 10. Audit & observability

```bash
python scripts/why.py --db runs/controller_state.sqlite3 --workflow ks_cancel --at 2027-01-06T19:00
```

```
2027-01-06T18:00  caller_id_degradation_detected     {"baseline_stats": {"D1": {"calls": 2398, "connected": 585, ...}, "D2": {"calls": 2402, "connected": 327, ...}}, "unhealthy_ids": ["D2"], ...}
2027-01-06T18:00  proposal_prepared                  {"kind": "caller_id_rotate", "proposal_id": "8d82ecc9-...", ...}
2027-01-06T18:00  approval_requested                 {"approval_id": "apr_00003", "status": "pending", ...}
2027-01-06T18:42  approval_decided_event             {"approval_id": "apr_00003", "status": "approved", ...}
2027-01-06T19:00  config_applied                     {"approval_id": "apr_00003", "kind": "caller_id_rotate", "version": 3, ...}
2027-01-06T19:00  config_changed_event               {"actor": "controller_approved", "changed_dims": ["caller_id"], "version": 3, ...}
2027-01-06T20:00  caller_id_rotation_verified        {"post_change_stats": {"calls": 400, ...}, "primary_change": 0.0151, "status": "verified_helpful", ...}
2027-01-07T11:30  config_changed_event               {"actor": "supervisor", "changed_dims": ["prompt_version"], "version": 4, ...}
2027-01-07T11:30  supervisor_config_change_detected  {"changed_dims": ["prompt_version"], "version": 4}
```

**Why** (D2 pickup 13.6% vs 24.4% over 2,400 calls each), **who** (approver, `apr_00003`), **what happened next** (verified, then a supervisor prompt edit the next day).

**Would page on:** controller not advancing for > 15 wall-minutes; ingest lag > 2 sim-hours; a proposal stuck in `submitting_*` across two loops; repeated 5xx from the simulator; credits < 20% during an open incident.
**Would not page on (morning summary instead):** a rejection or expiry; a supervisor edit; an experiment with no winner yet.
None of these alerts is implemented (C14 cut).

## 11. Results

**public_01** ([report](reports/public_01.md))

| workflow | conclusion rate | Δ | pickup | Δ | engagement | Δ |
|---|---|---|---|---|---|---|
| ks_cancel | 0.345 | +3.7% | 0.242 | +1.4% | 0.499 | +1.6% |
| nw_kyc | 0.372 | +2.5% | 0.241 | −0.5% | 0.604 | +2.3% |
| nw_renewal | 0.417 | −0.6% | 0.264 | +0.2% | 0.544 | −0.3% |
| ALL | 0.375 | **+1.8%** | 0.249 | +0.6% | 0.535 | +1.0% |

3 applied: 1 beneficial, 2 neutral, 0 harmful. 2 approvals (both approved), 1 auto-apply.

**public_02** ([report](reports/public_02.md))

| workflow | conclusion rate | Δ | pickup | Δ | engagement | Δ | hallucinated (base) |
|---|---|---|---|---|---|---|---|
| ks_cancel | 0.266 | +7.4% | 0.203 | +10.4% | 0.462 | **−3.0%** | 370 (330) |
| nw_kyc | 0.379 | +1.2% | 0.242 | −0.0% | 0.601 | +1.5% | 29 (31) |
| nw_renewal | 0.428 | +0.3% | 0.264 | +1.0% | 0.542 | −1.1% | 55 (55) |
| ALL | 0.344 | **+2.9%** | 0.232 | +4.3% | 0.522 | −1.6% | 454 (416) |

4 applied: 1 beneficial (caller-ID rotation), 3 neutral, 0 harmful. 3 approvals (all approved), 1 auto-apply.

**Interpretation:**
- **Where it helped:** almost all of the public_02 gain is the caller-ID fix on `ks_cancel` (pickup +10.4%).
- **Where it hurt:** `ks_cancel`'s **primary** KPI, engagement, ended −3.0% vs baseline. My likely explanation is that its 82/18 voice test was paused by the D3 supervisor edit and never resumed, so 18% of traffic stayed on unproven voices for the remaining 7 days. Recovered pickups may also be harder to engage. More hallucinated calls (370 vs 330) are probably a side effect of more connected calls, plus the unaddressed bad prompt.
- **Small lifts overall:** "engagement gap captured" is 0–8%. Voice-only tests at z = 3 rarely reach a winner, and each workflow gets one experiment ever.
- **What I'd change:** resume or close experiments after a supervisor edit, re-propose after expiry, and test welcome message next.

## 12. Trade-offs & rejected alternatives

| Option | Why considered | Why rejected / what instead |
|---|---|---|
| Thompson-sampling bandit | Shifts traffic to winners faster | Allocation shifts every step would hit `max_shift` and approval limits; harder for compliance to explain. Fixed 82/18 + CI test instead |
| Bayesian posterior for winners | Natural stopping rule | Wilson + z = 3 is deterministic, simple to explain, and conservative |
| Postgres | SRE already runs it | Not available in the grading box; SQLite is in-process with the same schema |
| Store only aggregates | 10× less disk | Raw events kept to answer "what did this customer hear" for audits; aggregates are the next step |
| 15-minute decision step | Faster reaction | 4× more round-trips, while detection windows are 24 h anyway |
| Auto-revert supervisor edits | Protects experiments | Violates the governance boundary and Anita's explicit ask; pause instead |

## 13. Known weaknesses

1. **Gives up after one "no".** Rejected, expired, lapsed, stale, completed or supervisor-paused experiments never restart. A bad caller ID stays in use if its one approval expires.
2. **No approver-hours timing.** Requests are sent at any hour (e.g. D0 00:00) and wait in the queue; Kestrel's 12 h TTL makes expiry likely.
3. **Voice tested on pickup-primary workflows** (`nw_kyc`): voice is heard after pickup, so it can barely move pickup, and the test mostly measures the guardrail.
4. **No cohorts, no call-time, no hallucination response.**
5. **Decay is checked once**, right after promotion; caller-ID verification can run on one hour of data.
6. **No retry on 5xx/timeouts.** The process exits and `run.sh` doesn't restart it, which would lose the run in grading.
7. **Rough proposal numbers:** fixed explore impact score, affected leads = batch size, dry run = observed counts.
8. **Storage:** raw events stored twice; 7 GB for public_03; `stats()` rescans rows every hour.

## 14. If this went to production at Formi next month

1. Add retry with backoff for 5xx and timeouts, and a supervisor process that restarts the controller (crash-safety already exists; restart doesn't).
2. Re-propose after expiry/stale with fresh data, and resume or close experiments after supervisor edits.
3. Hold requests until `approver_hours` (and voluntary limits like Kestrel's 11:00 / 2 per day); batch per workflow.
4. Replace raw-event storage with hourly aggregates in Postgres; shard decision workers by tenant.
5. Loop metrics (ingest lag, decisions/hour, credit burn) and the page/no-page alerts in §10.
6. A read-only supervisor view over `proposals` and `experiment` state, plus a weekly summary for Northwind.
7. Then the capabilities with the most lift: per-cohort experiments, and call-time from history.
