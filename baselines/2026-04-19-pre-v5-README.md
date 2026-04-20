# Pre-v5 Baseline — 2026-04-19

This baseline is the **rollback target** for the v5 dual-conviction rebuild. If v5 needs to be rolled back at any phase, restoring the v4.1.0 policy below is the canonical action.

## Identity

| Field | Value |
|---|---|
| **Active policy** | `v4.1.0` |
| **Policy hash** | `b95cb61155747fbc` (first 16 of 64) |
| **Policy created at** | 2026-04-19T04:10:30.644119+00:00 |
| **Lambda commit** | `ec04aa8` (latest v4.1.0 commit) |
| **Branch at snapshot** | `claude/quirky-sanderson-cca4b9` (clean, tracking origin/main) |
| **CI status** | Green (all 5 most recent runs success) |
| **Closed paper positions** | 18,567 |

## Files in this baseline

- `2026-04-19-pre-v5-policy.json` — Full active v4.1.0 policy JSON. POST this to `/api/policies` then activate to restore.
- `2026-04-19-pre-v5-v4-vs-v3-analysis.md` — Full v4 vs v3 ranking analysis on 18,567 closed positions. Captures the "v4.1.0 top decile is anti-predictive vs v3" finding.
- `2026-04-19-pre-v5-hr-diagnosis.txt` — Full home-run diagnosis (≥80 anomaly, individual trade examination, where home runs live in score distribution).
- `2026-04-19-pre-v5-historical-validation.md` — The v5 historical validation findings report that motivated the dual-conviction architecture.

## Headline pre-v5 metrics

From `analyze_v4_vs_v3_performance.py`:

| Score | Spearman ρ vs P&L | Top decile win % | Top decile mean P&L | Top decile big-win % |
|---|---|---|---|---|
| **v4.1.0 conviction** | +0.013 (noise) | 43.7% | −0.6% | 5.2% |
| **v3 conviction (legacy)** | +0.10 (modest) | 55.6% | +25.3% | 15.2% |

From `home_run_diagnosis.py`:

- 75–78 conviction band (n=318): **+23.3% mean P&L, 64.8% win rate** — the actual sweet spot under v4.1.0
- 78–80 band (n=90): +34.2% mean, 70% win
- **80–82 band (n=21): −8.2% mean, 42.9% win, ZERO big wins** — the score-collapses-at-80 problem
- ≥82 band (n=6): tiny sample, +4% mean, 0 big wins

From `/tmp/v5_findings_report.md` (the v5 validation):

- Spearman ρ(v4.1.0 conviction, HR200): −0.0064 (zero / slightly anti-predictive)
- Spearman ρ(v5 Wilson-lower conviction, HR200): **+0.1757** (the improvement v5 unlocks)
- Top decile under v5: **4.88% HR200 rate** (4.5× the 1.08% baseline)
- HR coverage with current 6 archetypes: 39.8% (the gap that motivates the GBM co-scorer)

## How to restore this baseline (rollback procedure)

```bash
# 1. Reactivate v4.1.0 policy (most likely sufficient)
curl -X POST "https://2nv5mt4d1k.execute-api.us-west-1.amazonaws.com/api/policies/v4.1.0/activate"

# 2. Verify
curl -s "https://2nv5mt4d1k.execute-api.us-west-1.amazonaws.com/api/policies/active" \
  | python3 -c "import sys,json;d=json.load(sys.stdin);print(d['version'],d['policy_hash'][:16])"
# Expect: v4.1.0 b95cb61155747fbc

# 3. If policy was destroyed/corrupted, re-seed from this baseline
curl -X POST "https://2nv5mt4d1k.execute-api.us-west-1.amazonaws.com/api/policies" \
  -H "Content-Type: application/json" \
  --data @baselines/2026-04-19-pre-v5-policy.json
curl -X POST "https://2nv5mt4d1k.execute-api.us-west-1.amazonaws.com/api/policies/v4.1.0/activate"

# 4. If Lambda code is broken too, version rollback
./scripts/deploy.sh rollback                    # to immediately previous version
# or
./scripts/deploy.sh versions                    # list all
./scripts/deploy.sh rollback N                  # to specific version
```

## v5 work begins

- Branch: `v5-dual-conviction` (created from this commit)
- Plan: `/Users/nicksmith/.claude/plans/i-want-you-to-giggly-tarjan.md`
- First phase: Phase 1 — Schema + Calibration Foundation
