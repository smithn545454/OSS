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

### Pipeline (8 stages, sequential)

The core system is an evaluation pipeline that processes tickers through 8 stages:

1. **Opportunity Discovery** (`app/scanners/`) — 4 scanners (Breakout, Compression, Cheap Options, Unusual Volume) identify ticker-level opportunities
2. **Underlying Quality Filters** (`app/filters/`) — Remove low-quality underlyings
3. **Contract Selection** (`app/selection/`) — Select contracts per DTE/delta bucket
4. **Feature Computation** (`app/features/`) — Calculate scoring inputs (liquidity, volatility, catalyst, etc.)
5. **Pillar Scoring** (`app/pillars/`) — Score three pillars: Directional, Volatility, Structure
6. **Hard Gates** (`app/gates/`) — Binary pass/fail checks; any failure → REJECT
7. **Decision Logic** (`app/decision/`) — Final verdict: APPROVE / WATCH / REJECT with quality tiers
8. **Paper Trading** (`app/paper_trading/`) — Track simulated performance

### Lambda Handler (`app/main.py`)

The backend runs as a single Lambda with three invocation modes:
- **API Gateway** — HTTP requests via Mangum
- **Coordinator** — EventBridge-triggered; splits watchlist into chunks, fans out to workers
- **Worker** — Processes a chunk of tickers through the pipeline

### Key Modules
- `app/core/schemas.py` — All Pydantic models (Policy, Evaluation, Opportunity, Decision, etc.)
- `app/core/watchlist.py` — Ticker watchlist management
- `app/db/tables.py` — DynamoDB table operations (all tables use PK/SK pattern, some with GSI1/GSI2)
- `app/config.py` — Settings via pydantic-settings
- `app/services/` — External API clients (Polygon, Finnhub, Slack)
- `app/llm/` — Post-decision trade thesis generation (LLM is never used in decision logic)
- `app/calibration/` — Performance tracking and analysis
- `app/observability/` — Pipeline stage tracing/telemetry

### Frontend
- React 18 + TypeScript + Vite + Tailwind CSS
- TanStack React Query for server state
- Path alias: `@/` → `src/`
- Pages: Dashboard, Opportunities, Pipeline Monitor, Policy Config, Calibration, Evaluation Detail

### Infrastructure (4 CDK Stacks)
- **DatabaseStack** — DynamoDB tables
- **BackendStack** — Lambda + API Gateway + Secrets Manager
- **FrontendStack** — S3 + CloudFront
- **UnusualVolumeStack** — Serverless fan-out for UV scanner (separate Lambda pipeline)

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
- Route: `/evaluation/:ticker/:evaluationId` (2 params, no timestamp)
- Backend endpoint: `GET /api/evaluations/detail/{ticker}/{evaluation_id}` — MUST be defined BEFORE the catch-all `/{ticker}/{timestamp}/{evaluation_id}` route in `evaluations.py` to avoid FastAPI route collision (both are 3-segment paths)
- `EvaluationTable.get_by_id()` queries by PK and scans SK suffix for evaluation_id (avoids URL-encoding issues with ISO timestamps containing `+00:00`)
- `EvaluationDetail.tsx` uses `fmt(val, decimals)` and `num(val)` helpers for null-safe number formatting — many API fields return null
- Error boundary (`EvaluationErrorBoundary`) wraps the page to catch render crashes gracefully

### Pillar Models: PillarResult vs PillarScore
- `PillarResult` (runtime, `app/pillars/models.py`): has `subscores: list[Subscore]` and `top_contributors` property
- `PillarScore` (schema, `app/core/schemas.py`): has `contributors: list[PillarContributor]`
- Convert with `pillar_result.to_pillar_score()` — do NOT manually construct PillarScore from PillarResult (there is no `contributors` attribute on PillarResult)

### Conviction Score (`frontend/src/lib/convictionScore.ts`)
- Client-side weighted calculation: EV (40%), Pillar composite (25%), Gate margin (15%), Scanner convergence (10%), Time sensitivity (10%)
- `DEFAULT_EV_BENCHMARK = 15` — theta-adjusted EV is per-contract dollars over a 5-day hold; typical range $-5 to $+15
- Urgency mapping: Breakout → act_now (100), Unusual Volume → hours (50), Compression/Cheap Options → patient (0)

### Opportunities Page
- Verdict filter dropdown: APPROVE / WATCH / ALL (query param on `/api/evaluations/approve`)
- Conviction Queue shows high-conviction (≥75) opportunities; All APPROVEs table shows everything

### Gate Journey (CSS)
- `.gate-journey` uses `align-items: flex-start` (not `center`) for consistent vertical alignment of gate circles
- `.gate-journey::before` connector line uses `top: 16px` (half of 32px circle diameter)

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

#### Option C: Restore known-good code (nuclear option)
```bash
git checkout pipeline-stable-2026-02-12 -- backend/
# Then redeploy using Step 3
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

### Pipeline Run ID Flow (Critical Context)
- **Coordinator** (`main.py`) is triggered by EventBridge every 15 min
- If tickers ≤ CHUNK_SIZE (100): processes directly, orchestrator creates PipelineRun with UUID
- If tickers > CHUNK_SIZE: coordinator creates PipelineRun, passes run_id to workers in payload
- **Workers** record all stage events under the coordinator's run_id
- **UV scanner** is a separate Lambda pipeline — its runs appear in the sidebar too but only have Stages 1-2
- The Pipeline Monitor queries `PipelineRunTable` for sidebar items and `StageEventTable.list_by_run(run_id)` for stage data

## Known Issues / Future Work

### Pipeline Monitor Restructure (Done)
- Pipeline Monitor now displays all 8 backend stages 1:1 (was 5 compressed stages)
- Stage mapper (`stage_mapper.py`) passes through all 8 stages individually
- Remaining: Add UV scanner as 4th scanner at Stage 1, normalize UV drop reason keys, add Stage 3 passthrough event for UV runs
- Remaining: Add per-filter drop tracking to contract selection (Stage 3)

### Pipeline Fixes Applied (Feb 12, 2026)
- Stages 2, 6, 7 TypeError crashes fixed (earnings_cache kwarg, pillar_results kwarg, pillar→pillars typo)
- Stage 3: Polygon basic tier has no bid/ask in snapshot — added day.close fallback with 5% spread estimate
- Stage 4: FeatureComputer positional arg bug (config passed as catalyst_service)
- Worker run_id flow: workers now use orchestrator-created UUID (was invisible `worker-xxx` IDs)
- Stage mapper: aggregate sums events correctly; fan-out stages (3, 8) don't trigger false anomalies
- All 8 stages flow data end-to-end; Stage 6 currently rejects all (gates working, thresholds may need tuning)

### Pipeline Audit Fixes (Mar 10, 2026)
- **Greeks Coherence gate was rejecting 91% of evaluations** — root cause: Black-Scholes fallback only triggered when BOTH delta=0 AND iv=0, but Polygon basic tier often returns IV without the other Greeks. Fixed: fallback now triggers when ANY critical Greek is zero.
- **IV Percentile gate hard-failed on missing data** — was rejecting evaluations where IV history hadn't accumulated (needs 20 days). Fixed: gate now fails open (passes) when data is missing, since missing data is not evidence of high IV.
- **IV history only written by Cheap Options scanner** — due to early exit optimization (Breakout/Compression trigger first → CheapOptions skipped), many tickers never accumulated IV history. Fixed: orchestrator now stores IV history for ALL tickers before scanners run.
- **Dead GateConfig fields**: `combined_score_min`, `pillar_minimum`, `pillar_spread_max` are defined in GateConfig but no production gate uses them (only backtest). The "relaxations" noted previously had zero effect on production.
- `breakout_volume_min` stays at 1.5x (intentional)

### Pending Verification
- Paper Trading section needs a new pipeline run to verify (GSI1 was added to `oss-dev-paper-positions` table)
- AI Trade Thesis generation should work on next pipeline run (PillarResult→PillarScore conversion was fixed)
- FinnhubClient "not initialized" errors (needs async context manager usage) — fails open, noisy but non-blocking
