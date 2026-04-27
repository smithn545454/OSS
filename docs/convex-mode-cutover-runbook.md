# Convex Mode — Single-Cutover Deployment Runbook

This is the operator's runbook for shipping Convex Mode to production.
Per the source plan §12, deployment is **one go** — schema, scanner
pause, pipeline activation, and alert routing all flip in a single
maintenance window.

> ⚠️ **Do not run this until** Phase 8 backtest validation has passed
> the §11 acceptance gates against the 12-month backfilled dataset.

---

## Pre-flight checklist (T-7 days)

- [ ] [docs/convex-mode-impact-report.md](./convex-mode-impact-report.md)
      reviewed and approved by Nick (Phase 0 deliverable).
- [ ] Phase 0.5 IV backfill complete: 12 months of multi-tenor + 25Δ skew
      data loaded into `oss-dev-iv-history` with ≥80% coverage on
      `iv_30d` / `iv_60d` / `iv_25d_put` / `iv_25d_call` per
      [docs/convex-mode-iv-backfill.md](./convex-mode-iv-backfill.md).
- [ ] Backtest validation has run successfully and the report's
      `passes_acceptance` is `True`. Tier A vs Tier C divergence
      observed; Smart Money Confirmation cohort outperforms.
- [ ] All 2843+ backend tests green (`pytest --no-cov -p no:randomly`).
- [ ] Frontend build clean (`npm run build`) and TypeScript check
      passes (`npx tsc -b`).
- [ ] CI green on the deployment branch (`gh run list --limit 1`).
- [ ] Most recent baseline tagged (e.g., `pipeline-stable-pre-convex-YYYY-MM-DD`)
      so rollback has a known-good target.
- [ ] Manual catalyst seed loaded: PDUFA dates for biotech subset, FOMC
      / CPI / NFP for macro proxies, written to
      `oss-dev-catalyst-calendar`.
- [ ] Alert webhook for `#convex-approvals` Slack channel created.

## Pre-flight checklist (T-24h)

- [ ] Run a fresh Convex universe build via the manual Lambda invoke
      (the EventBridge rule is still disabled at this stage). Verify the
      snapshot has 200-400 tickers and Nick's eye-test passes.
      ```bash
      AWS_REGION=us-west-1 aws lambda invoke \
        --function-name oss-dev-backend \
        --payload '{"source":"oss.scheduler","action":"convex_universe_refresh"}' \
        /tmp/convex-universe-out.json
      cat /tmp/convex-universe-out.json
      ```
- [ ] Inspect snapshot via API:
      `curl https://2nv5mt4d1k.execute-api.us-west-1.amazonaws.com/api/convex/universe`
- [ ] Run a Convex daily pipeline once manually to smoke-test the
      provider chain end to end (still gated by `convex_mode.enabled`
      defaulting to `False`; this run is a no-op but exercises the
      Lambda code path):
      ```bash
      AWS_REGION=us-west-1 aws lambda invoke \
        --function-name oss-dev-backend \
        --payload '{"source":"oss.scheduler","action":"convex_daily_run"}' \
        /tmp/convex-daily-out.json
      ```
- [ ] CloudWatch shows no errors for either Lambda invocation.

---

## Cutover sequence (T-0)

> **Run during a low-volume window: weekend or Friday after market close.**

### Step 1 — Snapshot the current policy

```bash
curl -s https://2nv5mt4d1k.execute-api.us-west-1.amazonaws.com/api/policies/active \
  > baselines/$(date -u +%Y-%m-%d)-pre-convex-policy.json
```

### Step 2 — Deploy schema migrations (DatabaseStack only)

The new tables (`convex-universe-snapshots`, `convex-stage-events`,
`catalyst-calendar`, `convex-evaluations`) are added in Phase 1 and
Phase 7 schema work. They are additive — no destructive operations.

```bash
cd infrastructure/cdk
source .venv/bin/activate
cdk deploy oss-dev-database
```

⚠️ **Never run `cdk deploy oss-dev-backend`** — replaces Lambda with an
unpackaged bundle. If you do, immediately rollback with
`./scripts/deploy.sh rollback`.

### Step 3 — Deploy backend

Backend deploy includes the new Lambda actions
(`convex_universe_refresh`, `convex_daily_run`), the Convex API routes,
and the daily-runner orchestration code.

```bash
./scripts/deploy.sh backend
```

This runs pytest first; aborts if anything fails. Records the version
number in the Lambda description.

### Step 4 — Deploy frontend

Frontend deploy includes the `/convex` page, the Convex Evaluation Detail
walkthrough, and the failed-candidates debug page.

```bash
./scripts/deploy.sh frontend
```

### Step 5 — Update the active policy (single atomic flip)

This is the load-bearing step. The new policy:

1. **Pauses the three legacy scanners** — sets
   `policy.config.scanner.unusual_volume.enabled = false`,
   `breakout.enabled = false`, `compression.enabled = false`,
   `cheap_options.enabled = false`. (Per Nick's choice all three
   *plus* breakout pause; legacy v5 still active for any flag-flipped
   exceptions.)
2. **Enables Convex Mode** — sets `policy.config.convex.enabled = true`.

Build the new policy JSON from the snapshot:

```bash
# Edit baselines/$(date -u +%Y-%m-%d)-pre-convex-policy.json:
#   - scanner.unusual_volume.enabled  -> false
#   - scanner.breakout.enabled         -> false
#   - scanner.compression.enabled      -> false
#   - scanner.cheap_options.enabled    -> false
#   - convex.enabled                   -> true
# Save as baselines/$(date -u +%Y-%m-%d)-convex-cutover-policy.json
```

Push the new policy:

```bash
NEW_POLICY=baselines/$(date -u +%Y-%m-%d)-convex-cutover-policy.json
curl -X POST -H "Content-Type: application/json" \
  -d @"$NEW_POLICY" \
  https://2nv5mt4d1k.execute-api.us-west-1.amazonaws.com/api/policies
```

Activate the new version:

```bash
VERSION=$(jq -r .config.version "$NEW_POLICY")  # adjust if version is in a different field
curl -X POST \
  https://2nv5mt4d1k.execute-api.us-west-1.amazonaws.com/api/policies/$VERSION/activate
```

### Step 6 — Disable the legacy UV Lambda EventBridge rule

The UV Lambda is a separate stack. Its EventBridge schedule must be
disabled so it stops scanning every 15 minutes:

```bash
AWS_REGION=us-west-1 aws events disable-rule \
  --name oss-dev-unusual-volume-publisher
AWS_REGION=us-west-1 aws events disable-rule \
  --name oss-dev-unusual-volume-aggregator
```

### Step 7 — Enable the Convex EventBridge rules

Both Convex rules are deployed disabled-by-default. Flip them on:

```bash
AWS_REGION=us-west-1 aws events enable-rule \
  --name oss-dev-convex-universe-refresh
AWS_REGION=us-west-1 aws events enable-rule \
  --name oss-dev-convex-daily-run
```

### Step 8 — Smoke test on live data

Trigger one Convex daily run and inspect the output:

```bash
AWS_REGION=us-west-1 aws lambda invoke \
  --function-name oss-dev-backend \
  --payload '{"source":"oss.scheduler","action":"convex_daily_run"}' \
  /tmp/convex-cutover-smoke.json
cat /tmp/convex-cutover-smoke.json
```

Expected output: `status=ok`, `universe_size > 0`, stage advancers
non-zero on at least Stage 1 + Stage 2. Tier counts may legitimately
be zero if the day's signals don't qualify; that's fine.

Visit the frontend:

- [https://d3upsbalspxt4n.cloudfront.net/convex](https://d3upsbalspxt4n.cloudfront.net/convex)
- Confirm the page loads. If there are any APPROVE candidates, click
  through to verify the four-stage walkthrough renders.
- Visit `/convex/runs/<run_id>/failed` to verify the debug page works.

### Step 9 — CloudWatch + Pipeline Monitor verification

```bash
# Last 5 minutes of ERROR logs
AWS_REGION=us-west-1 aws logs filter-log-events \
  --log-group-name "/aws/lambda/oss-dev-backend" \
  --filter-pattern "ERROR" \
  --start-time $(python3 -c "import time; print(int((time.time()-300)*1000))") \
  --limit 20 --query 'events[*].message' --output text
```

If there are ERROR logs related to the Convex pipeline, **rollback
immediately** (Step 11).

### Step 10 — Activate the Slack alert channel

Add `CONVEX_APPROVE` to the alert verdicts list and add the
`#convex-approvals` webhook to `webhook_channels`:

```bash
# Read current alert config
curl -s https://2nv5mt4d1k.execute-api.us-west-1.amazonaws.com/api/alerts/config

# POST updated config with:
#   verdicts: ["APPROVE", "CONVEX_APPROVE"]
#   webhook_channels: [..., {"name":"convex-approvals","url":"<webhook>"}]
```

Send a test alert per the alerts route's POST endpoint to confirm the
channel routes correctly.

---

## Rollback

If the smoke test or first day of Convex pipeline output is broken,
roll back **in this order**:

### Fast rollback (preferred)

1. **Re-enable the legacy scanners** by reverting the policy:
   ```bash
   PRIOR=$(jq -r .version baselines/$(date -u +%Y-%m-%d)-pre-convex-policy.json)
   curl -X POST \
     https://2nv5mt4d1k.execute-api.us-west-1.amazonaws.com/api/policies/$PRIOR/activate
   ```
2. **Disable the Convex EventBridge rules**:
   ```bash
   aws events disable-rule --name oss-dev-convex-daily-run
   aws events disable-rule --name oss-dev-convex-universe-refresh
   ```
3. **Re-enable the legacy UV EventBridge rules**:
   ```bash
   aws events enable-rule --name oss-dev-unusual-volume-publisher
   aws events enable-rule --name oss-dev-unusual-volume-aggregator
   ```

This puts OSS back in its pre-cutover state in under 5 minutes. The
new schema additions are non-destructive — they sit empty until the
next time you flip Convex on.

### Lambda rollback (only if backend deploy itself is broken)

```bash
./scripts/deploy.sh rollback   # to previous version
```

This reverts the Lambda code only; the policy remains active. Use
together with the policy revert above when the Lambda deploy is
suspected as the cause.

### Schema rollback (last resort)

The new tables can stay in place — they're harmless when empty. **Do
not delete** unless you're sure no records will be needed for an
audit / post-mortem of why the cutover failed. The `convex-stage-events`
table specifically is the source of truth for "why did Convex misbehave
during cutover?"

---

## Week-1 monitoring (post-cutover)

Per the source plan §13:

### Daily checks (every weekday morning)

```bash
# 1. Recent Convex pipeline runs
curl -s https://2nv5mt4d1k.execute-api.us-west-1.amazonaws.com/api/convex/evaluations?limit=10

# 2. Latest universe snapshot
curl -s https://2nv5mt4d1k.execute-api.us-west-1.amazonaws.com/api/convex/universe \
  | jq '.snapshot.total_count'

# 3. CloudWatch errors in the last 24h
AWS_REGION=us-west-1 aws logs filter-log-events \
  --log-group-name "/aws/lambda/oss-dev-backend" \
  --filter-pattern "ERROR" \
  --start-time $(python3 -c "import time; print(int((time.time()-86400)*1000))") \
  --limit 20 --query 'events[*].message' --output text
```

### Watch-for items

- **Universe drift**: total_count moves outside 200-400 for >5 days →
  Stage 1 gates need tuning.
- **No Tier A signals for 5+ days**: tier thresholds may be too tight,
  or Stage 2/3 signals are too rare. Review failed-candidates page.
- **Tier C inflation (>50% of total approvals)**: tier thresholds too
  loose; tighten Tier B floors.
- **Tier A and Tier C performance equivalence**: validates the source
  plan's "hidden coupling" risk; investigate which stage is leaking.
- **Smart Money Confirmation cohort underperforms unconfirmed**:
  validates the "UV signals can be hedges" risk; investigate before
  enabling tier-promotion behavior.

### Tunable parameters (config-driven; no deploy required)

All live in `policy.config.convex.*` and can be adjusted by activating
a new policy version:

- `vol_iv_rank_max` (default 40)
- `catalyst_compression_atr_ratio_max` (default 0.75)
- `catalyst_compression_signals_required` (default 2 of 5)
- `tier_a_stage2_strength_min` / `tier_a_stage3_composite_min`
- `tier_b_stage2_strength_min` / `tier_b_stage3_composite_min`
- `contract_dte_min` / `contract_dte_max`
- `sizing_tier_a_pct` / `sizing_tier_b_pct` / `sizing_tier_c_pct`

---

## Files touched in the Convex Mode build

This is a navigation aid for post-cutover diagnostic work. Every
production code path lives under `app/convex/` for grep-ability.

### Backend pipeline + persistence
- [app/convex/pipeline.py](../backend/app/convex/pipeline.py) — orchestrator
- [app/convex/_types.py](../backend/app/convex/_types.py) — Tier enum
- [app/convex/stage1_universe.py](../backend/app/convex/stage1_universe.py)
- [app/convex/stage2_catalyst.py](../backend/app/convex/stage2_catalyst.py)
- [app/convex/stage3_volatility.py](../backend/app/convex/stage3_volatility.py)
- [app/convex/stage4_contract.py](../backend/app/convex/stage4_contract.py)
- [app/convex/tier.py](../backend/app/convex/tier.py)
- [app/convex/providers.py](../backend/app/convex/providers.py) — production data wiring
- [app/convex/polygon_fetcher.py](../backend/app/convex/polygon_fetcher.py)
- [app/convex/universe_builder.py](../backend/app/convex/universe_builder.py)
- [app/convex/iv_extraction.py](../backend/app/convex/iv_extraction.py)
- [app/convex/daily_runner.py](../backend/app/convex/daily_runner.py)
- [app/convex/backtest.py](../backend/app/convex/backtest.py)

### API routes
- [app/api/routes/convex.py](../backend/app/api/routes/convex.py) — `/api/convex/*`

### Schemas + tables
- [app/core/schemas.py](../backend/app/core/schemas.py) — Verdict enum extended; `Decision` extended; new models
- [app/db/tables.py](../backend/app/db/tables.py) — 4 new tables

### Lambda handler
- [app/main.py](../backend/app/main.py) — `_run_convex_universe_refresh`, `_run_convex_daily_run`

### Infrastructure
- [infrastructure/cdk/stacks/database_stack.py](../infrastructure/cdk/stacks/database_stack.py) — 4 new tables
- [infrastructure/cdk/stacks/backend_stack.py](../infrastructure/cdk/stacks/backend_stack.py) — 2 new EventBridge rules

### Frontend
- [frontend/src/lib/convexTypes.ts](../frontend/src/lib/convexTypes.ts)
- [frontend/src/lib/convexApi.ts](../frontend/src/lib/convexApi.ts)
- [frontend/src/pages/ConvexOpportunities.tsx](../frontend/src/pages/ConvexOpportunities.tsx)
- [frontend/src/pages/ConvexEvaluationDetail.tsx](../frontend/src/pages/ConvexEvaluationDetail.tsx)
- [frontend/src/pages/ConvexFailedCandidates.tsx](../frontend/src/pages/ConvexFailedCandidates.tsx)
- [frontend/src/components/Layout.tsx](../frontend/src/components/Layout.tsx) — nav item
- [frontend/src/App.tsx](../frontend/src/App.tsx) — route wiring

### Backfill scripts (Phase 0.5)
- [backend/scripts/derive_iv_history.py](../backend/scripts/derive_iv_history.py)
- [backend/scripts/backfill_iv_history_dynamodb.py](../backend/scripts/backfill_iv_history_dynamodb.py)

### Documentation
- [docs/convex-mode-impact-report.md](./convex-mode-impact-report.md) — Phase 0
- [docs/convex-mode-iv-backfill.md](./convex-mode-iv-backfill.md) — Phase 0.5
- [docs/convex-mode-cutover-runbook.md](./convex-mode-cutover-runbook.md) — this document

---

## Final note

The Convex Mode build was an unusually deliberate one: the impact
report exhaustively mapped every consumer of the existing `Verdict`
enum and the legacy scanner pipeline before code was written. The
single-cutover deployment trades operational complexity for behavioral
clarity — there's no period where signals are coming from two
different systems pretending to be one.

If you find yourself unsure whether to ship a tweak as a tunable
config or as a code change, default to config. Convex Mode is going to
need a lot of week-1 calibration; preserving that as a no-deploy
operation keeps Nick's reaction time tight.
