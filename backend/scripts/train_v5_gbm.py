#!/usr/bin/env python3
"""Train v5 GBM co-scorer models — logistic regression + isotonic calibration.

The v5 plan calls this the "GBM co-scorer" (gradient-boosted machine) but
Lambda's 250 MB unzipped limit makes shipping XGBoost impractical. We
use L2-regularized logistic regression with isotonic calibration instead.

The purpose is unchanged: score trades that don't match any archetype
so the dual-conviction system isn't blind to non-archetype patterns.
The naming `gbm` is preserved in schemas and code because the REASON
(non-archetype fallback scorer) is more important than the specific
algorithm. Phase 7+ can upgrade to XGBoost via Lambda layers if the
linear model proves insufficient.

This script trains TWO models:
  1. HR model: predicts P(MFE >= 200%) — the home run probability
  2. P model: predicts P(profit) — the profitable-outcome probability

Both are serialized as JSON (standardization params + coefficients +
isotonic lookup table) for pure-Python inference in app/v5/gbm_scorer.py.

Features used (20):
  entry_delta, abs_delta, dte_at_entry, entry_iv, entry_iv_percentile,
  entry_iv_rv_ratio, adx_14, plus_di, minus_di, rs_20d, atr14_pct,
  theta_pct, pillar_dc, pillar_mp, pillar_ts, scanner_is_uv,
  scanner_is_cheap, scanner_is_breakdown, scanner_is_revalidation,
  option_is_call.

Training: temporal 80/20 split on entry_date. sklearn used locally only.

Usage:
  AWS_REGION=us-west-1 DYNAMODB_TABLE_PREFIX=oss-dev \\
  python3 backend/scripts/train_v5_gbm.py
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import boto3
import numpy as np
from boto3.dynamodb.conditions import Key
from sklearn.calibration import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score

# Make backend app importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TABLE_PREFIX = os.environ.get("DYNAMODB_TABLE_PREFIX", "oss-dev")
AWS_REGION = os.environ.get("AWS_REGION", "us-west-1")

OPT_TICKER_RE = re.compile(r"^(?:O:)?([A-Z]+)(\d{6})([CP])(\d+)$")

FVT_FEATURES = [
    "iv_percentile", "iv_rv_ratio", "rs_20d", "atr14_pct",
    "adx_14", "plus_di", "minus_di",
]

# Feature order MUST match gbm_scorer.FEATURE_NAMES
FEATURE_NAMES = [
    "entry_delta",
    "abs_delta",
    "dte_at_entry",
    "entry_iv",
    "entry_iv_percentile",
    "entry_iv_rv_ratio",
    "adx_14",
    "plus_di",
    "minus_di",
    "rs_20d",
    "atr14_pct",
    "theta_pct",
    "pillar_dc",
    "pillar_mp",
    "pillar_ts",
    "scanner_is_uv",
    "scanner_is_cheap",
    "scanner_is_breakdown",
    "scanner_is_revalidation",
    "option_is_call",
]


# ============================================================================
# Data loading
# ============================================================================

def _f(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_option_type(t: Optional[str]) -> Optional[str]:
    if not t:
        return None
    m = OPT_TICKER_RE.match(t)
    if not m:
        return None
    return "CALL" if m.group(3) == "C" else "PUT"


def _query_partition(table: Any, pk: str) -> list[dict]:
    items: list[dict] = []
    kwargs: dict[str, Any] = {"KeyConditionExpression": Key("PK").eq(pk)}
    while True:
        resp = table.query(**kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return items


def _fetch_fvt(fvt: Any, eval_id: str) -> dict[str, float]:
    try:
        resp = fvt.query(
            KeyConditionExpression=Key("PK").eq(f"EVAL#{eval_id}")
            & Key("SK").begins_with("FEATURE#"),
        )
    except Exception:
        return {}
    out: dict[str, float] = {}
    for item in resp.get("Items", []):
        name = str(item.get("SK", "")).replace("FEATURE#", "")
        if name in FVT_FEATURES:
            v = _f(item.get("value"))
            if v is not None:
                out[name] = v
    return out


def build_feature_vector(record: dict) -> Optional[list[float]]:
    """Extract FEATURE_NAMES values from a position + FVT record. None on any missing."""
    delta = record.get("delta")
    if delta is None:
        return None
    option_type = record.get("option_type")
    scanner = record.get("scanner_source") or "UNKNOWN"

    # Required numeric features — missing = drop row
    required = {
        "dte": record.get("dte"),
        "iv": record.get("entry_iv"),
    }
    if any(v is None for v in required.values()):
        return None

    # Optional numeric features — missing replaced with None (caller imputes means)
    def _g(key, default=None):
        v = record.get(key)
        return v if v is not None else default

    values: list[Optional[float]] = [
        float(delta),                        # entry_delta
        abs(float(delta)),                   # abs_delta
        float(required["dte"]),              # dte_at_entry
        float(required["iv"]),               # entry_iv
        _g("iv_percentile"),                 # entry_iv_percentile
        _g("iv_rv_ratio"),                   # entry_iv_rv_ratio
        _g("adx_14"),                        # adx_14
        _g("plus_di"),                       # plus_di
        _g("minus_di"),                      # minus_di
        _g("rs_20d"),                        # rs_20d
        _g("atr14_pct"),                     # atr14_pct
        _g("theta_pct"),                     # theta_pct
        _g("dc_score"),                      # pillar_dc
        _g("mp_score"),                      # pillar_mp
        _g("ts_score"),                      # pillar_ts
        1.0 if scanner == "UNUSUAL_VOLUME" else 0.0,
        1.0 if scanner == "CHEAP_OPTIONS" else 0.0,
        1.0 if scanner == "BREAKDOWN" else 0.0,
        1.0 if scanner == "REVALIDATION" else 0.0,
        1.0 if option_type == "CALL" else 0.0,
    ]
    return values  # type: ignore[return-value]


def load_training_data() -> tuple[list[list[Optional[float]]], list[int], list[int], list[str]]:
    """Load closed paper positions + FVT, return (X, y_hr, y_profit, entry_dates)."""
    print(f"Loading closed paper positions from {TABLE_PREFIX}-paper-positions...")
    dyn = boto3.resource("dynamodb", region_name=AWS_REGION)
    positions_table = dyn.Table(f"{TABLE_PREFIX}-paper-positions")
    fvt_table = dyn.Table(f"{TABLE_PREFIX}-feature-values")

    items = _query_partition(positions_table, "POS#CLOSED")
    print(f"  raw items: {len(items)}")

    rows = []
    for p in items:
        mfe = _f(p.get("max_favorable_excursion"))
        pnl = _f(p.get("current_pnl_pct"))
        if mfe is None or pnl is None:
            continue
        opt_type = str(p.get("option_type") or "").upper() or None
        if not opt_type:
            opt_type = _parse_option_type(p.get("option_ticker"))
        dte = _f(p.get("dte_at_entry"))
        if dte is None:
            try:
                exp = datetime.fromisoformat(str(p.get("expiration_date"))[:10])
                ent = datetime.fromisoformat(str(p.get("entry_date"))[:10])
                dte = (exp - ent).days
            except Exception:
                pass
        rows.append({
            "evaluation_id": str(p.get("evaluation_id") or ""),
            "entry_date": str(p.get("entry_date") or ""),
            "scanner_source": str(p.get("scanner_source") or "UNKNOWN"),
            "option_type": opt_type,
            "dte": dte,
            "delta": _f(p.get("entry_delta")),
            "entry_iv": _f(p.get("entry_iv")),
            "iv_percentile": _f(p.get("entry_iv_percentile")),
            "iv_rv_ratio": _f(p.get("entry_iv_rv_ratio")),
            "theta_pct": None,  # Derived from theta + premium but not denormalized
            "dc_score": _f(p.get("pillar_directional_conviction")),
            "mp_score": _f(p.get("pillar_move_potential")),
            "ts_score": _f(p.get("pillar_trade_structure")),
            "mfe": mfe,
            "pnl": pnl,
        })
    print(f"  usable rows: {len(rows)}")

    # FVT enrichment for features not on the position record
    eval_ids = list({r["evaluation_id"] for r in rows if r["evaluation_id"]})
    print(f"  fetching FVT for {len(eval_ids)} eval_ids...")
    enrich: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=32) as ex:
        futs = {ex.submit(_fetch_fvt, fvt_table, eid): eid for eid in eval_ids}
        done = 0
        for fut in as_completed(futs):
            enrich[futs[fut]] = fut.result()
            done += 1
            if done % 2000 == 0:
                print(f"    {done}/{len(eval_ids)}")
    for r in rows:
        feats = enrich.get(r["evaluation_id"], {})
        for fname in FVT_FEATURES:
            if r.get(fname) is None and fname in feats:
                r[fname] = feats[fname]
    print("  FVT complete")

    # Sort oldest-first for temporal split
    rows.sort(key=lambda r: r.get("entry_date") or "")

    X: list[list[Optional[float]]] = []
    y_hr: list[int] = []
    y_profit: list[int] = []
    dates: list[str] = []
    for r in rows:
        vec = build_feature_vector(r)
        if vec is None:
            continue
        X.append(vec)
        y_hr.append(1 if r["mfe"] >= 200.0 else 0)
        y_profit.append(1 if r["pnl"] > 0 else 0)
        dates.append(r.get("entry_date") or "")
    print(
        f"  feature vectors built: {len(X)} "
        f"(HR200 positives: {sum(y_hr)}, profit positives: {sum(y_profit)})"
    )
    return X, y_hr, y_profit, dates


# ============================================================================
# Training
# ============================================================================

def impute_standardize(
    X: list[list[Optional[float]]],
    means: Optional[np.ndarray] = None,
    stds: Optional[np.ndarray] = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Replace None with column mean, then standardize (zero mean, unit var).

    If means/stds supplied, use them (for holdout set — apply TRAIN's stats).
    Otherwise compute from the given X.
    """
    n = len(X)
    k = len(X[0])
    # First pass: build a dense matrix with NaN for missing
    Xd = np.full((n, k), np.nan, dtype=np.float64)
    for i in range(n):
        for j in range(k):
            v = X[i][j]
            if v is not None:
                Xd[i, j] = float(v)
    if means is None:
        means = np.nanmean(Xd, axis=0)
        # Replace any all-NaN columns with 0.0 (no signal)
        means = np.nan_to_num(means, nan=0.0)
    if stds is None:
        stds = np.nanstd(Xd, axis=0)
        # Replace NaN stds (all-NaN columns) with 1.0 BEFORE the < check
        stds = np.nan_to_num(stds, nan=1.0)
        # Avoid div by zero on zero-variance columns
        stds = np.where(stds < 1e-8, 1.0, stds)

    # Replace NaN in data with column mean (from training)
    for j in range(k):
        mask = np.isnan(Xd[:, j])
        if mask.any():
            Xd[mask, j] = means[j]
    # Standardize
    Xs = (Xd - means) / stds
    # Safety: any remaining NaN (numerical corner cases) → 0 post-standardize
    Xs = np.nan_to_num(Xs, nan=0.0, posinf=0.0, neginf=0.0)
    return Xs, means, stds


def train_one_model(
    X_train: np.ndarray, y_train: list[int],
    X_holdout: np.ndarray, y_holdout: list[int],
    target_name: str,
) -> dict:
    """Fit logistic regression + isotonic calibration, return JSON-ready model dict."""
    y_tr = np.array(y_train)
    y_ho = np.array(y_holdout)
    print(f"\n--- Training {target_name} model ---")
    print(f"    Train n={len(y_tr)}, positives={y_tr.sum()} ({y_tr.mean()*100:.2f}%)")
    print(f"    Holdout n={len(y_ho)}, positives={y_ho.sum()} ({y_ho.mean()*100:.2f}%)")

    # Logistic regression with L2 regularization (inverse strength C=1.0 default)
    lr = LogisticRegression(
        penalty="l2",
        C=1.0,
        solver="lbfgs",
        max_iter=1000,
        class_weight="balanced",
    )
    lr.fit(X_train, y_tr)

    # Holdout predictions (raw sigmoid output)
    p_holdout = lr.predict_proba(X_holdout)[:, 1]
    # Report uncalibrated metrics
    try:
        auc = roc_auc_score(y_ho, p_holdout) if len(set(y_ho.tolist())) > 1 else float("nan")
        brier = brier_score_loss(y_ho, p_holdout)
    except Exception:
        auc, brier = float("nan"), float("nan")
    print(f"    Uncalibrated: AUC={auc:.4f}, Brier={brier:.4f}")

    # Isotonic calibration on holdout
    iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
    iso.fit(p_holdout, y_ho)
    p_cal = iso.predict(p_holdout)
    try:
        auc_cal = roc_auc_score(y_ho, p_cal) if len(set(y_ho.tolist())) > 1 else float("nan")
        brier_cal = brier_score_loss(y_ho, p_cal)
    except Exception:
        auc_cal, brier_cal = float("nan"), float("nan")
    print(f"    Calibrated:   AUC={auc_cal:.4f}, Brier={brier_cal:.4f}")

    # Extract calibration as a monotonic (x, y) lookup table for pure-Python interp
    # Use the unique x-points from isotonic's internal nodes
    if hasattr(iso, "f_"):
        xs = iso.f_.x
        ys = iso.f_.y
    else:
        xs = iso.X_thresholds_
        ys = iso.y_thresholds_
    cal_table = list(zip([float(x) for x in xs], [float(y) for y in ys]))

    return {
        "target": target_name,
        "feature_names": FEATURE_NAMES,
        "intercept": float(lr.intercept_[0]),
        "coef": [float(c) for c in lr.coef_[0]],
        "n_train": int(len(y_tr)),
        "n_holdout": int(len(y_ho)),
        "positives_train": int(y_tr.sum()),
        "positives_holdout": int(y_ho.sum()),
        "auc_uncalibrated": float(auc),
        "auc_calibrated": float(auc_cal),
        "brier_uncalibrated": float(brier),
        "brier_calibrated": float(brier_cal),
        "calibration": cal_table,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "algorithm": "logistic_regression_l2_isotonic",
        "version": "v5.0.0",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", default="/tmp/v5_gbm_models",
                        help="Directory to save model JSONs")
    parser.add_argument("--holdout-fraction", type=float, default=0.20)
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    X, y_hr, y_profit, dates = load_training_data()
    n = len(X)
    if n < 500:
        print(f"ERROR: not enough training data (n={n}). Abort.")
        return 1

    # Temporal split: oldest 80% train, newest 20% holdout
    split = int(n * (1 - args.holdout_fraction))
    X_train_raw = X[:split]
    X_holdout_raw = X[split:]
    y_hr_train, y_hr_holdout = y_hr[:split], y_hr[split:]
    y_p_train, y_p_holdout = y_profit[:split], y_profit[split:]
    print(f"\nSplit: train={split} ({dates[0][:10]} to {dates[split-1][:10]}), "
          f"holdout={n-split} ({dates[split][:10]} to {dates[-1][:10]})")

    # Fit imputation + standardization on TRAIN only, apply to both
    X_train, means, stds = impute_standardize(X_train_raw)
    X_holdout, _, _ = impute_standardize(X_holdout_raw, means=means, stds=stds)

    # Train both models
    hr_model = train_one_model(X_train, y_hr_train, X_holdout, y_hr_holdout, "hr200")
    p_model = train_one_model(X_train, y_p_train, X_holdout, y_p_holdout, "profit")

    # Add shared preprocessing to each model
    for m in (hr_model, p_model):
        m["feature_means"] = [float(x) for x in means]
        m["feature_stds"] = [float(x) for x in stds]

    hr_path = out_dir / "v5_gbm_hr.json"
    p_path = out_dir / "v5_gbm_p.json"
    with open(hr_path, "w") as fp:
        json.dump(hr_model, fp, indent=2)
    with open(p_path, "w") as fp:
        json.dump(p_model, fp, indent=2)

    print(f"\nSaved HR model to: {hr_path} ({hr_path.stat().st_size} bytes)")
    print(f"Saved P model to:  {p_path} ({p_path.stat().st_size} bytes)")

    # Pretty-print top feature importances for each model
    for name, m in [("HR200", hr_model), ("Profit", p_model)]:
        print(f"\nTop features ({name}) by |standardized coefficient|:")
        coef = list(zip(m["feature_names"], m["coef"]))
        coef.sort(key=lambda c: -abs(c[1]))
        for feat, c in coef[:8]:
            sign = "+" if c > 0 else "-"
            print(f"    {feat:<24} {sign}{abs(c):.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
