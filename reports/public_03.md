# Grade report — public_03_load

## Outcomes vs baseline (no-op controller)

| workflow | conclusion rate | Δ | pickup | Δ | engagement | Δ | engagement gap captured | hallucinated calls (base) |
|---|---|---|---|---|---|---|---|---|
| arav_cancel_2 | 0.341 | -0.0% | 0.244 | -0.2% | 0.502 | +0.3% | 3% | 389 (408) |
| arav_kyc_1 | 0.358 | +1.1% | 0.213 | -0.6% | 0.601 | +0.9% | 4% | 330 (370) |
| arav_renewal_0 | 0.402 | +0.1% | 0.263 | -0.4% | 0.546 | +0.5% | 0% | 417 (440) |
| kest_cancel_2 | 0.350 | +3.3% | 0.243 | -0.1% | 0.514 | +3.2% | 20% | 391 (411) |
| kest_kyc_1 | 0.361 | +2.1% | 0.215 | +0.6% | 0.602 | +1.3% | 4% | 361 (339) |
| kest_renewal_0 | 0.404 | +1.0% | 0.264 | +0.1% | 0.547 | +0.8% | 0% | 442 (433) |
| nort_cancel_2 | 0.339 | +0.1% | 0.243 | +0.3% | 0.499 | -0.1% | 3% | 377 (393) |
| nort_kyc_1 | 0.356 | +0.6% | 0.214 | +0.5% | 0.596 | +0.1% | 0% | 373 (323) |
| nort_renewal_0 | 0.404 | -0.1% | 0.264 | +0.3% | 0.547 | -0.1% | 0% | 430 (411) |
| tide_cancel_2 | 0.339 | -0.1% | 0.243 | -0.7% | 0.501 | +0.3% | 3% | 385 (407) |
| tide_kyc_1 | 0.359 | +1.6% | 0.214 | -0.2% | 0.602 | +1.7% | 4% | 318 (356) |
| tide_renewal_0 | 0.403 | +0.6% | 0.264 | -0.2% | 0.546 | +0.7% | 0% | 421 (438) |
| ALL | 0.368 | +0.8% | 0.240 | -0.0% | 0.548 | +0.8% | 3% | 4634 (4729) |

## Changes applied by the controller

12 applied — neutral: 8, beneficial: 4

- nort_cancel_2 v2 (controller_auto) dims=['voice'] engageΔ=+0.005 didΔ=+0.000 → **neutral**
- kest_cancel_2 v2 (controller_auto) dims=['voice'] engageΔ=+0.005 didΔ=+0.000 → **neutral**
- arav_cancel_2 v2 (controller_auto) dims=['voice'] engageΔ=+0.005 didΔ=+0.000 → **neutral**
- tide_cancel_2 v2 (controller_auto) dims=['voice'] engageΔ=+0.005 didΔ=+0.000 → **neutral**
- nort_renewal_0 v2 (controller_approved) dims=['voice'] engageΔ=+0.001 didΔ=+0.000 → **neutral**
- kest_renewal_0 v2 (controller_approved) dims=['voice'] engageΔ=+0.001 didΔ=+0.000 → **neutral**
- kest_kyc_1 v2 (controller_approved) dims=['voice'] engageΔ=+0.013 didΔ=+0.000 → **beneficial**
- arav_renewal_0 v2 (controller_approved) dims=['voice'] engageΔ=+0.001 didΔ=+0.000 → **neutral**
- arav_kyc_1 v2 (controller_approved) dims=['voice'] engageΔ=+0.013 didΔ=+0.000 → **beneficial**
- tide_renewal_0 v2 (controller_approved) dims=['voice'] engageΔ=+0.001 didΔ=+0.000 → **neutral**
- tide_kyc_1 v2 (controller_approved) dims=['voice'] engageΔ=+0.013 didΔ=+0.000 → **beneficial**
- kest_cancel_2 v3 (controller_approved) dims=['voice'] engageΔ=+0.081 didΔ=+0.000 → **beneficial**

## Governance

```
{
  "approval_requests": 12,
  "decisions": {
    "approved": 8,
    "rejected": 1,
    "expired": 3
  },
  "rejection_reasons": {
    "approver declined (judgement call)": 1
  },
  "applied_via_approval": 8,
  "auto_applied": 4,
  "auto_apply_rejected": 0,
  "apply_conflicts": 0,
  "lapsed": 0,
  "peak_requests_per_24h": {
    "nort_renewal_0": 1,
    "nort_kyc_1": 1,
    "kest_renewal_0": 1,
    "kest_kyc_1": 1,
    "arav_renewal_0": 1,
    "arav_kyc_1": 1,
    "tide_renewal_0": 1,
    "tide_kyc_1": 1,
    "nort_cancel_2": 1,
    "arav_cancel_2": 1,
    "tide_cancel_2": 1,
    "kest_cancel_2": 1
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