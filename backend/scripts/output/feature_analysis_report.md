# Feature-Outcome Analysis & Pillar Redesign Report

Generated: 2026-04-10 03:20 UTC
Dataset: 15,505 closed positions, 15,505 with full feature data

## Executive Summary

- **Overall**: 41.7% win rate, -2.21% avg return
- **Old pillar correlations**: Dir r=-0.0296, Vol r=0.0492, Str r=0.0005, Conviction r=0.0029
- **New composite correlation**: r=0.2223 (p=0.0)
- **New composite Q1 (lowest scores)**: 24.4% WR, -28.47% avg return
- **New composite Q5 (highest scores)**: 57.0% WR, 19.28% avg return
- **Quintile spread**: 47.75 pp
- **Top 10% by new score**: 60.5% WR, 24.55% avg return
- **Bottom 10% by new score**: 18.8% WR, -34.48% avg return
- **Significant predictors**: 8 Tier 1, 11 Tier 2

---
## 1. Feature Correlation Rankings

### 1a. Tier 1 — All Positions (Numeric)

| Rank | Feature | n | r | p-value | Cohen's d | Direction | Monotonic | Q Spread |
|------|---------|---|---|---------|-----------|-----------|-----------|----------|
| 1 | **Entry Delta** | 11,834 | -0.2015 | 0.0000 | -0.4247 | lower_is_better | none | -32.8pp |
| 2 | **Days Held** | 15,505 | -0.0922 | 0.0000 | -0.1696 | lower_is_better | decreasing | -27.4pp |
| 3 | **Entry IV** | 11,834 | -0.0559 | 0.0000 | -0.1269 | lower_is_better | none | -9.2pp |
| 4 | Theta-Adj EV | 1,083 | 0.0056 | 0.8547 | -0.0769 | unclear | none | 19.6pp |
| 5 | Realized Vol 20d | 926 | -0.0224 | 0.4959 | 0.0669 | unclear | none | -6.1pp |
| 6 | **Pillar: Volatility** | 15,505 | 0.0492 | 0.0000 | 0.0572 | higher_is_better | none | 14.5pp |
| 7 | **Scanner Convergence** | 11,791 | 0.0226 | 0.0142 | 0.0501 | unclear | none | 16.1pp |
| 8 | Entry Theta | 11,834 | 0.0127 | 0.1678 | 0.0403 | unclear | none | 1.2pp |
| 9 | **DTE at Entry** | 11,834 | -0.0463 | 0.0000 | -0.0331 | lower_is_better | none | -14.9pp |
| 10 | **Pillar: Directional** | 15,505 | -0.0296 | 0.0002 | -0.0306 | unclear | none | 3.4pp |
| 11 | Pillar: Structure | 15,505 | 0.0005 | 0.9501 | -0.0266 | unclear | none | 2.8pp |
| 12 | Conviction Score | 15,505 | 0.0029 | 0.7141 | -0.0172 | unclear | none | 2.1pp |
| 13 | **Entry Price** | 15,505 | -0.0161 | 0.0452 | 0.0011 | unclear | none | -1.3pp |

### 1b. Tier 2 — Recent Positions with Full Features (Numeric)

| Rank | Feature | n | r | p-value | Cohen's d | Direction | Monotonic | Q Spread |
|------|---------|---|---|---------|-----------|-----------|-----------|----------|
| 1 | **ADX (14)** | 395 | -0.2266 | 0.0000 | -0.4087 | lower_is_better | none | -43.2pp |
| 2 | **MACD Histogram** | 395 | 0.1240 | 0.0133 | 0.3784 | higher_is_better | none | 63.9pp |
| 3 | **-DI** | 395 | 0.1686 | 0.0007 | 0.3001 | higher_is_better | none | 71.3pp |
| 4 | **SPY Return 5d** | 15,505 | 0.0839 | 0.0000 | 0.1547 | higher_is_better | none | 11.6pp |
| 5 | **Rel Strength 5d** | 15,505 | -0.0709 | 0.0000 | -0.1387 | lower_is_better | none | -7.8pp |
| 6 | +DI | 395 | 0.0726 | 0.1487 | 0.1168 | higher_is_better | none | 35.6pp |
| 7 | **Return 5d** | 15,505 | -0.0545 | 0.0000 | -0.1082 | lower_is_better | none | -4.1pp |
| 8 | **FVT Feasibility Ratio** | 15,505 | 0.0404 | 0.0000 | 0.1028 | higher_is_better | none | 15.6pp |
| 9 | **Time-Adj Feasibility** | 15,505 | 0.0452 | 0.0000 | 0.0972 | higher_is_better | none | 17.2pp |
| 10 | Theta % | 15,505 | 0.0146 | 0.0694 | -0.0733 | unclear | none | -1.1pp |
| 11 | RSI (14) | 395 | 0.0097 | 0.8474 | 0.0621 | unclear | none | 26.5pp |
| 12 | **Return 20d** | 15,505 | -0.0339 | 0.0000 | -0.0541 | lower_is_better | none | 0.0pp |
| 13 | **IV 10d Change** | 14,987 | 0.0176 | 0.0311 | 0.0333 | unclear | increasing | 16.5pp |
| 14 | SPY Return 20d | 15,505 | 0.0137 | 0.0891 | 0.0254 | unclear | none | 0.4pp |
| 15 | Expected Move % | 15,505 | -0.0073 | 0.3621 | 0.0187 | unclear | none | 6.7pp |
| 16 | Underlying Close | 15,505 | -0.0007 | 0.9299 | 0.0122 | unclear | none | 5.7pp |
| 17 | FVT Spread % | 15,505 | -0.0107 | 0.1835 | -0.0119 | unclear | none | 4.1pp |
| 18 | Required Move % | 15,505 | -0.0030 | 0.7074 | 0.0086 | unclear | none | 9.9pp |
| 19 | OI 5d Change % | 4,126 | -0.0045 | 0.7702 | -0.0026 | unclear | none | 5.0pp |
| 20 | **Option Mid Price** | 15,505 | -0.0162 | 0.0442 | 0.0011 | unclear | none | -1.3pp |

---
## 2. Quintile Analysis (Significant Features)

### Entry Delta (r=-0.2015, d=-0.4247, none)

| Quintile | Range | n | Win Rate | Avg Return | Median Return |
|----------|-------|---|----------|------------|---------------|
| Q1 | -0.9987 — -0.4989 | 2366 | 54.3% | 15.31% | 30.70% |
| Q2 | -0.4988 — -0.3095 | 2366 | 54.4% | 17.51% | 51.80% |
| Q3 | -0.3094 — 0.3417 | 2366 | 35.2% | -9.51% | -53.33% |
| Q4 | 0.3418 — 0.4964 | 2366 | 31.4% | -16.87% | -54.02% |
| Q5 | 0.4964 — 1.0000 | 2370 | 32.5% | -17.47% | -52.70% |

### Days Held (r=-0.0922, d=-0.1696, decreasing)

| Quintile | Range | n | Win Rate | Avg Return | Median Return |
|----------|-------|---|----------|------------|---------------|
| Q1 | 0.0000 — 2.0000 | 3101 | 53.5% | 16.35% | 51.94% |
| Q2 | 2.0000 — 3.0000 | 3101 | 40.1% | -2.90% | -52.17% |
| Q3 | 3.0000 — 5.0000 | 3101 | 39.9% | -5.61% | -50.79% |
| Q4 | 5.0000 — 9.0000 | 3101 | 38.7% | -7.83% | -51.49% |
| Q5 | 9.0000 — 40.0000 | 3101 | 36.4% | -11.08% | -51.72% |

### Entry IV (r=-0.0559, d=-0.1269, none)

| Quintile | Range | n | Win Rate | Avg Return | Median Return |
|----------|-------|---|----------|------------|---------------|
| Q1 | 0.0007 — 0.2717 | 2366 | 40.9% | -3.29% | -51.35% |
| Q2 | 0.2717 — 0.3336 | 2366 | 46.0% | 4.91% | -44.48% |
| Q3 | 0.3336 — 0.3994 | 2366 | 46.1% | 3.31% | -40.23% |
| Q4 | 0.3994 — 0.5114 | 2366 | 41.0% | -3.44% | -50.78% |
| Q5 | 0.5114 — 1.5320 | 2370 | 33.8% | -12.54% | -52.43% |

### Pillar: Volatility (r=0.0492, d=0.0572, none)

| Quintile | Range | n | Win Rate | Avg Return | Median Return |
|----------|-------|---|----------|------------|---------------|
| Q1 | 45.9800 — 71.0100 | 3101 | 40.2% | -6.48% | -51.34% |
| Q2 | 71.0100 — 76.0900 | 3101 | 42.5% | -2.75% | -50.80% |
| Q3 | 76.0900 — 80.0300 | 3101 | 38.3% | -6.52% | -51.64% |
| Q4 | 80.0300 — 85.9700 | 3101 | 41.3% | -3.33% | -50.94% |
| Q5 | 85.9700 — 88.0000 | 3101 | 46.5% | 8.01% | -34.78% |

### Scanner Convergence (r=0.0226, d=0.0501, none)

| Quintile | Range | n | Win Rate | Avg Return | Median Return |
|----------|-------|---|----------|------------|---------------|
| Q1 | 1.0000 — 1.0000 | 2358 | 38.5% | -8.13% | -52.33% |
| Q2 | 1.0000 — 1.0000 | 2358 | 38.9% | -3.98% | -51.20% |
| Q3 | 1.0000 — 1.0000 | 2358 | 42.0% | -2.47% | -50.57% |
| Q4 | 1.0000 — 1.0000 | 2358 | 40.1% | -4.68% | -50.61% |
| Q5 | 1.0000 — 2.0000 | 2359 | 48.1% | 7.98% | -48.60% |

### DTE at Entry (r=-0.0463, d=-0.0331, none)

| Quintile | Range | n | Win Rate | Avg Return | Median Return |
|----------|-------|---|----------|------------|---------------|
| Q1 | 7.0000 — 18.0000 | 2366 | 47.3% | 9.51% | -7.75% |
| Q2 | 18.0000 — 28.0000 | 2366 | 37.9% | -6.55% | -52.85% |
| Q3 | 28.0000 — 44.0000 | 2366 | 42.0% | -0.99% | -51.59% |
| Q4 | 44.0000 — 71.0000 | 2366 | 38.8% | -7.64% | -52.04% |
| Q5 | 71.0000 — 120.0000 | 2370 | 41.7% | -5.39% | -51.22% |

### Pillar: Directional (r=-0.0296, d=-0.0306, none)

| Quintile | Range | n | Win Rate | Avg Return | Median Return |
|----------|-------|---|----------|------------|---------------|
| Q1 | 25.5600 — 49.7100 | 3101 | 42.0% | -0.39% | -50.78% |
| Q2 | 49.7200 — 58.1300 | 3101 | 43.8% | 1.37% | -50.06% |
| Q3 | 58.1300 — 64.4500 | 3101 | 38.9% | -5.70% | -51.58% |
| Q4 | 64.4500 — 70.8300 | 3101 | 36.8% | -9.35% | -51.80% |
| Q5 | 70.8300 — 81.4500 | 3101 | 47.3% | 3.01% | -50.00% |

### Entry Price (r=-0.0161, d=0.0011, none)

| Quintile | Range | n | Win Rate | Avg Return | Median Return |
|----------|-------|---|----------|------------|---------------|
| Q1 | 0.0500 — 1.6800 | 3101 | 39.8% | -1.97% | -51.64% |
| Q2 | 1.6800 — 3.6000 | 3101 | 42.9% | 0.95% | -50.89% |
| Q3 | 3.6000 — 6.7500 | 3101 | 41.7% | -2.78% | -51.00% |
| Q4 | 6.7500 — 13.3500 | 3101 | 42.0% | -4.02% | -50.46% |
| Q5 | 13.3700 — 337.2500 | 3101 | 42.4% | -3.25% | -50.16% |

### Tier 2 Quintiles (Recent Positions)

### ADX (14) (r=-0.2266, d=-0.4087, none)

| Quintile | Range | n | Win Rate | Avg Return | Median Return |
|----------|-------|---|----------|------------|---------------|
| Q1 | 9.6310 — 13.6855 | 79 | 81.0% | 72.01% | 70.43% |
| Q2 | 13.6855 — 18.0382 | 79 | 72.2% | 67.37% | 67.92% |
| Q3 | 18.0441 — 20.2288 | 79 | 39.2% | 2.17% | -53.66% |
| Q4 | 20.2288 — 25.8669 | 79 | 73.4% | 44.83% | 61.22% |
| Q5 | 25.8669 — 65.9104 | 79 | 60.8% | 28.78% | 52.66% |

### MACD Histogram (r=0.1240, d=0.3784, none)

| Quintile | Range | n | Win Rate | Avg Return | Median Return |
|----------|-------|---|----------|------------|---------------|
| Q1 | -4.8916 — -0.0110 | 79 | 34.2% | -11.79% | -54.62% |
| Q2 | -0.0084 — 0.2215 | 79 | 64.6% | 39.85% | 61.59% |
| Q3 | 0.2215 — 0.5470 | 79 | 73.4% | 62.81% | 70.52% |
| Q4 | 0.5542 — 1.6215 | 79 | 70.9% | 72.16% | 75.31% |
| Q5 | 1.6215 — 5.3937 | 79 | 83.5% | 52.14% | 63.30% |

### -DI (r=0.1686, d=0.3001, none)

| Quintile | Range | n | Win Rate | Avg Return | Median Return |
|----------|-------|---|----------|------------|---------------|
| Q1 | 8.3638 — 21.0852 | 79 | 41.8% | -8.84% | -52.06% |
| Q2 | 21.1089 — 24.3175 | 79 | 67.1% | 59.31% | 62.82% |
| Q3 | 24.3175 — 25.7460 | 79 | 78.5% | 65.59% | 71.66% |
| Q4 | 25.7460 — 28.9858 | 79 | 67.1% | 36.64% | 56.50% |
| Q5 | 28.9858 — 47.6603 | 79 | 72.2% | 62.46% | 71.65% |

### SPY Return 5d (r=0.0839, d=0.1547, none)

| Quintile | Range | n | Win Rate | Avg Return | Median Return |
|----------|-------|---|----------|------------|---------------|
| Q1 | -3.9244 — -1.3623 | 3101 | 40.6% | -4.37% | -50.93% |
| Q2 | -1.3623 — -0.9697 | 3101 | 38.0% | -7.68% | -51.53% |
| Q3 | -0.9697 — -0.4990 | 3101 | 40.2% | -3.63% | -50.88% |
| Q4 | -0.4990 — 0.7042 | 3101 | 42.8% | -2.66% | -51.17% |
| Q5 | 0.7042 — 4.3119 | 3101 | 47.1% | 7.26% | -40.40% |

### Rel Strength 5d (r=-0.0709, d=-0.1387, none)

| Quintile | Range | n | Win Rate | Avg Return | Median Return |
|----------|-------|---|----------|------------|---------------|
| Q1 | -30.1628 — -3.4863 | 3101 | 45.2% | 3.62% | -50.00% |
| Q2 | -3.4863 — -1.0169 | 3101 | 46.4% | 5.98% | -36.24% |
| Q3 | -1.0169 — 1.4243 | 3101 | 39.7% | -6.10% | -51.41% |
| Q4 | 1.4257 — 4.9700 | 3101 | 36.0% | -10.38% | -52.18% |
| Q5 | 4.9727 — 30.5989 | 3101 | 41.4% | -4.19% | -51.32% |

### Return 5d (r=-0.0545, d=-0.1082, none)

| Quintile | Range | n | Win Rate | Avg Return | Median Return |
|----------|-------|---|----------|------------|---------------|
| Q1 | -32.8626 — -4.1365 | 3101 | 44.0% | 1.63% | -50.08% |
| Q2 | -4.1365 — -1.3057 | 3101 | 45.0% | 2.62% | -50.00% |
| Q3 | -1.3048 — 1.1287 | 3101 | 42.5% | -0.10% | -50.80% |
| Q4 | 1.1287 — 4.7746 | 3101 | 35.1% | -12.79% | -52.49% |
| Q5 | 4.7746 — 28.8039 | 3101 | 42.0% | -2.43% | -51.32% |

### FVT Feasibility Ratio (r=0.0404, d=0.1028, none)

| Quintile | Range | n | Win Rate | Avg Return | Median Return |
|----------|-------|---|----------|------------|---------------|
| Q1 | 0.0000 — 0.0000 | 3101 | 31.2% | -15.02% | -54.25% |
| Q2 | 0.0000 — 0.0000 | 3101 | 40.2% | -3.85% | -50.63% |
| Q3 | 0.0000 — 0.4142 | 3101 | 49.9% | 8.33% | -2.96% |
| Q4 | 0.4143 — 0.6366 | 3101 | 44.1% | -1.08% | -50.96% |
| Q5 | 0.6367 — 1.8681 | 3101 | 43.3% | 0.56% | -51.26% |

### Time-Adj Feasibility (r=0.0452, d=0.0972, none)

| Quintile | Range | n | Win Rate | Avg Return | Median Return |
|----------|-------|---|----------|------------|---------------|
| Q1 | 0.0000 — 0.0000 | 3101 | 31.2% | -15.02% | -54.25% |
| Q2 | 0.0000 — 0.0000 | 3101 | 40.2% | -3.85% | -50.63% |
| Q3 | 0.0000 — 0.3210 | 3101 | 50.1% | 7.68% | 10.84% |
| Q4 | 0.3210 — 0.5803 | 3101 | 43.5% | -2.02% | -51.08% |
| Q5 | 0.5807 — 1.2499 | 3101 | 43.6% | 2.15% | -51.22% |

### Return 20d (r=-0.0339, d=-0.0541, none)

| Quintile | Range | n | Win Rate | Avg Return | Median Return |
|----------|-------|---|----------|------------|---------------|
| Q1 | -35.2550 — -7.5347 | 3101 | 43.0% | -1.22% | -50.18% |
| Q2 | -7.5347 — -2.5350 | 3101 | 48.2% | 8.31% | -11.91% |
| Q3 | -2.5350 — 3.0647 | 3101 | 43.9% | 1.29% | -50.62% |
| Q4 | 3.0647 — 8.7503 | 3101 | 29.7% | -18.24% | -53.85% |
| Q5 | 8.7511 — 48.4195 | 3101 | 43.9% | -1.21% | -50.82% |

### IV 10d Change (r=0.0176, d=0.0333, increasing)

| Quintile | Range | n | Win Rate | Avg Return | Median Return |
|----------|-------|---|----------|------------|---------------|
| Q1 | -97.9000 — -12.7500 | 2997 | 35.9% | -11.28% | -51.89% |
| Q2 | -12.7400 — -4.4700 | 2997 | 42.3% | -1.94% | -50.85% |
| Q3 | -4.4700 — 3.1900 | 2997 | 43.7% | 0.82% | -50.67% |
| Q4 | 3.2000 — 12.8800 | 2997 | 42.7% | 1.76% | -50.59% |
| Q5 | 12.8800 — 2166.1800 | 2999 | 47.9% | 5.18% | -21.45% |

---
## 3. Categorical Feature Breakdown

### scanner_source *
Chi-squared p=0.0000, n=15,502

| Category | n | Win Rate | Avg Return | Median Return |
|----------|---|----------|------------|---------------|
| CHEAP_OPTIONS | 6,996 | 45.4% | 1.58% | -50.48% |
| UNUSUAL_VOLUME | 6,949 | 37.6% | -6.39% | -51.55% |
| BREAKOUT | 771 | 37.2% | -10.15% | -51.64% |
| COMPRESSION_EXPANSION | 538 | 40.5% | -4.59% | -51.38% |
| BREAKDOWN | 219 | 69.9% | 34.65% | 60.71% |
| REVALIDATION | 29 | 79.3% | 70.28% | 61.81% |

### option_type *
Chi-squared p=0.0000, n=11,834

| Category | n | Win Rate | Avg Return | Median Return |
|----------|---|----------|------------|---------------|
| CALL | 6,526 | 31.9% | -16.73% | -53.56% |
| PUT | 5,308 | 53.4% | 15.64% | 50.09% |

### dte_bucket *
Chi-squared p=0.0000, n=11,834

| Category | n | Win Rate | Avg Return | Median Return |
|----------|---|----------|------------|---------------|
| B | 4,001 | 43.8% | 2.78% | -42.37% |
| C | 3,417 | 39.9% | -5.37% | -51.93% |
| D | 3,042 | 38.2% | -9.56% | -51.91% |
| A | 1,374 | 46.3% | 7.37% | -13.94% |

### verdict_at_entry *
Chi-squared p=0.0001, n=15,505

| Category | n | Win Rate | Avg Return | Median Return |
|----------|---|----------|------------|---------------|
| WATCH | 12,302 | 40.8% | -3.36% | -51.18% |
| APPROVE | 3,203 | 45.2% | 2.20% | -50.07% |

### quality_tier_at_entry
Chi-squared p=0.7844, n=3,203

| Category | n | Win Rate | Avg Return | Median Return |
|----------|---|----------|------------|---------------|
| TIER_2 | 3,181 | 45.2% | 1.99% | -50.08% |
| TIER_3 | 18 | 55.6% | 42.16% | 66.83% |
| TIER_1 | 4 | 50.0% | -10.79% | -7.58% |

### fvt_ema_alignment
Chi-squared p=0.0746, n=395

| Category | n | Win Rate | Avg Return | Median Return |
|----------|---|----------|------------|---------------|
| MIXED | 183 | 57.9% | 35.59% | 57.60% |
| ABOVE_ALL | 93 | 81.7% | 78.00% | 63.37% |
| BULLISH_STACK | 80 | 72.5% | 38.83% | 61.26% |
| BEARISH_STACK | 34 | 44.1% | 2.79% | -51.06% |
| BELOW_ALL | 5 | 60.0% | 5.86% | 53.33% |

### fvt_obv_trend
Chi-squared p=0.7628, n=395

| Category | n | Win Rate | Avg Return | Median Return |
|----------|---|----------|------------|---------------|
| RISING | 169 | 62.1% | 31.60% | 58.80% |
| FALLING | 138 | 66.7% | 36.34% | 58.67% |
| FLAT | 88 | 69.3% | 75.49% | 68.58% |

### fvt_iv_regime *
Chi-squared p=0.0000, n=15,505

| Category | n | Win Rate | Avg Return | Median Return |
|----------|---|----------|------------|---------------|
| IV_NEUTRAL_REGIME | 3,952 | 38.8% | -6.31% | -51.41% |
| IV_TRENDING_UP | 3,864 | 47.5% | 5.74% | -29.13% |
| IV_TRENDING_DOWN | 3,851 | 36.4% | -10.43% | -51.80% |
| IV_LOW_REGIME | 2,542 | 45.8% | 5.45% | -40.51% |
| IV_HIGH_REGIME | 1,296 | 41.6% | -4.06% | -51.20% |

---
## 4. Feature Interactions (Top 20 Pairs)

| Feature A | Feature B | Both Fav WR | Both Fav Avg | A Only WR | B Only WR | Both Unfav WR | Synergy Lift |
|-----------|-----------|-------------|--------------|-----------|-----------|---------------|--------------|
| Entry Delta | Entry IV | 57.6% | 22.79% | 44.3% | 30.8% | 33.8% | +13.3pp |
| Entry Delta | Days Held | 57.3% | 24.22% | 45.4% | 35.6% | 29.6% | +11.9pp |
| Days Held | Pillar: Volatility | 50.5% | 13.12% | 40.9% | 35.9% | 39.6% | +9.6pp |
| Entry Delta | Entry Price | 53.6% | 12.80% | 47.0% | 27.1% | 37.6% | +6.6pp |
| Entry IV | Entry Price | 46.6% | 2.94% | 41.8% | 37.5% | 42.0% | +4.7pp |
| Days Held | Entry Price | 48.7% | 6.28% | 44.1% | 38.4% | 37.2% | +4.5pp |
| Pillar: Volatility | DTE at Entry | 44.3% | 5.94% | 41.1% | 40.4% | 40.0% | +3.2pp |
| Pillar: Volatility | Entry Price | 44.8% | 1.03% | 41.8% | 40.0% | 40.4% | +3.0pp |
| Entry Delta | DTE at Entry | 51.4% | 16.65% | 50.1% | 33.2% | 31.5% | +1.3pp |
| Entry IV | DTE at Entry | 44.5% | 5.17% | 43.3% | 40.4% | 38.1% | +1.3pp |
| Days Held | Entry IV | 47.0% | 9.43% | 46.1% | 41.2% | 33.9% | +0.9pp |
| Entry IV | Pillar: Volatility | 44.2% | 4.24% | 43.6% | 41.2% | 37.3% | +0.6pp |
| Pillar: Volatility | Pillar: Directional | 43.6% | -0.44% | 43.1% | 37.8% | 43.7% | +0.5pp |
| Entry Delta | Pillar: Volatility | 51.0% | 13.95% | 50.6% | 35.1% | 29.1% | +0.4pp |
| Days Held | DTE at Entry | 46.4% | 8.27% | 46.9% | 37.1% | 37.7% | -0.6pp |
| DTE at Entry | Pillar: Directional | 42.2% | -1.71% | 43.0% | 38.4% | 42.5% | -0.8pp |
| Entry IV | Pillar: Directional | 43.0% | -1.98% | 44.9% | 37.8% | 40.6% | -1.9pp |
| DTE at Entry | Entry Price | 41.0% | -3.31% | 44.4% | 41.5% | 39.2% | -3.4pp |
| Days Held | Pillar: Directional | 44.4% | 0.88% | 47.8% | 36.0% | 39.6% | -3.5pp |
| Pillar: Directional | Entry Price | 38.0% | -9.88% | 41.8% | 45.7% | 40.1% | -7.7pp |

---
## 5. Scanner Segment Analysis

### BREAKDOWN (n=219, WR=69.9%, Avg=34.65%)

| Feature | r | Cohen's d | Direction |
|---------|---|-----------|-----------|
| Days Held | -0.3888 | -0.8787 | lower_is_better |
| Pillar: Directional | -0.3346 | -0.6659 | lower_is_better |
| Scanner Convergence | -0.1688 | -0.3514 | lower_is_better |

### BREAKOUT (n=771, WR=37.2%, Avg=-10.15%)

| Feature | r | Cohen's d | Direction |
|---------|---|-----------|-----------|
| Pillar: Directional | 0.6330 | 1.8530 | higher_is_better |
| Entry Price | 0.1215 | 0.2989 | higher_is_better |
| Pillar: Volatility | 0.0758 | 0.1592 | higher_is_better |

### CHEAP_OPTIONS (n=6,996, WR=45.4%, Avg=1.58%)

| Feature | r | Cohen's d | Direction |
|---------|---|-----------|-----------|
| Entry Delta | -0.2343 | -0.4844 | lower_is_better |
| Days Held | -0.1483 | -0.2890 | lower_is_better |
| Pillar: Directional | -0.0838 | -0.1706 | lower_is_better |
| Entry IV | -0.0277 | -0.0947 | unclear |
| DTE at Entry | -0.0404 | -0.0169 | lower_is_better |

### COMPRESSION_EXPANSION (n=538, WR=40.5%, Avg=-4.59%)

| Feature | r | Cohen's d | Direction |
|---------|---|-----------|-----------|
| Days Held | -0.2024 | -0.4390 | lower_is_better |
| Entry Delta | -0.1926 | -0.4249 | lower_is_better |
| Entry Price | -0.1787 | -0.3560 | lower_is_better |
| Entry IV | -0.1287 | -0.2886 | lower_is_better |
| Pillar: Directional | 0.1208 | 0.2492 | higher_is_better |

### REVALIDATION (n=29, WR=79.3%, Avg=70.28%)

| Feature | r | Cohen's d | Direction |
|---------|---|-----------|-----------|
| DTE at Entry | 0.5507 | 1.8636 | higher_is_better |

### UNUSUAL_VOLUME (n=6,949, WR=37.6%, Avg=-6.39%)

| Feature | r | Cohen's d | Direction |
|---------|---|-----------|-----------|
| Entry Delta | -0.1901 | -0.4081 | lower_is_better |
| DTE at Entry | -0.0800 | -0.1250 | lower_is_better |
| Entry IV | -0.0597 | -0.1085 | lower_is_better |
| Pillar: Volatility | 0.0597 | 0.0780 | higher_is_better |
| Days Held | -0.0564 | -0.0766 | lower_is_better |

---
## 6. Temporal Stability

| Feature | Windows | Same-Sign % | r Range | r Mean |
|---------|---------|-------------|---------|--------|
| Days Held | 5 | 100.0% | [-0.2662, -0.0922] | -0.1662 |
| Entry IV | 5 | 100.0% | [-0.1806, -0.0470] | -0.0791 |
| Pillar: Volatility | 5 | 100.0% | [0.0492, 0.2979] | 0.1364 |
| DTE at Entry | 5 | 100.0% | [-0.0707, -0.0463] | -0.0579 |
| Scanner Convergence | 5 | 80.0% | [-0.0642, 0.0307] | 0.0028 |
| Entry Price | 5 | 80.0% | [-0.0978, 0.0990] | -0.0148 |
| Entry Delta | 5 | 60.0% | [-0.2015, 0.1255] | -0.0620 |
| Pillar: Directional | 5 | 60.0% | [-0.0398, 0.1176] | 0.0089 |

---
## 7. Proposed New Pillar System

### Contract Quality (Weight: 41.6%)

Importance: 1.1767, Subscores: 5

| Subscore | Feature | Weight | r | Cohen's d | Direction | Monotonic | Tier |
|----------|---------|--------|---|-----------|-----------|-----------|------|
| Option Type | `option_type` | 36.5% | 0.0000 | 0.4300 | categorical | categorical | tier1 |
| Entry Delta | `entry_delta` | 36.1% | -0.2015 | -0.4247 | lower_is_better | none | tier1 |
| Dte Bucket | `dte_bucket` | 13.8% | 0.0000 | 0.1620 | categorical | categorical | tier1 |
| Entry IV | `entry_iv` | 10.8% | -0.0559 | -0.1269 | lower_is_better | none | tier1 |
| DTE at Entry | `dte_at_entry` | 2.8% | -0.0463 | -0.0331 | lower_is_better | none | tier1 |

**Entry Delta breakpoints:**

| Value | Score | Quintile WR | Quintile Avg Return |
|-------|-------|-------------|---------------------|
| -0.7488 | 100 | 54.3% | 15.31% |
| -0.4042 | 80 | 54.4% | 17.51% |
| 0.0161 | 60 | 35.2% | -9.51% |
| 0.4191 | 40 | 31.4% | -16.87% |
| 0.7482 | 20 | 32.5% | -17.47% |

### Market Context (Weight: 30.3%)

Importance: 0.8569, Subscores: 7

| Subscore | Feature | Weight | r | Cohen's d | Direction | Monotonic | Tier |
|----------|---------|--------|---|-----------|-----------|-----------|------|
| Fvt Iv Regime | `fvt_iv_regime` | 25.9% | 0.0000 | 0.2220 | categorical | categorical | tier1 |
| SPY Return 5d | `fvt_spy_return_5d` | 18.1% | 0.0839 | 0.1547 | higher_is_better | none | tier2 |
| Rel Strength 5d | `fvt_rs_5d` | 16.2% | -0.0709 | -0.1387 | lower_is_better | none | tier2 |
| Return 5d | `fvt_return_5d` | 12.6% | -0.0545 | -0.1082 | lower_is_better | none | tier2 |
| FVT Feasibility Ratio | `fvt_feasibility_ratio` | 12.0% | 0.0404 | 0.1028 | higher_is_better | none | tier2 |
| Time-Adj Feasibility | `fvt_time_adjusted_feasibility` | 11.3% | 0.0452 | 0.0972 | higher_is_better | none | tier2 |
| IV 10d Change | `fvt_iv_10d_change` | 3.9% | 0.0176 | 0.0333 | unclear | increasing | tier2 |

**SPY Return 5d breakpoints:**

| Value | Score | Quintile WR | Quintile Avg Return |
|-------|-------|-------------|---------------------|
| -2.6433 | 20 | 40.6% | -4.37% |
| -1.1660 | 40 | 38.0% | -7.68% |
| -0.7344 | 60 | 40.2% | -3.63% |
| 0.1026 | 80 | 42.8% | -2.66% |
| 2.5080 | 100 | 47.1% | 7.26% |

**Rel Strength 5d breakpoints:**

| Value | Score | Quintile WR | Quintile Avg Return |
|-------|-------|-------------|---------------------|
| -16.8246 | 100 | 45.2% | 3.62% |
| -2.2516 | 80 | 46.4% | 5.98% |
| 0.2037 | 60 | 39.7% | -6.10% |
| 3.1978 | 40 | 36.0% | -10.38% |
| 17.7858 | 20 | 41.4% | -4.19% |

### Signal Quality (Weight: 28.0%)

Importance: 0.7921, Subscores: 3

| Subscore | Feature | Weight | r | Cohen's d | Direction | Monotonic | Tier |
|----------|---------|--------|---|-----------|-----------|-----------|------|
| Scanner Source | `scanner_source` | 82.6% | 0.0000 | 0.6540 | categorical | categorical | tier1 |
| Verdict At Entry | `verdict_at_entry` | 11.1% | 0.0000 | 0.0880 | categorical | categorical | tier1 |
| Scanner Convergence | `convergence_count` | 6.3% | 0.0226 | 0.0501 | unclear | none | tier1 |

**Scanner Convergence breakpoints:**

| Value | Score | Quintile WR | Quintile Avg Return |
|-------|-------|-------------|---------------------|
| 1.0000 | 20 | 38.5% | -8.13% |
| 1.0000 | 40 | 38.9% | -3.98% |
| 1.0000 | 60 | 42.0% | -2.47% |
| 1.0000 | 80 | 40.1% | -4.68% |
| 1.5000 | 100 | 48.1% | 7.98% |

### Composite Score Formula

```
composite = 0.42 x Contract Quality + 0.30 x Market Context + 0.28 x Signal Quality
```

### Tier 2 Feature Recommendations (Denormalize for Full History)

| Feature | Cohen's d | r | Recommendation |
|---------|-----------|---|----------------|
| ADX (14) | -0.4087 | -0.2266 | Denormalize onto PaperPosition for full-history analysis |
| MACD Histogram | 0.3784 | 0.1240 | Denormalize onto PaperPosition for full-history analysis |
| -DI | 0.3001 | 0.1686 | Denormalize onto PaperPosition for full-history analysis |
| SPY Return 5d | 0.1547 | 0.0839 | Denormalize onto PaperPosition for full-history analysis |
| Rel Strength 5d | -0.1387 | -0.0709 | Denormalize onto PaperPosition for full-history analysis |
| Return 5d | -0.1082 | -0.0545 | Denormalize onto PaperPosition for full-history analysis |
| FVT Feasibility Ratio | 0.1028 | 0.0404 | Denormalize onto PaperPosition for full-history analysis |
| Time-Adj Feasibility | 0.0972 | 0.0452 | Denormalize onto PaperPosition for full-history analysis |
| Return 20d | -0.0541 | -0.0339 | Denormalize onto PaperPosition for full-history analysis |
| IV 10d Change | 0.0333 | 0.0176 | Denormalize onto PaperPosition for full-history analysis |

---
## 8. Backtest Comparison

### Old vs New Pillar Correlations

| Metric | Old r | New r | Improvement |
|--------|-------|-------|-------------|
| Old Directional | -0.0296 | — | — |
| Old Volatility | 0.0492 | — | — |
| Old Structure | 0.0005 | — | — |
| Old Conviction | 0.0029 | — | — |
| New Contract Quality | — | 0.2239 | — |
| New Market Context | — | 0.1359 | — |
| New Signal Quality | — | 0.0787 | — |
| **Old Conviction (composite)** | **0.0029** | — | — |
| **New Composite** | — | **0.2223** | **+0.2194** |

### New Composite Quintile Performance

| Quintile | Range | n | Win Rate | Avg Return | Median Return |
|----------|-------|---|----------|------------|---------------|
| Q1 | 19.9 — 36.1 | 3101 | 24.4% | -28.47% | -54.72% |
| Q2 | 36.1 — 45.0 | 3101 | 37.0% | -8.47% | -52.36% |
| Q3 | 45.0 — 52.4 | 3101 | 42.6% | -0.76% | -51.02% |
| Q4 | 52.4 — 61.1 | 3101 | 47.7% | 7.35% | -20.63% |
| Q5 | 61.1 — 85.9 | 3101 | 57.0% | 19.28% | 52.17% |

### Contract Quality Quintile Performance

| Quintile | Range | n | Win Rate | Avg Return |
|----------|-------|---|----------|------------|
| Q1 | 16.4 — 29.8 | 2366 | 31.4% | -18.19% |
| Q2 | 29.8 — 34.9 | 2366 | 30.5% | -19.58% |
| Q3 | 34.9 — 69.8 | 2366 | 36.2% | -8.56% |
| Q4 | 69.8 — 80.0 | 2366 | 53.3% | 13.77% |
| Q5 | 80.0 — 95.0 | 2370 | 56.3% | 21.45% |

### Market Context Quintile Performance

| Quintile | Range | n | Win Rate | Avg Return |
|----------|-------|---|----------|------------|
| Q1 | 19.5 — 43.1 | 3101 | 30.6% | -18.58% |
| Q2 | 43.1 — 50.7 | 3101 | 38.9% | -6.81% |
| Q3 | 50.7 — 58.1 | 3101 | 42.3% | -0.57% |
| Q4 | 58.1 — 66.3 | 3101 | 45.1% | 3.98% |
| Q5 | 66.3 — 90.6 | 3101 | 51.7% | 10.92% |

### Signal Quality Quintile Performance

| Quintile | Range | n | Win Rate | Avg Return |
|----------|-------|---|----------|------------|
| Q1 | 10.0 — 23.9 | 3101 | 33.1% | -13.77% |
| Q2 | 23.9 — 24.1 | 3101 | 43.0% | 1.43% |
| Q3 | 24.1 — 50.3 | 3101 | 40.5% | -4.20% |
| Q4 | 50.3 — 52.3 | 3101 | 43.2% | -1.08% |
| Q5 | 52.3 — 85.6 | 3101 | 49.0% | 6.54% |

### Top/Bottom Decile Analysis

| Segment | n | Win Rate | Avg Return | Median Return | Avg Composite |
|---------|---|----------|------------|---------------|---------------|
| top_10pct | 1550 | 60.5% | 24.55% | 54.78% | 71.1 |
| bottom_10pct | 1550 | 18.8% | -34.48% | -54.72% | 29.0 |

### Scanner-Specific Comparison

| Scanner | n | Old Conviction r | New Composite r | Improvement |
|---------|---|------------------|-----------------|-------------|
| BREAKDOWN | 219 | -0.3523 | 0.3027 | -0.0496 |
| BREAKOUT | 771 | 0.3199 | 0.5828 | +0.2629 |
| CHEAP_OPTIONS | 6,996 | -0.0162 | 0.2096 | +0.1934 |
| COMPRESSION_EXPANSION | 538 | 0.1508 | 0.1961 | +0.0453 |
| REVALIDATION | 29 | 0.2261 | -0.1609 | -0.0652 |
| UNUSUAL_VOLUME | 6,949 | -0.0158 | 0.2094 | +0.1937 |
