# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

OSS (Option Scanner System) — a deterministic, fully observable system that identifies single-leg long options trades. Python/FastAPI backend, React/TypeScript frontend, DynamoDB database, deployed to AWS via CDK.

## Design Philosophy

For each proposed change, examine the existing system and redesign it into the most elegant solution that would have emerged if the change had been a foundational assumption from the start. Do not bolt features onto the side — reshape the system so the new capability feels native and inevitable. Ask questions to clarify where there is uncertainty, ambiguity, or lack of direction before proceeding with implementation.

## Common Commands

### Backend (from `backend/`)
```bash
pip install -e ".[dev]"                    # Install with dev deps
uvicorn app.main:app --reload --port 8001  # Run dev server
pytest tests/ --tb=short -q                # Run all tests
pytest tests/test_gates.py -q              # Run single test file
pytest tests/test_gates.py::test_name -q   # Run single test
ruff check app/                            # Lint
ruff format app/                           # Format
mypy app/                                  # Type check
```

### Frontend (from `frontend/`)
```bash
npm install          # Install deps
npm run dev          # Dev server (port 5173, proxies /api to localhost:8001)
npm test             # Run tests (vitest)
npm run test:watch   # Watch mode
npm run lint         # ESLint
npm run build        # Production build (tsc + vite)
```

### Git / GitHub
```bash
git add -A && git commit -m "message"  # Stage and commit all changes
git push                                # Push to GitHub
gh repo view --web                      # Open repo in browser
gh pr create --title "title" --body ""  # Create a pull request
```

### Infrastructure (from `infrastructure/cdk/`)
```bash
source .venv/bin/activate
cdk synth            # Synthesize CloudFormation templates
cdk deploy oss-dev-database   # Deploy database changes only (SAFE)
cdk deploy oss-dev-frontend   # Deploy frontend infra only (SAFE)
```

**WARNING: NEVER run `cdk deploy oss-dev-backend` or `cdk deploy --all`.** CDK backend deploy replaces the Lambda code with a raw package from the worktree that lacks bundled dependencies (fastapi, etc.), breaking the backend. Always use `./scripts/deploy.sh backend` for backend deployments — it properly packages dependencies. For database-only changes (new tables, GSIs), deploy only the DatabaseStack. The Lambda's IAM policy uses wildcard `dynamodb:*` on `oss-dev-*` tables, so new tables are accessible without redeploying the backend.

### Deployment
```bash
./scripts/deploy.sh backend              # Backend deploy (runs tests first)
./scripts/deploy.sh backend --skip-tests # Backend deploy without tests (emergencies only)
./scripts/deploy.sh frontend             # Frontend deploy
./scripts/deploy.sh all                  # Full deployment (infra + backend + frontend)
./scripts/deploy.sh rollback             # Rollback to previous Lambda version
./scripts/deploy.sh rollback N           # Rollback to specific Lambda version N
./scripts/deploy.sh rollback-frontend    # Rollback frontend to previous deploy
./scripts/deploy.sh versions             # List all published Lambda versions
```

## Architecture

### Pipeline (Convex Mode — 4 stages, sequential)

The core system is the **Convex pipeline**, a four-stage gated evaluator that
identifies asymmetric long-premium "exploder" setups. It cut over to be the
sole production pipeline on 2026-04-29 (the legacy 8-stage scanner is gone).

1. **Stage 1 — Kinetic Universe** (`app/convex/stage1_universe.py`) —
   monthly construction of the eligible ticker set (options volume, market
   cap, ATM spread, tail-event count, HV regime). Persists snapshots to
   `ConvexUniverseSnapshotTable` for the daily run to consume.
2. **Stage 2 — Catalyst + Direction** (`app/convex/stage2_catalyst.py`) —
   daily detection of date-known catalysts (earnings/FDA), compression
   breakouts, sympathy moves, **and 5-day momentum**. Resolves trade
   direction from (5d momentum × UV skew) — Stage 2 PASSES only when a
   catalyst fires AND direction is non-ambiguous.
3. **Stage 3 — PL Pricing Pre-Screen** (`app/convex/stage3_volatility.py`) —
   computes a representative Premium Leverage score (using ATM-ish chain
   inputs) so the pipeline can fail fast before Stage 4. Replaced the
   legacy IV/HV envelope after walk-forward analysis showed PL is the
   strongest single signal. PASSES when `pl_pre_score ≥ pl_pre_screen_min`.
4. **Stage 4 — Contract Selection + PL Recompute**
   (`app/convex/stage4_contract.py`) — pick a specific contract within
   the tightened delta/DTE/spread envelope (Δ 0.10–0.35, DTE 7–28,
   OI ≥ 100), then recompute the PL pillar on the actual selected
   contract for tier mapping.

Tier assignment runs after Stage 4 (`app/convex/tier.py`):
- **Tier A**: PL ≥ 80 AND momentum-aligned AND UV detected (production
  UV scanner GSI confirms aligned skew).
- **Tier B**: PL ≥ 80 AND momentum-aligned.
- **Tier C**: PL ≥ 85 alone, OR PL ≥ 80 + UV detected.
- **Reject**: anything else (no Decision emitted).

Within-tier ranking uses `pl_score / 100` directly. Sizing is tier-driven
(A=50%, B=35%, C=25% of standard).

The PL pillar (`app/convex/pl_pillar.py`) is the legacy v5 Premium
Leverage formula reconstructed from `pipeline-stable-convex-cutover-2026-04-29^`
(commit 8a3dda4^). Direction-agnostic 0–100 score using IV (51.6% weight),
|delta| (27.5%), IV percentile (14.5%), IV/RV ratio (6.4%) — piecewise-
linear interpolation across Policy v3.0.0 breakpoints.

### Lambda Handler (`app/main.py`)

Single Lambda with two invocation modes:
- **API Gateway** — HTTP requests via Mangum
- **Scheduled events** — EventBridge dispatches actions:
  `convex_daily_run`, `convex_universe_refresh`, `paper_update`,
  `paper_update_worker`, `paper_trading_update`, `earnings_refresh`,
  `price_history_refresh`, `earnings_history_refresh`, `daily_data_capture`,
  `pattern_discovery_worker`, `custom_analysis_worker`, `thesis_worker`,
  `stock_summary_worker`.

### Key Modules
- `app/convex/` — the production pipeline (stages, providers, tier, daily runner, backtest harness)
- `app/core/schemas.py` — all Pydantic models (Policy, ConvexConfig, Decision, ConvexEvaluation, PaperPosition, etc.)
- `app/db/tables.py` — DynamoDB table operations (all tables use PK/SK; some with GSI1/GSI2)
- `app/config.py` — settings via pydantic-settings
- `app/services/` — external API clients (Polygon, Finnhub) and Slack alert service
- `app/llm/` — post-decision trade thesis generation (Convex-shaped prompt; LLM is never in decision logic)
- `app/paper_trading/` — position creation from Convex finalised candidates, daily updates, exit checking
- `app/observability/` — representative trace sampling
- `app/data_capture/` — daily market snapshot for backtesting

### Downstream consumer wiring

The Convex daily runner fans out to three downstream consumers after
finalization (each isolated by try/except so one failure doesn't block the
others):
1. `paper_trading.position_manager.create_position_from_convex_candidate(...)` — tier-based scanner_source, no pillar denorm.
2. `llm.generator.ThesisGenerator.generate_convex(...)` — Convex 4-stage walkthrough prompt; reuses the existing rate limiter and JSON output contract.
3. `services.slack.SlackAlertService.send_convex_alert(...)` — gated on `ConvexConfig.alerts_enabled`; tier filtering via `convex_min_tier` config (default "B" — A+B alert; C suppressed).

### Frontend
- React 18 + TypeScript + Vite + Tailwind CSS
- TanStack React Query for server state
- Path alias: `@/` → `src/`
- Canonical pages: `/opportunities` (ConvexOpportunities), `/evaluation/:ticker/:evaluationId` (ConvexEvaluationDetail)
- `/convex/*` aliases preserved one release for bookmarks
- Other pages: Paper Trading, My Trades, Intelligence, Alerts, Backtesting, Policy Config
- **Pending:** `/pipeline` and `/calibration` legacy pages still render but their backend routes are gone — they will 404 on data calls until Phase 5 (rebuild Pipeline Monitor for Convex) and Phase 6 (delete legacy frontend pages) land

### Infrastructure (4 CDK Stacks)
- **DatabaseStack** — DynamoDB tables
- **BackendStack** — Lambda + API Gateway + Secrets Manager (CDK is documentation only — never `cdk deploy oss-dev-backend`)
- **FrontendStack** — S3 + CloudFront
- **UnusualVolumeStack** — serverless fan-out for the UV scanner (writes the GSI Convex Stage 4 reads for smart-money confirmation)

## Non-Negotiable Principles

1. **Single-leg long options only** — no spreads, combos, or short positions
2. **Deterministic decisions** — same inputs + same policy → identical outputs
3. **No LLM in decision logic** — LLM only for post-decision trade thesis
4. **Hard gates dominate** — any failed gate → REJECT regardless of scores
5. **Everything is explainable** — every score emits reason codes
6. **Config over code** — all thresholds come from Policy and are editable in UI

## Testing Patterns

- Backend uses `pytest` with `asyncio_mode = "auto"` and `moto` for DynamoDB mocking
- `conftest.py` sets `DYNAMODB_TABLE_PREFIX=oss-test` and fake AWS credentials before app imports
- The `moto_dynamodb` fixture creates all tables fresh per test
- Coverage threshold: 60% (`--cov-fail-under=60`)
- Frontend uses Vitest with jsdom environment and React Testing Library

## Code Style

- Backend: Ruff (line-length=100, Python 3.12), MyPy strict mode
- Frontend: ESLint with react-hooks and react-refresh plugins
- DynamoDB tables use single-table design with `PK`/`SK` keys and optional `GSI1`/`GSI2`

## Key Implementation Details

### Evaluation Detail Page
- Route: `/evaluation/:ticker/:evaluationId` → `ConvexEvaluationDetail`
- Backend: `GET /api/convex/evaluations/{ticker}/{evaluation_id}` returns `ConvexEvaluation` with embedded `convex_stages` for the four-stage walkthrough
- Tier A expands by default; B and C collapse for scan-and-drill UX
- Error boundary (`EvaluationErrorBoundary`) wraps the page to catch render crashes gracefully

### Decision shape (`app/core/schemas.py`)
- `Decision.verdict = Verdict.CONVEX_APPROVE` for all production decisions
- Convex-specific fields: `convex_tier` (A/B/C), `convex_stages` (the four `ConvexStagePayload`s), `convex_strength_composite`, `smart_money_confirmation`, `convex_uv_signal`, `position_sizing_recommendation`
- Legacy v3/v4/v5 pillar/conviction fields remain on the schema (Optional) for historical row deserialization until the legacy `EvaluationTable` is dropped (~30 days post-cutover); new rows leave them null
- Decision is **frozen** — when populating `convex_uv_signal` after `finalise_candidate`, use `decision.model_copy(update={...})` and rebuild the `FinalisedConvexCandidate`

### Convex composite strength (`app/convex/tier.py`)
- Within-tier ranking uses `pl_score / 100` directly (PL is read off the Stage 4 `extras["pl_score"]`); falls back to Stage 4 strength when PL is missing
- `Decision.final_score = 0.0` is the sentinel — Convex doesn't compute a blended composite; tier + PL tell the story
- `PaperPosition.conviction_score` for new Convex positions = `composite_strength × 100` (legacy 0–100 scale projection so existing UI sorting keeps working)
- The new tier rule is enforced in `assign_tier(candidate, config, uv_signal)` — UV signal must be passed in for Tier A; the orchestrator looks it up via `lookup_uv_signal` once per candidate and the daily runner reuses the cached result

### Opportunities Page
- Tier filter dropdown: A / B / C / ALL
- 100 evaluations cached for 60s via React Query
- `SmartMoneyBadge` flags UV-confirmed candidates

### API Base URL
- Production API: `https://2nv5mt4d1k.execute-api.us-west-1.amazonaws.com` (no trailing slash, no `/prod` suffix)
- Frontend CloudFront: `https://d3upsbalspxt4n.cloudfront.net`

## Deployment Protocol (MANDATORY)

Claude Code MUST follow this protocol for every deployment. Do NOT skip steps, combine steps, or declare success early. The user is not an engineer — walk them through what is happening at each step and report results clearly before proceeding to the next step.

### When to Deploy

- Deploy after each logical change, not multiple changes batched together
- If something breaks, you know exactly which change caused it
- Never deploy untested code. Never deploy with failing tests

### Step 1: Pre-Deploy Checks

Run these checks BEFORE committing. If any fail, stop and fix.

```bash
# Backend: run tests and lint
cd backend && pytest tests/ --tb=short -q
cd backend && ruff check app/

# Frontend: build and lint (if frontend files changed)
cd frontend && npm run build
cd frontend && npm run lint
```

**If tests fail:** Fix the failing tests. Do NOT use --skip-tests to work around them. Tell the user which tests failed and why.

**If lint fails:** Fix the lint errors. These are code quality issues that should be resolved before deploying.

### Step 2: Commit and Push

```bash
git add <specific files that changed>    # NEVER use git add -A blindly
git commit -m "type: description"        # Use conventional commits (fix:, feat:, docs:, etc.)
git push
```

After pushing, check that GitHub Actions CI is green:
```bash
gh run list --limit 1
```

If CI fails, do NOT proceed to deploy. Fix the issue first.

### Step 3: Deploy

```bash
./scripts/deploy.sh backend    # For backend changes
./scripts/deploy.sh frontend   # For frontend changes
```

The deploy script will:
1. Run pytest automatically (aborts if tests fail)
2. Package and upload code to Lambda
3. Wait for Lambda to finish updating
4. Record the git commit hash in the Lambda description
5. Publish a numbered Lambda version (immutable snapshot for rollback)
6. Print the version number — report this to the user

### Step 4: Verify (REQUIRED — do NOT skip)

Wait 1-2 minutes after deploy, then run ALL of these checks. Do NOT declare the deploy successful until every check passes.

#### 4a. Check CloudWatch for errors
```bash
# Get logs from the last 5 minutes
AWS_REGION=us-west-1 aws logs filter-log-events \
  --log-group-name "/aws/lambda/oss-dev-backend" \
  --filter-pattern "ERROR" \
  --start-time $(python3 -c "import time; print(int((time.time()-300)*1000))") \
  --limit 20 --query 'events[*].message' --output text
```
If there are ERROR logs related to the change, the deploy has a problem. Investigate before continuing.

#### 4b. Check Pipeline Monitor API (for pipeline changes)
```bash
# Get the latest pipeline runs
curl -s "https://2nv5mt4d1k.execute-api.us-west-1.amazonaws.com/api/pipeline/runs?limit=3" | python3 -c "
import sys, json
data = json.load(sys.stdin)
runs = data.get('runs', [])
for r in runs:
    print(f\"Run {r.get('run_id', '?')[:12]}... status={r.get('status')} started={r.get('started_at', '?')[:19]}\")
"
```

**What to look for:**
- All 8 stages should show In/Out counts (not all zeros for stages 3-8)
- No false anomaly flags on fan-out stages (Stage 3: Contract Selection, Stage 8: Paper Trading)
- Run should have a proper UUID run_id (not `worker-xxx`)

#### 4c. Check health endpoint
```bash
curl -s "https://2nv5mt4d1k.execute-api.us-west-1.amazonaws.com/health" | python3 -m json.tool
```

#### 4d. Report results to user
Tell the user exactly what you found:
- Which version was published
- Whether CloudWatch shows errors
- What the Pipeline Monitor stages look like
- Whether the deploy is confirmed working or needs attention

### Step 5: Merge to Main (REQUIRED — do NOT skip)

After a successful deploy and verification, merge the working branch to `main` so production code and `main` stay in sync. This ensures future sessions start with all deployed code.

```bash
# From the main repo (not worktree), or using git commands:
git checkout main
git pull origin main
git merge origin/<branch-name> --no-edit    # Merge the deployed branch
git push origin main
```

After merging, delete the branch to keep the repo clean:
```bash
git push origin --delete <branch-name>
```

**Why this matters:** The deploy script uploads code to Lambda directly from the current branch — it does NOT merge to `main`. If you skip this step, future Claude Code sessions will start from `main` and be missing the deployed code. This has caused lost work in the past.

**If there are merge conflicts:** Resolve them carefully, keeping changes from both sides. Test after resolving to make sure nothing broke.

### Step 6: If Something Goes Wrong

If verification fails, take these steps in order:

#### Option A: Rollback Lambda (fastest, ~30 seconds)
```bash
./scripts/deploy.sh rollback
```
This reverts to the previous Lambda version. Verify again after rollback.

#### Option B: Rollback to a specific version
```bash
./scripts/deploy.sh versions          # List all versions with descriptions
./scripts/deploy.sh rollback N        # Rollback to version N
```

#### Option C: Restore known-good baseline (nuclear option)
```bash
git checkout pipeline-stable-2026-03-13 -- backend/
# Then redeploy using Step 3
# If policy also needs restoring, see baselines/2026-03-13-README.md
```

After any rollback, tell the user:
- What went wrong
- What version was rolled back to
- What the current state is (verified working or still investigating)

### Deployment Safety Rules

1. **One change at a time** — deploy after each logical change
2. **Never skip tests** — `--skip-tests` is for emergencies only, with explicit user approval
3. **Never declare success without verification** — always run Step 4
4. **Always report the Lambda version number** — the user needs this for rollback
5. **If CI is red, do not deploy** — fix the CI failure first
6. **If unsure, ask** — it's better to pause and ask the user than to deploy broken code
7. **Tag milestones** — when the pipeline is working well after a significant change, suggest tagging: `git tag pipeline-stable-YYYY-MM-DD && git push --tags`
8. **No shortcuts for "simple" changes** — follow every step regardless of how minor the change appears. Frontend-only changes still require CloudWatch and Pipeline Monitor checks.
9. **Always merge to main after deploy** — a deploy without merging leaves `main` out of sync. Future sessions start from `main`, so unmerged code is effectively invisible to them. Step 5 is mandatory.
10. **Clean up branches** — after merging, delete the remote branch. One active branch at a time keeps things simple.
11. **Never `cdk deploy` the backend stack** — `cdk deploy oss-dev-backend` replaces Lambda code with an unpackaged bundle that lacks dependencies, immediately breaking the backend. Use `cdk deploy oss-dev-database` for database changes, `./scripts/deploy.sh backend` for backend code. If you accidentally run `cdk deploy` on the backend, immediately rollback: `./scripts/deploy.sh rollback`.

### EventBridge schedule (production)

Live rule states (CDK is documentation-only; flip via `aws events enable-rule`/`disable-rule`/`put-rule`). Verify with `aws events describe-rule --name <rule>` since the CDK file may diverge from production.

- `oss-dev-convex-daily-run` — ENABLED — `cron(0/15 13-21 ? * MON-FRI *)` (every 15 min during market session, Mon-Fri 13:00-21:00 UTC). Name is legacy ("daily-run") — schedule was changed to intraday on 2026-05-04 because contract optimality shifts with the underlying during the session. ~33 runs/weekday.
- `oss-dev-convex-universe-refresh` — ENABLED — monthly, 1st at 02:00 UTC
- `oss-dev-paper-trading-update` — ENABLED — daily 21:15 UTC
- `oss-dev-earnings-refresh`, `oss-dev-price-history-refresh`, `oss-dev-earnings-history-refresh`, `oss-dev-data-capture` — ENABLED — daily/weekly data jobs
- `oss-dev-scan-schedule` (legacy 8-stage scanner) — DISABLED at the 2026-04-29 cutover
- `oss-dev-calibration-weekly` — DISABLED (legacy v5 archetype rate refresh; handler removed)

## Known Issues / Watch Items

- **FinnhubClient "not initialized"** — needs async context manager usage. Earnings lookups in `services/catalyst.py` (used by Convex Stage 2 catalyst detection) catch this and return `None` so the catalyst pass fails open. Noisy in logs, non-blocking.
- **SEC EDGAR rate limiting** — `CatalystDataService.prefetch_batch()` runs 5 concurrent requests with only 0.1s delay, exceeding SEC's 10 req/s limit. Can trigger 429s; `recent_sec_filing` defaults to `False` on failure.
- **Backfill script region default** — `backfill_iv_history_dynamodb.py` uses `AWS_DEFAULT_REGION` but `app.config.Settings` reads `AWS_REGION`. Run with `AWS_REGION=us-west-1 DYNAMODB_TABLE_PREFIX=oss-dev` env vars explicitly set.
- **Legacy frontend pages still bundled** — `/pipeline` and `/calibration` routes render legacy components that call deleted backend routes (404). Phase 5 (rebuild Pipeline Monitor for Convex) and Phase 6 (delete legacy frontend pages) are deferred follow-ups.
- **Decision schema legacy fields** — v3/v4/v5 pillar/conviction fields remain on the `Decision` model (Optional, null on new rows) for historical row deserialization while `EvaluationTable` is retained ~30 days. Drop in a follow-up after the legacy table is dropped.

## Polygon API

- **Advanced Options plan** — Polygon returns native Greeks (delta, gamma, theta, vega) and IV directly on snapshot endpoints
- **IV field location**: `implied_volatility` is at the top level of each snapshot result, NOT inside `greeks`
- Convex Stage 4 contract selection consumes these directly; no Newton-Raphson IV recovery is needed in the production path

## Baselines

Baselines capture a known-good production state: code (git tag) + policy config (exported JSON). Stored in `baselines/` with restore instructions.

**Current production baseline: `pipeline-stable-convex-cutover-2026-04-29`**
- Convex 4-stage pipeline, sole production scorer
- Lambda v287 (cutover backend deploy), commit `014d615`
- `ConvexConfig.enabled=True`, `alerts_enabled=True`; Convex Slack alert filter `convex_min_tier=B` (A+B alert; C suppressed)
- Legacy 8-stage modules (`scanners/`, `filters/`, `selection/`, `features/`, `pillars/`, `gates/`, `decision/`, `v5/`, `archetypes/`, `calibration/`, `backtest/`) deleted; legacy DynamoDB tables retained ~30 days for historical browsing

**Previous baseline (legacy): `pipeline-stable-v5.0-2026-04-20`** — last legacy v5 baseline. Restore is non-trivial because legacy code is deleted; for emergency rollback use `./scripts/deploy.sh rollback` to v286 (the pre-cutover Lambda) plus re-enable `oss-dev-scan-schedule` and disable the two Convex EventBridge rules.

### Convention
- Tag code: `git tag pipeline-stable-YYYY-MM-DD && git push --tags`
- Export policy: `curl -s .../api/policies/active > baselines/YYYY-MM-DD-policy.json`
- Document: create `baselines/YYYY-MM-DD-README.md` with identifiers + metrics + restore steps
