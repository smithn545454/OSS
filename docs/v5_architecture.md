# v5 Dual-Conviction Architecture

**Active since:** 2026-04-20 02:34 UTC (policy v4.1.1, Lambda v254→v255)
**Design doc for:** `/Users/nicksmith/.claude/plans/i-want-you-to-giggly-tarjan.md`
**Audience:** future-you, future-Claude sessions, anyone wiring new behavior into OSS

## North star

**Conviction is the calibrated probability of a profitable outcome.** Not a blend
of subscores, not a general quality axis. Two parallel scores, each honest
about a different question:

- **HR conviction (0–20 scale):** `100 × Wilson_lower(P(MFE ≥ 200%)) × fit × regime`.
  A score of 14 means "we estimate 14% probability this trade hits ≥200% MFE."
  The probability semantics carry end-to-end.
- **P conviction (0–100 scale):** `100 × Wilson_lower(P(win)) × normalized_pnl × fit × regime`.
  Calibrated profitability — captures grinder patterns that never produce home
  runs but deliver consistent returns.

Trader sees both. Different questions, different answers. The UI mode toggle
lets you hunt sharpshooter (HR) or steady (P) depending on intent.

## The pipeline

```
Scanner → Underlying filter → Contract selection → Feature computation
  → Pillar scoring (as features, not decision)
  → Hard data-quality gates (spread, OI, liquidity, DTE)
  → Anti-archetype gates (hard REJECT on known losing patterns)
  ↓
  Per-evaluation v5 envelope (the new work):
    1. HR archetype match (12 patterns in lib)
    2. P archetype match (10 patterns in lib)
    3. GBM co-scorer (LR + isotonic, 20 features)
    4. Regime alignment (SPY 20d, VIX, sign-aware by option_type)
    5. Combine: final_hr = max(archetype_hr, gbm_hr × v5_gbm_hr_weight)
               final_p  = max(archetype_p,  gbm_p  × v5_gbm_p_weight)
  ↓
  Decision:
    if scanner in v5_active_scanners:
      → v5 verdict rules (APPROVE/WATCH/REJECT by HR + P thresholds)
    else:
      → v4.1.0 fallback (legacy composite + tier)
  ↓
  Paper trading / persistence
```

## What changed vs v4.1.0

| Dimension | v4.1.0 | v5 |
|---|---|---|
| Primary score | Pillar composite (0–100) | Two convictions (HR 0–20, P 0–100) |
| What conviction means | Blended quality score | Calibrated outcome probability |
| Archetype role | Secondary axis (best of pillar OR archetype) | Primary axis (archetypes + GBM co-scorer) |
| Small-sample handling | Point estimate | Wilson lower bound |
| Regime awareness | None | Bullish-calm / bearish-fear / chop multiplier (sign-aware) |
| Non-archetype path | None (pillar composite catches everything) | GBM co-scorer with capped weight |
| Auto-discovery | None | Weekly mining + drift detection |

## Policy configuration

v5 behavior lives entirely in `PolicyConfig` — no code flags:

```python
v5_active: bool = False                      # Master switch
v5_active_scanners: list[str] = []           # Per-scanner opt-in
v5_calibration: V5CalibrationConfig          # Wilson / regime / P&L norm params
v5_hr_archetypes: ArchetypeConfig            # HR pattern library
v5_p_archetypes:  ArchetypeConfig            # P pattern library
v5_hr_threshold: float = 7.0                 # APPROVE floor for HR
v5_p_threshold:  float = 50.0                # APPROVE floor for P
v5_gbm_enabled: bool = False                 # GBM co-scorer kill switch
v5_gbm_hr_weight: float = 0.5                # Cap on GBM HR contribution
v5_gbm_p_weight:  float = 0.7                # Cap on GBM P contribution (0.0 live — AUC 0.50)
```

Currently active (v4.1.1): `v5_active=True`, scanners `{UNUSUAL_VOLUME, CHEAP_OPTIONS,
BREAKDOWN, REVALIDATION}`, `v5_gbm_hr_weight=0.5`, **`v5_gbm_p_weight=0.0`** (P GBM
holdout AUC 0.501 — essentially random; disable until retrain).

BREAKOUT and COMPRESSION_EXPANSION fall through to v4.1.0 since no positive v5
archetypes exist for them. Anti-archetypes still fire (BREAKOUT × MP_ELITE is 0%
win rate — hard reject).

> **Note on REVALIDATION.** It is not a primary scanner. It's a synthetic
> re-evaluation pass that re-injects recent APPROVEs (last 8 hours) so
> their convictions get refreshed against current prices and Greeks.
> Each REVALIDATION opportunity carries
> `scanner_metrics.originating_scanner` pointing at the real upstream
> scanner that produced the prior APPROVE, so signal attribution stays
> clean. Frontend and Pipeline Monitor both label it "Re-evaluation".

## Module map

Under `backend/app/v5/`:

```
v5/
├── hr_archetypes.py     — 12 HR pattern definitions
├── hr_matcher.py        — Thin wrapper over v4.1.0 matcher
├── hr_conviction.py     — compute_hr_conviction(ctx, ...) → HRConvictionResult
├── p_archetypes.py      — 10 P pattern definitions
├── p_matcher.py         — Thin wrapper
├── p_conviction.py      — compute_p_conviction(ctx, ...) → PConvictionResult + PRateEstimate
├── gbm_scorer.py        — Pure-Python LR + isotonic inference
├── models/
│   ├── v5_gbm_hr.json   — HR model (holdout AUC 0.687)
│   └── v5_gbm_p.json    — P model (holdout AUC 0.501, weight=0 in active policy)
└── pipeline.py          — compute_v5_envelope() + derive_v5_verdict()
```

Under `backend/app/calibration/`:

```
wilson.py                — Wilson score interval (pure Python, no scipy)
archetype_rates.py       — Per-archetype rolling rate estimation (EWMA optional)
regime.py                — Market regime alignment multiplier
archetype_discovery.py   — Weekly auto-discovery + drift detection
```

## The decision flow (code path)

1. **Stage 7 entry** — `decision/stage.py::DecisionStage.execute()`
2. **v4.1.0 archetype match** — `_compute_archetype_results()` (retained; runs on every eval regardless of v5 state)
3. **v5 envelope computation** — `_compute_v5_envelopes()` (no-op when `policy.v5_active=False`)
4. **Per-evaluation decision** — `calculator.compute_decision()`:
   - Hard gates → REJECT_BY_GATES
   - Anti-archetype → ANTI_ARCHETYPE_{id}
   - If `v5_policy.v5_active AND scanner in v5_active_scanners`:
     → `derive_v5_verdict(envelope, policy, gates, anti)` — sets verdict, reason, tier
   - Otherwise v4.1.0 path: `determine_verdict(final_score, gates_passed)`
5. **Denormalize v5 fields** onto Decision (always when envelope present — shadow data)

## Verdict rules (v5)

Priority order:

1. Any hard gate failed → REJECT (`REJECTED_BY_GATES`)
2. Anti-archetype fired → REJECT (`ANTI_ARCHETYPE_{id}`)
3. `hr_conviction ≥ 14` → APPROVE TIER_1 (`V5_SHARPSHOOTER`)
4. `hr_conviction ≥ 7 OR p_conviction ≥ 70` → APPROVE TIER_2 (`V5_QUALITY`)
5. `hr_conviction ≥ hr_threshold (7) OR p_conviction ≥ p_threshold (50)` → APPROVE TIER_3 (`V5_TRADEABLE`)
6. Either conviction ≥ threshold/2 → WATCH (`V5_WATCH`)
7. Otherwise → REJECT (`V5_REJECTED_BY_SCORE`)

## Known gotchas + maintenance notes

**List-vs-dict normalization:** The worker pipeline passes `opportunities` and
`feature_sets` as `list[Opportunity]` / `list[FeatureSet]`. The stage helpers
expected dicts keyed by ticker / eval_id. Fixed via `_normalize_opportunities` +
`_normalize_feature_sets` in `decision/stage.py`. Under v4.1.0 this was a silent
failure (try/except swallowed the TypeError); v5 rollout exposed and fixed it.

**v3 pillar code retained:** `pillars/premium_leverage.py` etc. are still live
because v4.1.0 fallback runs for BREAKOUT + COMPRESSION_EXPANSION. Don't delete
until either (a) every scanner moves to v5, or (b) Phase 10 auto-discovery
surfaces positive archetypes for those two. Plan's "Phase 9" cleanup is
intentionally deferred.

**Rate lookup is currently empty:** `compute_v5_envelope` passes
`hr_rate_lookup=None` and `p_rate_lookup=None` from stage.py today. This
triggers the seed-fallback path in `hr_conviction.py` — each archetype uses
`historical_hr200_rate × 0.5` as a conservative lower bound. A rolling rate
estimator that queries paper-positions and caches Wilson bounds per archetype
(15-min TTL) is the next logical addition.

**GBM limitations:** The HR model (AUC 0.687) is useful. The P model (AUC 0.501)
is random — profit in the paper simulator is driven by exit rules more than
entry features. `v5_gbm_p_weight=0` disables it. To fix: upgrade to a real
GBM tree model (requires Lambda layers for xgboost) or engineer better
features (exit-time proxies).

**BREAKOUT + COMPRESSION still v4.1.0:** Not in `v5_active_scanners`. The
discovery system may eventually find positive v5 archetypes for them; until
then they ride the legacy composite.

## How to run things

```bash
# Snapshot active policy
curl -s "https://2nv5mt4d1k.execute-api.us-west-1.amazonaws.com/api/policies/active" \
  | python3 -c "import sys,json;d=json.load(sys.stdin);print(d['version'],d['policy_hash'][:16])"

# Seed archetype rates from live data (JSON output)
AWS_REGION=us-west-1 DYNAMODB_TABLE_PREFIX=oss-dev \
  python3 backend/scripts/seed_v5_archetype_rates.py --out /tmp/rates.json

# Weekly discovery run (markdown + JSON report)
AWS_REGION=us-west-1 DYNAMODB_TABLE_PREFIX=oss-dev \
  python3 backend/scripts/v5_weekly_discovery.py --out-dir /tmp/v5_weekly

# Retrain GBM models (local sklearn; models saved as JSON in app/v5/models/)
AWS_REGION=us-west-1 DYNAMODB_TABLE_PREFIX=oss-dev \
  python3 backend/scripts/train_v5_gbm.py --out-dir /tmp/v5_gbm_models

# Build + activate a new v5 policy
AWS_REGION=us-west-1 DYNAMODB_TABLE_PREFIX=oss-dev \
  python3 backend/scripts/build_and_activate_v5_policy.py --create --activate
```

## Rollback

```bash
# Instant policy rollback (30 sec)
curl -X POST "https://2nv5mt4d1k.execute-api.us-west-1.amazonaws.com/api/policies/v4.1.0/activate"

# Code rollback (if Lambda broken too)
./scripts/deploy.sh rollback                    # → one version back
./scripts/deploy.sh rollback N                  # → specific Lambda version
```

The v4.1.0 policy is still stored in DynamoDB, inactive. Reactivating it turns
v5 off everywhere. All v5 fields on historical Decisions remain; they just stop
being populated on new decisions.
