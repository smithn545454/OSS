# Pillar v4.1.0 Execution Plan — Archetype-Aware Scoring

**Author:** Principal engineering plan (Claude)
**Date:** 2026-04-18 (rev 2)
**Status:** Ready for execution in a fresh Claude session.
**Predecessor:** v4.0.1 (live as of 2026-04-18, Lambda v244, commit `57adf07`,
baseline tag `pipeline-stable-v4.0.1-2026-04-18`).
**Key new input:** [home_run_archetypes_findings.md](../backend/scripts/output/home_run_archetypes_findings.md)
— a parallel deep-dive that identified six discrete home-run archetypes and
three anti-archetypes on the 18,567 closed paper-trade dataset.
**Constraint:** ZERO tolerance for disruption. Frontend must not lose
functionality. No shadow mode. Historical v3 + v4.0.0 + v4.0.1 data must
continue to render.

---

## Table of Contents

1. [Purpose](#1-purpose)
2. [Context — How We Got Here](#2-context--how-we-got-here)
3. [The Four Changes](#3-the-four-changes)
4. [The Architecture Question — "Boost" vs "Restructure"](#4-the-architecture-question--boost-vs-restructure)
5. [Target State](#5-target-state)
6. [Non-Disruption Strategy](#6-non-disruption-strategy)
7. [Phase 0 — Prerequisites & Baseline Snapshot](#7-phase-0--prerequisites--baseline-snapshot)
8. [Phase 1 — ADX Subscore Rebuild](#8-phase-1--adx-subscore-rebuild)
9. [Phase 2 — Weighted-MAX Composite Formula](#9-phase-2--weighted-max-composite-formula)
10. [Phase 3 — Archetype Matcher](#10-phase-3--archetype-matcher)
11. [Phase 4 — Anti-Archetype Gates](#11-phase-4--anti-archetype-gates)
12. [Phase 5 — Archetype-Aware Tier Assignment](#12-phase-5--archetype-aware-tier-assignment)
13. [Phase 6 — Frontend Archetype Visualization](#13-phase-6--frontend-archetype-visualization)
14. [Phase 7 — Build + Seed Policy v4.1.0](#14-phase-7--build--seed-policy-v410)
15. [Phase 8 — Pre-Deploy Verification](#15-phase-8--pre-deploy-verification)
16. [Phase 9 — Deploy + Activate](#16-phase-9--deploy--activate)
17. [Phase 10 — Re-Rescore Paper Positions](#17-phase-10--re-rescore-paper-positions)
18. [Phase 11 — Performance Verification + Go/No-Go](#18-phase-11--performance-verification--gono-go)
19. [Phase 12 — Baseline + Merge to Main](#19-phase-12--baseline--merge-to-main)
20. [Rollback Plans](#20-rollback-plans)
21. [Risk Register](#21-risk-register)
22. [Test Strategy](#22-test-strategy)
23. [Frontend Touchpoints Inventory](#23-frontend-touchpoints-inventory)
24. [Context for Executing Claude Session](#24-context-for-executing-claude-session)

---

## 1. Purpose

Replace v4.0.1's home-run-blind scoring with v4.1.0, which surfaces the
historical grand-slam pattern via three architectural changes:

1. **ADX subscore rebuild** — invert the ADX curve to peak at ADX=22
   (early-stage trend, where home runs empirically live).
2. **Weighted-MAX composite formula** — replace the geometric mean so that
   a single exceptional pillar surfaces the trade rather than being
   averaged down.
3. **Archetype-aware scoring** — add a second scoring axis that explicitly
   matches trades against six empirically-validated home-run archetypes and
   gate-rejects three empirically-validated anti-archetypes.

**Success metric:** on the 18,567-position paper-trade dataset, v4.1.0 must
satisfy ≥ 5 of 7 measured outcomes in the Phase 11 scorecard. The headline
metric is that the top-5% of `max(composite, archetype_match_score)` catches
at least 30 of 201 ≥200% MFE winners (vs v4.0.1's 3/221, vs v3's 48/221).

**What does NOT change:**
- Decision flow (gates → pillars → composite → verdict/tier), now extended
  with archetype logic in additive positions
- DynamoDB schema stability (additive optional fields only)
- URL structure, API endpoint paths, tier enum names
- Historical v3 / v4.0.0 / v4.0.1 evaluations and paper positions remain
  queryable and renderable

---

## 2. Context — How We Got Here

- **v4.0.0** (activated 2026-04-17): three-pillar geometric-mean composite.
  On 20,562 paper positions, showed Pearson −0.030 vs P&L.
- **v4.0.1** (activated 2026-04-18): direction-aware DC + per-scanner pillar
  weights. Pearson improved to −0.008. Top-5% P&L capture improved from
  −7% to +79%. Still trails v3's +257%.
- **Home-run diagnosis** (2026-04-18, `backend/scripts/home_run_diagnosis.py`):
  found that **zero home runs score above 80** on v4.0.1. Home runs cluster
  at composite 50-65. Top 5% of v4.0.1 catches only 3/221 grand slams.
- **Archetype analysis** (2026-04-18,
  `backend/scripts/output/home_run_archetypes_findings.md`): identified
  six distinct archetypes and three anti-archetypes with clean
  statistically-significant signal:

### Home-run archetypes (codify as first-class scoring targets)

| ID | Archetype | n | HR200 rate | Lift | Win% | Mean P&L |
|---|---|---|---|---|---|---|
| A | UV × DTE 14-21 × \|delta\|<0.25 × CALL | 114 | **20.2%** | **18.7×** | 66% | +84% |
| B | UV × DTE 14-21 × TS≥75 | 274 | 9.5% | 8.8× | 60% | +50% |
| C | UV × PUT × TS≥75 × RS_against | 220 | 10.5% | 9.7× | 55% | +44% |
| D | CHEAP × ADX<20 × composite 65-78 × ATR 4-6% | 93 | 7.5% | 22.2× | 48% | +27% |
| E | CHEAP × ATR≥6% × RS_against × IVRV 1.0-1.3 | 50 | 8.0% | 23.6× | **74%** | +52% |
| F | CHEAP × DTE<14 × CALL × IVP<30 | 37 | **10.8%** | **32×** | **87%** | +80% |

Baseline HR200 rate across all 18,567 trades: **1.08%**.

### Anti-archetypes (codify as hard rejects)

| ID | Pattern | n | HR200 | Mean P&L | Win% |
|---|---|---|---|---|---|
| AA1 | BREAKOUT × MP_score ≥ 75 | 321 | 0% | **−57.5%** | **0%** |
| AA2 | UV × DTE ≥ 45 | 2,241 | 0.6% | −9.3% | 37% |
| AA3 | CHEAP × DC_score ≥ 75 | 2,315 | 0.1% | −5.0% | 41% |

The BREAKOUT × MP_ELITE finding — **321 trades with 0% win rate** — is the
clearest possible signal that the current pipeline is systematically mis-
ranking a specific combination. MP double-counts the momentum already baked
into the BREAKOUT scanner trigger.

---

## 3. The Four Changes

### Change 1 — ADX subscore rebuild (inverted-U, peak at ADX=22)

Current `_adx_directional_agreement` maps ADX monotonically up (15→base 30,
40→base 85). Data says home runs live at ADX 20-25 and are rare above 35.
Rewrite the helper so the ADX→base-score mapping is an inverted-U peaking
at 22 and declining beyond 30.

### Change 2 — Weighted-MAX composite formula

Replace `prod(score_i ** weight_i)` with:
```
composite = 0.6 × max(DC, MP, TS) + 0.4 × weighted_arithmetic_mean(DC, MP, TS)
```
Plus a soft floor: any pillar below 25 triggers `composite × 0.7`. Removes
the v4 min-subscore zero-collapse.

### Change 3 — Archetype Matcher

New scoring axis parallel to pillars. Six archetype definitions (A-F above)
plus graded "feather" matching so boundary-adjacent trades still get partial
credit. Best archetype fit becomes the `archetype_match_score` on the
Decision. Tier assignment considers both `composite` and
`archetype_match_score` (whichever is higher defines the tier).

### Change 4 — Anti-Archetype Gates

New gate category. Three anti-archetype rules (AA1-AA3 above) that
hard-reject matching trades before they reach pillar scoring. Gates are
configurable in the Policy like all other gates.

---

## 4. The Architecture Question — "Boost" vs "Restructure"

This is a deliberate design choice that shapes every subsequent section.

### We're NOT doing

- Bolting a flat "+10 bonus" onto the composite for matching trades
- Hiding the archetype logic inside the pillar composite
- Replacing the pillars with archetypes

### We ARE doing

- **Adding a second scoring axis** (archetype_match_score, 0-100) that is
  computed independently of pillars
- **Using max() for the final conviction**, so a trade can reach TIER_1 via
  either strong general-purpose pillars OR a strong archetype match
- **Separating rejection from scoring**: anti-archetype gates fire before
  scoring and are not part of the composite
- **Surfacing the matched archetype to the UI** so every trade's top-level
  setup thesis is visible to the user

### Why this specific architecture

1. **Pillars stay general-purpose**. Every trade gets scored, even ones
   that don't match any archetype. The pillar composite remains the
   baseline scoring regime.
2. **Archetypes stay specific**. They encode discrete setups with clean
   boundaries (DTE 14-21, ATR≥6%, scanner=UV). Averaging would dilute them.
3. **TIER_1 becomes explainable**. When a trade is TIER_1, the UI can
   state: "This trade is TIER_1 because (a) its pillar composite is 83, OR
   (b) it matches Archetype A (UV Lottery Call, 92% fit, historical
   HR200 rate 20.2%)." Either path is legible.
4. **Anti-archetype gates are independent**. If a trade matches AA1
   (BREAKOUT × MP_ELITE), it's rejected regardless of any other score.
   Gates belong with gates, not with scoring.

### Downstream data model implications

Every `Decision` carries (all optional for backwards compat):
- `archetype_matched: Optional[str]` — ID of the best-matching archetype
- `archetype_match_score: Optional[float]` — 0-100 fit score of the best match
- `archetype_all_fits: Optional[dict[str, float]]` — every archetype's fit
  (for UI debug + future ML training)
- `anti_archetype_triggered: Optional[str]` — if set, the anti-archetype
  that rejected the trade

Historical evaluations have these fields as None, rendering as "Not
available (pre-v4.1.0)" in the UI.

---

## 5. Target State

### Policy v4.1.0 spec

- `composite_formula: "weighted_max"` (new Literal value)
- ADX helper rebuilt in code (Phase 1)
- `archetypes` section populated with 6 archetype definitions
- `anti_archetypes` section populated with 3 anti-archetype gate rules
- Per-scanner weights retained from v4.0.1 (no change in Phase 1-11)
- Tier thresholds recalibrated post-rescore (empirically tuned in Phase 11)

### Schema additions (all additive, all Optional)

- `Decision.archetype_matched: Optional[str]`
- `Decision.archetype_match_score: Optional[float]`
- `Decision.archetype_all_fits: Optional[dict[str, float]]`
- `Decision.anti_archetype_triggered: Optional[str]`
- `EvaluationSnapshot` — same 4 fields
- `PaperPosition` — same 4 fields (denormalized)
- `PillarConfig.composite_formula` Literal extended:
  `"weighted_sum"` | `"weighted_geometric_mean"` | `"weighted_max"`
- `PolicyConfig.archetypes: Optional[ArchetypeConfig]` (new — see §10)
- `PolicyConfig.anti_archetypes: Optional[AntiArchetypeConfig]` (new — see §11)
- `DecisionConfig.archetype_tier_1_threshold: float = 80.0`
- `DecisionConfig.archetype_tier_2_threshold: float = 70.0`

### Code additions

- `backend/app/pillars/composite.py::compute_weighted_max()` — new function
- `backend/app/pillars/directional_conviction.py::_adx_directional_agreement`
  — rewritten helper
- `backend/app/archetypes/` — new package:
  - `__init__.py`
  - `matcher.py` — archetype + anti-archetype compute
  - `schema.py` — config types (or into core/schemas.py, TBD in Phase 3)
  - `defaults.py` — the six archetype + three anti-archetype defaults
- `backend/app/decision/calculator.py` — invokes archetype matcher + gates

### Frontend additions

- `frontend/src/components/ArchetypeMatchCard.tsx` — new detail card
- `frontend/src/components/ArchetypeBadge.tsx` — compact badge for tables
- `frontend/src/lib/archetypeMeta.ts` — display metadata (colors, icons)
- `frontend/src/lib/types.ts` — extend `Decision`, `PaperPosition`
- Archetype-aware updates to: EvaluationDetail, TradeDetail, TradeLibrary,
  Opportunities, PolicyConfig (read-only view)

---

## 6. Non-Disruption Strategy

1. **All schema fields additive and Optional**. v3 / v4.0.x records
   deserialize cleanly with the new fields as None.
2. **composite_formula Literal extended, never narrowed**.
3. **Archetype matcher opt-in at policy level**. If
   `PolicyConfig.archetypes` is None, the matcher is skipped and the
   decision is composite-only. Preserves v3 / v4.0.x behavior.
4. **Anti-archetype gates opt-in at policy level**. If
   `PolicyConfig.anti_archetypes` is None, no new gates fire. Existing
   v4.0.x gate behavior preserved.
5. **Frontend reads all four new Decision fields as Optional**. Historical
   evaluations show "No archetype match available (pre-v4.1.0)".
6. **Policy v4.0.1 remains activatable as rollback**. v4.1.0 only
   activates when explicitly chosen.

---

## 7. Phase 0 — Prerequisites & Baseline Snapshot

**Estimated time:** 30 min.

### 7.1 Environment checks

```bash
git checkout main && git pull origin main
git log --oneline -5
# Last commit should be 49a2734 docs: pillar v4.1.0 execution plan + home-run diagnosis script
```

### 7.2 Verify active policy is v4.0.1

```bash
curl -s "https://2nv5mt4d1k.execute-api.us-west-1.amazonaws.com/api/policies/active" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['version'], d['policy_hash'][:12])"
# Expect: v4.0.1 c43a82b2254f
```

### 7.3 Verify paper-position scores are v4.0.1

```bash
AWS_REGION=us-west-1 DYNAMODB_TABLE_PREFIX=oss-dev \
  python3 backend/scripts/verify_rescore_v4.py 2>&1 | tail -30
# Expect: 100% coverage on v4 fields; 20,562 positions with scoring_regime='v4'
```

### 7.4 Refresh the archetype analysis

If stale (re-run if anything has changed since 2026-04-18):

```bash
cd backend
AWS_REGION=us-west-1 DYNAMODB_TABLE_PREFIX=oss-dev \
  python3 scripts/home_run_archetypes_v3.py
# Updates scripts/output/home_run_archetypes_findings.md
```

**IMPORTANT**: the six archetypes and three anti-archetypes in Phase 3 and
Phase 4 defaults are derived from this analysis. If the data has shifted
materially, regenerate the defaults from the new analysis BEFORE proceeding.

### 7.5 Snapshot current metrics (pre-v4.1.0 baseline)

```bash
AWS_REGION=us-west-1 DYNAMODB_TABLE_PREFIX=oss-dev \
  python3 backend/scripts/analyze_v4_vs_v3_performance.py \
  --out /tmp/v4_1_0_BASELINE_v401.md
```

Save. These are the numbers v4.1.0 must beat.

### 7.6 Create working branch

```bash
git checkout -b claude/v4-1-0-archetype-scoring
```

### 7.7 Acceptance

- [ ] Active policy = v4.0.1
- [ ] All paper positions have `scoring_regime='v4'`
- [ ] Archetype analysis file fresh (confirm date)
- [ ] Baseline metrics file saved
- [ ] Working branch created from main

---

## 8. Phase 1 — ADX Subscore Rebuild

**Goal:** Replace monotonic-up ADX mapping with inverted-U peaking at 22.
**Estimated time:** 1 day.
**Files touched:** `backend/app/pillars/directional_conviction.py`,
`backend/tests/test_directional_conviction.py`.

### 8.1 Rewrite `_adx_directional_agreement`

Current (at `directional_conviction.py:175-210`):

```python
adx_clamped = max(0.0, min(50.0, float(adx)))
base = 30.0 + (adx_clamped - 15.0) * (85.0 - 30.0) / (40.0 - 15.0)
```

Replace with:

```python
def _adx_directional_agreement(ctx: ScoringContext) -> Optional[float]:
    """Combine ADX magnitude with ±DI sign agreement → 0-100 score.

    v4.1.0 change: the ADX→base-score mapping is an inverted-U peaking
    at ADX=22 (the empirical home-run sweet spot). Established trends
    (ADX > 40) are penalised because by that point the move is late-
    stage and premium typically too expensive for convex reward. Very
    weak trends (ADX < 10) are also penalised — no direction to trade.

    Breakpoints (ADX → base):
         0 → 20
        10 → 50
        18 → 85
        22 → 100  (peak — home-run sweet spot)
        30 → 80
        40 → 55
        55 → 30
        80+→ 15
    """
    adx, plus_di, minus_di = ctx.adx_14, ctx.plus_di, ctx.minus_di
    if adx is None or plus_di is None or minus_di is None:
        return None

    breakpoints = [
        (0.0, 20.0), (10.0, 50.0), (18.0, 85.0), (22.0, 100.0),
        (30.0, 80.0), (40.0, 55.0), (55.0, 30.0), (80.0, 15.0),
    ]
    base = _piecewise_linear(float(adx), breakpoints)

    bullish = ctx.option_type == "CALL"
    dominant_di = plus_di if bullish else minus_di
    opposing_di = minus_di if bullish else plus_di
    di_diff = dominant_di - opposing_di

    if di_diff >= 10:    bonus = 15.0
    elif di_diff >= 0:   bonus = 5.0
    elif di_diff >= -10: bonus = -15.0
    else:                bonus = -25.0

    return max(0.0, min(100.0, base + bonus))


def _piecewise_linear(x: float, breakpoints: list[tuple[float, float]]) -> float:
    if x <= breakpoints[0][0]:
        return breakpoints[0][1]
    if x >= breakpoints[-1][0]:
        return breakpoints[-1][1]
    for (x1, y1), (x2, y2) in zip(breakpoints, breakpoints[1:]):
        if x1 <= x <= x2:
            if x2 == x1:
                return y1
            return y1 + (x - x1) / (x2 - x1) * (y2 - y1)
    return 50.0
```

### 8.2 Unit tests

Add `class TestAdxInvertedU` to `tests/test_directional_conviction.py`:

- `test_peak_at_22`: ADX=22, strong +DI → score ≥ 95
- `test_very_weak_trend_scores_low`: ADX=5, neutral DI → 30-50
- `test_very_strong_trend_scores_lower_than_peak`: ADX=50 → 45-70 (not 85+)
- `test_late_stage_trend_penalized_vs_early_stage`: ADX=22 > ADX=48 with
  same DI agreement
- `test_piecewise_linear_interpolation`: ADX=15 → 68-80

### 8.3 Update existing ADX tests that expected monotonic behavior

Lines 148-169: `test_strong_trend_with_agreement_scores_high` expects
score ≥ 80 at ADX=35. Update to `score >= 70`.

### 8.4 Acceptance

- [ ] `pytest backend/tests/test_directional_conviction.py -q --no-cov` all green
- [ ] Full suite (`pytest backend/tests/ --tb=short -q --no-cov`) 2,293+ pass
- [ ] `ruff check backend/app/pillars/directional_conviction.py` clean
- [ ] Commit: `fix(pillar-v4.1.0): ADX inverted-U curve peaks at 22`

### 8.5 Rollback

Single-file revert. No schema or policy change.

---

## 9. Phase 2 — Weighted-MAX Composite Formula

**Goal:** Add `"weighted_max"` composite formula option.
**Estimated time:** 1 day.
**Files touched:** `backend/app/core/schemas.py`,
`backend/app/pillars/composite.py`, new test module.

### 9.1 Extend `PillarConfig.composite_formula` Literal

In `backend/app/core/schemas.py:1118`:

```python
composite_formula: Literal[
    "weighted_sum",
    "weighted_geometric_mean",
    "weighted_max",   # v4.1.0 addition
] = "weighted_sum"
```

Update `_validate_regime_consistency` (schemas.py:1190-1260) to accept
`"weighted_max"` for v4 regimes in addition to `"weighted_geometric_mean"`.
v3 regime still requires `"weighted_sum"`.

### 9.2 Add `compute_weighted_max` in `backend/app/pillars/composite.py`

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
    """v4.1.0 composite: max_weight × max-pillar + mean_weight × weighted-mean.

    Grand-slam trades historically have one very strong pillar (typically
    TS) with middling DC/MP. The geometric mean drags these to the middle;
    the weighted-max surfaces them while still requiring all pillars to
    clear a soft floor.

    When any pillar falls below ``floor_penalty_threshold`` (default 25),
    the composite is multiplied by ``floor_penalty_multiplier`` (default
    0.7). Softer alternative to the v4.0.0 min-subscore zero-collapse.
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
    if min(pillar_scores) < floor_penalty_threshold:
        composite *= floor_penalty_multiplier
    return max(0.0, min(100.0, composite))
```

### 9.3 Extend dispatch in `compute_composite_score`

```python
if config.composite_formula == "weighted_max":
    return compute_weighted_max(pillar_results, weights)
if config.composite_formula == "weighted_geometric_mean":
    return weighted_geometric_mean(pillar_results, weights)
return weighted_sum(pillar_results, weights)
```

### 9.4 Tests — `backend/tests/test_composite_weighted_max.py` (new)

- `test_strong_ts_middling_dc_mp_surfaces`: TS=76, DC=51, MP=52 → ~65-72
  (confirms asymmetric-pillar grand-slam profile surfaces correctly)
- `test_geo_mean_same_inputs_underperforms_for_asymmetric`:
  `weighted_max > geometric_mean + 5` on the same input
- `test_balanced_high_pillars_all_formulas_similar`: all 85 → composite
  82-88
- `test_soft_floor_penalty_fires_below_25`: one pillar at 20 → 0.7×
  multiplier triggers
- `test_zero_pillar_no_longer_collapses`: one pillar at 0 → composite > 10
  (unlike geometric mean)
- `test_clamped_to_0_100`: all 100 → composite = 100

### 9.5 Acceptance

- [ ] All composite tests green
- [ ] Full suite green
- [ ] `mypy app/pillars/composite.py` + `app/core/schemas.py` clean
- [ ] Schema validator accepts `"weighted_max"` on v4 PillarConfig and
  rejects it on v3
- [ ] Commit: `feat(pillar-v4.1.0): weighted_max composite formula`

### 9.6 Rollback

Revert two files. No data impact — no policy uses the formula yet.

---

## 10. Phase 3 — Archetype Matcher

**Goal:** New scoring axis that matches trades against six empirically-
validated archetypes with graded fit scoring.
**Estimated time:** 3 days (this is the biggest phase).
**Files touched:** many — see §5.

### 10.1 Schema — `ArchetypeConfig`, `ArchetypeDefinition`, `ArchetypeCondition`

In `backend/app/core/schemas.py` (insert near `PillarConfigV2`):

```python
class ArchetypeCondition(OSSBaseModel):
    """One boolean condition within an archetype definition.

    A condition evaluates a feature against a rule and returns a
    *graded fit score* (0-100). A fit of 100 means the feature is
    squarely in the target range; lower scores indicate progressive
    degradation outside that range, capped by the ``feather``.

    Exactly one of {between, lte, gte, eq, in_values} must be set.

    If ``feather`` is set (e.g. 3.0), a value ``feather`` units outside
    the boundary still earns non-zero credit (linearly from 100 at the
    boundary to 0 at boundary±feather). Discrete operators (eq, in_values)
    ignore feather — they are hard match/no-match.
    """

    condition_id: str               # e.g. "dte_14_21"
    display_name: str               # e.g. "DTE in [14, 21]"
    feature_field: str              # ScoringContext attribute name

    between: Optional[list[float]] = None     # [lo, hi], inclusive
    lte: Optional[float] = None
    gte: Optional[float] = None
    eq: Optional[Union[str, float]] = None
    in_values: Optional[list[Union[str, float]]] = None

    feather: Optional[float] = None           # grading distance outside range
    required: bool = True                     # if True, missing feature → 0
                                              # if False, missing → neutral 50

    @model_validator(mode="after")
    def _validate_exactly_one_op(self) -> "ArchetypeCondition":
        ops = [self.between, self.lte, self.gte, self.eq, self.in_values]
        n_set = sum(1 for o in ops if o is not None)
        if n_set != 1:
            raise ValueError(
                f"ArchetypeCondition '{self.condition_id}' must set exactly "
                f"one of between/lte/gte/eq/in_values (got {n_set})"
            )
        return self


class ArchetypeDefinition(OSSBaseModel):
    """A named home-run archetype defined as an AND of conditions.

    The archetype's fit score for a given trade is the minimum of all
    condition fit scores (the weakest-link gates the match). An archetype
    is considered 'matched' when fit ≥ ``min_fit_to_match``.
    """

    archetype_id: str               # e.g. "UV_LOTTERY_CALL"
    display_name: str               # e.g. "UV Lottery Call"
    description: str                # human-readable thesis
    conditions: list[ArchetypeCondition]

    # Metadata — drives UI, tuning, and the analyst audit trail
    historical_n: int               # sample size in archetype analysis
    historical_hr200_rate: float    # e.g. 0.2018 (20.18%)
    historical_win_rate: float      # e.g. 0.658 (65.8%)
    historical_mean_pnl_pct: float  # e.g. 84.36

    # Fit threshold for "matched" — below this, the archetype does not count
    min_fit_to_match: float = 75.0
    # When matched, the trade's archetype_match_score is the fit value
    # multiplied by this. Allows relative weighting across archetypes if
    # needed (default 1.0 — fit is the score).
    match_score_multiplier: float = 1.0


class ArchetypeConfig(OSSBaseModel):
    """Collection of archetype definitions, evaluated in registration order.
    The best-matching (highest fit) archetype becomes the trade's
    archetype_match.
    """
    archetypes: list[ArchetypeDefinition]
```

### 10.2 Compute module — `backend/app/archetypes/matcher.py`

```python
"""Archetype matcher — v4.1.0 secondary scoring axis.

Evaluates a Decision's ScoringContext against all configured archetypes.
Each archetype is an AND of conditions; condition fit scores (0-100)
are combined via MIN; the best archetype (highest min-fit) becomes the
matched archetype if its fit ≥ min_fit_to_match.

Anti-archetypes live in a sibling module (gates.py) because they have
hard-reject semantics; here we only compute positive matches.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional, Union

from app.core.schemas import (
    ArchetypeCondition,
    ArchetypeConfig,
    ArchetypeDefinition,
)
from app.pillars.models import ScoringContext

logger = logging.getLogger(__name__)


@dataclass
class ConditionFit:
    condition_id: str
    display_name: str
    raw_value: Any
    fit: float                      # 0-100


@dataclass
class ArchetypeFit:
    archetype_id: str
    display_name: str
    fit: float                      # min of condition fits
    condition_fits: list[ConditionFit]
    matched: bool                   # fit >= min_fit_to_match


@dataclass
class ArchetypeMatchResult:
    best: Optional[ArchetypeFit]    # best matched archetype (fit ≥ threshold)
    best_match_score: Optional[float]  # = best.fit × multiplier (0-100)
    all_fits: dict[str, float]      # every archetype's fit (for UI / debug)
    tags: list[str]                 # for downstream filtering


def compute_archetype_match(
    ctx: ScoringContext,
    config: ArchetypeConfig,
    *,
    pillar_scores: Optional[dict[str, float]] = None,
) -> ArchetypeMatchResult:
    """Evaluate all archetypes, return the best match + per-archetype fits.

    `pillar_scores` allows archetype conditions to reference pillar scores
    like `ts_score` (e.g., "TS≥75" condition in Archetype B).
    """
    pillar_scores = pillar_scores or {}
    all_fits: dict[str, float] = {}
    archetype_fits: list[ArchetypeFit] = []

    for archetype in config.archetypes:
        cond_fits = [
            _evaluate_condition(ctx, cond, pillar_scores)
            for cond in archetype.conditions
        ]
        # Min-fit: weakest condition gates the match
        if cond_fits:
            min_fit = min(c.fit for c in cond_fits)
        else:
            min_fit = 0.0
        matched = min_fit >= archetype.min_fit_to_match
        archetype_fits.append(ArchetypeFit(
            archetype_id=archetype.archetype_id,
            display_name=archetype.display_name,
            fit=min_fit,
            condition_fits=cond_fits,
            matched=matched,
        ))
        all_fits[archetype.archetype_id] = round(min_fit, 2)

    # Pick the best matched archetype
    matched = [af for af in archetype_fits if af.matched]
    best: Optional[ArchetypeFit] = max(matched, key=lambda af: af.fit) if matched else None

    best_score = None
    tags: list[str] = []
    if best is not None:
        # Look up multiplier from original config
        multiplier = next(
            (a.match_score_multiplier for a in config.archetypes
             if a.archetype_id == best.archetype_id),
            1.0,
        )
        best_score = min(100.0, best.fit * multiplier)
        tags.append(f"ARCHETYPE_{best.archetype_id}")

    return ArchetypeMatchResult(
        best=best,
        best_match_score=best_score,
        all_fits=all_fits,
        tags=tags,
    )


def _evaluate_condition(
    ctx: ScoringContext,
    cond: ArchetypeCondition,
    pillar_scores: dict[str, float],
) -> ConditionFit:
    """Compute 0-100 fit score for a single condition."""
    raw = _resolve_feature(ctx, cond.feature_field, pillar_scores)

    if raw is None:
        fit = 0.0 if cond.required else 50.0
        return ConditionFit(cond.condition_id, cond.display_name, None, fit)

    try:
        fit = _condition_fit(raw, cond)
    except Exception:
        logger.exception(
            f"archetype condition eval failed: {cond.condition_id}"
        )
        fit = 0.0

    return ConditionFit(cond.condition_id, cond.display_name, raw, fit)


def _resolve_feature(
    ctx: ScoringContext,
    field: str,
    pillar_scores: dict[str, float],
) -> Any:
    """Look up a feature on ctx OR a pillar score by name.

    Pillar score aliases recognised: 'ts_score', 'mp_score', 'dc_score'.
    Derived fields recognised: 'abs_delta', 'rs_contrarian'.
    """
    if field in ("ts_score", "mp_score", "dc_score"):
        return pillar_scores.get(field.upper().replace("_SCORE", ""))
    if field == "abs_delta":
        d = ctx.delta
        return abs(d) if d is not None else None
    if field == "rs_contrarian":
        # 1 if rs_20d and option direction are opposed (bearish-call or
        # bullish-put); 0 otherwise. None if rs_20d missing.
        rs = ctx.rs_20d
        if rs is None:
            return None
        if ctx.option_type == "CALL":
            return 1.0 if rs < 0 else 0.0
        return 1.0 if rs > 0 else 0.0
    # Default: direct attribute on ctx
    return getattr(ctx, field, None)


def _condition_fit(value: Any, cond: ArchetypeCondition) -> float:
    """Return fit score for a resolved feature value against a condition."""
    # Discrete eq / in_values — no feather
    if cond.eq is not None:
        return 100.0 if value == cond.eq else 0.0
    if cond.in_values is not None:
        return 100.0 if value in cond.in_values else 0.0

    # Numeric conditions — graded with feather
    v = float(value)
    feather = cond.feather or 0.0

    if cond.between is not None:
        lo, hi = cond.between[0], cond.between[1]
        if lo <= v <= hi:
            return 100.0
        if feather > 0:
            if lo - feather <= v < lo:
                return 100.0 * (v - (lo - feather)) / feather
            if hi < v <= hi + feather:
                return 100.0 * ((hi + feather) - v) / feather
        return 0.0

    if cond.lte is not None:
        if v <= cond.lte:
            return 100.0
        if feather > 0 and v <= cond.lte + feather:
            return 100.0 * ((cond.lte + feather) - v) / feather
        return 0.0

    if cond.gte is not None:
        if v >= cond.gte:
            return 100.0
        if feather > 0 and v >= cond.gte - feather:
            return 100.0 * (v - (cond.gte - feather)) / feather
        return 0.0

    return 50.0  # unreachable if validation passed
```

### 10.3 Add to `PolicyConfig`

In `backend/app/core/schemas.py` `PolicyConfig`:

```python
archetypes: Optional[ArchetypeConfig] = None
anti_archetypes: Optional[AntiArchetypeConfig] = None  # see §11
```

### 10.4 Extend `Decision`, `EvaluationSnapshot`, `PaperPosition`

All three schemas gain:

```python
archetype_matched: Optional[str] = None
archetype_match_score: Optional[float] = None
archetype_all_fits: Optional[dict[str, float]] = None
anti_archetype_triggered: Optional[str] = None
```

### 10.5 Integrate into `backend/app/decision/calculator.py`

After pillars are computed (around line 128 currently), invoke the
matcher if policy has archetypes defined:

```python
# v4.1.0: archetype matching (secondary scoring axis)
archetype_result = None
if config.archetypes is not None:
    pillar_scores = {
        r.pillar_id.value: r.score for r in pillar_results
    }
    archetype_result = compute_archetype_match(
        ctx, config.archetypes, pillar_scores=pillar_scores,
    )

# Attach to Decision (anti-archetype handled earlier via gates, see §11)
decision = Decision(
    ...,
    archetype_matched=(
        archetype_result.best.archetype_id if archetype_result and archetype_result.best else None
    ),
    archetype_match_score=(
        round(archetype_result.best_match_score, 2)
        if archetype_result and archetype_result.best_match_score is not None
        else None
    ),
    archetype_all_fits=(
        archetype_result.all_fits if archetype_result else None
    ),
)
```

### 10.6 Default archetypes — `backend/app/archetypes/defaults.py`

The six archetypes from the analysis, encoded with feather values that give
graded fit at edge-of-window cases:

```python
def default_archetypes() -> ArchetypeConfig:
    """v4.1.0 default archetypes — derived from
    home_run_archetypes_findings.md (2026-04-18) on 18,567 paper trades.
    """
    return ArchetypeConfig(archetypes=[

        # Archetype A — UV Lottery Call
        # UV × DTE 14-21 × |delta|<0.25 × CALL → 20.2% HR200 (18.7× baseline)
        ArchetypeDefinition(
            archetype_id="UV_LOTTERY_CALL",
            display_name="UV Lottery Call",
            description=(
                "Unusual-volume flagged stock with short-dated (DTE 14-21), "
                "far-OTM (|delta|<0.25) call. Tight gamma profile turns small "
                "underlying moves into outsized % gains — classic asymmetric "
                "lottery ticket."
            ),
            historical_n=114,
            historical_hr200_rate=0.2018,
            historical_win_rate=0.658,
            historical_mean_pnl_pct=84.36,
            conditions=[
                ArchetypeCondition(
                    condition_id="scanner_uv",
                    display_name="Scanner = UNUSUAL_VOLUME",
                    feature_field="scanner_source",
                    eq="UNUSUAL_VOLUME",
                ),
                ArchetypeCondition(
                    condition_id="dte_14_21",
                    display_name="DTE in [14, 21]",
                    feature_field="dte",
                    between=[14.0, 21.0], feather=3.0,
                ),
                ArchetypeCondition(
                    condition_id="low_delta",
                    display_name="|delta| < 0.25",
                    feature_field="abs_delta",
                    lte=0.25, feather=0.05,
                ),
                ArchetypeCondition(
                    condition_id="option_call",
                    display_name="Option type = CALL",
                    feature_field="option_type",
                    eq="CALL",
                ),
            ],
        ),

        # Archetype B — UV Structural Explosion
        ArchetypeDefinition(
            archetype_id="UV_STRUCTURAL",
            display_name="UV Structural Explosion",
            description=(
                "Unusual-volume stock with short-dated option + excellent "
                "Trade Structure pillar (TS≥75). Contract microstructure "
                "(spread, OI, liquidity) is clean — low friction lets moves "
                "translate to wins without TS-grade filtering out."
            ),
            historical_n=274,
            historical_hr200_rate=0.0949,
            historical_win_rate=0.599,
            historical_mean_pnl_pct=50.32,
            conditions=[
                ArchetypeCondition(
                    condition_id="scanner_uv",
                    display_name="Scanner = UNUSUAL_VOLUME",
                    feature_field="scanner_source",
                    eq="UNUSUAL_VOLUME",
                ),
                ArchetypeCondition(
                    condition_id="dte_14_21",
                    display_name="DTE in [14, 21]",
                    feature_field="dte",
                    between=[14.0, 21.0], feather=3.0,
                ),
                ArchetypeCondition(
                    condition_id="ts_high",
                    display_name="TS pillar ≥ 75",
                    feature_field="ts_score",
                    gte=75.0, feather=5.0,
                ),
            ],
        ),

        # Archetype C — UV Reversal Put
        ArchetypeDefinition(
            archetype_id="UV_REVERSAL_PUT",
            display_name="UV Reversal Put",
            description=(
                "Unusual-volume PUT on a stock that's been rallying "
                "(RS>0 → contrarian for a put). Captures institutional "
                "hedging/reversal flow that UV identifies."
            ),
            historical_n=220,
            historical_hr200_rate=0.1045,
            historical_win_rate=0.545,
            historical_mean_pnl_pct=44.19,
            conditions=[
                ArchetypeCondition(
                    condition_id="scanner_uv",
                    display_name="Scanner = UNUSUAL_VOLUME",
                    feature_field="scanner_source",
                    eq="UNUSUAL_VOLUME",
                ),
                ArchetypeCondition(
                    condition_id="option_put",
                    display_name="Option type = PUT",
                    feature_field="option_type",
                    eq="PUT",
                ),
                ArchetypeCondition(
                    condition_id="ts_high",
                    display_name="TS pillar ≥ 75",
                    feature_field="ts_score",
                    gte=75.0, feather=5.0,
                ),
                ArchetypeCondition(
                    condition_id="rs_contrarian",
                    display_name="RS direction opposes option (contrarian)",
                    feature_field="rs_contrarian",
                    eq=1.0,
                ),
            ],
        ),

        # Archetype D — Cheap Options Compression Breakout
        ArchetypeDefinition(
            archetype_id="CHEAP_COMPRESSION",
            display_name="Cheap Options Compression Breakout",
            description=(
                "Cheap-options entry on a coiled (low-ADX) underlying with "
                "mid-range ATR and elevated move-potential pillar. The "
                "'spring about to uncoil' setup."
            ),
            historical_n=93,
            historical_hr200_rate=0.0753,
            historical_win_rate=0.484,
            historical_mean_pnl_pct=26.99,
            conditions=[
                ArchetypeCondition(
                    condition_id="scanner_cheap",
                    display_name="Scanner = CHEAP_OPTIONS",
                    feature_field="scanner_source",
                    eq="CHEAP_OPTIONS",
                ),
                ArchetypeCondition(
                    condition_id="adx_low",
                    display_name="ADX < 20",
                    feature_field="adx_14",
                    lte=20.0, feather=3.0,
                ),
                ArchetypeCondition(
                    condition_id="atr_mid_high",
                    display_name="ATR% in [4.0, 6.0]",
                    feature_field="atr14_pct",
                    between=[4.0, 6.0], feather=1.0,
                ),
                ArchetypeCondition(
                    condition_id="mp_high",
                    display_name="MP pillar ≥ 60",
                    feature_field="mp_score",
                    gte=60.0, feather=5.0,
                ),
            ],
        ),

        # Archetype E — Cheap Options Volatile Reversal
        ArchetypeDefinition(
            archetype_id="CHEAP_VOL_REVERSAL",
            display_name="Cheap Options Volatile Reversal",
            description=(
                "High-ATR underlying where options are fairly-priced and "
                "relative strength is against the option's direction — "
                "catches sharp reversals."
            ),
            historical_n=50,
            historical_hr200_rate=0.0800,
            historical_win_rate=0.740,
            historical_mean_pnl_pct=51.74,
            conditions=[
                ArchetypeCondition(
                    condition_id="scanner_cheap",
                    display_name="Scanner = CHEAP_OPTIONS",
                    feature_field="scanner_source",
                    eq="CHEAP_OPTIONS",
                ),
                ArchetypeCondition(
                    condition_id="atr_high",
                    display_name="ATR% ≥ 6.0",
                    feature_field="atr14_pct",
                    gte=6.0, feather=1.0,
                ),
                ArchetypeCondition(
                    condition_id="ivrv_fair",
                    display_name="IV/RV in [1.0, 1.3] (fairly priced)",
                    feature_field="iv_rv_ratio",
                    between=[1.0, 1.3], feather=0.1,
                ),
                ArchetypeCondition(
                    condition_id="rs_contrarian",
                    display_name="RS direction opposes option (contrarian)",
                    feature_field="rs_contrarian",
                    eq=1.0,
                ),
            ],
        ),

        # Archetype F — Cheap Options Ultra-Short Call
        ArchetypeDefinition(
            archetype_id="CHEAP_ULTRA_CALL",
            display_name="Cheap Options Ultra-Short Call",
            description=(
                "Ultra-short-dated (DTE<14) CALL on a low-IV underlying "
                "flagged by the cheap-options scanner. Huge historical "
                "hit rate (87%) but tiny sample — treat with caution."
            ),
            historical_n=37,
            historical_hr200_rate=0.1081,
            historical_win_rate=0.865,
            historical_mean_pnl_pct=79.82,
            min_fit_to_match=80.0,  # raise threshold due to small sample
            conditions=[
                ArchetypeCondition(
                    condition_id="scanner_cheap",
                    display_name="Scanner = CHEAP_OPTIONS",
                    feature_field="scanner_source",
                    eq="CHEAP_OPTIONS",
                ),
                ArchetypeCondition(
                    condition_id="dte_ultra",
                    display_name="DTE < 14",
                    feature_field="dte",
                    lte=14.0, feather=2.0,
                ),
                ArchetypeCondition(
                    condition_id="option_call",
                    display_name="Option type = CALL",
                    feature_field="option_type",
                    eq="CALL",
                ),
                ArchetypeCondition(
                    condition_id="ivp_low",
                    display_name="IV percentile < 30",
                    feature_field="iv_percentile",
                    lte=30.0, feather=5.0,
                ),
            ],
        ),

    ])
```

### 10.7 Unit tests — `backend/tests/test_archetype_matcher.py`

- `test_archetype_a_perfect_match`: UV + DTE=17 + delta=0.18 + CALL → fit=100
- `test_archetype_a_edge_of_feather`: DTE=23 (2 past hi=21, feather=3) →
  fit = (3-2)/3 × 100 = 33
- `test_archetype_a_outside_feather`: DTE=25 → fit=0
- `test_missing_feature_on_required_condition_fails`: adx_14=None → fit=0
- `test_missing_feature_on_optional_condition_neutral`: same but
  `required=False` → fit=50
- `test_best_match_selected_when_multiple_archetypes_match`: setup matching
  both A and B → the higher-fit one wins
- `test_below_min_fit_not_matched`: all conditions fit 70, threshold 75 →
  no match
- `test_discrete_eq_no_feather`: scanner_source mismatched → fit=0
- `test_rs_contrarian_derived_feature`: CALL with rs_20d=-2 (weak stock)
  → rs_contrarian=1.0
- `test_condition_validator_rejects_multi_op`:
  `ArchetypeCondition(between=[0,1], lte=5)` → ValueError

### 10.8 Acceptance

- [ ] `pytest backend/tests/test_archetype_matcher.py -q --no-cov` green
- [ ] Full suite green
- [ ] `ruff check backend/app/archetypes/` clean
- [ ] `mypy backend/app/archetypes/` clean
- [ ] Decision with archetypes config populated → `archetype_matched`,
  `archetype_match_score`, `archetype_all_fits` populated
- [ ] Decision without archetypes config → all four new fields are None
- [ ] Historical v3 / v4.0.0 / v4.0.1 evaluations deserialize cleanly
- [ ] Commit: `feat(pillar-v4.1.0): archetype matcher + 6 default archetypes`

### 10.9 Rollback

Revert the commit. All schema additions are Optional — no data impact.

---

## 11. Phase 4 — Anti-Archetype Gates

**Goal:** Hard-reject three empirically-validated losing patterns.
**Estimated time:** 1 day.
**Files touched:** `backend/app/core/schemas.py`,
`backend/app/archetypes/gates.py` (new), `backend/app/gates/gates.py`,
`backend/app/decision/calculator.py`.

### 11.1 Schema — `AntiArchetypeConfig`, `AntiArchetypeDefinition`

Anti-archetypes reuse `ArchetypeCondition` for their condition model but
evaluate with ALL-must-hold AND discrete match (no graded fit). When
all conditions hold, the gate fires and the trade is REJECTed.

```python
class AntiArchetypeDefinition(OSSBaseModel):
    """A named losing pattern that REJECTS a trade when all conditions hold.

    Unlike positive archetypes, anti-archetypes are binary: either all
    conditions match (trade rejected) or not (trade proceeds to scoring).
    Feather values on conditions are ignored for this purpose.
    """

    anti_archetype_id: str      # e.g. "BREAKOUT_MP_ELITE"
    display_name: str
    description: str
    conditions: list[ArchetypeCondition]

    # Metadata for analyst audit trail
    historical_n: int
    historical_win_rate: float  # e.g. 0.0
    historical_mean_pnl_pct: float  # e.g. -57.5

    # REJECT reason displayed in the Decision + UI
    rejection_reason: str       # short code e.g. "ANTI_BREAKOUT_MP_ELITE"

    # If False, the gate is disabled (useful for policy tuning)
    enabled: bool = True


class AntiArchetypeConfig(OSSBaseModel):
    anti_archetypes: list[AntiArchetypeDefinition]
```

### 11.2 Compute module — `backend/app/archetypes/gates.py`

```python
"""Anti-archetype gates — v4.1.0 hard-REJECT logic.

An anti-archetype fires when all its conditions hold (discrete match, no
feather). When one fires, the decision is returned as REJECT with the
anti-archetype's rejection_reason attached.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.archetypes.matcher import _evaluate_condition
from app.core.schemas import AntiArchetypeConfig
from app.pillars.models import ScoringContext


@dataclass
class AntiArchetypeResult:
    triggered: bool
    anti_archetype_id: Optional[str]
    rejection_reason: Optional[str]


def check_anti_archetypes(
    ctx: ScoringContext,
    config: AntiArchetypeConfig,
    *,
    pillar_scores: Optional[dict[str, float]] = None,
) -> AntiArchetypeResult:
    """Return the first matching anti-archetype, or no-match.

    All conditions must have fit == 100.0 (discrete match) for the gate
    to fire. Feather values are honoured on their positive-archetype
    side — here we treat them as exact-match for REJECT semantics.
    """
    pillar_scores = pillar_scores or {}
    for aa in config.anti_archetypes:
        if not aa.enabled:
            continue
        all_match = True
        for cond in aa.conditions:
            fit = _evaluate_condition(ctx, cond, pillar_scores)
            if fit.fit < 100.0:  # strict match — no partial credit
                all_match = False
                break
        if all_match:
            return AntiArchetypeResult(
                triggered=True,
                anti_archetype_id=aa.anti_archetype_id,
                rejection_reason=aa.rejection_reason,
            )
    return AntiArchetypeResult(triggered=False, anti_archetype_id=None, rejection_reason=None)
```

### 11.3 Default anti-archetypes — extend `backend/app/archetypes/defaults.py`

```python
def default_anti_archetypes() -> AntiArchetypeConfig:
    """Three empirically-validated losing patterns (2026-04-18 analysis).
    All three have n ≥ 321 and confirm historical loss rates at statistical
    significance."""
    return AntiArchetypeConfig(anti_archetypes=[

        # AA1 — BREAKOUT × MP_ELITE: 321 trades, 0% win, -57.5% mean
        AntiArchetypeDefinition(
            anti_archetype_id="BREAKOUT_MP_ELITE",
            display_name="BREAKOUT with Elite MP Score",
            description=(
                "The BREAKOUT scanner already captures momentum. When MP "
                "(move-potential) pillar ALSO scores ≥ 75, we're double-"
                "counting momentum and the resulting entry is catastrophic "
                "(0% win, -57.5% mean P&L on 321 historical trades)."
            ),
            historical_n=321,
            historical_win_rate=0.0,
            historical_mean_pnl_pct=-57.5,
            rejection_reason="ANTI_ARCHETYPE_BREAKOUT_MP_ELITE",
            conditions=[
                ArchetypeCondition(
                    condition_id="scanner_breakout",
                    display_name="Scanner = BREAKOUT",
                    feature_field="scanner_source",
                    eq="BREAKOUT",
                ),
                ArchetypeCondition(
                    condition_id="mp_elite",
                    display_name="MP pillar ≥ 75",
                    feature_field="mp_score",
                    gte=75.0,
                ),
            ],
        ),

        # AA2 — UV × DTE ≥ 45: 2,241 trades, 0.6% HR200, -9.3% mean
        AntiArchetypeDefinition(
            anti_archetype_id="UV_LONG_DTE",
            display_name="UV with Long DTE",
            description=(
                "Unusual-volume signals are short-cycle catalysts. Long-"
                "dated (DTE ≥ 45) options bleed theta while waiting for a "
                "move that has already peaked. 2,241 historical trades → "
                "0.6% HR200, -9.3% mean P&L."
            ),
            historical_n=2241,
            historical_win_rate=0.374,
            historical_mean_pnl_pct=-9.3,
            rejection_reason="ANTI_ARCHETYPE_UV_LONG_DTE",
            conditions=[
                ArchetypeCondition(
                    condition_id="scanner_uv",
                    display_name="Scanner = UNUSUAL_VOLUME",
                    feature_field="scanner_source",
                    eq="UNUSUAL_VOLUME",
                ),
                ArchetypeCondition(
                    condition_id="dte_long",
                    display_name="DTE ≥ 45",
                    feature_field="dte",
                    gte=45.0,
                ),
            ],
        ),

        # AA3 — CHEAP_OPTIONS × DC_ELITE: 2,315 trades, 0.1% HR200, -5% mean
        AntiArchetypeDefinition(
            anti_archetype_id="CHEAP_DC_ELITE",
            display_name="Cheap Options with Elite DC Score",
            description=(
                "CHEAP_OPTIONS entries are compression/reversal setups. "
                "High DC (directional conviction) means the stock is "
                "already trending strongly — the compression thesis is "
                "invalidated. 2,315 trades → 0.1% HR200, -5% mean P&L."
            ),
            historical_n=2315,
            historical_win_rate=0.414,
            historical_mean_pnl_pct=-5.0,
            rejection_reason="ANTI_ARCHETYPE_CHEAP_DC_ELITE",
            conditions=[
                ArchetypeCondition(
                    condition_id="scanner_cheap",
                    display_name="Scanner = CHEAP_OPTIONS",
                    feature_field="scanner_source",
                    eq="CHEAP_OPTIONS",
                ),
                ArchetypeCondition(
                    condition_id="dc_elite",
                    display_name="DC pillar ≥ 75",
                    feature_field="dc_score",
                    gte=75.0,
                ),
            ],
        ),

    ])
```

### 11.4 Integrate into decision calculator

In `backend/app/decision/calculator.py`, after pillar scoring but BEFORE
composite calculation (so anti-archetype gates fire as early as possible):

```python
# v4.1.0: anti-archetype gates — REJECT before proceeding to composite
anti_archetype_result = None
if config.anti_archetypes is not None:
    pillar_scores = {r.pillar_id.value: r.score for r in pillar_results}
    anti_archetype_result = check_anti_archetypes(
        ctx, config.anti_archetypes, pillar_scores=pillar_scores,
    )
    if anti_archetype_result.triggered:
        # Short-circuit to REJECT
        return Decision(
            ...,
            verdict=Verdict.REJECT,
            quality_tier=None,
            composite_score=0.0,  # or compute-and-ignore — team call
            anti_archetype_triggered=anti_archetype_result.anti_archetype_id,
            rejection_reasons=[anti_archetype_result.rejection_reason],
            ...
        )

# Proceed to composite + archetype matching as normal
```

### 11.5 Unit tests — `backend/tests/test_anti_archetype_gates.py`

- `test_breakout_mp_elite_rejected`: BREAKOUT + MP=80 → triggered=True,
  id="BREAKOUT_MP_ELITE"
- `test_breakout_mp_below_threshold_not_rejected`: BREAKOUT + MP=74 →
  triggered=False
- `test_non_breakout_with_high_mp_not_rejected`: UV + MP=90 → triggered=False
- `test_uv_long_dte_rejected`: UV + DTE=50 → triggered=True
- `test_cheap_dc_elite_rejected`: CHEAP_OPTIONS + DC=80 → triggered=True
- `test_disabled_gate_does_not_fire`: anti-archetype with enabled=False →
  triggered=False even when all conditions match
- `test_missing_pillar_score_not_rejected`: DC=None → triggered=False
  (can't evaluate condition)
- `test_decision_calculator_integration`: construct full evaluation with
  anti-archetype match; resulting Decision has verdict=REJECT,
  anti_archetype_triggered set

### 11.6 Acceptance

- [ ] `pytest backend/tests/test_anti_archetype_gates.py -q --no-cov` green
- [ ] Full suite green
- [ ] Decision with anti-archetype match has verdict=REJECT and
  `anti_archetype_triggered` set
- [ ] Decision without anti-archetype match proceeds normally
- [ ] Policy without `anti_archetypes` configured → no new gates fire
- [ ] Commit: `feat(pillar-v4.1.0): anti-archetype gates with 3 defaults`

### 11.7 Rollback

Revert the commit. Schema fields are Optional; live pipeline behavior
unchanged if policy config lacks `anti_archetypes`.

---

## 12. Phase 5 — Archetype-Aware Tier Assignment

**Goal:** TIER_1 and TIER_2 now support a second qualification path via the
archetype match score.
**Estimated time:** 0.5 day.
**Files touched:** `backend/app/core/schemas.py` (DecisionConfig),
`backend/app/decision/calculator.py`.

### 12.1 Extend `DecisionConfig`

```python
class DecisionConfig(OSSBaseModel):
    # ... existing ...
    tier_1_threshold: float = 92.0
    tier_2_threshold: float = 82.0
    tier_3_threshold: float = 72.0
    watch_threshold: float = 62.0

    # v4.1.0 additions
    archetype_tier_1_threshold: float = 80.0  # archetype_match_score ≥ 80 → TIER_1 path
    archetype_tier_2_threshold: float = 70.0
```

### 12.2 Tier-assignment logic

```python
def _assign_quality_tier(
    composite: float,
    archetype_score: Optional[float],
    config: DecisionConfig,
) -> Optional[QualityTier]:
    """v4.1.0 dual-axis tier assignment.

    TIER_1: composite ≥ tier_1_threshold OR archetype ≥ archetype_tier_1_threshold
    TIER_2: composite ≥ tier_2_threshold OR archetype ≥ archetype_tier_2_threshold
    TIER_3: composite ≥ tier_3_threshold (composite only)
    WATCH / REJECT: composite below thresholds (None returned)
    """
    if composite >= config.tier_1_threshold:
        return QualityTier.TIER_1
    if archetype_score is not None and archetype_score >= config.archetype_tier_1_threshold:
        return QualityTier.TIER_1
    if composite >= config.tier_2_threshold:
        return QualityTier.TIER_2
    if archetype_score is not None and archetype_score >= config.archetype_tier_2_threshold:
        return QualityTier.TIER_2
    if composite >= config.tier_3_threshold:
        return QualityTier.TIER_3
    return None
```

Simpler than the previous plan's co-criterion logic — archetype score alone
can surface TIER_1 if it's strong enough.

### 12.3 Update the final `conviction_score` displayed

Introduce a derived value `displayed_conviction` = `max(composite, archetype_match_score if matched else 0)`. This
is what the Opportunities/Trade pages show as "Conviction" by default.
Composite + archetype fields are also available for power users.

Implementation: add a `@computed_field` or property on `Decision`:

```python
@property
def displayed_conviction(self) -> float:
    if self.archetype_match_score is None:
        return self.composite_score or 0.0
    return max(self.composite_score or 0.0, self.archetype_match_score)
```

Or compute and store explicitly in `conviction_score` during calculator
execution (probably simpler — preserves the existing single-scalar contract
for downstream consumers).

### 12.4 Unit tests

- `test_composite_path_tier_1`: composite=92.5, archetype=None → TIER_1
- `test_archetype_path_tier_1`: composite=60, archetype=85 → TIER_1
- `test_archetype_path_tier_2`: composite=50, archetype=72 → TIER_2
- `test_neither_threshold_met`: composite=70, archetype=60 → TIER_3
- `test_historical_v3_policy_no_archetype`: archetype=None,
  composite=82 → TIER_2 (composite path only)

### 12.5 Acceptance

- [ ] Tests pass
- [ ] A v3-regime Decision with no archetype data → same tier as pre-v4.1.0
- [ ] Commit: `feat(pillar-v4.1.0): archetype-aware tier assignment`

### 12.6 Rollback

Revert the commit. Fields are additive.

---

## 13. Phase 6 — Frontend Archetype Visualization

**Goal:** Surface matched archetypes as first-class UI elements.
**Estimated time:** 1.5 days.

### 13.1 TypeScript types — `frontend/src/lib/types.ts`

```typescript
export type ArchetypeId =
  | "UV_LOTTERY_CALL" | "UV_STRUCTURAL" | "UV_REVERSAL_PUT"
  | "CHEAP_COMPRESSION" | "CHEAP_VOL_REVERSAL" | "CHEAP_ULTRA_CALL"
  | string;  // forward-compat for future additions

export type AntiArchetypeId =
  | "BREAKOUT_MP_ELITE" | "UV_LONG_DTE" | "CHEAP_DC_ELITE"
  | string;

export interface Decision {
  // ... existing ...
  archetype_matched?: ArchetypeId | null;
  archetype_match_score?: number | null;
  archetype_all_fits?: Record<string, number> | null;
  anti_archetype_triggered?: AntiArchetypeId | null;
}

export interface PaperPosition {
  // ... existing ...
  archetype_matched?: ArchetypeId | null;
  archetype_match_score?: number | null;
}
```

### 13.2 Archetype metadata — `frontend/src/lib/archetypeMeta.ts`

```typescript
import { TrendingUp, Zap, ArrowDownUp, Coil, Flame, Rocket, AlertOctagon } from "lucide-react";

export interface ArchetypeMeta {
  id: string;
  label: string;
  shortLabel: string;
  thesis: string;
  color: string;            // tailwind color class
  icon: React.ComponentType;
  historicalHr200Rate: number;
  historicalWinRate: number;
  historicalN: number;
  legacy?: boolean;
}

export const ARCHETYPE_META: Record<string, ArchetypeMeta> = {
  UV_LOTTERY_CALL: {
    id: "UV_LOTTERY_CALL",
    label: "UV Lottery Call",
    shortLabel: "UV Lottery",
    thesis: "Short-DTE far-OTM call on unusual-volume stock",
    color: "text-yellow-400",
    icon: Rocket,
    historicalHr200Rate: 0.2018,
    historicalWinRate: 0.658,
    historicalN: 114,
  },
  UV_STRUCTURAL: { /* ... */ },
  UV_REVERSAL_PUT: { /* ... */ },
  CHEAP_COMPRESSION: { /* ... */ },
  CHEAP_VOL_REVERSAL: { /* ... */ },
  CHEAP_ULTRA_CALL: { /* ... */ },
};

export const ANTI_ARCHETYPE_META: Record<string, ArchetypeMeta> = {
  BREAKOUT_MP_ELITE: {
    id: "BREAKOUT_MP_ELITE",
    label: "BREAKOUT × MP Elite (rejected)",
    shortLabel: "AA: Brkt-MP",
    thesis: "BREAKOUT already captures momentum; MP ≥ 75 is a double-count",
    color: "text-red-500",
    icon: AlertOctagon,
    historicalHr200Rate: 0,
    historicalWinRate: 0,
    historicalN: 321,
  },
  // ...
};

export function archetypeMeta(id: string): ArchetypeMeta {
  return ARCHETYPE_META[id] ?? {
    id, label: id, shortLabel: id, thesis: "",
    color: "text-slate-400", icon: TrendingUp,
    historicalHr200Rate: 0, historicalWinRate: 0, historicalN: 0,
  };
}
```

### 13.3 `ArchetypeMatchCard.tsx` (new)

Large detail card for EvaluationDetail page:

```tsx
interface Props {
  archetypeId?: string | null;
  matchScore?: number | null;
  allFits?: Record<string, number> | null;
  antiArchetypeTriggered?: string | null;
}

export function ArchetypeMatchCard({ archetypeId, matchScore, allFits, antiArchetypeTriggered }: Props) {
  if (antiArchetypeTriggered) {
    const meta = ANTI_ARCHETYPE_META[antiArchetypeTriggered];
    return (
      <div className="card border-red-500/50">
        <h3 className="text-red-400">⛔ Anti-Archetype Triggered</h3>
        <p className="font-bold">{meta.label}</p>
        <p className="text-sm text-slate-300">{meta.thesis}</p>
        <p className="text-xs text-slate-400 mt-2">
          Historical: n={meta.historicalN}, win rate {(meta.historicalWinRate * 100).toFixed(0)}%
        </p>
      </div>
    );
  }
  if (!archetypeId || matchScore == null) {
    return (
      <div className="card">
        <h3>Archetype Match</h3>
        <p className="text-slate-400">No specific archetype matched</p>
        {allFits && <AllFitsTable fits={allFits} />}
      </div>
    );
  }
  const meta = archetypeMeta(archetypeId);
  return (
    <div className="card border-yellow-400/50">
      <h3 className={meta.color}>★ {meta.label}</h3>
      <Gauge value={matchScore} threshold={80} label={`${matchScore.toFixed(0)}% fit`} />
      <p className="text-sm text-slate-300 mt-2">{meta.thesis}</p>
      <div className="mt-3 grid grid-cols-3 gap-2 text-xs">
        <Stat label="HR200 rate" value={`${(meta.historicalHr200Rate*100).toFixed(1)}%`} />
        <Stat label="Win rate" value={`${(meta.historicalWinRate*100).toFixed(0)}%`} />
        <Stat label="Sample" value={`n=${meta.historicalN}`} />
      </div>
      {allFits && <AllFitsTable fits={allFits} highlight={archetypeId} />}
    </div>
  );
}
```

### 13.4 `ArchetypeBadge.tsx` — compact badge for tables

```tsx
export function ArchetypeBadge({ archetypeId, score }: {
  archetypeId?: string | null;
  score?: number | null;
}) {
  if (!archetypeId) return <span className="text-slate-500">—</span>;
  const meta = archetypeMeta(archetypeId);
  return (
    <span className={`badge ${meta.color}`}>
      <meta.icon size={12} /> {meta.shortLabel}
      {score != null && ` ${score.toFixed(0)}%`}
    </span>
  );
}
```

### 13.5 Page updates

- **EvaluationDetail.tsx**: add `<ArchetypeMatchCard />` above the three
  pillar cards.
- **TradeDetail.tsx**: add archetype badge next to conviction score; card
  below.
- **Opportunities.tsx**: add "Archetype" column + filter; "Archetype ≥ 80"
  filter chip in the toolbar.
- **TradeLibrary.tsx** (My Trades): add sortable "Archetype" column.
- **PolicyConfig.tsx**: new read-only section listing the archetypes with
  their condition detail (editor is future work).

### 13.6 Acceptance

- [ ] `npm run build` green
- [ ] `npm run lint` clean
- [ ] `npm test` green
- [ ] Historical v3/v4.0/v4.0.1 evaluation renders — card shows "No
  specific archetype matched"
- [ ] New v4.1.0 evaluation renders with full archetype card + per-
  archetype fits table
- [ ] Anti-archetype-triggered evaluation shows the red warning card
- [ ] Opportunities filter "Archetype ≥ 80" works
- [ ] Commit: `feat(frontend-v4.1.0): archetype + anti-archetype rendering`

### 13.7 Rollback

Frontend revert. Backend continues to emit archetype data for other
consumers.

---

## 14. Phase 7 — Build + Seed Policy v4.1.0

**Estimated time:** 0.5 day.
**Files touched:** `backend/scripts/build_policy_v4_1_0.py` (new),
`backend/scripts/seed_policy_v4_1_0.py` (new), output JSON.

### 14.1 New script — `backend/scripts/build_policy_v4_1_0.py`

Clone structure from `build_policy_v4_1.py`. Changes:
- `VERSION = "v4.1.0"`
- `composite_formula="weighted_max"` (not geometric mean)
- Add `archetypes=default_archetypes()`
- Add `anti_archetypes=default_anti_archetypes()`
- Retain v4.0.1 scanner weights (no change)
- Retain v4.0.1 global pillar weights

### 14.2 New seed script — `backend/scripts/seed_policy_v4_1_0.py`

Clone from `seed_policy_v4_1.py`. Changes:
- `TARGET_VERSION = "v4.1.0"`
- Validation: `config.pillars.composite_formula == "weighted_max"`
- Validation: `config.archetypes is not None and len(config.archetypes.archetypes) >= 6`
- Validation: `config.anti_archetypes is not None and len(config.anti_archetypes.anti_archetypes) >= 3`

### 14.3 Build + seed

```bash
cd backend
python3 scripts/build_policy_v4_1_0.py
AWS_REGION=us-west-1 DYNAMODB_TABLE_PREFIX=oss-dev \
  PYTHONPATH=. python3 scripts/seed_policy_v4_1_0.py
```

### 14.4 Acceptance

- [ ] `scripts/output/v4_1_0_policy.json` exists and round-trips through
  `PolicyConfig.model_validate`
- [ ] Policy v4.1.0 exists in DynamoDB, `is_active=false`
- [ ] `curl .../api/policies/v4.1.0` returns expected structure
- [ ] `composite_formula = "weighted_max"`
- [ ] `archetypes.archetypes` has 6 entries
- [ ] `anti_archetypes.anti_archetypes` has 3 entries
- [ ] Active policy is still v4.0.1 (no live change)
- [ ] Commit: `feat(policy-v4.1.0): build + seed script`

### 14.5 Rollback

Delete the draft policy row:

```bash
AWS_REGION=us-west-1 aws dynamodb delete-item \
  --table-name oss-dev-policies \
  --key '{"PK":{"S":"POLICY"},"SK":{"S":"v4.1.0"}}'
```

---

## 15. Phase 8 — Pre-Deploy Verification

**Estimated time:** 30 min.

### 15.1 Backend full check

```bash
cd backend
pytest tests/ --tb=short -q --no-cov     # target: 2,340+ pass
ruff check app/ scripts/                  # clean
mypy app/                                 # clean
```

### 15.2 Frontend full check

```bash
cd frontend
npm run build
npm run lint
npm test
```

### 15.3 Local smoke — active policy is still v4.0.1

```bash
cd frontend && npm run dev &
cd backend && uvicorn app.main:app --reload --port 8001 &
```

Visit:
- `/` — Opportunities loads
- `/evaluation/:ticker/:id` for a v4.0.1 evaluation — 3 pillar cards
  render; no Archetype card (active policy has no archetypes)
- `/policies` — v4.0.1 is active, v4.1.0 exists as draft
- My Trades page renders v4.0.1 positions

### 15.4 Acceptance

- [ ] All backend + frontend checks green
- [ ] Local visual smoke on v4.0.1 identical to pre-change
- [ ] No console errors

---

## 16. Phase 9 — Deploy + Activate

**Estimated time:** 30 min + 30 min monitoring.

### 16.1 Push branch

```bash
git push origin HEAD
```

### 16.2 Deploy backend

```bash
cd backend
./scripts/deploy.sh backend
# Record Lambda version N
```

### 16.3 Post-deploy checks (still on v4.0.1)

```bash
curl -s "https://2nv5mt4d1k.execute-api.us-west-1.amazonaws.com/health"
AWS_REGION=us-west-1 aws logs filter-log-events \
  --log-group-name "/aws/lambda/oss-dev-backend" --filter-pattern "ERROR" \
  --start-time $(python3 -c "import time; print(int((time.time()-300)*1000))") \
  --limit 10 --query 'events[*].message' --output text
```

### 16.4 Observe one pipeline run on v4.0.1

Wait 15 min, then query the pipeline monitor API. Expect clean run.

### 16.5 Deploy frontend

```bash
./scripts/deploy.sh frontend
```

### 16.6 Activate v4.1.0

```bash
curl -sX POST "https://2nv5mt4d1k.execute-api.us-west-1.amazonaws.com/api/policies/v4.1.0/activate" \
  | python3 -m json.tool
```

### 16.7 Post-activation monitoring (30 min)

After next pipeline run, verify:

```bash
# Top 5 approvals — should have archetype_matched or None; never crash
curl -s "https://2nv5mt4d1k.execute-api.us-west-1.amazonaws.com/api/evaluations/approve?limit=5" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); \
    [print(e.get('ticker'), e.get('conviction_score'), e.get('archetype_matched'), e.get('archetype_match_score')) for e in d.get('evaluations', [])]"
```

Check:
- `archetype_matched` is a string or None (not crash)
- `archetype_match_score` is a number or None
- `anti_archetype_triggered` is None on APPROVEs (would be REJECT otherwise)
- Pipeline Monitor: 8 stages green
- Evaluation detail for a new evaluation renders the Archetype card

### 16.8 Acceptance

- [ ] Lambda v4.1.0 deployed
- [ ] Health 200
- [ ] No ERROR logs post-deploy or post-activation
- [ ] v4.1.0 activated successfully
- [ ] First post-activation run: evaluations carry archetype fields
- [ ] Frontend displays archetype card on new evaluation

---

## 17. Phase 10 — Re-Rescore Paper Positions

**Goal:** Rescore all 20,562 paper positions against v4.1.0 so analysis
compares apples-to-apples.
**Estimated time:** 3 hours unattended + 10 min verification.

### 17.1 Extend rescore script — `backend/scripts/rescore_all_positions_v4.py`

After pillar computation, compute archetype matcher + anti-archetype check
and write them into the position row. Specifically:

```python
# After compute_final_score_from_results:
if pillar_config_archetypes is not None:
    pillar_scores = {r.pillar_id.value: r.score for r in results}
    am_result = compute_archetype_match(
        ctx, pillar_config_archetypes, pillar_scores=pillar_scores,
    )
    new_scores["archetype_matched"] = am_result.best.archetype_id if am_result.best else None
    new_scores["archetype_match_score"] = (
        round(am_result.best_match_score, 2) if am_result.best_match_score else None
    )
    new_scores["archetype_all_fits"] = am_result.all_fits

if pillar_config_anti_archetypes is not None:
    aa_result = check_anti_archetypes(
        ctx, pillar_config_anti_archetypes, pillar_scores=pillar_scores,
    )
    new_scores["anti_archetype_triggered"] = (
        aa_result.anti_archetype_id if aa_result.triggered else None
    )
```

Extend `update_position_v4` to SET these four new fields (nullable).

**Important**: the rescore preserves historical realized P&L unchanged — it
only updates scoring fields. Paper position identity (PK/SK, entry_date,
exit_date, current_pnl_pct) is never touched.

### 17.2 Run rescore

```bash
cd backend
AWS_REGION=us-west-1 DYNAMODB_TABLE_PREFIX=oss-dev \
  python3 -u scripts/rescore_all_positions_v4.py > /tmp/rescore_v410.log 2>&1 &
```

### 17.3 Monitor

Use the Monitor tool: watch for progress and error events.

### 17.4 Verify post-rescore — `verify_rescore_v4.py` (extend to cover new fields)

```bash
AWS_REGION=us-west-1 DYNAMODB_TABLE_PREFIX=oss-dev \
  python3 scripts/verify_rescore_v4.py | tail -50
```

Expected:
- 100% coverage on DC/MP/TS/composite
- `archetype_all_fits` populated on ~100% (every trade gets fit scores
  even if no archetype matches)
- `archetype_matched` populated on ~5-15% of positions (only when a match
  exists)
- `anti_archetype_triggered` populated on ~20% of positions (3 anti-
  archetypes together should reject ~4,800 of 20,562)

### 17.5 Acceptance

- [ ] Rescore completes with 0 errors
- [ ] `archetype_all_fits` populated on 20,562 positions
- [ ] Expected-proportion of trades have `archetype_matched` and
  `anti_archetype_triggered` populated
- [ ] Backup JSON safely written
- [ ] No regression on v4.0.1-era fields (`pillar_directional_conviction`,
  etc. — those remain from Phase D of v4.0.1)

### 17.6 Rollback

Restore from backup JSON via `restore_position_scores.py`.

---

## 18. Phase 11 — Performance Verification + Go/No-Go

**Goal:** Explicit scorecard against v4.0.1 and v3. Go/No-Go decision gate.
**Estimated time:** 1 hour.

### 18.1 Standard analysis

```bash
AWS_REGION=us-west-1 DYNAMODB_TABLE_PREFIX=oss-dev \
  python3 backend/scripts/analyze_v4_vs_v3_performance.py --out /tmp/v4_1_0_analysis.md
```

### 18.2 Home-run diagnosis

```bash
AWS_REGION=us-west-1 DYNAMODB_TABLE_PREFIX=oss-dev \
  python3 backend/scripts/home_run_diagnosis.py > /tmp/v4_1_0_hr_diagnosis.txt
```

### 18.3 Archetype-specific verification (new diagnostic)

Write `backend/scripts/verify_archetype_performance.py`:
- For each archetype: count matched positions, compute their mean P&L,
  win rate, HR200 rate. Compare to historical reference in the archetype
  metadata.
- For each anti-archetype: count triggered positions, verify ~expected
  historical-n (e.g., BREAKOUT_MP_ELITE should trigger on ~320 positions).
- Tier distribution with archetype path enabled: how many additional TIER_1/
  TIER_2 come in via archetype score vs composite.

### 18.4 Go/No-Go scorecard

| # | Metric | v4.0.1 baseline | v4.1.0 target | Weight |
|---|---|---|---|---|
| 1 | Top-5% P&L capture (max(comp, archetype)) | +79% | ≥ +250% | high |
| 2 | Top-5% catches ≥200% HRs | 3/221 | ≥ 30/201 | **critical** |
| 3 | Top-10% catches ≥100% HRs | 85/1465 | ≥ 250/1465 | high |
| 4 | BREAKOUT×MP_ELITE trigger count | 321 (still entering) | = 321 (now REJECT) | **critical** |
| 5 | Archetype A matched count ≥ min_fit | n/a | ≥ 100 | medium |
| 6 | Archetype A matched mean P&L | n/a | ≥ +50% | medium |
| 7 | Anti-archetype-triggered mean P&L | n/a | ≤ −20% | medium |

**Decision rule:**
- Critical items (2 and 4) MUST both pass
- Of the remaining 5, **4+ must pass** → ship v4.1.0, proceed to Phase 12
- 2-3 pass → surgical tune + re-run scorecard
- < 2 pass → rollback to v4.0.1

### 18.5 Spot-check

Manually inspect 10 archetype A matches from the rescored set:
- Did they actually earn ≥ 100% P&L? (Archetype A target is 84% mean P&L
  historical; current matches should cluster there.)
- Did the historical rescore correctly identify them ex-post?

Manually inspect 10 BREAKOUT_MP_ELITE anti-archetype triggers:
- Do they actually have BREAKOUT scanner + MP ≥ 75?
- What was their realized P&L?

### 18.6 Acceptance

- [ ] Analysis files written
- [ ] Scorecard complete
- [ ] Decision recorded in decision log
- [ ] If Go: proceed to Phase 12
- [ ] If No-Go: activate v4.0.1 immediately, document

---

## 19. Phase 12 — Baseline + Merge to Main

**Estimated time:** 30 min.
Only applies if Phase 11 was Go.

### 19.1 Export active policy

```bash
curl -s "https://2nv5mt4d1k.execute-api.us-west-1.amazonaws.com/api/policies/active" \
  | python3 -m json.tool > baselines/$(date +%Y-%m-%d)-v4.1.0-policy.json
```

### 19.2 Baseline README

Create `baselines/YYYY-MM-DD-v4.1.0-README.md`:
- Version / policy hash / Lambda version / git commit
- Full scorecard outcomes
- Key design changes: ADX inverted-U, weighted-max composite, archetype
  matcher, anti-archetype gates
- Restore instructions

### 19.3 Commit + tag

```bash
git add baselines/
git commit -m "baseline: pipeline-stable-v4.1.0-YYYY-MM-DD"
git tag pipeline-stable-v4.1.0-YYYY-MM-DD
git push origin HEAD --tags
```

### 19.4 Merge to main

```bash
cd /Users/nicksmith/OSS
git checkout main
git pull origin main
git merge claude/v4-1-0-archetype-scoring --no-edit
git push origin main
```

### 19.5 Delete branch

```bash
git push origin --delete claude/v4-1-0-archetype-scoring
```

### 19.6 Acceptance

- [ ] Tag pushed
- [ ] Policy JSON + README committed
- [ ] Main up to date
- [ ] Remote branch deleted
- [ ] CI green on main

---

## 20. Rollback Plans

### Fast (<1 min) — policy reactivation

```bash
curl -sX POST ".../api/policies/v4.0.1/activate"
```

### Medium — Lambda rollback

```bash
./scripts/deploy.sh rollback N-1
```

### Slow — tag revert

```bash
git checkout pipeline-stable-v4.0.1-2026-04-18 -- backend/ frontend/
./scripts/deploy.sh backend
./scripts/deploy.sh frontend
# Plus reactivate v4.0.1 policy
```

### Data rollback — paper positions

The rescore overwrites `conviction_score` and the four new archetype
fields. `conviction_score_v3` (pre-v4.0.0 archive) is intact.

```bash
python3 backend/scripts/restore_position_scores.py \
  --backup backend/scripts/output/position_scores_backup_v4_YYYYMMDDT...Z.json
```

### Irreversible — none

---

## 21. Risk Register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Anti-archetype gate rejects too many current APPROVEs on live pipeline | Medium | Med | Conservative thresholds; Phase 9 watches first run; rollback ready |
| Archetype matcher fires on no live trades (FVT gaps on ATR%/IVRV on live) | Low | Med | Phase 9 explicit check: first 10 approvals show archetype fields populated |
| Archetype feather values produce unintuitive fits | Med | Low | Unit tests cover feather edge cases; UI exposes individual condition fits |
| `archetype_all_fits` dict-typed column is hard to query downstream | Low | Low | Denormalize `archetype_matched` + `archetype_match_score` as simple scalars for queries |
| User loses trust if archetype match doesn't correlate with P&L on live data | Med | High | Phase 11 explicit scorecard before baseline; keep v4.0.1 rollback ready |
| Weighted-max composite + archetype path combined produces too many TIER_1s | Med | Low | Thresholds configurable; adjust via Policy page |
| Historical evaluations fail to render post-deploy | Low | High | All Optional fields; Phase 8 explicitly verifies |
| ADX curve change breaks existing DC tests | Expected | Low | Update in Phase 1.3 |
| Schema validators reject v3 policies | Low | High | Literal extended, never narrowed |
| Anti-archetype fires before pillars are fully computed → missing MP score | Low | Med | Compute pillars FIRST; anti-archetype check uses pillar_scores dict |
| `in_values` operator in ArchetypeCondition has subtle pydantic issue | Low | Low | Explicit unit tests + validator |
| Frontend badges flood the UI with noise on BREAKOUT evaluations (most rejected) | Low | Low | UI decision: only show badge when matched; anti-archetype-rejected shows red warning not a badge |
| `rs_contrarian` derived feature misfires on edge cases (rs_20d=0 exactly) | Low | Low | Treat 0 as "not contrarian" (both directions); test explicitly |

---

## 22. Test Strategy

### 22.1 Unit tests (required before each commit)

- Phase 1: ADX inverted-U curve
- Phase 2: weighted_max composite
- Phase 3: archetype matcher, condition evaluation, feather, rs_contrarian
- Phase 4: anti-archetype gates, pillar-score-based conditions
- Phase 5: dual-axis tier assignment

### 22.2 Integration tests

- End-to-end: v4.1.0 policy → evaluation with archetype fields populated
- End-to-end: v4.0.1 policy → evaluation with archetype fields = None
- Anti-archetype triggered → REJECT short-circuit
- Historical Decision schema deserialization (no regression)

### 22.3 Regression tests

All 2,293 existing tests continue to pass. Post-v4.1.0 target: 2,360+
tests pass.

### 22.4 Visual / manual (Phase 8, 9)

- Historical v3/v4.0/v4.0.1 eval → renders, no archetype card
- New v4.1.0 eval with archetype match → full card + fit breakdown
- Anti-archetype-triggered eval → red warning card
- Opportunities filter "Archetype ≥ 80" returns matches
- Policy page shows v4.1.0 with archetypes + anti-archetypes listed

### 22.5 Production smoke

- First 3 pipeline runs post-activation: no ERROR logs
- Tier distribution stable: 2-8 TIER_1, 10-25 TIER_2, 20-60 TIER_3 per run
- Anti-archetype-triggered count per-run ≤ 50 (well under total eval count)
- At least 1 archetype match per pipeline run

---

## 23. Frontend Touchpoints Inventory

**Tier 1 (must update):**
- `frontend/src/lib/types.ts` — extend Decision + PaperPosition
- `frontend/src/lib/archetypeMeta.ts` — new
- `frontend/src/components/ArchetypeMatchCard.tsx` — new
- `frontend/src/components/ArchetypeBadge.tsx` — new
- `frontend/src/pages/EvaluationDetail.tsx` — add card
- `frontend/src/pages/TradeDetail.tsx` — add badge + card
- `frontend/src/pages/Opportunities.tsx` — column + filter
- `frontend/src/components/paper-trading/TradeLibrary.tsx` — column

**Tier 2 (nice-to-have):**
- `frontend/src/components/paper-trading/PositionTracker.tsx` — badge in row
- `frontend/src/pages/PolicyConfig.tsx` — read-only archetype + anti-
  archetype listing

**Tier 3 (tests):**
- Extend existing test mocks to include archetype fields

---

## 24. Context for Executing Claude Session

### 24.1 Read in this order

1. This document in full.
2. [CLAUDE.md](../CLAUDE.md) — deployment protocol, non-negotiables.
3. [docs/pillar_v4_execution_plan.md](pillar_v4_execution_plan.md) — v4.0.0
   predecessor plan (context only, v4.0.0 is live).
4. [baselines/2026-04-18-v4.0.1-README.md](../baselines/2026-04-18-v4.0.1-README.md)
   — current state.
5. [backend/scripts/output/home_run_archetypes_findings.md](../backend/scripts/output/home_run_archetypes_findings.md)
   — the analysis that motivates every archetype definition. If this
   file is stale, re-generate before starting (see Phase 7.4 in the v4.0.0
   plan and the `home_run_archetypes_v3.py` script).

### 24.2 Operational rules

- Branch `claude/v4-1-0-archetype-scoring` from
  `pipeline-stable-v4.0.1-2026-04-18`.
- State each phase's objective before writing code. Get a green light
  before proceeding. Do not batch phases.
- `pytest`, `ruff`, `mypy` before every commit. `npm run build`, `npm
  run lint` before frontend commits.
- Never skip the deploy protocol in CLAUDE.md §"Deployment Protocol".
- Deploy after each logical change — do not batch.
- Merge to main after Phase 12. Delete the branch.
- Never delete the rescore backup JSONs in `backend/scripts/output/`.

### 24.3 Questions to answer before starting

1. Is v4.0.1 still the live active policy? (Phase 0 check)
2. Is the archetype analysis fresh? If stale, regenerate before starting
   — the defaults in Phase 3 and 4 are data-derived.
3. Is the paper-trade dataset still ~20k positions? If dramatically
   different, re-verify Section 2 findings hold before encoding archetypes.
4. Are Nick's expectations clear on Phase 11 go/no-go? Confirm the
   scorecard and decision rule before committing to a plan of record.

### 24.4 What to do if something is unclear

- Re-read the relevant plan section — most details are explicit.
- If the plan is contradicted by observed state, STOP and ask Nick.
- If a phase's acceptance criteria can't be met, STOP and ask.

### 24.5 Deliverables summary at end of Phase 12

**Backend code**
- `backend/app/pillars/directional_conviction.py` — modified
- `backend/app/pillars/composite.py` — modified (new function)
- `backend/app/archetypes/__init__.py` — new
- `backend/app/archetypes/matcher.py` — new
- `backend/app/archetypes/gates.py` — new
- `backend/app/archetypes/defaults.py` — new
- `backend/app/core/schemas.py` — extended
- `backend/app/decision/calculator.py` — extended
- `backend/scripts/build_policy_v4_1_0.py` — new
- `backend/scripts/seed_policy_v4_1_0.py` — new
- `backend/scripts/rescore_all_positions_v4.py` — modified
- `backend/scripts/verify_archetype_performance.py` — new

**Frontend code**
- `frontend/src/lib/types.ts` — modified
- `frontend/src/lib/archetypeMeta.ts` — new
- `frontend/src/components/ArchetypeMatchCard.tsx` — new
- `frontend/src/components/ArchetypeBadge.tsx` — new
- Multiple pages modified (see §23)

**Tests**
- `backend/tests/test_directional_conviction.py` — extended
- `backend/tests/test_composite_weighted_max.py` — new
- `backend/tests/test_archetype_matcher.py` — new
- `backend/tests/test_anti_archetype_gates.py` — new
- Multiple existing tests updated for new tier-assignment logic

**Policy**
- `backend/scripts/output/v4_1_0_policy.json` — written
- v4.1.0 row in `oss-dev-policies`; active after Phase 9

**Baselines**
- `baselines/YYYY-MM-DD-v4.1.0-policy.json`
- `baselines/YYYY-MM-DD-v4.1.0-README.md`
- Tag `pipeline-stable-v4.1.0-YYYY-MM-DD`

**Performance artifacts**
- `/tmp/v4_1_0_BASELINE_v401.md`
- `/tmp/v4_1_0_analysis.md`
- `/tmp/v4_1_0_hr_diagnosis.txt`
- Scorecard document (commit to baselines/ or paste into README)

---

**End of Plan. Ready for execution.**

**Total estimated elapsed time:** 10-13 working days (Phase 10 rescore is
3 hours unattended). Most of the total is Phase 3 (archetype matcher — 3
days) and Phase 6 (frontend — 1.5 days). Careful testing throughout.

**Most-important principle:** the six archetypes and three anti-archetypes
are not theoretical — they are measured patterns from
`home_run_archetypes_findings.md` on 18,567 real closed paper trades. Every
condition, threshold, and feather value in the defaults is directly
traceable to that analysis. If a threshold feels wrong, check the analysis
before changing it. If the data has shifted since 2026-04-18, regenerate
the analysis AND the defaults together — they move as a unit.
