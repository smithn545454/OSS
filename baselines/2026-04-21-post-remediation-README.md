# Baseline: Post-Remediation 2026-04-21

Captured after executing Phases 1–5 of the signal-quality remediation plan
(`audits/2026-04-20-signal-quality-remediation-plan.md`).

## Identifiers

- **Policy version:** v4.1.1 (unchanged from v5 cutover — remediation was
  code-only, no policy changes)
- **Git tag:** `pipeline-stable-2026-04-21-post-remediation`
- **Lambda version:** 267
- **Head commit:** `85e5a3f` — fix(v5): preserve v5 + archetype fields
  through concentration warnings (audit C1)

## Phase-by-phase deployment log

| Phase | Audit ID | Lambda | Commit | Summary |
|---|---|---|---|---|
| 1 | C3 | v258 | `6087005` | Polygon + selection drop counters on Stage 1/3 metadata |
| 2 | C4 | v259 | `b833f99` | Weekly calibration rate-lookup loop + CalibrationRatesTable |
| 3 | C2 | v260–261 | `719870b`, `1d0a1b4` | BREAKDOWN/REVALIDATION labels + trigger_counts merge |
| 4 | C5 | v262–263 | `970041f`, `97908db` | REVALIDATION → Re-evaluation + originating_scanner + scanner_type str hotfix |
| 5a | C1 | v265 | `2c4305e` | v5 envelope instrumentation (diagnostic only) |
| 5b | C1 | v267 | `85e5a3f` | **Root cause fix** — concentration warnings preserve all Decision fields |

## Root cause (Phase 5)

`update_decisions_with_warnings` in `app/decision/concentration.py` was
hand-rolling a new `Decision` with only 14 fields. It silently dropped
every v5 dual-conviction field (`v5_scoring_version`, `hr_conviction`,
`p_conviction`, Wilson bounds, regime_alignment, GBM scores) and every
v4.1.0 archetype field (`archetype_matched`, `archetype_match_score`,
`archetype_all_fits`, `anti_archetype_triggered`) on any evaluation that
received a concentration warning.

CHEAP_OPTIONS hits concentration warnings near-100% of the time (same
ticker produces many cheap contracts), so its v5 data always got wiped.
UNUSUAL_VOLUME hit warnings less often — which is exactly why the
pre-remediation audit saw ~15% UV retention vs 0% CHEAP retention.

Fix: use Pydantic `model_copy(update={"concentration_warnings": ...})`
so every field survives.

## Observability additions (persistent)

- `StageEvent.metadata.polygon_drops` on Stage 1 + Stage 3 (Phase 1)
- `StageEvent.metadata.selector_drops`, `evaluation_builder_drops` on Stage 3 (Phase 1)
- `StageEvent.metadata.trigger_counts` on Stage 1 (Phase 3)
- Warning log when a scanner produces triggers but isn't in
  `v5_active_scanners` (Phase 3)
- `CalibrationRatesTable` at PK=`CALIBRATION#RATES` — `LATEST` row + versioned
  snapshots, populated by EventBridge rule `oss-dev-calibration-weekly`
  (Mon 07:00 UTC) (Phase 2)
- `scanner_metrics.originating_scanner` on every REVALIDATION opportunity (Phase 4)

## Restore / rollback

- **Code:** `git checkout pipeline-stable-2026-04-21-post-remediation` +
  `./scripts/deploy.sh backend`
- **Policy:** unchanged from v5 cutover; restore via
  `baselines/2026-04-19-v5-cutover-policy.json` if ever corrupted
- **Lambda rollback:** `./scripts/deploy.sh rollback N` (any version ≤ 267)

## Verification metrics at baseline capture

- Health: `healthy`
- Pipeline run frequency: every 10 min, 13:00–21:00 UTC Mon–Fri
- Full test suite: 2529 passed
- Weekly calibration bootstrap: LATEST row present with 12 HR + 10 P
  archetypes (all n=0 — v5 archetype fields only started being written on
  2026-04-20 cutover, so pre-v5 closed positions don't match; rates will
  populate with n>0 data within 1-2 weeks of v5-era trades closing)

## Known follow-ups (explicit non-goals, see plan § "What we are NOT doing")

- BREAKOUT / COMPRESSION negative P&L archetype library (audit D2)
- 0% CHEAP archetype match rate (audit D4) — requires Phase 2 calibration data
- TIME_EXIT 35–43% win rate (audit D9)
- SPY/VIX regime alignment wiring
- Removal of dead GateConfig fields (`combined_score_min`, etc.)
