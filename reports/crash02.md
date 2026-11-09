# Grade report — public_02_incidents

## Outcomes vs baseline (no-op controller)

| workflow | conclusion rate | Δ | pickup | Δ | engagement | Δ | engagement gap captured | hallucinated calls (base) |
|---|---|---|---|---|---|---|---|---|
| ks_cancel | 0.244 | -1.4% | 0.183 | -0.2% | 0.474 | -0.5% | 1% | 348 (330) |
| nw_kyc | 0.388 | +3.6% | 0.242 | -0.2% | 0.615 | +3.9% | 8% | 44 (31) |
| nw_renewal | 0.436 | +2.1% | 0.265 | +1.3% | 0.549 | +0.1% | 4% | 62 (55) |
| ALL | 0.338 | +1.3% | 0.223 | +0.4% | 0.534 | +0.8% | 3% | 454 (416) |

## Changes applied by the controller

3 applied — neutral: 3

- ks_cancel v2 (controller_auto) dims=['voice'] engageΔ=+0.002 didΔ=+0.000 → **neutral**
- nw_renewal v2 (controller_approved) dims=['voice'] engageΔ=-0.004 didΔ=+0.000 → **neutral**
- nw_kyc v2 (controller_approved) dims=['voice'] engageΔ=+0.007 didΔ=+0.000 → **neutral**

## Governance

```
{
  "approval_requests": 3,
  "decisions": {
    "approved": 2,
    "expired": 1
  },
  "rejection_reasons": {},
  "applied_via_approval": 2,
  "auto_applied": 1,
  "auto_apply_rejected": 0,
  "apply_conflicts": 0,
  "lapsed": 0,
  "peak_requests_per_24h": {
    "nw_renewal": 1,
    "nw_kyc": 1,
    "ks_cancel": 1
  }
}
```

## Incidents

```
[
  {
    "type": "did_flagged",
    "workflow": "ks_cancel",
    "at": "D2 10:00",
    "flagged_did_share_by_day": {
      "2": 0.506,
      "3": 0.501,
      "4": 0.51,
      "5": 0.5,
      "6": 0.499,
      "7": 0.496,
      "8": 0.506,
      "9": 0.488
    }
  },
  {
    "type": "supervisor_edit",
    "workflow": "ks_cancel",
    "at": "D3 11:30",
    "mttr_minutes": null,
    "fixed_by": null
  },
  {
    "type": "supervisor_edit",
    "workflow": "nw_renewal",
    "at": "D5 15:00"
  }
]
```

## Decaying variants — share of in-population calls still on the variant, by day after onset

```
{}
```

Credits used: 0 · validation runs: 0 · LLM fix calls: 0