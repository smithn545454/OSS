# OSS - Option Scanner System

A deterministic, fully observable system that identifies single-leg long options trades. It scans the market on a schedule, evaluates contracts through an 8-stage pipeline, and surfaces high-conviction opportunities via a real-time web interface.

## How It Works

Every 10 minutes during market hours, an AWS Lambda scans a watchlist of tickers using four independent scanners (Breakout, Breakdown, Compression/Expansion, and Cheap Options). A separate Unusual Volume scanner runs its own fan-out pipeline across the S&P 500. Tickers that trigger at least one scanner become opportunities.

Each opportunity passes through quality filters, then the system selects the best option contracts across DTE buckets and delta bands. For each contract, it computes 40+ features (technical, volatility, liquidity, catalyst), scores three pillars (Directional, Volatility, Structure), and runs 17 hard gates. Any failed gate means automatic rejection. Contracts that pass all gates receive a final weighted score and a verdict: APPROVE, WATCH, or REJECT.

Approved trades enter paper trading for simulated performance tracking. The frontend ranks opportunities by a client-side conviction score and presents them in a queue with full explainability into every score, gate, and decision. All thresholds are driven by a versioned policy that can be edited in the UI without touching code.

## Tech Stack

| Layer | Technology |
|-------|------------|
| Backend | Python 3.12, FastAPI, Mangum (Lambda adapter) |
| Frontend | React 18, TypeScript, Vite, Tailwind CSS, TanStack React Query |
| Database | Amazon DynamoDB (single-table design, PAY_PER_REQUEST) |
| Infrastructure | AWS CDK (Python), 4 CloudFormation stacks |
| Compute | AWS Lambda (single function, 3 invocation modes) |
| API | Amazon API Gateway (HTTP API) |
| Hosting | S3 + CloudFront (SPA) |
| Scheduling | Amazon EventBridge (scan, paper trading update, earnings refresh) |
| External Data | Polygon.io (market data), Finnhub (earnings, filings) |
| AI | Anthropic Claude (post-decision trade thesis only, never in decision logic) |
| Alerts | Slack (optional, high-conviction opportunities) |

## Pipeline

The core system is an 8-stage sequential pipeline. Each stage is independently observable with item counts, drop reasons, and processing time.

```
Tickers ─► [1] Scanners ─► [2] Filters ─► [3] Contract Selection ─► [4] Features
                                                                          │
           [8] Paper Trading ◄─ [7] Decision ◄─ [6] Hard Gates ◄─ [5] Pillars
```

### Stage 1: Opportunity Discovery

Four scanners run in parallel against the ticker watchlist:

| Scanner | What It Detects | Direction |
|---------|----------------|-----------|
| **Breakout** | Price above 20-day high | CALL |
| **Breakdown** | Price below 20-day low | PUT |
| **Compression/Expansion** | ATR-based range expansion (14-day ATR, 1.10x multiplier) | Inferred |
| **Cheap Options** | Low IV relative to realized volatility (IV/RV ratio < 1.10) | Inferred |

A fifth scanner, **Unusual Volume**, runs as a separate Lambda pipeline that fans out across the S&P 500 via SNS/SQS, detecting contracts with 2x+ volume relative to baseline and 15%+ open interest changes. Results feed into the main pipeline at Stage 4 via a UV bridge.

When multiple scanners fire on the same ticker, the opportunity gets a convergence bonus that carries through to the conviction score.

### Stage 2: Underlying Quality Filters

Removes low-quality underlyings before expensive contract analysis:
- Minimum stock price ($5)
- Minimum average dollar volume ($20M)
- Maximum missing trading days (2)
- Earnings exclusion window (5 days)

### Stage 3: Contract Selection

For each passing ticker, the system selects the best option contracts using a ranking algorithm:

**DTE Buckets:** A (7-21 days), B (22-45), C (46-75), D (76-120)

**Delta Bands:** 0.20-0.75 for calls, -0.75 to -0.20 for puts (target: 0.45 / -0.45)

Contracts are ranked by a weighted score of liquidity (40%), delta proximity to target (35%), and spread tightness (25%). The top 3 per DTE bucket advance.

### Stage 4: Feature Computation

Computes 40+ features per contract across 6 categories:

- **Technical:** SMA20/50, 5d/20d returns, trend alignment, ATR
- **Relative Strength:** Performance vs SPY over 5 and 20 days
- **Volatility:** IV, RV20, IV/RV ratio, IV percentile (252-day), IV regime classification (8 regimes)
- **Contract-Specific:** Spread %, theta burden, breakeven, feasibility ratio, theta-adjusted edge
- **Liquidity:** Open interest, volume, 5-day OI change
- **Catalyst:** Days to earnings, recent SEC filings

### Stage 5: Pillar Scoring

Three pillars scored 0-100, each composed of weighted subscores:

**Directional (35% of final score):** Trend alignment (30%), momentum (25%), signal confirmation (20%), relative strength (15%), catalyst proximity (10%). Momentum blending adapts to DTE bucket.

**Volatility (35%):** IV/RV ratio (35%), IV percentile (25%), IV regime (20%), theta-adjusted edge (20%)

**Structure (30%):** Spread tightness (30%), open interest (25%), volume (20%), theta burden (15%), liquidity trend (10%)

Each pillar emits its top 3 contributors (features with highest distance from neutral) for explainability.

### Stage 6: Hard Gates

17 binary pass/fail gates. Any enabled gate failure results in automatic rejection regardless of scores.

| Gate | Threshold | What It Checks |
|------|-----------|----------------|
| Min Open Interest | >= 300 | Sufficient liquidity |
| Min Volume | >= 75 | Active trading |
| Max Spread | <= 8% | Tight bid-ask |
| DTE Range | 7-120 days | Avoids gamma risk and capital inefficiency |
| Move Sufficiency | <= 1.25 | Required move is achievable within timeframe |
| IV Percentile Max | <= 85 | Not buying elevated IV |
| Breakout Volume | >= 1.5x (breakout scanner only) | Confirms breakout with volume |
| Greeks Coherence | Valid | Greeks data quality check |
| Theta Burden Max | <= 4% | Limits daily decay impact |
| Combined Score Min | >= 60 | Weighted pillar composite |
| Pillar Minimum | >= 45 | No pillar too weak |
| Pillar Spread Max | <= 40 | Pillars are balanced |
| Delta Range | 0.20-0.70 | Sensible delta exposure |
| IV/RV Ratio Max | <= 1.5 | Not overpaying for volatility |
| Feasibility Ratio Max | <= 1.5 | Achievable breakeven |
| Max Premium | <= $20 | Position sizing control |
| Trend Alignment Min | >= 0.6 | Directional alignment |

### Stage 7: Decision Logic

Final verdict based on weighted pillar scores:

```
final_score = (directional x 0.35) + (volatility x 0.35) + (structure x 0.30)
```

- **APPROVE** (score >= 75): Qualifies for paper trading, assigned a quality tier (TIER_1/2/3)
- **WATCH** (score >= 65): Tracked for near-miss analysis
- **REJECT** (score < 65 or any gate failed): Dropped, but a subset are shadow-tracked for calibration

Quality tiers for APPROVEs:
- TIER_1: Score >= 85, all pillars >= 70, spread <= 5%
- TIER_2: Score >= 75, all pillars >= 55, spread <= 8%
- TIER_3: Score >= 75

APPROVE verdicts can trigger on-demand AI trade thesis generation (Claude Haiku) with setup summary, supporting evidence, risks, invalidation conditions, and exit plan.

### Stage 8: Paper Trading

Approved and watched evaluations enter simulated position tracking:

- **Entry:** At current mid price on evaluation date
- **Exit Rules:** Profit target (+50%), stop loss (-50%), time exit (5 DTE remaining)
- **Shadow Tracking:** 5% random sample of rejects + near-misses (score 60+) + single-gate failures, tracked for gate calibration
- **Daily Updates:** Scheduled Lambda runs after market close to update prices and check exit conditions
- **Snapshots:** Daily position snapshots for equity curve and performance analysis

## Lambda Architecture

The backend runs as a single Lambda function with three invocation modes:

**API Gateway mode** — HTTP requests routed through FastAPI via Mangum. Serves the frontend API.

**Coordinator mode** — Triggered by EventBridge every 10 minutes during market hours. Loads the watchlist, creates a pipeline run with a UUID, splits tickers into chunks of 100, and invokes worker Lambdas asynchronously. If the watchlist fits in one chunk, processes directly.

**Worker mode** — Receives a chunk of tickers and a shared run_id. Processes all tickers through the full 8-stage pipeline. The last worker to complete (tracked via atomic counter) finalizes the pipeline run.

### Scheduled Events

| Schedule | Action |
|----------|--------|
| Every 10 min, weekdays 13:00-21:00 UTC | Main pipeline scan |
| Daily 21:15 UTC | Paper trading position updates |
| Daily 12:00 UTC | Earnings data refresh |

## Unusual Volume Scanner

A separate serverless pipeline for contract-level unusual volume detection across the S&P 500:

```
EventBridge ─► Publisher Lambda ─► SNS Topic ─► SQS Queue ─► Worker Lambdas (1 per ticker)
                                                                     │
                                                              DynamoDB Streams
                                                                     │
                                                              Handoff Lambda ─► Main Pipeline
```

- **Publisher:** Fans out one message per S&P 500 ticker every 15 minutes
- **Workers:** Check each ticker's options for unusual volume (2x baseline) and OI changes (15%+)
- **Handoff:** Validates candidates and inserts them into the evaluations table as PENDING, picked up by the main pipeline's UV bridge
- **Aggregator:** Compiles scan summary metrics 2 minutes after each scan
- **Nightly Stats:** Pre-computes volume baselines at 1:00 UTC for next-day comparisons

## Frontend

A dark-themed React SPA for monitoring and decision support.

### Pages

**Opportunities** — Primary decision-making interface. Shows a ranked conviction queue (score >= 75) with opportunity cards displaying contract details, scanner badges, urgency indicators, conviction gauges, and key metrics. An expandable table shows all APPROVEs with filtering by verdict, scanner, DTE, and option type. Includes a WATCH intelligence panel showing gate pressure and near-miss patterns.

**Pipeline Monitor** — Operational dashboard showing all 8 pipeline stages with item flow counts, drop reasons, gate failure breakdowns, and anomaly detection. Left sidebar lists recent runs. Supports filtering by time range, scanner type, verdict, DTE bucket, and option side.

**Paper Trading** — Four-tab workstation: Performance Overview (KPIs, equity curve, monthly P&L), Position Tracker (paginated positions with status, entry/exit, P&L), Score Calibration (tier comparison, exit reason analysis), and AI Strategy Advisor (LLM-generated insights by category).

**Evaluation Detail** — Deep dive into a single evaluation showing pillar scores with contributor breakdowns, gate results with measured vs threshold values, computed features, position snapshots, and AI trade thesis.

**Calibration** — Weekly performance reports, gate effectiveness analysis, and threshold adjustment suggestions.

**Policy Config** — Edit all policy thresholds, view/activate policy versions, diff two versions side-by-side. Changes take effect on the next pipeline run.

**Backtesting** — Configure and run backtests against historical data, view equity curves, monthly P&L, segment analysis, and AI readiness assessment.

### Conviction Score

A client-side weighted score (0-100) that ranks opportunities beyond the backend's APPROVE/WATCH verdict:

| Component | Weight | Source |
|-----------|--------|--------|
| Theta-Adjusted EV | 40% | Normalized to $15 benchmark |
| Composite Pillar | 25% | Average of 3 pillar scores |
| Gate Margin | 15% | How far above gate thresholds |
| Scanner Convergence | 10% | Bonus for multiple scanners (2=50, 3=75, 4+=100) |
| Time Sensitivity | 10% | Urgency: Breakout=100, Unusual Volume=50, Others=0 |

## Database

DynamoDB with single-table design patterns. All tables use PK/SK keys with optional GSI1/GSI2 for alternative access patterns.

### Core Tables

| Table | Purpose | TTL |
|-------|---------|-----|
| policies | Policy versions and active flag | - |
| opportunities | Ticker-level scan results | - |
| evaluations | Per-contract evaluations with verdicts | - |
| pipeline-runs | Pipeline execution tracking | - |
| stage-events | Per-stage telemetry (items in/out, drops, timing) | - |
| feature-values | Computed features per evaluation | - |
| pillar-scores | Pillar scores per evaluation | - |
| gate-results | Gate pass/fail per evaluation | - |
| paper-positions | Simulated positions (open/closed) | - |
| paper-snapshots | Daily position snapshots | - |
| trade-thesis | LLM-generated trade theses | - |
| llm-usage | LLM API call tracking | - |
| calibration-reports | Performance analysis reports | - |
| iv-history | Historical implied volatility | 365 days |
| oi-history | Historical open interest | 90 days |
| earnings-cache | Cached earnings dates | Auto |

### UV Scanner Tables

| Table | Purpose | TTL |
|-------|---------|-----|
| sp500-tickers | S&P 500 watchlist | - |
| volume-stats | Pre-computed volume baselines | 7 days |
| unusual-volume-candidates | Scan results (DynamoDB Streams enabled) | 24 hours |
| scan-runs | UV scanner run metadata | 7 days |
| underlying-stats | Underlying quality data for UV filtering | - |

## Infrastructure

Four AWS CDK stacks deployed to `us-west-1`:

**DatabaseStack** — All DynamoDB tables (15 core + 5 UV scanner). PAY_PER_REQUEST billing. Dev environment uses DESTROY removal policy; prod uses RETAIN.

**BackendStack** — Lambda function (Python 3.12, 3008 MB memory, 10-min timeout), HTTP API Gateway with catch-all route, Secrets Manager for API keys (Polygon, Finnhub), 3 EventBridge rules for scheduled scans, and CloudWatch log group (2-week retention).

**FrontendStack** — S3 bucket for static assets, CloudFront distribution with Origin Access Identity, SPA error handling (404/403 -> index.html), and HTTPS enforcement.

**UnusualVolumeStack** — 5 Lambda functions (Publisher, Worker, Aggregator, Handoff, Nightly Stats), SNS topic + SQS queue for fan-out, Dead Letter Queue, DynamoDB Streams trigger, and 3 EventBridge schedules.

## Getting Started

### Backend

```bash
cd backend
python -m venv venv
source venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload --port 8001
```

### Frontend

```bash
cd frontend
npm install
npm run dev  # Proxies /api to localhost:8001
```

### DynamoDB Local (Optional)

```bash
docker run -p 8000:8000 amazon/dynamodb-local
python infrastructure/dynamodb_tables.py
```

### Environment Variables

Create a `.env` file in the backend directory:

```env
AWS_REGION=us-west-1
DYNAMODB_ENDPOINT=http://localhost:8000  # For local development
POLYGON_API_KEY=your_polygon_key
FINNHUB_API_KEY=your_finnhub_key
```

## Deployment

```bash
./scripts/deploy.sh backend              # Deploy backend (runs tests first)
./scripts/deploy.sh frontend             # Build and deploy frontend
./scripts/deploy.sh all                  # Full deployment (infra + backend + frontend)
./scripts/deploy.sh rollback             # Rollback to previous Lambda version
./scripts/deploy.sh versions             # List published Lambda versions
```

Each backend deploy runs tests, packages the code, uploads to Lambda, and publishes an immutable numbered version for rollback. Frontend deploys build the React app, sync to S3, and invalidate the CloudFront cache.

## Testing

```bash
# Backend
cd backend
pytest tests/ --tb=short -q     # All tests
ruff check app/                  # Lint
ruff format app/                 # Format

# Frontend
cd frontend
npm test                         # Vitest
npm run lint                     # ESLint
npm run build                    # Type check + build
```

## Non-Negotiable Principles

1. **Single-leg long options only** — No spreads, combos, or short positions
2. **Deterministic decisions** — Same inputs + same policy = identical outputs
3. **No LLM in decision logic** — AI is used only for post-decision trade thesis generation
4. **Hard gates dominate** — Any failed gate = REJECT, regardless of scores
5. **Everything is explainable** — Every score emits reason codes; every gate shows measured vs threshold
6. **Config over code** — All thresholds come from the active policy and are editable in the UI

## License

Private - Internal Use Only
