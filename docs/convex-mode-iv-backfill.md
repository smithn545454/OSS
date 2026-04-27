# Convex Mode — Phase 0.5 IV Backfill Run Plan

This is the operator's runbook for the multi-tenor + 25Δ-skew IV history
backfill that Convex Mode Stage 3 (Volatility Mispricing) depends on.

## Context

OSS's existing `iv-history` table stores only `atm_iv` per ticker per
day. Convex Stage 3 requires four additional metrics:

| Field         | Used by                                                     |
|---------------|-------------------------------------------------------------|
| `iv_30d`      | Front-month tenor (term-structure shape vs `iv_60d`)       |
| `iv_60d`      | 60-day tenor (term-structure shape)                        |
| `iv_25d_put`  | 25Δ put IV (skew positioning)                              |
| `iv_25d_call` | 25Δ call IV (skew positioning)                             |

The `IVHistory` Pydantic schema and the DynamoDB table both already
accept these fields (added in Phase 1.1). They are `Optional[float]` so
records that pre-date the backfill remain valid; Stage 3 fails *open*
on missing skew data (treated as "no signal" rather than rejecting the
candidate).

**Coverage target:** trailing 12 months (Apr 2025 → Apr 2026) for the
Convex kinetic universe (~300 tickers). Supplemented by the existing
ATM IV backfill (Dec 2025 – Mar 2026, per CLAUDE.md).

---

## Prerequisites

1. **Polygon options-chain S3 bucket** must contain
   `options-chains/date=YYYY-MM-DD/data.parquet` files for the target
   12-month window. Schema must include columns:
   - `ticker`, `delta`, `bid_iv`, `ask_iv`, `expiry_date`
2. **AWS credentials** for the OSS dev account, region `us-west-1`.
3. **Python environment** matching `backend/pyproject.toml`
   (`pyarrow`, `boto3`, `pydantic`).
4. **Disk / S3 space** for the derived `iv-history/` partition (~10×
   smaller than source options-chains; ~1-2 GB for 12 months).

> If the source bucket is missing dates, see the "Coverage gaps"
> section at the end. The backfill is designed to tolerate missing
> days — Stage 3's calibration just sees a smaller sample.

---

## Step 1 — Derive the multi-tenor + skew columns

The `derive_iv_history.py` script reads source options-chains parquet
and writes the extended `iv-history/` partition with all five columns:
`atm_iv`, `iv_30d`, `iv_60d`, `iv_25d_put`, `iv_25d_call`.

### Dry run (recommended first)

```bash
cd backend
python scripts/derive_iv_history.py \
    --s3-bucket oss-dev-backtest-<account-id> \
    --dry-run
```

The dry run reports per-date row counts and a final coverage summary
across all processed dates, e.g.:

```
Coverage across all processed dates:
  atm_iv        :  98.2%  (123,456 / 125,712)
  iv_30d        :  96.8%  ( ... )
  iv_60d        :  94.1%  ( ... )
  iv_25d_put    :  88.6%  ( ... )
  iv_25d_call   :  89.1%  ( ... )
```

**Acceptance threshold:** at least **80% coverage** on `iv_30d`,
`iv_60d`, `iv_25d_put`, and `iv_25d_call` across the trailing-12-month
window. Below that, investigate the source parquet (likely missing
delta/IV columns or thin chains for some tickers).

### Full run (writes parquet back to S3)

```bash
python scripts/derive_iv_history.py \
    --s3-bucket oss-dev-backtest-<account-id>
```

Runtime: ~30-60 minutes for 12 months at 8-thread S3 concurrency.

---

## Step 2 — Backfill DynamoDB from the derived parquet

`backfill_iv_history_dynamodb.py` reads the derived `iv-history/`
partition and writes records into `oss-dev-iv-history`. It is
backward-compatible: if older parquet files are present (atm_iv only),
those rows are written without skew/tenor fields. New rows are written
with all five fields populated.

### Dry run (column coverage report)

```bash
AWS_REGION=us-west-1 DYNAMODB_TABLE_PREFIX=oss-dev \
  python scripts/backfill_iv_history_dynamodb.py \
    --s3-bucket oss-dev-backtest-<account-id> \
    --dry-run
```

The dry run prints per-column coverage across all records that would
be written, e.g.:

```
DRY RUN: Would write 75,000 records
  Tickers: 312
  Date range: 2025-04-28 to 2026-04-25
  atm_iv        : 100.0%  (75,000 / 75,000)
  iv_30d        :  97.3%  ( ... )
  iv_60d        :  95.1%  ( ... )
  iv_25d_put    :  89.4%  ( ... )
  iv_25d_call   :  90.0%  ( ... )
```

### Full backfill

```bash
AWS_REGION=us-west-1 DYNAMODB_TABLE_PREFIX=oss-dev \
  python scripts/backfill_iv_history_dynamodb.py \
    --s3-bucket oss-dev-backtest-<account-id>
```

Runtime: ~10-15 minutes for ~75k records at 500-record batch size.

---

## Step 3 — Verify

The backfill script automatically samples 5 tickers and prints the
latest record per ticker, including the new fields:

```
Verification:
  AAPL: 252 days (2025-04-28 to 2026-04-25), latest atm=0.2245 iv_30d=0.2189 iv_60d=0.2310 skew=0.2401/0.2078
  MSFT: 251 days ( ... )
  ...
```

If any sample shows `skew=` missing on otherwise complete records,
that's the data-completeness signal — re-derive for those tickers
or accept the gap.

---

## Step 4 — Spot-check Convex Stage 3 readiness

Confirm a representative ticker has all four new fields populated for
recent dates:

```python
from app.db.tables import IVHistoryTable

records = await IVHistoryTable.list_by_ticker("NVDA", limit=30)
for r in records:
    print(r.date, r.atm_iv, r.iv_30d, r.iv_60d, r.iv_25d_put, r.iv_25d_call)
```

Stage 3 will fail open on `None` skew values, so partial coverage
across older dates is fine; recent 60+ days should be fully populated
to support live signals.

---

## Coverage gaps (acceptable behaviour)

| Gap                                     | Stage 3 behaviour                                     |
|-----------------------------------------|-------------------------------------------------------|
| Missing `iv_30d` for some dates         | IV Rank / Percentile compute from `atm_iv` (back-compat). |
| Missing `iv_60d` for some dates         | Term-structure check fails open (no penalty).         |
| Missing `iv_25d_put` or `iv_25d_call`   | Skew alignment scored as neutral, no penalty.         |
| Entire date missing                     | Date excluded from IV Percentile lookback for that ticker. |

Backtest validation in Phase 8 will surface any coverage gap that
materially weakens signal — at which point we re-derive that subset.

---

## Rollback

The new columns are additive (`Optional[float]`), so the backfill is
*non-destructive*. Reverting just means stopping the script; existing
records keep their newly populated fields, which the legacy code paths
(IV Rank, IV Percentile) ignore. To wipe the new fields entirely you
would need a custom DynamoDB UPDATE script — not normally necessary.

---

## Files touched (Phase 0.5)

- [backend/app/convex/iv_extraction.py](../backend/app/convex/iv_extraction.py) — pure-function multi-tenor + skew extractor (testable, no I/O)
- [backend/scripts/derive_iv_history.py](../backend/scripts/derive_iv_history.py) — extended schema; delegates to the pure extractor
- [backend/scripts/backfill_iv_history_dynamodb.py](../backend/scripts/backfill_iv_history_dynamodb.py) — reads new columns, writes them to DynamoDB, surfaces coverage in dry-run + verification logs
- [backend/tests/test_convex_iv_extraction.py](../backend/tests/test_convex_iv_extraction.py) — 22 unit tests covering selectors, edge cases, completeness
- [backend/app/core/schemas.py](../backend/app/core/schemas.py) — `IVHistory` model already extended in Phase 1.1
