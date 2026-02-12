# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

OSS (Option Scanner System) — a deterministic, fully observable system that identifies single-leg long options trades. Python/FastAPI backend, React/TypeScript frontend, DynamoDB database, deployed to AWS via CDK.

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
cdk deploy --all     # Deploy all stacks
```

### Deployment
```bash
./scripts/deploy.sh all       # Full deployment
./scripts/deploy.sh backend   # Backend only
./scripts/deploy.sh frontend  # Frontend only
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
- Production API: `https://2nv5mt4d1k.execute-api.us-west-1.amazonaws.com/` (no `/prod` suffix)
- Frontend CloudFront: `https://d3upsbalspxt4n.cloudfront.net`

## Post-Deploy Verification (Required)

After every backend deploy that touches pipeline logic, API routes, or data flow, verify the change works **end-to-end** — not just in CloudWatch logs, but through the actual API endpoints the frontend calls. Do not declare a fix complete until both checks pass.

### Backend Verification
1. Check CloudWatch for the next pipeline run:
   ```bash
   AWS_REGION=us-west-1 aws logs filter-log-events --log-group-name "/aws/lambda/oss-dev-backend" \
     --filter-pattern '"Stage"' --start-time <epoch_ms> --limit 30 --query 'events[*].message' --output text
   ```
2. Confirm no ERROR-level logs for the changed stages
3. Verify stage events are recorded under a proper UUID run_id (not `worker-xxx`)

### Frontend/API Verification
1. Check the Pipeline Monitor API returns the new run in the sidebar:
   ```bash
   curl -s "https://2nv5mt4d1k.execute-api.us-west-1.amazonaws.com/api/pipeline/runs?limit=5" | python3 -m json.tool
   ```
2. Check stage data is populated for all 8 stages:
   ```bash
   curl -s "https://2nv5mt4d1k.execute-api.us-west-1.amazonaws.com/api/pipeline/runs/{run_id}" | python3 -c "
   import sys, json
   data = json.load(sys.stdin)
   for s in data['data']['stages']:
       print(f\"Stage {s['id']}: {s['name']:25s}  In: {s['input']:>4}  Out: {s['output']:>4}  {s['status']}\")
   "
   ```
3. If the change affects Evaluations or Opportunities pages, also verify those API endpoints return expected data

### What "Verified" Means
- The run appears in the sidebar with a non-zero contract count (unless all are legitimately filtered)
- All 8 stages show In/Out counts (not all zeros for stages 3-8)
- No false anomaly flags on fan-out stages (Stage 3: Contract Selection)
- Data matches what CloudWatch logs show

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

### Pending Verification
- Paper Trading section needs a new pipeline run to verify (GSI1 was added to `oss-dev-paper-positions` table)
- AI Trade Thesis generation should work on next pipeline run (PillarResult→PillarScore conversion was fixed)
- Gate threshold relaxations applied: breakout_volume_min 1.5→1.0, combined_score_min 75→60, pillar_minimum 60→45, pillar_spread_max 30→40
- Stage 6 rejecting 100% of evaluations — investigate which gates are failing (likely spread/liquidity from fallback pricing)
- FinnhubClient "not initialized" errors (needs async context manager usage) — fails open, noisy but non-blocking
