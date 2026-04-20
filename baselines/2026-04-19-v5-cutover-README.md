# v5 Cutover Baseline — 2026-04-19

The v5 dual-conviction scoring regime went live.

## Identity

| Field | Value |
|---|---|
| **Active policy** | `v4.1.1` |
| **Policy hash** | `0ef0cc52ae35340c` |
| **Activated at** | 2026-04-20 02:34 UTC |
| **Lambda version at cutover** | v254 (commit `ead2daa`) |
| **v5_active** | True |
| **v5_active_scanners** | `[UNUSUAL_VOLUME, CHEAP_OPTIONS, BREAKDOWN, REVALIDATION]` |
| **v5_gbm_enabled** | True |
| **v5_gbm_hr_weight** | 0.5 |
| **v5_gbm_p_weight** | 0.0 (P GBM disabled — AUC 0.50 noise) |
| **v5_hr_threshold** | 7.0 |
| **v5_p_threshold** | 50.0 |

BREAKOUT and COMPRESSION_EXPANSION remain on v4.1.0 (no positive v5
archetypes — auto-discovery Phase 10 may surface one).

## Cutover sequence

| Time (UTC) | Action | Lambda |
|---|---|---|
| 02:33 | `/deploy.sh backend` with v5_policy wiring | v252 |
| 02:34 | POST + activate v5 policy (v4.1.0 → v4.1.1) | v252 |
| 02:35 | Smoke test #1 — found bug (opportunities list→dict) | v252 |
| 02:44 | Smoke test #2 — bug persisted (feature_sets also list) | v253 |
| 02:49 | Smoke test #3 — **54/55 evaluations carry v5 fields** | v254 |

## Files in this baseline

- `2026-04-19-v5-cutover-policy.json` — full v4.1.1 active policy JSON
- `2026-04-19-v5-cutover-README.md` — this file

## Rollback

v4.1.0 is still stored in DynamoDB, inactive. To rollback:

```bash
# 1. Reactivate v4.1.0 (30 sec)
curl -X POST "https://2nv5mt4d1k.execute-api.us-west-1.amazonaws.com/api/policies/v4.1.0/activate"

# 2. Verify
curl -s "https://2nv5mt4d1k.execute-api.us-west-1.amazonaws.com/api/policies/active" \
  | python3 -c "import sys,json;d=json.load(sys.stdin);print(d['version'],d['policy_hash'][:16])"
# Expect: v4.1.0 b95cb61155747fbc
```

If Lambda code is broken too:
```bash
./scripts/deploy.sh rollback   # → Lambda v253, then v252, etc.
```

## Verification from first live run

Smoke test #3 (run_id 20ffb9b9-b798-4d1b-97c4-64b15ffe5f69) on SMCI/PLTR/NVDA:
- 55 evaluations
- 54 populated with `v5_scoring_version="v5.0.0"`
- HR conviction range observed: 0.22–1.16 (GBM producing scores when no archetype matches — expected; market closed, no pattern matches live)
- V5 verdict reasons firing: `V5_REJECTED_BY_SCORE`, `REJECTED_BY_GATES`

Market was closed during cutover — real test comes with Monday 09:30 ET scan
when UV/CHEAP scanners will fire against actual volatility.

## Week-1 monitoring

Monday checklist:
1. CloudWatch errors in past 10 min (expect zero)
2. First UV scan produces Decisions with HR/P convictions
3. First TIER_1 candidate visible in Opportunities
4. Pipeline Monitor stages all flow (no 0-count anomalies)
5. At least one evaluation where `hr_archetype_matched != None`

Known open items:
- P model AUC 0.50 → GBM P disabled; archetypes carry P signal
- BREAKOUT/COMPRESSION stay on v4.1.0 (no v5 archetypes)
- Rolling rate recalibration not yet wired — Wilson bounds use seed
  values from archetype definitions until Phase 8
