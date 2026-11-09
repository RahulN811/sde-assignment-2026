# Load test — public_03_load

Run on: Windows 11 laptop, Python 3.11, simulator and controller on the same machine.
Commands:

```bash
python -m sim baseline --scenario scenarios/public_03_load.yaml --run-dir runs/base_public03
python -m sim serve    --scenario scenarios/public_03_load.yaml --run-dir runs/mine_public03 --force
python controller.py --url http://127.0.0.1:8700 --db runs/controller03.sqlite3
PYTHONUTF8=1 python -m sim grade --run runs/mine_public03 --baseline runs/base_public03 --out reports/public_03.md
```

## Result

Finished all 5 simulated days with no manual steps (exit code 0). Grade: [public_03.md](public_03.md).

| Measure | Value |
|---|---|
| Total wall time | 1170 s (~19.5 min) for 5 sim days + 7 days of history |
| History bootstrap | 5,537,379 events in ~224 s → ~24,700 events/s |
| Live run | 4,200,084 events over 5 sim days in ~946 s (~190 s per sim day, includes simulator generation time) |
| Speed vs real time | 5 sim days in ~16 min ≈ 450× faster than real time |
| Decision lag | Controller evaluates every 60 sim-minutes, right after ingesting that hour's chunk |
| Peak memory (RSS) | ~2.5 GB during history bootstrap; ~80–100 MB during the live days |
| SQLite size at end | ~7.0 GB (9.7M raw events + 7.4M call rows) |
| Approvals | 12 requests: 8 approved, 1 rejected, 3 expired; 4 auto-applied; 0 apply conflicts |
| Outcome | Conclusion rate +0.8% overall vs baseline; 12 changes applied (4 beneficial, 8 neutral, 0 harmful) |
| Credits used | 0 of 400 |

## Where it breaks at 10×

- **Disk:** every event is stored twice (raw `events` + parsed `calls`), so 10× ≈ 70 GB for 5 days. Fix: keep only parsed rows plus hourly aggregates, and drop raw JSON after parsing.
- **Bootstrap memory:** the 2.5 GB peak during history load would exceed the 8 GB box at 10×. Fix: stream history chunks in smaller batches instead of holding a whole chunk response in memory.
- **Bootstrap time:** ~37 min at 10× before the first decision. Fix: aggregate history into per-cohort counts instead of inserting every row.
- **Throughput is fine:** the live phase handles ~190 s of work per sim day, so even at 10× it stays far ahead of a real 12-hour calling day.
