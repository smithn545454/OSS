# Baseline: pillar-stable-v4-2026-04-17-fixed (Phase 7 + same-day wiring fix)

## Identifiers

- **Git tag:** `pillar-stable-v4-2026-04-17-fixed`
- **Lambda version:** `v243` (commit `866300b`, B1 + C1 — `CURRENT_SCORING_REGIME` bumped to v4 + Phase 7 outcomes documented)
- **Policy:** `v4.0.0`, hash `5f2380b8132bb331`, active since `2026-04-17T19:50:24Z` (unchanged from pre-fix snapshot)
- **Frontend:** CloudFront bundle from commit `a36b14c` (Phase 4)
- **Activated by:** Pillar v4 Phase 7 sequence (per [docs/pillar_v4_execution_plan.md](../docs/pillar_v4_execution_plan.md) §7.10)

## What changed since `pillar-stable-v4-2026-04-17` (v238)

This baseline pins the post-fix state: A1 (PriceHistoryService wiring) + B1 (`CURRENT_SCORING_REGIME` bump) + C1 (plan §7.10 outcomes doc) all landed on `main` between v238 and v243.

The active policy (`v4.0.0`, hash `5f2380b8132bb331`) is identical to the pre-fix snapshot — only Lambda code and the regime constant changed.

### Lambda version walk
- **v238** — Phase 6 baseline; v4.0.0 activated against this code; data-availability log showed `ma_200=0/N high_52w=0/N sector_rs_20d=0/N historical_move_magnitude=0/N`. Captured in [2026-04-17-v4-README.md](2026-04-17-v4-README.md).
- **v240** — parallel session's `fix(pillar-v4): wire Phase 1 services into Stage 4` (commit `52b06fc`). Switched bar-fetch path from Polygon grouped to `PriceHistoryService` (DDB-backed), wired `EarningsCalendarService` and `sector_map` through `FeatureComputer` / `FeatureComputationStage` / `run_feature_computation` / orchestrator (both batch + streaming) and the UV bridge in `main.py`.
- **v241** — this branch's interim fix (commits `7e45d00` + `6e1d502`) inadvertently overwrote v240. Rolled the PriceHistoryService wiring back on production.
- **v242** — merge commit `61e00d0` reconciled the two: took `--theirs` for `app/features/stage.py` and `app/scanners/orchestrator.py` so the parallel session's superset fix won. Restored PriceHistoryService wiring on production.
- **v243** — this commit (`866300b`): `CURRENT_SCORING_REGIME = "v4"` in `pattern_discovery.py` (cut-over hygiene per Phase 6 deferral §7.9 #2) + Phase 7 outcomes documented in plan §7.10.

## Verification (post-fix Lambda v242)

| Metric | Result |
|---|---|
| Backend test suite | 2278 passing (1 flaky `test_finnhub` pre-existing, env-shared state) |
| CloudWatch ERRORs | only pre-existing transient Polygon JSON parse errors |
| Active policy | v4.0.0 (unchanged) |
| `ma_200` coverage | 87–100% (was 0%) |
| `high_52w` coverage | 87–100% (was 0%) |
| `sector_rs_20d` coverage | 60–90% (was 0%) |
| `historical_move_magnitude` coverage | 65–100% (was 0%) |
| `bb_width_percentile` coverage | 100% (unchanged) |
| `days_to_earnings` coverage | 23–70% (FinnhubClient flake — pre-existing, see CLAUDE.md Known Issues) |
| Approvals per coordinator-fan-out run | 36–42 v4 APPROVE on ~1970 contracts (~19% approve rate) |
| TIER_1 emergence | **0 yet** — composites top out at 84; `tier_1_min_score=85` |

## Top APPROVE tickers post-fix (sample, manual scan)

```
TKR     FINAL TIER        DC    MP    TS   DELTA  DTE
UAMY    84.51 TIER_2    79.3  90.3  82.1  0.6391   28
LITE    82.17 TIER_2    87.1  77.6  81.0  0.5460   28
NBIS    82.11 TIER_2    86.1  75.3  85.9  0.4669   28
ALB     81.29 TIER_2    80.2  77.9  88.2  0.3159   28
RKLB    80.98 TIER_2    87.1  78.5  75.2  0.5877   62
```

Compare to pre-fix sample (PL/NBIS/ALB/RKLB at 82–85 with limited subscores). Post-fix rankings differ because Stage 2 trend / 52w / sector RS / historical move are now contributing to the geometric-mean composite.

## Restore instructions

### Reactivate v3.1.3 (instant rollback, preserves all v4 historical data):
```bash
curl -X POST "https://2nv5mt4d1k.execute-api.us-west-1.amazonaws.com/api/policies/v3.1.3/activate"
```

### Re-seed v4.0.0 if the row is lost:
```bash
cd backend && python -c "
import json, requests
p = json.load(open('../baselines/2026-04-17-v4-policy-fixed.json'))
r = requests.post(
    'https://2nv5mt4d1k.execute-api.us-west-1.amazonaws.com/api/policies',
    json=p['config'],
)
print(r.status_code, r.text[:200])
"
```

### Lambda rollback ladder:
```bash
./scripts/deploy.sh rollback        # to v242 (merge of A1)
./scripts/deploy.sh rollback 240    # to parallel session's PriceHistoryService fix
./scripts/deploy.sh rollback 238    # to pre-A1 Phase 6 baseline (v4 active but Phase 1 features at 0%)
./scripts/deploy.sh rollback 237    # to pre-Phase-6 (v3.1.3 era, bypass v4 entirely)
```

## Pending items for Phase 8

- **Calibrate TIER_1 thresholds.** If 7 trading days pass with zero TIER_1, lower `tier_1_min_score` from 85 toward 82 or revisit subscore breakpoints. Tuning is via Policy page, no code changes.
- **Watch data-availability log on Monday's 13:00 UTC scheduled run.** If `ma_200` or `high_52w` drop below 85% on a 1970+ contract run, investigate `PriceHistoryService` cache freshness — the daily refresh hook (`oss-dev-price-history-refresh` at 5am UTC Tue–Sat) appends yesterday's bar; missed runs would degrade coverage.
- **Resolve `FinnhubClient` "not initialized" issue** (CLAUDE.md Known Issues). `days_to_earnings` partial coverage degrades the catalyst subscore in Move Potential (3.5% of total weight). Bounded impact, not blocking.
- **Phase 10 unblocked.** Historical paper-position rescore can run against the post-fix wiring once a v4 rescore script is built (parallel session may already have started this).
