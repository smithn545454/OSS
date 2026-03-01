# Backtest Data Ingestion Progress

**Date:** 2026-02-28
**Branch:** `claude/xenodochial-joliot`
**Lambda Version:** v68

## What Was Done

### 1. Fixed 0-Trade Backtest Bug (v67)
- **Root cause:** `CreateRunRequest.tickers` defaults to `[]`, which bypassed `WatchlistManager` fallback
- **Fix:** Resolve tickers via `WatchlistManager.from_policy_async()` in API endpoint before building frozen `PolicyConfig`; defense-in-depth in worker converts `[]` to `None`
- **Files changed:** `backend/app/api/routes/backtest.py`, `backend/app/backtest/worker.py`

### 2. Full S3 Options Data Ingestion (v68)
- Rewrote `backend/scripts/ingest_options.py` using pandas C engine (300K rows/s vs 65K with Python csv)
- **Result:** 917,883,172 rows across 584 trading dates from 5,900+ tickers per quarter
- Source: `OSS/Historic Data/{YEAR}_q{N}_option_chain_{hash}/` — 10 quarters (Oct 2023 – Feb 2026)
- Completed in 107 minutes

### 3. IV History Re-Derived
- Ran `backend/scripts/derive_iv_history.py` with 8 parallel threads against expanded options data
- 584 dates processed in 77 minutes
- Output: `s3://oss-dev-backtest-982534389101/iv-history/date=YYYY-MM-DD/data.parquet`

### 4. PyArrow Push-Down Filters in HistoricalDataProvider
- Replaced all Python-level `for idx, t in enumerate(tickers_col)` loops with `table.filter(pc.field("ticker") == ticker)`
- Critical for performance: parquets now have ~1.4M rows per date (up from 96K with 50 tickers)
- **File changed:** `backend/app/core/historical_data_provider.py`

## What's Left

### Verify Backtest Produces Trades
A 5-day test backtest was started (Jan 15-19, 2024, run ID `42d1dd3a-dbaa-43ae-9594-53d0f42e5662`) but hadn't completed before the session ended. CloudWatch showed the worker was processing days correctly — no errors.

**To check results:**
```bash
curl -s "https://2nv5mt4d1k.execute-api.us-west-1.amazonaws.com/api/backtest/runs/42d1dd3a-dbaa-43ae-9594-53d0f42e5662" | python3 -m json.tool
```

**If 0 evaluations/trades, check CloudWatch:**
```bash
AWS_REGION=us-west-1 aws logs filter-log-events \
  --log-group-name "/aws/lambda/oss-dev-backend" \
  --filter-pattern "42d1dd3a" \
  --start-time $(python3 -c "import time; print(int((time.time()-7200)*1000))") \
  --limit 50 --query 'events[*].message' --output text
```

**Possible issues to investigate if still 0 trades:**
- Stage 6 (Hard Gates) may still reject 100% — gate thresholds may need tuning
- Spread/liquidity gates may fail due to fallback pricing (no real-time bid/ask in historical data)
- Check which gates are failing: look for "GATE_FAIL" in CloudWatch logs

### Merge to Main
After verifying the backtest works:
```bash
git checkout main
git pull origin main
git merge origin/claude/xenodochial-joliot --no-edit
git push origin main
git push origin --delete claude/xenodochial-joliot
```

## S3 Data Summary

| Dataset | Dates | Rows/Date | Total Size |
|---------|-------|-----------|------------|
| options-chains | 584 | ~1.4M | ~87 GB |
| iv-history | 584 | ~5,000 | ~170 MB |
| stock-ohlcv | 584 | ~10,000 | ~3.4 GB |

## Key Files Modified This Session

| File | Change |
|------|--------|
| `backend/app/api/routes/backtest.py` | Ticker resolution fix |
| `backend/app/backtest/worker.py` | Empty tickers defense-in-depth |
| `backend/app/core/historical_data_provider.py` | PyArrow push-down filters |
| `backend/scripts/ingest_options.py` | Pandas-based rewrite |
| `backend/scripts/derive_iv_history.py` | Parallel S3 processing |
