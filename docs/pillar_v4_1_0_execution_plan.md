# Pillar v4.1.0 Execution Plan — Home-Run Surfacing Rebuild

**Author:** Principal engineering plan (Claude)
**Date:** 2026-04-18
**Status:** Ready for execution in a fresh Claude session.
**Predecessor:** v4.0.1 (live as of 2026-04-18, Lambda v244, commit `57adf07`,
baseline tag `pipeline-stable-v4.0.1-2026-04-18`).
**Constraint:** ZERO tolerance for disruption. Frontend must not lose
functionality. No shadow mode. Historical v3 + v4.0.0 + v4.0.1 data must
continue to render.

---

## Table of Contents

1. [Purpose](#1-purpose)
2. [Context — How We Got Here](#2-context--how-we-got-here)
3. [The Three Changes](#3-the-three-changes)
4. [Target State](#4-target-state)
5. [Non-Disruption Strategy](#5-non-disruption-strategy)
6. [Phase 0 — Prerequisites & Baseline Snapshot](#6-phase-0--prerequisites--baseline-snapshot)
7. [Phase 1 — ADX Subscore Rebuild](#7-phase-1--adx-subscore-rebuild)
8. [Phase 2 — Weighted-MAX Composite Formula](#8-phase-2--weighted-max-composite-formula)
9. [Phase 3 — ConvexitySetup Scoring Lens](#9-phase-3--convexitysetup-scoring-lens)
10. [Phase 4 — Tier Assignment (Dual-Path TIER_1)](#10-phase-4--tier-assignment-dual-path-tier_1)
11. [Phase 5 — Frontend Dual-Score Rendering](#11-phase-5--frontend-dual-score-rendering)
12. [Phase 6 — Build + Seed Policy v4.1.0](#12-phase-6--build--seed-policy-v410)
13. [Phase 7 — Pre-Deploy Verification](#13-phase-7--pre-deploy-verification)
14. [Phase 8 — Deploy + Activate](#14-phase-8--deploy--activate)
15. [Phase 9 — Re-Rescore Paper Positions](#15-phase-9--re-rescore-paper-positions)
16. [Phase 10 — Performance Verification + Go/No-Go](#16-phase-10--performance-verification--gono-go)
17. [Phase 11 — Baseline + Merge to Main](#17-phase-11--baseline--merge-to-main)
18. [Rollback Plans](#18-rollback-plans)
19. [Risk Register](#19-risk-register)
20. [Test Strategy](#20-test-strategy)
21. [Frontend Touchpoints Inventory](#21-frontend-touchpoints-inventory)
22. [Context for Executing Claude Session](#22-context-for-executing-claude-session)

---

## 1. Purpose

Replace v4.0.1's home-run-blind scoring design with v4.1.0, which surfaces
the actual historical grand-slam fingerprint: early-stage trend (ADX 20-25),
cheap IV, convex structure, scanner source UV/CHEAP_OPTIONS/REVALIDATION.

**Success metric:** top-5% of v4.1.0 conviction scores should catch at
least 20 of the 221 historical ≥200% winners (vs 3/221 under v4.0.1, 48/221
under v3). Observable on the existing 20,562-position paper trade dataset
via the `analyze_v4_vs_v3_performance.py` and `home_run_diagnosis.py`
diagnostic scripts.

**What does NOT change:**
- Decision flow (gates → pillars → composite → verdict/tier)
- DynamoDB schema stability (additive fields only)
- URL structure, API endpoint paths, tier names
- Historical v3 / v4.0.0 / v4.0.1 evaluations and paper positions remain
  queryable and renderable

---

## 2. Context — How We Got Here

- **v4.0.0 (activated 2026-04-17):** three-pillar geometric-mean composite.
  On 20,562 paper positions, showed Pearson −0.030 vs P&L. Tier_1 (≥92)
  caught 0 trades.
- **v4.0.1 (activated 2026-04-18):** direction-aware DC (rs_20d +
  sector_rs_20d flipped for PUTs) + per-scanner pillar weights. Pearson
  improved to −0.008. Top-5% P&L capture improved from −7% to +79%. Still
  trailed v3's +257% top-5% P&L capture.
- **Home-run diagnosis (2026-04-18):** ran `home_run_diagnosis.py` on the
  same 20,562 rescored trades. Findings that motivate v4.1.0:

  1. **Zero home runs score above 80** on v4.0.1. None of the 1,465 ≥100%
     winners, 221 ≥200% winners, or 9 ≥500% winners land in the 80+ bucket.
     Home runs cluster at scores **50-65**, in the middle of the distribution.

  2. **UNUSUAL_VOLUME produces 77% of grand slams** (170 of 221). v4.0.1
     scores UV trades mean ~52 — middle of distribution, not high.

  3. **ADX subscore is inverted.** On 1,632 FVT-sampled trades, raw ADX
     correlates −0.18 Pearson with P&L (t-stat −7.2). Grand slams have mean
     ADX 22 vs losers mean ADX 26. v4's `_adx_directional_agreement` helper
     rewards HIGH ADX (15→30, 40→85), scoring home runs LOW on ADX.

  4. **Geometric mean aggregator punishes asymmetric profiles.** Grand slams
     have TS mean 76, DC mean 51, MP mean 52. The geometric mean drags the
     composite to 56. A MAX-weighted composite on the same trades would
     surface them at ~70-75.

  5. **Home-run entry fingerprint** (≥200% winners vs ≤−50% losers):

     | Feature | HR mean | Loser mean | t-stat |
     |---|---|---|---|
     | iv_percentile | 32.4 | 43.3 | −8.1 |
     | adx_14 | 22.4 | 26.3 | −7.2 |
     | iv_rv_ratio | 0.89 | 0.94 | −4.2 |
     | \|delta\| | 0.37 | 0.43 | ~ |
     | days_held | 2.9 | 6.6 | ~ |
     | TS pillar | 76.2 | 71.5 | + |
     | DC pillar | 50.6 | 57.7 | − |

  6. **v3 conviction catches 48/221 grand slams in top 5%** — 16× better
     than v4.0.1. Not because v3 is a better design: v3 was empirically fit
     to these outcomes. We need an explicitly home-run-aware v4 design.

Full diagnostic output: `/tmp/hr_diagnosis.txt` (may need regeneration —
script lives at `backend/scripts/home_run_diagnosis.py`).

---

## 3. The Three Changes

### Change 1 — ADX subscore rebuild (inverted-U curve peaking at ADX 22)

Current `_adx_directional_agreement` maps ADX monotonically up: 15→base 30,
40→base 85. The data says home runs live at ADX 20-25 and are rare above 35.
Rewrite the helper to peak at 22 and decline on both sides.

### Change 2 — Weighted-MAX composite formula

Current: `composite = prod(score_i ** weight_i)` (weighted geometric mean).
Problem: if DC=50, MP=52, TS=76, composite ≈ 57 (masks the strong TS).

Proposed: `composite = 0.6 * max(DC,MP,TS) + 0.4 * weighted_arithmetic_mean`.
This rewards trades with one exceptional pillar while still requiring a
reasonable baseline. Same DC=50/MP=52/TS=76 → composite = 0.6×76 + 0.4×56 = 68.

Geometric-mean + min-subscore-rule are removed; replaced with a floor: any
pillar < 25 triggers composite × 0.7 (a soft penalty instead of zero-collapse).

### Change 3 — ConvexitySetup scoring lens (new, parallel to pillars)

A **separate top-level score** (not a fourth pillar) that directly measures
the empirically-derived home-run fingerprint. Stored on `Decision`,
`EvaluationSnapshot`, `PaperPosition` alongside the composite.

Formula inputs (each 0-100, arithmetically averaged):

| Component | 100 pts when... | 0 pts when... |
|---|---|---|
| IV cheapness | `iv_percentile ≤ 20` | `iv_percentile ≥ 80` |
| Vol underpriced | `iv_rv_ratio ≤ 0.85` | `iv_rv_ratio ≥ 1.5` |
| Convex delta | `\|delta\| ∈ [0.28, 0.38]` | outside `[0.10, 0.70]` |
| Early trend | `adx_14 ∈ [18, 28]` | `adx_14 < 10` or `> 45` |
| TS passthrough | `TS ≥ 85` | `TS ≤ 40` |
| Scanner lineage | source ∈ {UV, CHEAP, REVAL} | source ∈ {COMP, BREAKOUT} |
| Catalyst proximity | `days_to_earnings ∈ [0, 5]` | no catalyst within 30 days |

Breakpoints/weights fully policy-configurable (a new
`ConvexitySetupConfig` schema element).

**Tier use:** TIER_1 qualification requires EITHER composite ≥ 75 OR
(convexity_setup_score ≥ 80 AND TS ≥ 75). Two paths to the top tier.

---

## 4. Target State

### Policy v4.1.0 spec

- `composite_formula: "weighted_max"` (new value added to Literal)
- ADX helper rebuilt in code (Phase 1); no subscore breakpoint change needed
- `convexity_setup` config element populated
- Per-scanner weights retained from v4.0.1
- Tier thresholds recalibrated post-rescore (empirically derived in Phase 10)

### Schema additions (all additive, all Optional)

- `Decision.convexity_setup_score: Optional[float]`
- `Decision.convexity_setup_components: Optional[dict[str, float]]` (for UI)
- `EvaluationSnapshot.convexity_setup_score: Optional[float]`
- `PaperPosition.convexity_setup_score: Optional[float]` (denormalized)
- `PillarConfig.composite_formula` Literal extended: `"weighted_sum"`,
  `"weighted_geometric_mean"`, `"weighted_max"`
- `PolicyConfig.convexity_setup: Optional[ConvexitySetupConfig]`
- `DecisionConfig.convexity_tier_1_threshold: float = 80.0`
- `DecisionConfig.convexity_tier_2_threshold: float = 70.0`
- `DecisionConfig.convexity_co_criterion_ts_min: float = 75.0`

### Code additions

- `backend/app/pillars/convexity_setup.py` (new module — compute function,
  accessor, tag generator)
- `backend/app/pillars/composite.py::compute_weighted_max()` (new function)
- `backend/app/pillars/directional_conviction.py::_adx_directional_agreement`
  (rewrite helper)

### Frontend additions

- `frontend/src/components/ConvexitySetupCard.tsx` (new component)
- `frontend/src/lib/types.ts` — extend `Decision`, `PaperPosition`
- Display convexity score on: EvaluationDetail, TradeDetail, TradeLibrary
- Policy page gains ConvexitySetup editor section (read-only initially,
  configurable in Phase 8)

---

## 5. Non-Disruption Strategy

1. **All schema changes are Optional**: Pydantic deserialization of v3 /
   v4.0.0 / v4.0.1 records (without `convexity_setup_score`) continues to work.
2. **composite_formula Literal is extended, never narrowed**: existing
   `"weighted_sum"` and `"weighted_geometric_mean"` cases remain valid.
3. **New `convexity_setup` is opt-in at policy level**: if a policy does not
   define it, the decision calculator skips the convexity score (returns
   None) and tier assignment falls back to composite-only logic.
4. **Frontend reads `convexity_setup_score` as `Optional`**: if a historical
   evaluation lacks it, the Convexity card displays "Not available" rather
   than crashing.
5. **Policy v4.0.1 remains activatable as rollback**: v4.1.0 only activates
   when explicitly chosen.

---

## 6. Phase 0 — Prerequisites & Baseline Snapshot

**Estimated time:** 30 min.

### 6.1 Environment checks

```bash
# Confirm current branch state from main
git checkout main && git pull origin main
git log --oneline -5  # last commit should be the v4.0.1 baseline (57adf07)
```

### 6.2 Verify active policy is v4.0.1

```bash
curl -s "https://2nv5mt4d1k.execute-api.us-west-1.amazonaws.com/api/policies/active" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['version'], d['policy_hash'][:12])"
# Expect: v4.0.1 c43a82b2254f
```

### 6.3 Verify paper-position scores are v4.0.1

```bash
AWS_REGION=us-west-1 DYNAMODB_TABLE_PREFIX=oss-dev \
  python3 backend/scripts/verify_rescore_v4.py 2>&1 | tail -30
# Expect: 100% coverage on v4 fields, scoring_regime='v4' on all 20,562
```

### 6.4 Snapshot current metrics (pre-v4.1.0 baseline)

```bash
AWS_REGION=us-west-1 DYNAMODB_TABLE_PREFIX=oss-dev \
  python3 backend/scripts/analyze_v4_vs_v3_performance.py \
  --out /tmp/v4_1_0_BASELINE_v401.md
```

Save. These are the numbers v4.1.0 must beat.

### 6.5 Create working branch

```bash
git checkout -b claude/v4-1-0-home-run-rebuild
```

### 6.6 Acceptance

- [ ] Active policy = v4.0.1
- [ ] All paper positions have `scoring_regime='v4'`
- [ ] Baseline metrics file saved
- [ ] Working branch created from main

---

## 7. Phase 1 — ADX Subscore Rebuild

**Goal:** Replace the monotonic-up ADX → base-score mapping in
`_adx_directional_agreement` with an inverted-U that peaks at ADX 22.
**Estimated time:** 1 day.
**Files touched:** `backend/app/pillars/directional_conviction.py`,
`backend/tests/test_directional_conviction.py`.

### 7.1 Rewrite the helper

Current code at `backend/app/pillars/directional_conviction.py:175-210`:

```python
def _adx_directional_agreement(ctx: ScoringContext) -> Optional[float]:
    # Map ADX to a 0-100 base score: weak trend ~15 → 30, strong trend ~40 → 85.
    adx_clamped = max(0.0, min(50.0, float(adx)))
    base = 30.0 + (adx_clamped - 15.0) * (85.0 - 30.0) / (40.0 - 15.0)
    base = max(0.0, min(100.0, base))
    # + DI agreement bonus / disagreement penalty ...
```

**Replace with** an inverted-U piecewise-linear curve:

```python
def _adx_directional_agreement(ctx: ScoringContext) -> Optional[float]:
    """Combine ADX magnitude with ±DI sign agreement → 0-100 score.

    v4.1.0 change: the ADX→base-score mapping is now an inverted-U that
    peaks at ADX=22 (empirically the home-run sweet spot on paper-trade
    outcomes). Established trends (ADX > 40) are penalised because by
    that point the move is late-stage and option premium is typically
    too expensive for convex reward. Very weak trends (ADX < 10) are
    also penalised because there is no direction to trade.

    Breakpoints (ADX → base):
        0  → 20
        10 → 50
        18 → 85
        22 → 100   (peak — home-run sweet spot)
        30 → 80
        40 → 55
        55 → 30
        80+ → 15
    """
    adx = ctx.adx_14
    plus_di = ctx.plus_di
    minus_di = ctx.minus_di
    if adx is None or plus_di is None or minus_di is None:
        return None

    adx_f = float(adx)
    breakpoints = [
        (0.0,  20.0),
        (10.0, 50.0),
        (18.0, 85.0),
        (22.0, 100.0),
        (30.0, 80.0),
        (40.0, 55.0),
        (55.0, 30.0),
        (80.0, 15.0),
    ]
    base = _piecewise_linear(adx_f, breakpoints)

    bullish = ctx.option_type == "CALL"
    dominant_di = plus_di if bullish else minus_di
    opposing_di = minus_di if bullish else plus_di
    di_diff = dominant_di - opposing_di

    if di_diff >= 10:
        bonus = 15.0
    elif di_diff >= 0:
        bonus = 5.0
    elif di_diff >= -10:
        bonus = -15.0
    else:
        bonus = -25.0

    return max(0.0, min(100.0, base + bonus))


def _piecewise_linear(x: float, breakpoints: list[tuple[float, float]]) -> float:
    """Return y at x given a sorted list of (x, y) breakpoints,
    linearly interpolated between them. Clamps at the endpoints.
    """
    if x <= breakpoints[0][0]:
        return breakpoints[0][1]
    if x >= breakpoints[-1][0]:
        return breakpoints[-1][1]
    for (x1, y1), (x2, y2) in zip(breakpoints, breakpoints[1:]):
        if x1 <= x <= x2:
            if x2 == x1:
                return y1
            t = (x - x1) / (x2 - x1)
            return y1 + t * (y2 - y1)
    return 50.0  # unreachable
```

### 7.2 Unit tests

New test class in `backend/tests/test_directional_conviction.py`:

```python
class TestAdxInvertedU:
    """v4.1.0: ADX curve peaks at ADX 22 and declines on both sides."""

    def test_peak_at_22(self):
        # ADX=22 with +DI dominant → score ~ 100 + 15 = 100 (clamped)
        ctx = _ctx(adx_14=22.0, plus_di=35.0, minus_di=15.0)
        assert _adx_directional_agreement(ctx) >= 95

    def test_very_weak_trend_scores_low(self):
        # ADX=5 → base ~35; neutral DI → total ~ 40
        ctx = _ctx(adx_14=5.0, plus_di=18.0, minus_di=17.0)
        score = _adx_directional_agreement(ctx)
        assert score is not None
        assert 30 <= score <= 50

    def test_very_strong_trend_scores_lower_than_peak(self):
        # ADX=50 → base ~42; DI agreement bonus → ~57
        ctx = _ctx(adx_14=50.0, plus_di=35.0, minus_di=15.0)
        score = _adx_directional_agreement(ctx)
        assert score is not None
        assert 45 <= score <= 70  # NOT 85+ like v4.0.1

    def test_late_stage_trend_penalized_vs_early_stage(self):
        """The same +DI/-DI but different ADX — early beats late."""
        early = _adx_directional_agreement(
            _ctx(adx_14=22.0, plus_di=30.0, minus_di=20.0)
        )
        late = _adx_directional_agreement(
            _ctx(adx_14=48.0, plus_di=30.0, minus_di=20.0)
        )
        assert early is not None and late is not None
        assert early > late, f"early={early} should beat late={late}"

    def test_piecewise_linear_interpolation(self):
        # At ADX=15 (between 10→50 and 18→85), interpolated to ~70
        ctx = _ctx(adx_14=15.0, plus_di=25.0, minus_di=20.0)
        score = _adx_directional_agreement(ctx)
        assert score is not None
        # base ~ 68-72, DI agreement +5 → ~73-77
        assert 68 <= score <= 80
```

### 7.3 Backwards-compat check

- Existing `TestAdxDirectionalAgreement` class will have several tests fail
  (e.g., `test_strong_trend_with_agreement_scores_high` expects score ≥ 80
  at ADX=35 which will now be ~75). Update those tests to reflect new
  curve, not delete. See test_directional_conviction.py lines 148-169.
- Specifically:
  - `test_strong_trend_with_agreement_scores_high`: change assertion from
    `score >= 80` to `score >= 70`.
  - `test_weak_trend_scores_moderate`: keep as-is (ADX=10 → base 50).

### 7.4 Acceptance

- [ ] `pytest backend/tests/test_directional_conviction.py -q --no-cov` — all green
- [ ] Full suite: `pytest backend/tests/ --tb=short -q --no-cov` — 2,293+ pass
- [ ] `ruff check backend/app/pillars/directional_conviction.py` — clean
- [ ] Manual verification: `_adx_directional_agreement` with ADX in
  `[20,22,24]` produces scores ≥ 95 on strong +DI agreement, ≤ 65 on
  disagreement
- [ ] Commit with message `fix(pillar-v4.1.0): ADX inverted-U curve peaks at 22`

### 7.5 Rollback

Single-file revert. No schema or policy change.

---

## 8. Phase 2 — Weighted-MAX Composite Formula

**Goal:** Add `"weighted_max"` as a new composite formula option. When
active, composite = 0.6 × max(DC, MP, TS) + 0.4 × weighted_mean. Soft
floor penalty instead of zero-collapse.
**Estimated time:** 1 day.
**Files touched:** `backend/app/core/schemas.py`,
`backend/app/pillars/composite.py`, `backend/tests/test_composite.py` (or
equivalent).

### 8.1 Extend `PillarConfig.composite_formula` Literal

In `backend/app/core/schemas.py:1118`:

```python
composite_formula: Literal[
    "weighted_sum",
    "weighted_geometric_mean",
    "weighted_max",   # v4.1.0 addition
] = "weighted_sum"
```

Also update the `_validate_regime_consistency` validator (schemas.py:1190-
1260) to accept `"weighted_max"` for v4 regimes in addition to
`"weighted_geometric_mean"`. The v3 regime still requires `"weighted_sum"`.

### 8.2 Add composite function

In `backend/app/pillars/composite.py`:

```python
def compute_weighted_max(
    pillar_results: Sequence[PillarResult],
    weights: PillarWeights,
    *,
    max_weight: float = 0.6,
    mean_weight: float = 0.4,
    floor_penalty_threshold: float = 25.0,
    floor_penalty_multiplier: float = 0.7,
) -> float:
    """v4.1.0 composite: 0.6 × max-pillar + 0.4 × weighted-mean.

    Grand-slam trades historically have one very strong pillar (typically
    TS) with middling DC/MP. The geometric mean drags these to the middle;
    the weighted-max surfaces them while still requiring all pillars to
    clear a soft floor.

    When any pillar falls below ``floor_penalty_threshold`` (default 25),
    the composite is multiplied by ``floor_penalty_multiplier`` (default
    0.7). This is a softer alternative to the v4.0.0 min-subscore rule's
    zero-collapse.
    """
    scores_by_id = {r.pillar_id: r.score for r in pillar_results}
    pillar_scores: list[float] = []
    weighted_sum_val = 0.0
    weight_total = 0.0

    for pillar_id, weight in _pillar_weights_items(weights):
        w = weight or 0.0
        if w <= 0:
            continue
        score = scores_by_id.get(pillar_id, 0.0)
        pillar_scores.append(score)
        weighted_sum_val += w * score
        weight_total += w

    if not pillar_scores:
        return 0.0

    max_pillar = max(pillar_scores)
    weighted_mean = weighted_sum_val / weight_total if weight_total > 0 else 0.0

    composite = max_weight * max_pillar + mean_weight * weighted_mean

    # Soft floor penalty (replaces v4.0 min-subscore zero-collapse)
    if min(pillar_scores) < floor_penalty_threshold:
        composite *= floor_penalty_multiplier

    return max(0.0, min(100.0, composite))
```

### 8.3 Extend dispatch

In `compute_composite_score()` at `backend/app/pillars/composite.py:80`:

```python
if config.composite_formula == "weighted_max":
    return compute_weighted_max(pillar_results, weights)
if config.composite_formula == "weighted_geometric_mean":
    return weighted_geometric_mean(pillar_results, weights)
return weighted_sum(pillar_results, weights)
```

### 8.4 Unit tests

New `backend/tests/test_composite_weighted_max.py` (or extend existing):

```python
class TestWeightedMaxComposite:
    def test_strong_ts_middling_dc_mp_surfaces(self):
        """Grand-slam profile: TS=76, DC=51, MP=52 → composite ~ 66."""
        pillars = [
            PillarResult(pillar_id=PillarId.DIRECTIONAL_CONVICTION,
                         evaluation_id="x", score=51, subscores=[], tags=[]),
            PillarResult(pillar_id=PillarId.MOVE_POTENTIAL,
                         evaluation_id="x", score=52, subscores=[], tags=[]),
            PillarResult(pillar_id=PillarId.TRADE_STRUCTURE,
                         evaluation_id="x", score=76, subscores=[], tags=[]),
        ]
        weights = PillarWeights.v4_default()  # 0.40/0.35/0.25
        c = compute_weighted_max(pillars, weights)
        # max = 76, weighted_mean = 0.40*51 + 0.35*52 + 0.25*76 = 20.4+18.2+19 = 57.6
        # composite = 0.6*76 + 0.4*57.6 = 45.6 + 23.04 = 68.64
        assert 65 <= c <= 72

    def test_geo_mean_same_inputs_underperforms_for_asymmetric(self):
        """Confirms weighted-max > geo mean on same asymmetric input."""
        pillars = [...]  # same as above
        weights = PillarWeights.v4_default()
        wm = compute_weighted_max(pillars, weights)
        gm = weighted_geometric_mean(pillars, weights)
        assert wm > gm + 5  # weighted-max surfaces asymmetric trades

    def test_balanced_high_pillars_all_formulas_similar(self):
        """When all pillars are high and similar, composite ≈ that level."""
        pillars = [...score=85 for each...]
        wm = compute_weighted_max(pillars, PillarWeights.v4_default())
        gm = weighted_geometric_mean(pillars, PillarWeights.v4_default())
        assert abs(wm - gm) < 5
        assert 82 <= wm <= 88

    def test_soft_floor_penalty_fires_below_25(self):
        """Any pillar < 25 triggers 0.7× multiplier."""
        pillars = [
            _p(PillarId.DIRECTIONAL_CONVICTION, 20),  # below floor
            _p(PillarId.MOVE_POTENTIAL, 70),
            _p(PillarId.TRADE_STRUCTURE, 80),
        ]
        c = compute_weighted_max(pillars, PillarWeights.v4_default())
        # max=80, weighted_mean = 0.4*20+0.35*70+0.25*80 = 8+24.5+20 = 52.5
        # raw = 0.6*80 + 0.4*52.5 = 48 + 21 = 69
        # with 0.7 multiplier → 48.3
        assert 45 <= c <= 52

    def test_zero_pillar_no_longer_collapses(self):
        """Unlike geometric mean, a single zero pillar does NOT zero the composite."""
        pillars = [
            _p(PillarId.DIRECTIONAL_CONVICTION, 0),
            _p(PillarId.MOVE_POTENTIAL, 70),
            _p(PillarId.TRADE_STRUCTURE, 80),
        ]
        c = compute_weighted_max(pillars, PillarWeights.v4_default())
        assert c > 10  # not zero
        assert c < 55  # but penalized

    def test_clamped_to_0_100(self):
        pillars = [_p(pid, 100) for pid in (
            PillarId.DIRECTIONAL_CONVICTION, PillarId.MOVE_POTENTIAL, PillarId.TRADE_STRUCTURE
        )]
        c = compute_weighted_max(pillars, PillarWeights.v4_default())
        assert c == 100
```

### 8.5 Acceptance

- [ ] `pytest tests/ -q --no-cov -k "composite"` — all green
- [ ] Full test suite green
- [ ] `mypy app/pillars/composite.py` clean
- [ ] `ruff check app/pillars/composite.py app/core/schemas.py` clean
- [ ] Schema validator accepts `"weighted_max"` on v4 PillarConfig and
  rejects it on v3 PillarConfig
- [ ] Commit: `feat(pillar-v4.1.0): weighted_max composite formula`

### 8.6 Rollback

Revert two files. No schema data impact since no policy uses the new
formula yet.

---

## 9. Phase 3 — ConvexitySetup Scoring Lens

**Goal:** New parallel signal capturing the empirically-derived home-run
fingerprint. Stored on `Decision` alongside composite; used as co-criterion
in tier assignment.
**Estimated time:** 2 days.
**Files touched:** many — this is the biggest change.

### 9.1 New schema element — `ConvexitySetupConfig`

In `backend/app/core/schemas.py` (insert near `PillarConfigV2`):

```python
class ConvexityComponentConfig(OSSBaseModel):
    """One component of the ConvexitySetup score.

    Each component is a piecewise-linear curve over a feature value,
    weighted against the other components. All component weights must
    sum to 1.0. Components with None feature values are redistributed
    out (mean shifts to remaining components, like the v4 pillar rule).
    """

    component_id: str  # e.g. "iv_cheapness"
    display_name: str
    feature_field: str  # attribute on ScoringContext
    weight: float
    breakpoints: list[SubscoreBreakpoint]

    # Optional: only compute this component when the scanner source
    # matches this list. E.g. "catalyst_proximity" only relevant when
    # UV or REVALIDATION fired.
    restrict_to_scanners: Optional[list[str]] = None


class ConvexitySetupConfig(OSSBaseModel):
    """Configuration for the ConvexitySetup scoring lens.

    The lens produces a 0-100 score that directly targets the historical
    grand-slam fingerprint: cheap IV, convex delta, early-stage trend,
    strong trade structure, home-run-prone scanner, catalyst proximity.
    """

    components: list[ConvexityComponentConfig]

    @model_validator(mode="after")
    def _validate_component_weights(self) -> "ConvexitySetupConfig":
        total = sum(c.weight for c in self.components)
        if abs(total - 1.0) > 1e-6:
            raise ValueError(
                f"ConvexitySetup component weights must sum to 1.0, got {total}"
            )
        return self
```

### 9.2 Add to `PolicyConfig`

In `backend/app/core/schemas.py:1348` region:

```python
class PolicyConfig(OSSBaseModel):
    pillars: PillarConfig = Field(default_factory=PillarConfig.v3_default)
    gates: GateConfig = Field(default_factory=GateConfig)
    convexity_setup: Optional[ConvexitySetupConfig] = None  # v4.1.0 addition
    # ... existing fields
```

### 9.3 Extend `Decision`, `EvaluationSnapshot`, `PaperPosition`

Each schema gains:

```python
convexity_setup_score: Optional[float] = None
convexity_setup_components: Optional[dict[str, float]] = None
```

Add to `backend/app/core/schemas.py`:
- `Decision` class (approximate line 401, same section as pillar scores)
- `EvaluationSnapshot` class (approximate line 1700)
- `PaperPosition` class (approximate line 1900)

### 9.4 New module — `backend/app/pillars/convexity_setup.py`

```python
"""ConvexitySetup — v4.1.0 scoring lens targeting the grand-slam fingerprint.

Separate from the three pillars. Computed per evaluation and stored on
the Decision. Used as a co-criterion alongside the composite when
assigning quality tiers: TIER_1 requires EITHER
  - composite_score ≥ decision.tier_1_threshold, OR
  - convexity_setup_score ≥ decision.convexity_tier_1_threshold
    AND trade_structure_pillar_score ≥ decision.convexity_co_criterion_ts_min

Subscores (each 0-100, weighted, mirror the home-run t-stat analysis):

  1. IV cheapness          (reward low iv_percentile)
  2. Vol underpriced       (reward low iv_rv_ratio)
  3. Convex delta          (peak at abs(delta)=0.32)
  4. Early-stage trend     (peak at adx_14=22)
  5. Structure passthrough (TS pillar score)
  6. Scanner lineage       (reward UV / CHEAP / REVAL source)
  7. Catalyst proximity    (reward days_to_earnings ∈ [0, 5])
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from app.core.schemas import ConvexitySetupConfig
from app.pillars.models import ScoringContext
from app.pillars.scoring_engine import score_numeric_subscore

logger = logging.getLogger(__name__)


@dataclass
class ConvexitySetupResult:
    evaluation_id: str
    score: float  # 0-100
    components: list["ConvexityComponentResult"]
    tags: list[str]


@dataclass
class ConvexityComponentResult:
    component_id: str
    display_name: str
    raw_value: Any
    score: float
    weight: float


def compute_convexity_setup(
    ctx: ScoringContext,
    config: ConvexitySetupConfig,
    ts_pillar_score: Optional[float] = None,
) -> ConvexitySetupResult:
    """Produce a ConvexitySetupResult from the ScoringContext.

    Components with None feature values have their weight redistributed
    across available components (mirroring the pillar weight-redistribution
    behaviour).
    """
    components: list[ConvexityComponentResult] = []
    weighted_sum = 0.0
    weight_total = 0.0

    accessor = _ConvexityAccessor(ctx, ts_pillar_score)

    for comp_cfg in config.components:
        if comp_cfg.restrict_to_scanners:
            if ctx.scanner_source not in comp_cfg.restrict_to_scanners:
                continue  # not applicable

        raw = getattr(accessor, comp_cfg.feature_field, None)
        score = score_numeric_subscore(
            value=raw,
            breakpoints=comp_cfg.breakpoints,
        )
        components.append(ConvexityComponentResult(
            component_id=comp_cfg.component_id,
            display_name=comp_cfg.display_name,
            raw_value=raw,
            score=score if score is not None else 0.0,
            weight=comp_cfg.weight,
        ))
        if score is not None:
            weighted_sum += comp_cfg.weight * score
            weight_total += comp_cfg.weight

    final_score = (weighted_sum / weight_total) if weight_total > 0 else 0.0
    final_score = max(0.0, min(100.0, final_score))

    tags = _generate_tags(ctx, components, final_score)

    return ConvexitySetupResult(
        evaluation_id=ctx.evaluation_id,
        score=final_score,
        components=components,
        tags=tags,
    )


class _ConvexityAccessor:
    """Exposes the Convexity feature_fields computed from ScoringContext."""

    HOME_RUN_SCANNERS = {"UNUSUAL_VOLUME", "CHEAP_OPTIONS", "REVALIDATION"}

    def __init__(self, ctx: ScoringContext, ts_score: Optional[float]) -> None:
        self._ctx = ctx
        self._ts = ts_score

    # Passthroughs
    @property
    def iv_percentile(self) -> Optional[float]:
        return self._ctx.iv_percentile

    @property
    def iv_rv_ratio(self) -> Optional[float]:
        return self._ctx.iv_rv_ratio

    # Derived: |delta| with peak at 0.32
    @property
    def abs_delta(self) -> Optional[float]:
        d = self._ctx.delta
        return abs(d) if d is not None else None

    @property
    def adx_14(self) -> Optional[float]:
        return self._ctx.adx_14

    @property
    def ts_pillar_score(self) -> Optional[float]:
        return self._ts

    @property
    def scanner_home_run_lineage(self) -> Optional[float]:
        """Categorical-ish score. Returns 100 if scanner is UV/CHEAP/REVAL,
        50 for BREAKOUT/BREAKDOWN, 20 for COMPRESSION, None for missing.
        """
        s = self._ctx.scanner_source
        if s is None:
            return None
        if s in self.HOME_RUN_SCANNERS:
            return 100.0
        if s in {"BREAKOUT", "BREAKDOWN"}:
            return 50.0
        return 20.0

    @property
    def catalyst_proximity(self) -> Optional[float]:
        d = self._ctx.days_to_earnings
        return float(d) if d is not None else None


def _generate_tags(
    ctx: ScoringContext,
    components: list[ConvexityComponentResult],
    score: float,
) -> list[str]:
    tags: list[str] = []
    if score >= 85:
        tags.append("CONVEXITY_GRAND_SLAM")
    elif score >= 70:
        tags.append("CONVEXITY_HIGH")
    scores_by_id = {c.component_id: c.score for c in components}
    if scores_by_id.get("iv_cheapness", 0) >= 85:
        tags.append("CHEAP_IV")
    if scores_by_id.get("scanner_lineage", 0) >= 90:
        tags.append("HR_SCANNER")
    if scores_by_id.get("catalyst_proximity", 0) >= 85:
        tags.append("CATALYST_IN_WINDOW")
    if scores_by_id.get("adx_early_stage", 0) >= 85:
        tags.append("EARLY_STAGE_TREND")
    return tags
```

### 9.5 Wire into decision calculator

In `backend/app/decision/calculator.py` (around line 128-150 — the final
composite logic):

```python
# v4.1.0: compute ConvexitySetup alongside pillars
convexity_result: Optional[ConvexitySetupResult] = None
if config.convexity_setup is not None:
    ts_pillar_score = _find_pillar_score(pillar_results, PillarId.TRADE_STRUCTURE)
    convexity_result = compute_convexity_setup(
        ctx, config.convexity_setup, ts_pillar_score=ts_pillar_score
    )

# Attach to Decision
decision = Decision(
    # ... existing fields ...
    convexity_setup_score=(
        round(convexity_result.score, 2) if convexity_result else None
    ),
    convexity_setup_components=(
        {c.component_id: round(c.score, 2) for c in convexity_result.components}
        if convexity_result else None
    ),
)
```

Corresponding import changes at top of the file.

### 9.6 Seed default `ConvexitySetupConfig`

Used in `build_policy_v4_1_0.py` (Phase 6); defined here for reference:

```python
def _convexity_setup_config() -> ConvexitySetupConfig:
    """v4.1.0 default — breakpoints derived from home_run_diagnosis.py
    analysis on 20,562 paper positions (April 2026)."""

    def _bp(value: float, score: float) -> SubscoreBreakpoint:
        return SubscoreBreakpoint(value=value, score=score)

    return ConvexitySetupConfig(
        components=[
            # 1. IV cheapness — HR mean 32, loser mean 43, t=-8.1
            ConvexityComponentConfig(
                component_id="iv_cheapness",
                display_name="IV Cheapness",
                feature_field="iv_percentile",
                weight=0.20,
                breakpoints=[
                    _bp(0.0,   100.0),
                    _bp(20.0,  90.0),
                    _bp(35.0,  70.0),
                    _bp(50.0,  45.0),
                    _bp(70.0,  25.0),
                    _bp(100.0, 10.0),
                ],
            ),
            # 2. Vol underpriced — HR mean 0.89, loser mean 0.94, t=-4.2
            ConvexityComponentConfig(
                component_id="vol_underpriced",
                display_name="Vol Underpriced",
                feature_field="iv_rv_ratio",
                weight=0.15,
                breakpoints=[
                    _bp(0.5,  100.0),
                    _bp(0.85, 85.0),
                    _bp(1.0,  55.0),
                    _bp(1.15, 30.0),
                    _bp(1.5,  15.0),
                    _bp(2.5,  5.0),
                ],
            ),
            # 3. Convex delta — HR |delta| mean 0.37, loser 0.43
            # Slight bias toward OTM + ATM, penalize deep-ITM/OTM
            ConvexityComponentConfig(
                component_id="convex_delta",
                display_name="Convex Delta",
                feature_field="abs_delta",
                weight=0.10,
                breakpoints=[
                    _bp(0.05, 15.0),
                    _bp(0.15, 55.0),
                    _bp(0.25, 85.0),
                    _bp(0.32, 100.0),
                    _bp(0.40, 85.0),
                    _bp(0.50, 55.0),
                    _bp(0.70, 25.0),
                    _bp(0.90, 10.0),
                ],
            ),
            # 4. Early-stage trend — HR ADX mean 22, loser 26, t=-7.2
            ConvexityComponentConfig(
                component_id="adx_early_stage",
                display_name="Early-Stage Trend",
                feature_field="adx_14",
                weight=0.15,
                breakpoints=[
                    _bp(0.0,  25.0),
                    _bp(10.0, 55.0),
                    _bp(18.0, 90.0),
                    _bp(22.0, 100.0),
                    _bp(28.0, 90.0),
                    _bp(35.0, 60.0),
                    _bp(45.0, 35.0),
                    _bp(60.0, 15.0),
                    _bp(100.0, 5.0),
                ],
            ),
            # 5. Structure passthrough — HR TS mean 76, loser 72
            ConvexityComponentConfig(
                component_id="structure",
                display_name="Trade Structure (passthrough)",
                feature_field="ts_pillar_score",
                weight=0.20,
                breakpoints=[
                    _bp(0.0,   0.0),
                    _bp(40.0,  25.0),
                    _bp(60.0,  55.0),
                    _bp(75.0,  85.0),
                    _bp(85.0,  100.0),
                    _bp(100.0, 100.0),
                ],
            ),
            # 6. Scanner lineage — 77% of HRs from UV+CHEAP+REVAL
            ConvexityComponentConfig(
                component_id="scanner_lineage",
                display_name="Scanner Lineage",
                feature_field="scanner_home_run_lineage",
                weight=0.10,
                breakpoints=[
                    _bp(0.0,   0.0),
                    _bp(20.0,  20.0),
                    _bp(50.0,  50.0),
                    _bp(100.0, 100.0),
                ],
            ),
            # 7. Catalyst proximity — days_to_earnings in [0,5] optimal
            ConvexityComponentConfig(
                component_id="catalyst_proximity",
                display_name="Catalyst Proximity",
                feature_field="catalyst_proximity",
                weight=0.10,
                breakpoints=[
                    _bp(0.0,  100.0),
                    _bp(5.0,  95.0),
                    _bp(10.0, 70.0),
                    _bp(20.0, 50.0),
                    _bp(30.0, 35.0),
                    _bp(60.0, 20.0),
                    _bp(180.0, 5.0),
                ],
            ),
        ],
    )
```

### 9.7 Unit tests

New `backend/tests/test_convexity_setup.py`:

```python
class TestConvexitySetupHappyPath:
    def test_grand_slam_fingerprint_scores_high(self):
        """The empirical HR profile should score 80+."""
        config = _default_config()
        ctx = _ctx(
            iv_percentile=25.0,      # cheap
            iv_rv_ratio=0.88,        # underpriced
            delta=0.32,              # convex
            adx_14=22.0,             # early-stage
            scanner_source="UNUSUAL_VOLUME",
            days_to_earnings=3,
        )
        result = compute_convexity_setup(ctx, config, ts_pillar_score=80)
        assert result.score >= 80
        assert "CONVEXITY_GRAND_SLAM" in result.tags

    def test_typical_loser_profile_scores_low(self):
        config = _default_config()
        ctx = _ctx(
            iv_percentile=65.0,
            iv_rv_ratio=1.1,
            delta=0.55,
            adx_14=38.0,
            scanner_source="BREAKOUT",
            days_to_earnings=90,
        )
        result = compute_convexity_setup(ctx, config, ts_pillar_score=65)
        assert result.score < 50

class TestConvexitySetupWeightRedistribution:
    def test_missing_iv_percentile_redistributes(self):
        """When one feature is None, remaining weights re-normalize."""
        config = _default_config()
        ctx = _ctx(iv_percentile=None, iv_rv_ratio=0.88, delta=0.32,
                   adx_14=22.0, scanner_source="UNUSUAL_VOLUME",
                   days_to_earnings=3)
        result = compute_convexity_setup(ctx, config, ts_pillar_score=80)
        assert result.score > 60  # still meaningful


class TestConvexitySetupConfigValidation:
    def test_weights_must_sum_to_one(self):
        bad = {
            "components": [
                {"component_id": "a", "display_name": "a", "feature_field": "iv_percentile",
                 "weight": 0.5, "breakpoints": [{"value": 0, "score": 0}, {"value": 100, "score": 100}]},
                {"component_id": "b", "display_name": "b", "feature_field": "iv_rv_ratio",
                 "weight": 0.3, "breakpoints": [{"value": 0, "score": 0}, {"value": 100, "score": 100}]},
            ]
        }
        with pytest.raises(ValueError, match="weights must sum to 1.0"):
            ConvexitySetupConfig.model_validate(bad)


class TestConvexitySetupTags:
    def test_cheap_iv_tag(self): ...
    def test_catalyst_tag(self): ...
    def test_early_stage_trend_tag(self): ...
    def test_hr_scanner_tag(self): ...


class TestDecisionCalculatorIntegration:
    def test_convexity_score_attached_to_decision(self):
        # Build a PolicyConfig with convexity_setup; run evaluation; assert
        # Decision.convexity_setup_score is populated.
        ...

    def test_decision_without_convexity_config_leaves_field_none(self):
        # Policy v3 or v4.0 has no convexity_setup → Decision score is None.
        ...
```

### 9.8 Acceptance

- [ ] `pytest tests/test_convexity_setup.py -q --no-cov` — all green
- [ ] Full test suite green (target: 2,320+ tests pass)
- [ ] `ConvexitySetupConfig` validator rejects weights that don't sum to 1
- [ ] Decision with a convexity config carries `convexity_setup_score`
- [ ] Decision without convexity config has `convexity_setup_score = None`
- [ ] Historical evaluations (v3/v4.0/v4.0.1) deserialize without error
- [ ] `ruff check` + `mypy app/` clean
- [ ] Commit: `feat(pillar-v4.1.0): ConvexitySetup scoring lens`

### 9.9 Rollback

- Revert the commit.
- Schema additions are Optional, so data already written with
  `convexity_setup_score=None` remains valid.

---

## 10. Phase 4 — Tier Assignment (Dual-Path TIER_1)

**Goal:** Redefine tier assignment so that exceptional convexity scores can
surface TIER_1 candidates that the composite alone would miss.
**Estimated time:** 0.5 day.
**Files touched:** `backend/app/core/schemas.py` (DecisionConfig),
`backend/app/decision/calculator.py`.

### 10.1 Extend `DecisionConfig`

In `backend/app/core/schemas.py` (find `class DecisionConfig`):

```python
class DecisionConfig(OSSBaseModel):
    # ... existing tier thresholds ...
    tier_1_threshold: float = 92.0
    tier_2_threshold: float = 82.0
    tier_3_threshold: float = 72.0
    watch_threshold: float = 62.0

    # v4.1.0 additions — Convexity co-criterion
    convexity_tier_1_threshold: float = 80.0
    convexity_tier_2_threshold: float = 70.0
    convexity_co_criterion_ts_min: float = 75.0
```

### 10.2 Tier-assignment logic update

In `backend/app/decision/calculator.py`, find the `_assign_quality_tier()`
(or equivalent) function:

```python
def _assign_quality_tier(
    composite: float,
    convexity_score: Optional[float],
    ts_score: Optional[float],
    config: DecisionConfig,
) -> Optional[QualityTier]:
    """Assign TIER_1 / TIER_2 / TIER_3 / None.

    v4.1.0: TIER_1 and TIER_2 support a second qualification path via
    ConvexitySetup + TS. A trade qualifies for TIER_N if EITHER:

      - composite ≥ tier_N_threshold, OR
      - convexity_score ≥ convexity_tier_N_threshold AND
        ts_score ≥ convexity_co_criterion_ts_min

    TIER_3 uses composite only (conservative).
    """
    # Composite path (legacy + v4.1.0)
    if composite >= config.tier_1_threshold:
        return QualityTier.TIER_1
    if composite >= config.tier_2_threshold:
        return QualityTier.TIER_2

    # Convexity co-criterion path (v4.1.0 only)
    if (
        convexity_score is not None
        and ts_score is not None
        and ts_score >= config.convexity_co_criterion_ts_min
    ):
        if convexity_score >= config.convexity_tier_1_threshold:
            return QualityTier.TIER_1
        if convexity_score >= config.convexity_tier_2_threshold:
            return QualityTier.TIER_2

    # TIER_3 / below — composite only
    if composite >= config.tier_3_threshold:
        return QualityTier.TIER_3
    if composite >= config.watch_threshold:
        return None  # WATCH (no tier)
    return None  # REJECT / no tier
```

### 10.3 Unit tests

```python
class TestDualPathTierAssignment:
    def test_composite_path_tier_1(self):
        tier = _assign_quality_tier(
            composite=92.5, convexity_score=50, ts_score=70,
            config=DecisionConfig()
        )
        assert tier == QualityTier.TIER_1

    def test_convexity_path_tier_1_when_composite_falls_short(self):
        """High convexity + TS, middle composite → still TIER_1."""
        tier = _assign_quality_tier(
            composite=65.0, convexity_score=85, ts_score=80,
            config=DecisionConfig()
        )
        assert tier == QualityTier.TIER_1

    def test_convexity_without_ts_co_criterion_does_not_promote(self):
        """High convexity but low TS → no promotion."""
        tier = _assign_quality_tier(
            composite=65.0, convexity_score=90, ts_score=60,
            config=DecisionConfig()
        )
        # composite 65 is TIER_3 (≥72? no, 65 is WATCH-range)
        assert tier is None  # WATCH, not TIER_1

    def test_tier_3_composite_only_unchanged(self):
        tier = _assign_quality_tier(
            composite=75.0, convexity_score=None, ts_score=None,
            config=DecisionConfig()
        )
        assert tier == QualityTier.TIER_3

    def test_policies_without_convexity_fall_back_cleanly(self):
        """v3 / v4.0.0 policies → convexity_score is None → composite path."""
        tier = _assign_quality_tier(
            composite=82.0, convexity_score=None, ts_score=None,
            config=DecisionConfig()
        )
        assert tier == QualityTier.TIER_2
```

### 10.4 Acceptance

- [ ] Tests pass
- [ ] A v3-regime Decision with `convexity_setup_score=None` still produces
  the same tier as pre-v4.1.0
- [ ] Commit: `feat(pillar-v4.1.0): dual-path TIER_1 via Convexity co-criterion`

### 10.5 Rollback

Revert the two files. DecisionConfig fields are additive, no data impact.

---

## 11. Phase 5 — Frontend Dual-Score Rendering

**Goal:** Display `convexity_setup_score` on all relevant pages; retain
full back-compat for historical evaluations without the field.
**Estimated time:** 1 day.
**Files touched:** several frontend components.

### 11.1 TypeScript types

In `frontend/src/lib/types.ts`:

```typescript
export interface ConvexitySetupComponent {
  component_id: string;
  display_name: string;
  score: number;
  weight: number;
}

export interface Decision {
  // ... existing ...
  convexity_setup_score?: number | null;
  convexity_setup_components?: Record<string, number> | null;
}

export interface PaperPosition {
  // ... existing ...
  convexity_setup_score?: number | null;
}
```

### 11.2 New component — `ConvexitySetupCard.tsx`

`frontend/src/components/ConvexitySetupCard.tsx`:

```typescript
interface Props {
  score?: number | null;
  components?: Record<string, number> | null;
}

const componentLabels: Record<string, string> = {
  iv_cheapness: "IV Cheapness",
  vol_underpriced: "Vol Underpriced",
  convex_delta: "Convex Delta",
  adx_early_stage: "Early-Stage Trend",
  structure: "Trade Structure",
  scanner_lineage: "Scanner Lineage",
  catalyst_proximity: "Catalyst Proximity",
};

export function ConvexitySetupCard({ score, components }: Props) {
  if (score == null) {
    return (
      <div className="card">
        <h3>Convexity Setup</h3>
        <p className="text-slate-400">Not available (pre-v4.1.0 evaluation)</p>
      </div>
    );
  }

  // Render gauge + component breakdown
  return (
    <div className="card">
      <h3>Convexity Setup</h3>
      <div className="flex items-center gap-4">
        <Gauge value={score} threshold={80} label="Convexity" />
      </div>
      {components && (
        <div className="mt-4 space-y-2">
          {Object.entries(components).map(([id, val]) => (
            <div key={id} className="flex justify-between">
              <span>{componentLabels[id] ?? id}</span>
              <span>{val.toFixed(1)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
```

### 11.3 Update `EvaluationDetail.tsx`

Add the Convexity card below the three pillar cards. Render only if the
evaluation has `convexity_setup_score`.

### 11.4 Update `TradeDetail.tsx`

Add convexity score next to composite score in the header section.

### 11.5 Update `TradeLibrary.tsx` (My Trades)

Add optional column "Convexity" to the table — sortable, displays as
badge/chip. Hide for pre-v4.1.0 positions.

### 11.6 Update `Opportunities.tsx`

On approved opportunities list, show convexity score in the row/card along
with conviction score. Add a dedicated filter: "Convexity ≥ 80".

### 11.7 Policy page read-only display

In `PolicyConfig.tsx`, add a collapsible section "Convexity Setup"
displaying the component configuration (read-only in v4.1.0; editable in
a future iteration).

### 11.8 Acceptance

- [ ] `npm run build` green
- [ ] `npm run lint` clean
- [ ] `npm test` — all existing tests pass
- [ ] Historical v3 / v4.0 / v4.0.1 evaluation renders without
  crash — Convexity card shows "Not available"
- [ ] New v4.1.0 evaluation renders with full Convexity card + components
- [ ] Opportunities filter "Convexity ≥ 80" works
- [ ] Commit: `feat(frontend-v4.1.0): render ConvexitySetup score + card`

### 11.9 Rollback

Frontend revert. Backend already supports historical evaluations with or
without convexity.

---

## 12. Phase 6 — Build + Seed Policy v4.1.0

**Goal:** Create the v4.1.0 policy JSON that makes the code reachable on
live pipeline runs.
**Estimated time:** 1 day.
**Files touched:** `backend/scripts/build_policy_v4_1_0.py` (new),
`backend/scripts/seed_policy_v4_1_0.py` (new), output JSON.

### 12.1 New script — `backend/scripts/build_policy_v4_1_0.py`

Clone structure from `build_policy_v4_1.py` and change:

- `VERSION = "v4.1.0"`
- `OUTPUT_PATH = OUTPUT_DIR / "v4_1_0_policy.json"`
- `composite_formula="weighted_max"` (not `"weighted_geometric_mean"`)
- Call a helper that adds `convexity_setup=_convexity_setup_config()` to
  the returned `PolicyConfig`
- Retain v4.0.1 scanner weights unchanged (we're tuning everything else)
- Retain v4.0.1 global pillar weights (40/35/25)

### 12.2 New script — `backend/scripts/seed_policy_v4_1_0.py`

Clone structure from `seed_policy_v4_1.py` and change:

- `TARGET_VERSION = "v4.1.0"`
- Validator check: `config.pillars.composite_formula == "weighted_max"`
- Validator check: `config.convexity_setup is not None and len(components) == 7`
- Rest identical

### 12.3 Build + seed

```bash
cd backend
python3 scripts/build_policy_v4_1_0.py
AWS_REGION=us-west-1 DYNAMODB_TABLE_PREFIX=oss-dev \
  PYTHONPATH=. python3 scripts/seed_policy_v4_1_0.py
```

Expect output: `Created policy version v4.1.0 (inactive). policy_hash=...`

### 12.4 Acceptance

- [ ] `scripts/output/v4_1_0_policy.json` exists
- [ ] Policy `v4.1.0` exists in DynamoDB, `is_active=false`
- [ ] `curl .../api/policies/v4.1.0` returns the new policy
- [ ] The policy's `composite_formula` is `"weighted_max"`
- [ ] The policy's `convexity_setup.components` has 7 entries summing to 1.0
- [ ] Scanner weights match v4.0.1 exactly
- [ ] Active policy is still v4.0.1 (no pipeline change yet)
- [ ] Commit: `feat(policy-v4.1.0): build + seed script`

### 12.5 Rollback

```bash
# Remove the draft policy
AWS_REGION=us-west-1 aws dynamodb delete-item \
  --table-name oss-dev-policies \
  --key '{"PK":{"S":"POLICY"},"SK":{"S":"v4.1.0"}}'
```

No impact on live pipeline or data.

---

## 13. Phase 7 — Pre-Deploy Verification

**Estimated time:** 30 min.

### 13.1 Backend full check

```bash
cd backend
pytest tests/ --tb=short -q --no-cov    # target: 2,320+ pass
ruff check app/ scripts/                 # clean
mypy app/                                # clean
```

### 13.2 Frontend full check

```bash
cd frontend
npm run build
npm run lint
npm test
```

### 13.3 Visual smoke on the current policy

Load the frontend (local dev):

```bash
cd frontend && npm run dev
# In a second terminal:
cd backend && uvicorn app.main:app --reload --port 8001
```

Navigate to:
- `/` — Opportunities page loads
- `/evaluation/:ticker/:id` for a known v4.0.1 evaluation — three pillar
  cards render, no "Convexity Setup" card (since active policy is v4.0.1)
- `/policies` — Policy page loads, shows v4.0.1 as active
- Open a v4.0.1 trade in My Trades — renders correctly

### 13.4 Acceptance

- [ ] All backend tests green
- [ ] All frontend tests green
- [ ] Visual smoke on v4.0.1 looks identical to pre-v4.1.0
- [ ] No console errors in frontend

---

## 14. Phase 8 — Deploy + Activate

**Estimated time:** 30 min + 30 min monitoring.

### 14.1 Commit + push branch

```bash
git push origin HEAD
```

### 14.2 Deploy backend

```bash
cd backend
./scripts/deploy.sh backend     # runs pytest, packages, publishes version
# Expect: "Backend deployed! Lambda version: N, Git commit: XXXXXXX"
```

**Record Lambda version N** in phase notes.

### 14.3 Post-deploy checks (on v4.0.1 policy, which is still active)

```bash
# Health
curl -s "https://2nv5mt4d1k.execute-api.us-west-1.amazonaws.com/health"

# CloudWatch: ERROR scan last 5 min
AWS_REGION=us-west-1 aws logs filter-log-events \
  --log-group-name "/aws/lambda/oss-dev-backend" \
  --filter-pattern "ERROR" \
  --start-time $(python3 -c "import time; print(int((time.time()-300)*1000))") \
  --limit 10 --query 'events[*].message' --output text
```

### 14.4 One-run observation

Wait for next 15-min pipeline run. Check:

```bash
curl -s "https://2nv5mt4d1k.execute-api.us-west-1.amazonaws.com/api/pipeline/runs?limit=1" \
  | python3 -m json.tool | head -40
```

Expect: 8 stages completed, no error counts, decisions carry v4.0.1 scores.

### 14.5 Deploy frontend

```bash
cd frontend
../backend/scripts/deploy.sh frontend    # via the unified deploy script
# Or if there's a dedicated frontend deploy
```

### 14.6 Activate v4.1.0

```bash
curl -sX POST "https://2nv5mt4d1k.execute-api.us-west-1.amazonaws.com/api/policies/v4.1.0/activate" \
  | python3 -m json.tool
# Expect: {"message":"Policy v4.1.0 activated", ...}
```

### 14.7 Post-activation monitoring (30 min)

Wait 15 min for next pipeline run. Then:

```bash
# Verify new evaluations carry convexity_setup_score
curl -s "https://2nv5mt4d1k.execute-api.us-west-1.amazonaws.com/api/evaluations/approve?limit=5" \
  | python3 -c "import sys, json; d = json.load(sys.stdin); [print(e.get('ticker'), e.get('conviction_score'), e.get('convexity_setup_score')) for e in d.get('evaluations', [])]"
```

Expect: `convexity_setup_score` populated (not null) on the top 5 approvals.

Additional checks:
- Pipeline Monitor page: all 8 stages green
- Opportunities page: top opportunities show new conviction distribution
  (should see fewer approvals at the very top, more in 60-75 range)
- A v4.1.0 evaluation detail: all three pillar cards + Convexity Setup card

### 14.8 Rollback if needed

```bash
# Policy rollback (fastest)
curl -sX POST ".../api/policies/v4.0.1/activate"

# Code rollback (if v4.1.0 code itself is broken)
./scripts/deploy.sh rollback N-1   # where N is the v4.1.0 Lambda version
```

### 14.9 Acceptance

- [ ] Lambda version N deployed
- [ ] Health returns 200
- [ ] Zero ERROR logs post-deploy
- [ ] v4.1.0 activated successfully
- [ ] First post-activation pipeline run has new evaluations with
  `convexity_setup_score` populated
- [ ] Frontend renders new evaluations with Convexity card

---

## 15. Phase 9 — Re-Rescore Paper Positions

**Goal:** Run `rescore_all_positions_v4.py` with v4.1.0 active, so the
historical 20,562 trades carry v4.1.0 scores for analysis.
**Estimated time:** 3 hours unattended + 10 min verification.

### 15.1 Extend rescore script to capture convexity

In `backend/scripts/rescore_all_positions_v4.py`:

1. After `calculator.compute_pillars(...)` returns, capture the TS pillar
   score.
2. If active policy has `convexity_setup`, call `compute_convexity_setup()`.
3. Include `convexity_setup_score` in the `update_item` SET expression.

Specific code change — around the existing `update_position_v4()` call:

```python
convexity_score: Optional[float] = None
convexity_components: Optional[dict[str, float]] = None
if pillar_config_convexity_ref is not None:  # set once outside the loop
    ts_score = score_by_id.get("TRADE_STRUCTURE")
    conv_result = compute_convexity_setup(
        ctx, pillar_config_convexity_ref, ts_pillar_score=ts_score
    )
    convexity_score = round(conv_result.score, 2)
    convexity_components = {
        c.component_id: round(c.score, 2) for c in conv_result.components
    }

new_scores = {
    # ... existing ...
    "convexity_setup_score": convexity_score,
    "convexity_setup_components": convexity_components,
}
```

Extend `update_position_v4()` to SET these two new fields (nullable).

### 15.2 Run rescore

```bash
cd backend
AWS_REGION=us-west-1 DYNAMODB_TABLE_PREFIX=oss-dev \
  python3 -u scripts/rescore_all_positions_v4.py > /tmp/rescore_v410.log 2>&1 &
```

### 15.3 Monitor progress

Use the existing Monitor tool with the same regex. Expected runtime: 3 hours
(20,562 positions @ ~2 pos/sec).

### 15.4 Verify post-rescore

```bash
AWS_REGION=us-west-1 DYNAMODB_TABLE_PREFIX=oss-dev \
  python3 scripts/verify_rescore_v4.py | tail -50
```

Expect: 100% coverage on DC/MP/TS/convexity_setup_score/conviction_score.

### 15.5 Acceptance

- [ ] Rescore completed with 0 errors
- [ ] All 20,562 positions have `convexity_setup_score` populated
- [ ] `conviction_score_v3` archive column intact (from v4.0.0 rescore)
- [ ] No data loss vs pre-rescore backup JSON

### 15.6 Rollback

Backup JSON (produced at rescore start) + `restore_position_scores.py`.

---

## 16. Phase 10 — Performance Verification + Go/No-Go

**Goal:** Compare v4.1.0 to v4.0.1 and v3 on the same 20,562 trades. Make
the go/no-go call using explicit thresholds.
**Estimated time:** 1 hour.

### 16.1 Run the standard analysis

```bash
AWS_REGION=us-west-1 DYNAMODB_TABLE_PREFIX=oss-dev \
  python3 scripts/analyze_v4_vs_v3_performance.py --out /tmp/v4_1_0_analysis.md
```

### 16.2 Run the home-run diagnosis

```bash
AWS_REGION=us-west-1 DYNAMODB_TABLE_PREFIX=oss-dev \
  python3 scripts/home_run_diagnosis.py > /tmp/v4_1_0_hr_diagnosis.txt
```

### 16.3 Compare against go/no-go thresholds

| Metric | v4.0.1 baseline | v4.1.0 target | Go/No-Go |
|---|---|---|---|
| Conviction Pearson vs P&L | −0.008 | ≥ +0.05 | Go if met |
| Top 5% P&L capture | +79% | ≥ +200% | Go if met |
| Top 10% catches ≥200% HRs | 3/221 | ≥ 20/221 | Go if met |
| Top 5% catches ≥100% HRs | 2/1465 | ≥ 50/1465 | Go if met |
| Convexity ≥80 cohort size | n/a | ≥ 100 trades | Must have |
| Convexity ≥80 cohort mean P&L | n/a | ≥ +25% | Go if met |
| Convexity ≥80 cohort big-win rate | n/a | ≥ 10% | Go if met |

**Decision rule:**
- **5+ of 7 metrics met** → ship v4.1.0, proceed to Phase 11
- **3-4 metrics met** → surgical tune, repeat Phase 10 after tune
- **≤2 metrics met** → roll back to v4.0.1, gather evidence, rethink

### 16.4 Spot-check home runs

From the diagnostic output, find the subset of ≥200% historical winners
where v4.1.0 now scores them ≥ 75 AND Convexity ≥ 80. Expect this set to
grow substantially. Manually inspect 10 of them:

- Entry date, scanner, ticker, P&L
- Pillar scores + Convexity components
- Did they hit TIER_1 via composite path or convexity co-criterion path?

### 16.5 Acceptance

- [ ] Performance analysis written
- [ ] Go/No-Go decision recorded in decision log
- [ ] If go: proceed to Phase 11
- [ ] If no-go: activate v4.0.1 immediately, document findings

---

## 17. Phase 11 — Baseline + Merge to Main

**Estimated time:** 30 min.
Applies only if Phase 10 decision was Go.

### 17.1 Export active policy

```bash
curl -s "https://2nv5mt4d1k.execute-api.us-west-1.amazonaws.com/api/policies/active" \
  > baselines/$(date +%Y-%m-%d)-v4.1.0-policy.json
python3 -m json.tool baselines/$(date +%Y-%m-%d)-v4.1.0-policy.json \
  > /tmp/pretty.json && mv /tmp/pretty.json baselines/$(date +%Y-%m-%d)-v4.1.0-policy.json
```

### 17.2 Baseline README

Create `baselines/YYYY-MM-DD-v4.1.0-README.md` with:
- Version + policy hash + Lambda version + git commit
- Measured metrics vs v4.0.1 and v3
- Key design changes: ADX inverted-U, weighted-max composite, Convexity lens
- Restore instructions

### 17.3 Commit + tag

```bash
git add baselines/
git commit -m "baseline: pipeline-stable-v4.1.0-YYYY-MM-DD"
git tag pipeline-stable-v4.1.0-YYYY-MM-DD
git push origin HEAD
git push origin --tags
```

### 17.4 Merge to main

```bash
cd /Users/nicksmith/OSS
git checkout main
git pull origin main
git merge claude/v4-1-0-home-run-rebuild --no-edit
git push origin main
```

### 17.5 Delete branch

```bash
git push origin --delete claude/v4-1-0-home-run-rebuild
```

### 17.6 Acceptance

- [ ] Baseline tag pushed
- [ ] Policy JSON exported and committed
- [ ] README committed
- [ ] Main branch is up to date with v4.1.0
- [ ] Remote branch deleted
- [ ] CI green on main

---

## 18. Rollback Plans

### Fast (< 1 min) — policy reactivation

```bash
curl -sX POST ".../api/policies/v4.0.1/activate"
```

Reverts evaluation behaviour to v4.0.1 within one pipeline run. Historical
v4.1.0 evaluations remain queryable; frontend still renders them.

### Medium — Lambda rollback

```bash
./scripts/deploy.sh rollback N-1
```

Use if v4.1.0 code is crashing on live pipeline runs.

### Slow — full revert from baseline tag

```bash
git checkout pipeline-stable-v4.0.1-2026-04-18 -- backend/ frontend/
./scripts/deploy.sh backend
./scripts/deploy.sh frontend
# Also reactivate v4.0.1 policy per fast rollback above
```

### Data rollback — paper positions

The v4.1.0 rescore overwrites `conviction_score` on the 20,562 positions.
`conviction_score_v3` (from the v4.0.0 rescore backup) is intact.

To fully restore pre-v4.1.0 scores:

```bash
python3 backend/scripts/restore_position_scores.py \
  --backup backend/scripts/output/position_scores_backup_v4_YYYYMMDDT...Z.json
```

### Irreversible — nothing

Every v4.1.0 change is reversible. There are no destructive migrations.

---

## 19. Risk Register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Weighted-max composite produces unstable live scores | Low | Med | Extensive unit tests in Phase 2; visual smoke in Phase 8 |
| Convexity components have zero FVT coverage on live data | Low | Med | Phase 8 post-activation check explicitly verifies population |
| Convexity co-criterion promotes too many TIER_1s | Med | Low | Threshold configurable; Phase 10 verifies < 10 TIER_1s/run |
| Convexity co-criterion promotes too few TIER_1s | Med | Low | Same as above; can lower threshold via Policy page |
| Frontend crashes on pre-v4.1.0 evaluation (no convexity field) | Low | High | All fields Optional; visual smoke in Phase 7 |
| Home-run catch rate doesn't hit Phase 10 thresholds | Med | Med | Explicit go/no-go rule; surgical tune OR rollback |
| Rescore produces wrong convexity scores due to FVT gaps | Low | Low | Graceful degradation — weight redistribution in compute function |
| ADX curve change breaks existing DC subscore tests | Expected | Low | Test updates explicitly required in Phase 1.3 |
| Schema validation rejects v3 policies after v4.1.0 | Low | High | New Literal value is additive; v3 still uses `"weighted_sum"` |
| Policy page can't render Convexity editor section | Low | Low | Phase 11 read-only; editor is future work |
| A Decision is emitted WITHOUT convexity score when policy has it | Low | Med | Phase 10 integration test covers the happy path |
| mypy strict flags the new Optional fields | Low | Low | Phase 7 mypy check |
| Ruff rules reject new code | Low | Low | Phase 7 ruff check; auto-fix |
| Live pipeline fails on first run post-activation | Low | High | Phase 8 observation + instant rollback path |

---

## 20. Test Strategy

### 20.1 Unit tests (required before each commit)

- Phase 1: ADX inverted-U curve, backwards-compat of existing ADX tests
- Phase 2: weighted-max formula, all composite dispatch paths, soft floor
- Phase 3: ConvexitySetup compute, weight redistribution, tags,
  config validation
- Phase 4: tier assignment — composite path, convexity path,
  co-criterion failure, historical v3/v4.0 fallback

### 20.2 Integration tests

- End-to-end: v4.1.0 policy → evaluation with convexity score populated
- End-to-end: v4.0.1 policy → evaluation with convexity score null
- Decision calculator integrates ConvexitySetup only when config present
- Paper-position denormalization includes convexity score

### 20.3 Regression tests

- All 2,293 existing tests continue to pass
- Full suite target after v4.1.0: 2,340+ tests

### 20.4 Visual / manual tests (Phase 7, 8, 10)

- Historical v3 eval still renders
- Historical v4.0 / v4.0.1 eval still renders
- New v4.1.0 eval renders with Convexity card + all components
- Opportunities page renders Convexity column + filter
- My Trades renders Convexity column
- Policy page renders v4.1.0 with convexity_setup section

### 20.5 Production smoke (Phase 8)

- First 3 pipeline runs post-activation: 0 ERROR logs
- New evaluations have `convexity_setup_score ∈ [0, 100]`
- Tier distribution sanity check: 2-8 TIER_1, 8-20 TIER_2, 20-60 TIER_3
  per Russell 1000 scan

---

## 21. Frontend Touchpoints Inventory

For the executing Claude session to verify coverage:

**Tier 1 (must update):**
- `frontend/src/lib/types.ts` — add `convexity_setup_score`,
  `convexity_setup_components`
- `frontend/src/pages/EvaluationDetail.tsx` — add Convexity card
- `frontend/src/pages/TradeDetail.tsx` — show convexity score next to composite
- `frontend/src/pages/Opportunities.tsx` — new column + filter
- `frontend/src/components/paper-trading/TradeLibrary.tsx` — new column

**Tier 2 (nice to have):**
- `frontend/src/components/paper-trading/PositionTracker.tsx` — convexity in
  row detail
- `frontend/src/components/paper-trading/ScoreCalibration.tsx` — convexity
  as candidate x-axis
- `frontend/src/pages/PolicyConfig.tsx` — render Convexity section
  (read-only)

**Tier 3 (tests):**
- Update `frontend/src/pages/PolicyConfig.test.tsx` mocks
- Update `frontend/src/lib/convictionScore.test.ts` if relevant

### New components to create

- `frontend/src/components/ConvexitySetupCard.tsx` — the detail card
- `frontend/src/components/ConvexityBadge.tsx` — inline badge for tables
  (optional, if space-constrained)

---

## 22. Context for Executing Claude Session

If you are a fresh Claude session starting this work, follow this order:

### 22.1 Read in this order

1. This document in full (don't skip sections).
2. [CLAUDE.md](../CLAUDE.md) — operational rules, deployment protocol,
   non-negotiables.
3. [docs/pillar_v4_execution_plan.md](pillar_v4_execution_plan.md) — the
   predecessor v4.0.0 execution plan (context only — v4.0.0 is already live).
4. [baselines/2026-04-18-v4.0.1-README.md](../baselines/2026-04-18-v4.0.1-README.md)
   — the current state.
5. The home-run diagnosis (re-run if stale):
   ```
   AWS_REGION=us-west-1 DYNAMODB_TABLE_PREFIX=oss-dev \
     python3 backend/scripts/home_run_diagnosis.py
   ```

### 22.2 Operational rules

- Work on the branch `claude/v4-1-0-home-run-rebuild`, branched from the
  `pipeline-stable-v4.0.1-2026-04-18` tag.
- At the start of each phase, state the objective out loud and get a
  green light before coding. Do not batch phases.
- Run `pytest`, `ruff`, `mypy` before every commit. Run `npm run build`
  and `npm run lint` before every frontend commit.
- Never skip the deploy protocol in CLAUDE.md §"Deployment Protocol".
- Deploy after every logical change — not batched.
- Merge to main after every successful deploy (Phase 11). Delete the branch.
- On any rescore: do NOT delete the backup JSONs in
  `backend/scripts/output/`. They are the only paper-position rollback path.

### 22.3 Questions to answer before starting

1. **Is v4.0.1 still the live active policy?** (should be — run Phase 0 check)
2. **Is Nick available for phase sign-offs?** Phase 8 activation and Phase
   10 go/no-go specifically require explicit approval.
3. **Is there fresh data since 2026-04-18?** If so, re-run the home-run
   diagnosis first — the breakpoints in Section 9.6 may need updating if
   the grand-slam fingerprint shifted.
4. **Is the paper-trades dataset still ~20k positions?** If dramatically
   different, re-verify Section 2 findings hold before changing code.

### 22.4 What to do if something is unclear

- Re-read the relevant section of this plan — most details are explicit.
- If the plan is wrong or contradicted by observed state, STOP and ask Nick.
  Do not guess.
- If a phase's acceptance criteria can't be met, STOP and ask. Do not
  proceed to the next phase.

### 22.5 Deliverables summary

At end of Phase 11, the following artifacts should exist:

**Code**
- `backend/app/pillars/directional_conviction.py` — modified
- `backend/app/pillars/composite.py` — modified
- `backend/app/pillars/convexity_setup.py` — new
- `backend/app/core/schemas.py` — modified (additive)
- `backend/app/decision/calculator.py` — modified
- `backend/scripts/build_policy_v4_1_0.py` — new
- `backend/scripts/seed_policy_v4_1_0.py` — new
- `backend/scripts/rescore_all_positions_v4.py` — modified (convexity support)
- `frontend/src/components/ConvexitySetupCard.tsx` — new
- `frontend/src/lib/types.ts` — modified
- Multiple frontend pages modified

**Tests**
- `backend/tests/test_directional_conviction.py` — extended with
  `TestAdxInvertedU`
- `backend/tests/test_convexity_setup.py` — new (class listed in Section 9.7)
- `backend/tests/test_composite_weighted_max.py` — new
- Updated existing composite / decision-calculator / ADX tests as needed

**Policy**
- `backend/scripts/output/v4_1_0_policy.json` — written
- v4.1.0 row in `oss-dev-policies` table
- v4.1.0 active (after Phase 8)

**Baselines**
- `baselines/YYYY-MM-DD-v4.1.0-policy.json`
- `baselines/YYYY-MM-DD-v4.1.0-README.md`
- Git tag `pipeline-stable-v4.1.0-YYYY-MM-DD`

**Performance**
- `/tmp/v4_1_0_BASELINE_v401.md` — pre-change baseline
- `/tmp/v4_1_0_analysis.md` — post-change comparative analysis
- `/tmp/v4_1_0_hr_diagnosis.txt` — post-change home-run diagnosis

---

**End of Plan. Ready for execution.**

**Total estimated elapsed time:** 8-11 working days (with Phase 9 3-hour
rescore unattended). Most of the total is coding + careful testing; one
full day is reserved for Phase 5 frontend work.

**Most-important single principle from this plan:** the historical
grand-slam fingerprint is specific, measured, and statistically
significant. Every tuning decision should flow from the measured fingerprint
(low IV percentile, early ADX, convex delta, high TS, UV-lineage scanner,
catalyst proximity) — not from an abstract "sharpshooter thesis." When in
doubt, look at the data in `home_run_diagnosis.py` output.
