# Grade report — public_01_dev

## Outcomes vs baseline (no-op controller)

| workflow | conclusion rate | Δ | pickup | Δ | engagement | Δ | engagement gap captured | hallucinated calls (base) |
|---|---|---|---|---|---|---|---|---|
| ks_cancel | 0.345 | +3.7% | 0.242 | +1.4% | 0.499 | +1.6% | 4% | 62 (47) |
| nw_kyc | 0.372 | +2.5% | 0.241 | -0.5% | 0.604 | +2.3% | 5% | 25 (21) |
| nw_renewal | 0.417 | -0.6% | 0.264 | +0.2% | 0.544 | -0.3% | 0% | 56 (42) |
| ALL | 0.375 | +1.8% | 0.249 | +0.6% | 0.535 | +1.0% | 2% | 143 (110) |

## Changes applied by the controller

3 applied — neutral: 2, beneficial: 1

- ks_cancel v2 (controller_auto) dims=['voice'] engageΔ=+0.005 didΔ=+0.000 → **neutral**
- nw_renewal v2 (controller_approved) dims=['voice'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- nw_kyc v2 (controller_approved) dims=['voice'] engageΔ=+0.013 didΔ=+0.000 → **beneficial**

## Governance

```
{
  "approval_requests": 2,
  "decisions": {
    "approved": 2,
    "expired": 0
  },
  "rejection_reasons": {},
  "applied_via_approval": 2,
  "auto_applied": 1,
  "auto_apply_rejected": 0,
  "apply_conflicts": 0,
  "lapsed": 0,
  "peak_requests_per_24h": {
    "nw_renewal": 1,
    "nw_kyc": 1
  }
}
```

## Incidents

```
[]
```

## Decaying variants — share of in-population calls still on the variant, by day after onset

```
{}
```

Credits used: 0 · validation runs: 0 · LLM fix calls: 0