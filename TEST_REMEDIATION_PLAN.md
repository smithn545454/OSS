# Test Remediation Plan

**Created:** 2026-02-13
**Status:** 183 test failures + 7 CI-ignored files that can't import
**Passing:** 1,345 tests pass
**Goal:** Green test suite, remove CI ignore list

---

## Phase 1: Quick Wins (test-only changes, ~100 tests fixed)

These require NO production code changes. All fixes are in test files.

### 1A. Fix gate count assertions (19 → 9)

The gate system was consolidated from 19 fine-grained gates to 9 focused gates.
Tests still assert the old count. The 9 actual gates in `ALL_GATES` (`app/gates/gates.py:451-461`):

```
GATE_MIN_OPEN_INTEREST, GATE_MIN_VOLUME, GATE_MAX_SPREAD_PCT,
GATE_DTE_RANGE, GATE_MOVE_SUFFICIENCY, GATE_IV_PERCENTILE_MAX,
GATE_BREAKOUT_VOLUME, GATE_GREEKS_COHERENCE, GATE_THETA_BURDEN_MAX
```

**Files to fix:**

| File | Line | Current | Change to |
|------|------|---------|-----------|
| `tests/test_invariants.py` | 239 | `assert len(ALL_GATES) == 19` | `assert len(ALL_GATES) == 9` |
| `tests/test_stage_gates.py` | 99 | `assert len(ge.gate_results) == 19` | `assert len(ge.gate_results) == 9` |
| `tests/test_fault_injection.py` | 176 | `assert len(gate_eval.gate_results) == 19` | `assert len(gate_eval.gate_results) == 9` |

Also in `tests/test_invariants.py`, review `test_gate_error_defaults_to_fail` (line 241) and
`test_greeks_gate_catches_violations` (line 465) — these may reference GateContext fields
(`combined_score`) that no longer exist on the dataclass. Check after fixing the count assertion.

**Tests fixed: ~5**

---

### 1B. Delete boundary tests for nonexistent gate functions

`tests/test_boundary_values.py` tests 7 gate functions that were removed during the gate
consolidation. These functions do not exist anywhere in the codebase.

**Delete these test methods entirely:**

| Line | Method | References |
|------|--------|------------|
| 224 | `test_iv_rv_ratio_boundary` | `check_iv_rv_ratio` (doesn't exist) |
| 238 | `test_expected_move_boundary` | `check_expected_move` (doesn't exist) |
| 252 | `test_delta_range_boundary` | `check_delta_range` (doesn't exist) |
| 266 | `test_max_loss_boundary` | `check_max_loss` (doesn't exist) |
| 279 | `test_combined_score_boundary` | `check_combined_score` (doesn't exist) |
| 292 | `test_pillar_minimum_boundary` | `check_pillar_minimum` (doesn't exist) |
| 306 | `test_confidence_interval_boundary` | `check_confidence_interval` (doesn't exist) |

Also remove the corresponding imports at the top of the file and any parametrize
decorators for these methods.

The remaining tests in the file (`test_min_open_interest_boundary`, `test_min_volume_boundary`,
`test_max_spread_pct_boundary`, `test_dte_range_boundary`, `test_move_sufficiency_boundary`,
`test_iv_percentile_max_boundary`, `test_theta_burden_boundary`) should still pass — verify.

**Tests fixed: ~80**

---

### 1C. Update schema default assertions

Gate thresholds were tuned on Feb 12 but test assertions still have old values.

**File:** `tests/test_schemas.py`

**test_gate_config_defaults (lines 181-200):**

Check each assertion against the actual defaults in `app/core/schemas.py` (search for
`GateConfig` class). The following are known mismatches — update to match production:

| Line | Field | Test expects | Verify actual default in schemas.py |
|------|-------|-------------|-------------------------------------|
| 190 | `breakout_volume_min` | `1.0` | Check `GateConfig.breakout_volume_min` default |
| 198 | `combined_score_min` | `60.0` | Check `GateConfig.combined_score_min` default |
| 199 | `pillar_minimum` | `45.0` | Check `GateConfig.pillar_minimum` default |
| 200 | `pillar_spread_max` | `40.0` | Check `GateConfig.pillar_spread_max` default |

Read the actual `GateConfig` defaults in `app/core/schemas.py` and update all assertions
to match. Some fields may have been added/removed entirely during the gate refactor.

**test_contract_selection_defaults (lines 227-238):**
Read `ContractSelectionConfig` in schemas.py and update assertions.

**test_thesis_config_defaults (lines 263-269):**
Read `ThesisConfig` in schemas.py and update assertions.

**Tests fixed: ~3**

---

### 1D. Fix history loader test data format

`tests/test_history_loader.py` — `_valid_row()` (line 141) returns a **list**, but
`parse_option_chain_row()` in production (`app/features/history_loader.py`) expects a
**dict** (csv.DictReader format with string keys like `"Trade Date"`, `"Strike"`, etc.).

**What to do:**
1. Read `parse_option_chain_row()` in `app/features/history_loader.py` to get the expected dict keys
2. Rewrite `_valid_row()` to return a dict with those keys
3. Update all tests that use `_valid_row()` accordingly
4. Also fix `TestBuildOptionTicker` — tests expect no `O:` prefix but production prepends it
5. Fix `TestExtractTicker.test_monthly` — check what filename patterns production supports
6. Fix `TestFindFiles.test_find_files` — check expected file glob patterns

**Tests fixed: ~10**

---

### 1E. Fix scanner route mock targets

`tests/test_scanners_route.py` patches attributes that don't exist on `app.api.routes.scanners`:

| Line | Patches | Problem |
|------|---------|---------|
| 52, 62 | `app.api.routes.scanners.ScanStatusTable` | Not imported in that module |
| 209, 221 | `app.api.routes.scanners.UVCandidateTable` | Not imported in that module |
| 172, 189 | `app.api.routes.scanners.UV_PUBLISHER_FUNCTION_NAME` | Not a module-level variable |

**What to do:**
1. Read `app/api/routes/scanners.py` to find where these tables/variables are actually used
2. Update patch targets to the correct module paths (likely `app.db.tables.ScanStatusTable`, etc.)
3. If the tables don't exist at all (see Phase 3), these tests need to wait for Phase 3
4. Same issue in `tests/test_routes_scanners.py` (lines 49, 57) — fix patch targets

**Tests fixed: ~8 (if tables exist) or 0 (if blocked by Phase 3)**

---

### 1F. Fix underlying filter test fixture

`tests/test_underlying_filter.py` — `sample_bars` fixture (line 56-71) generates 30 bars
with dates `2026-01-01` through `2026-01-30`. But many of these are weekends, so the filter
sees only ~17 trading days and flags 4+ missing bars, exceeding the max of 2.

**What to do:**
1. Read `_check_data_completeness()` in `app/filters/underlying.py` to understand exact logic
2. Either: generate bars only on weekdays (skip Sat/Sun), or increase bar count to ensure
   enough valid trading days

**Tests fixed: ~2**

---

### 1G. Un-ignore test_pipeline_route.py

`tests/test_pipeline_route.py` (line 10) imports `_parse_scanner_type` which was renamed.

**Fix:** Change import from `_parse_scanner_type` to the current function name.
Check `app/api/routes/pipeline.py` — `parse_time_range` exists (line 34). Determine if
`_parse_scanner_type` was folded into the `scanner` query param or is a separate helper.
Update test imports and calls accordingly.

Then remove `--ignore=tests/test_pipeline_route.py` from CI and deploy script.

**Tests fixed: unknown (need to check how many tests are in the file)**

---

## Phase 2: Production Bug Fixes (~5 tests fixed)

Small fixes to production code that fix real bugs.

### 2A. Fix broken f-strings in LLM prompt builder

**File:** `app/llm/prompt.py`

Lines 60-63 and 78-82 have f-string ternary expressions written incorrectly.

**Current (broken):**
```python
f"- 5-Day Return: {underlying['return_5d']:.1f}% if underlying['return_5d'] else 'N/A'"
```

**Should be:**
```python
f"- 5-Day Return: {f'{underlying[\"return_5d\"]:.1f}%' if underlying['return_5d'] else 'N/A'}"
```

Or cleaner:
```python
f"- 5-Day Return: {f'{v:.1f}%' if (v := underlying['return_5d']) else 'N/A'}"
```

**Lines to fix:** 60, 61, 62, 63, 78, 79, 80, 81, 82

This is a real production bug — trade thesis prompts are malformed when generated.

**Tests fixed: ~2 (test_llm.py)**

---

### 2B. Fix GateContext.from_evaluation_and_features signature

**File:** `app/gates/models.py` (line 70)

Current signature:
```python
def from_evaluation_and_features(cls, evaluation, feature_set=None, opportunity=None)
```

Tests in `test_gates_models.py` call it with `pillar_results=` and `pillar_weights=` kwargs.
The GateContext dataclass may also be missing `combined_score` and `pillar_scores` fields.

**What to do:**
1. Read the GateContext dataclass fields (models.py)
2. Check if `combined_score` / `pillar_scores` are fields or were removed
3. If removed: update tests to not pass these. If needed: add optional fields back
4. Add `pillar_results` and `pillar_weights` as optional kwargs if the method should
   support computing combined scores from pillars

**Tests fixed: ~2 (test_gates_models.py)**

---

### 2C. Fix HardGatesStage._persist_results run_id stamping

**File:** `app/gates/stage.py` (line 151)

`_persist_results()` takes `results: dict[str, GateEvaluation]` but tests expect it
to stamp `run_id` on each GateResult before writing to DynamoDB.

**What to do:**
1. Read `_persist_results` implementation (line 151-164)
2. Check if `run_id` is being set on gate results before `GateResultTable.put()`
3. If not, add `result.run_id = self.run_id` before persisting
4. Check that `self.run_id` is available (set during stage initialization)

**Tests fixed: ~3 (test_gates_stage.py)**

---

## Phase 3: Missing Infrastructure (~30 tests fixed)

Implement missing DynamoDB table classes and a missing endpoint.

### 3A. Implement CalibrationReportTable

**File:** `app/db/tables.py`

This table class does not exist. Tests expect these methods:
- `put(report)` — store a CalibrationReport
- `get(report_id)` — retrieve by ID
- `list_recent(limit=10)` — list recent reports
- `update_suggestion_status(report_id, suggestion_id, new_status, expected_current_status)` — update suggestion within a report

**Pattern to follow:** Copy the pattern from any existing table class (e.g., `PolicyTable`,
`PipelineRunTable`). All tables use PK/SK pattern with the `DYNAMODB_TABLE_PREFIX`.

**DynamoDB table name:** `{prefix}-calibration-reports` (check if this table exists in CDK —
if not, it needs to be added to `infrastructure/cdk/stacks/database_stack.py`)

**After implementing:** Update `app/api/routes/calibration.py` to replace the in-memory
`_reports_store` dict (line 26) with real `CalibrationReportTable` calls.

**Tests fixed: ~9 (test_db_tables.py CalibrationReport tests, test_routes_calibration.py)**

---

### 3B. Implement ScanStatusTable

**File:** `app/db/tables.py`

Tests expect:
- `put(run_id, data)` — store scan status
- `get(run_id)` — retrieve by run ID
- `list_recent(limit=10)` — list recent scans

**DynamoDB table name:** `{prefix}-scan-status` (check CDK)

**After implementing:** Import in `app/api/routes/scanners.py` so mock patch targets work.

**Tests fixed: ~6**

---

### 3C. Implement UVCandidateTable

**File:** `app/db/tables.py`

Tests expect a table for UV scanner candidate storage.

**After implementing:** Import in `app/api/routes/scanners.py`.

**Tests fixed: ~3**

---

### 3D. Add seed policy endpoint

**File:** `app/api/routes/policies.py`

Tests in `test_policies_route.py` (lines 74, 88, 103) expect `POST /api/policies/seed`.
This endpoint should create a default policy if none exists, or return the existing one.

**What to do:**
1. Read the test expectations to understand the endpoint contract
2. Add a `@router.post("/seed")` endpoint
3. Logic: check if active policy exists → if yes return it, if no create default and return

**Tests fixed: ~3**

---

### 3E. Add pandas to dev dependencies

**File:** `backend/pyproject.toml`

`tests/test_catalyst_service.py` fails because `pandas` is not installed.

**What to do:**
1. Add `pandas` to the `[project.optional-dependencies] dev` list
2. Remove `--ignore=tests/test_catalyst_service.py` from CI

**Tests fixed: unknown (depends on how many tests are in the file)**

---

## Phase 4: Complete Calibration Module (~35 tests fixed)

This is the largest phase. The calibration system was designed and tested but only
partially implemented.

### 4A. Add missing dataclasses to calibration models

**File:** `app/calibration/models.py`

Currently defines: `SuggestionStatus`, `RecommendationType`, `GateAnalysis`,
`EstimatedImpact`, `ThresholdSuggestion`, `ScoreBandAnalysis`, `CalibrationReport`

**Need to add:**
- `CounterfactualResult` — with `verdict_changes: dict`, `scenario_label: str`
- `CounterfactualSummary` — with `gate_scenarios: list`, `score_scenarios: list`
- `ScoreThresholdResult` — with `verdict_changes: dict`, `counterfactual_counts: dict`

Read test files to determine exact field structures:
- `tests/test_simulator.py` — how CounterfactualResult and ScoreThresholdResult are used
- `tests/test_calibration.py` (CI-ignored) — how CounterfactualSummary is used

**Tests fixed: enables ~35 tests (but they need the methods in 4B/4C too)**

---

### 4B. Implement missing simulator methods

**File:** `app/calibration/simulator.py`

Currently has: `__init__`, `simulate_threshold_change`, `generate_suggestions`,
`_get_threshold_value`, `_would_pass_with_threshold`

**Need to add:**
- `simulate_gate_counterfactual(gate_id, change_pct)` → returns `CounterfactualResult`
- `simulate_score_threshold(approve_threshold, watch_threshold=None)` → returns `ScoreThresholdResult`

Also update `__init__` to accept optional `eval_decisions` parameter.

Read `tests/test_simulator.py` to understand exact expected behavior:
- `TestSimulateGateCounterfactual` (4 tests) — loosening/tightening effects on verdict counts
- `TestSimulateScoreThreshold` (4 tests) — how changing approve/watch thresholds affects verdicts
- `TestHelpers` (2 tests) — `_would_pass_gte` and `_would_pass_lte` methods

**Tests fixed: ~16**

---

### 4C. Implement missing reporter methods

**File:** `app/calibration/reporter.py`

Currently has: `__init__`, `generate_report`, `_load_data`, `_calculate_summary_stats`,
`_analyze_gates`, `_generate_suggestions`, `_analyze_score_bands`

**Need to add:**
- `_build_eval_decisions()` → returns `dict[eval_id, decision_data]`
- `_analyze_watch_to_approve()` → returns object with `total_watch`, `rate`, `would_flip_count`
- `_generate_counterfactual_summary(analyses)` → returns `CounterfactualSummary`
- Initialize `self._simulator` in `__init__`

Read `tests/test_calibration_reporter.py` for exact expected behavior and return types.

**Tests fixed: ~6**

---

### 4D. Un-ignore remaining CI files

After Phases 3 and 4 are complete, update these files:

| CI-Ignored File | Blocked By | Un-ignore After |
|-----------------|------------|-----------------|
| `test_calibration.py` | Phase 4A (CounterfactualSummary) | Phase 4 |
| `test_calibration_route.py` | Phase 3A + route refactor | Phase 3 |
| `test_catalyst_service.py` | Phase 3E (pandas dep) | Phase 3 |
| `test_db_integration.py` | Phase 3A (CalibrationReportTable) | Phase 3 |
| `test_gate_calculator.py` | Phase 1B (removed gates) | Needs separate analysis — may need full rewrite |
| `test_pipeline_route.py` | Phase 1G (renamed function) | Phase 1 |
| `test_reporter.py` | Phase 4A (CounterfactualResult) | Phase 4 |

**Final step:** Remove all `--ignore=` lines from both `.github/workflows/ci.yml` (lines 42-48)
and `scripts/deploy.sh` (the `run_backend_tests` function).

---

## Verification Checklist

After each phase, run:

```bash
cd backend
python3 -m pytest tests/ --tb=short -q --no-cov \
  --ignore=tests/test_calibration.py \
  --ignore=tests/test_calibration_route.py \
  --ignore=tests/test_catalyst_service.py \
  --ignore=tests/test_db_integration.py \
  --ignore=tests/test_gate_calculator.py \
  --ignore=tests/test_pipeline_route.py \
  --ignore=tests/test_reporter.py
```

**Expected results per phase:**

| After Phase | Expected Failures | Notes |
|-------------|-------------------|-------|
| Phase 1 | ~83 → ~83 remaining | Drops from 183 to ~83 |
| Phase 2 | ~83 → ~78 remaining | Fixes 5 more |
| Phase 3 | ~78 → ~48 remaining | Fixes ~30, enables calibration |
| Phase 4 | ~48 → 0 remaining | Fixes remaining ~35 + un-ignore files |

---

## Summary

| Phase | Tests Fixed | Effort | Production Changes |
|-------|------------|--------|-------------------|
| 1 | ~100 | 2-3 hours | None (test-only) |
| 2 | ~5 | 1 hour | 3 small fixes |
| 3 | ~30 | 3-4 hours | 3 new table classes, 1 endpoint |
| 4 | ~35 | 4-6 hours | Calibration module completion |
| **Total** | **~170+** | **~12 hours** | |
