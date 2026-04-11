# Feature-Outcome Analysis & Pillar Redesign Report

Generated: 2026-04-10 20:07 UTC
Dataset: 15,505 closed positions, 15,505 with full feature data

## Executive Summary

- **Overall**: 41.7% win rate, -2.21% avg return
- **Old pillar correlations**: Dir r=-0.0296, Vol r=0.0492, Str r=0.0005, Conviction r=0.0029
- **New composite correlation**: r=0.1553 (p=0.0)
- **New composite Q1 (lowest scores)**: 30.1% WR, -18.61% avg return
- **New composite Q5 (highest scores)**: 54.2% WR, 14.09% avg return
- **Quintile spread**: 32.7 pp
- **Top 10% by new score**: 58.9% WR, 18.98% avg return
- **Bottom 10% by new score**: 29.7% WR, -19.27% avg return
- **Significant predictors**: 9 Tier 1, 19 Tier 2

---
## 1. Feature Correlation Rankings

### 1a. Tier 1 — All Positions (Numeric)

| Rank | Feature | n | r | p-value | Cohen's d | Direction | Monotonic | Q Spread |
|------|---------|---|---|---------|-----------|-----------|-----------|----------|
| 1 | **Entry Delta (signed)** | 11,834 | -0.2015 | 0.0000 | -0.4247 | lower_is_better | none | -32.8pp |
| 2 | **Days Held** | 15,505 | -0.0922 | 0.0000 | -0.1696 | lower_is_better | decreasing | -27.4pp |
| 3 | **Entry IV** | 11,834 | -0.0559 | 0.0000 | -0.1269 | lower_is_better | none | -9.2pp |
| 4 | **|Entry Delta|** | 11,834 | 0.0383 | 0.0000 | 0.1189 | higher_is_better | none | 9.6pp |
| 5 | Theta-Adj EV | 1,083 | 0.0056 | 0.8547 | -0.0769 | unclear | none | 19.6pp |
| 6 | Realized Vol 20d | 926 | -0.0224 | 0.4959 | 0.0669 | unclear | none | -6.1pp |
| 7 | **Pillar: Volatility** | 15,505 | 0.0492 | 0.0000 | 0.0572 | higher_is_better | none | 14.5pp |
| 8 | **Scanner Convergence** | 11,791 | 0.0226 | 0.0142 | 0.0501 | unclear | none | 16.1pp |
| 9 | Entry Theta | 11,834 | 0.0127 | 0.1678 | 0.0403 | unclear | none | 1.2pp |
| 10 | **DTE at Entry** | 11,834 | -0.0463 | 0.0000 | -0.0331 | lower_is_better | none | -14.9pp |
| 11 | **Pillar: Directional** | 15,505 | -0.0296 | 0.0002 | -0.0306 | unclear | none | 3.4pp |
| 12 | Pillar: Structure | 15,505 | 0.0005 | 0.9501 | -0.0266 | unclear | none | 2.8pp |
| 13 | Conviction Score | 15,505 | 0.0029 | 0.7141 | -0.0172 | unclear | none | 2.1pp |
| 14 | **Entry Price** | 15,505 | -0.0161 | 0.0452 | 0.0011 | unclear | none | -1.3pp |

### 1b. Tier 2 — Recent Positions with Full Features (Numeric)

| Rank | Feature | n | r | p-value | Cohen's d | Direction | Monotonic | Q Spread |
|------|---------|---|---|---------|-----------|-----------|-----------|----------|
| 1 | **ADX (14)** | 395 | -0.2266 | 0.0000 | -0.4087 | lower_is_better | none | -43.2pp |
| 2 | **MACD Histogram** | 395 | 0.1240 | 0.0133 | 0.3784 | higher_is_better | none | 63.9pp |
| 3 | **-DI** | 395 | 0.1686 | 0.0007 | 0.3001 | higher_is_better | none | 71.3pp |
| 4 | **FVT Implied Vol** | 15,505 | -0.0987 | 0.0000 | -0.2233 | lower_is_better | none | -18.3pp |
| 5 | **Realized Vol 20d** | 15,505 | -0.0763 | 0.0000 | -0.1919 | lower_is_better | none | -11.9pp |
| 6 | **SPY Return 5d** | 15,505 | 0.0839 | 0.0000 | 0.1547 | higher_is_better | none | 11.6pp |
| 7 | **Rel Strength 5d** | 15,505 | -0.0709 | 0.0000 | -0.1387 | lower_is_better | none | -7.8pp |
| 8 | +DI | 395 | 0.0726 | 0.1487 | 0.1168 | higher_is_better | none | 35.6pp |
| 9 | **Volume** | 15,505 | -0.0469 | 0.0000 | -0.1154 | lower_is_better | none | -8.1pp |
| 10 | **Return 5d** | 15,505 | -0.0545 | 0.0000 | -0.1082 | lower_is_better | none | -4.1pp |
| 11 | **Feasibility Ratio** | 15,505 | 0.0404 | 0.0000 | 0.1028 | higher_is_better | none | 15.6pp |
| 12 | **Time-Adj Feasibility** | 15,505 | 0.0452 | 0.0000 | 0.0972 | higher_is_better | none | 17.2pp |
| 13 | **ATR % (14)** | 15,505 | -0.0329 | 0.0000 | -0.0963 | lower_is_better | none | -6.1pp |
| 14 | **Open Interest** | 15,505 | -0.0410 | 0.0000 | -0.0840 | lower_is_better | none | -11.7pp |
| 15 | Theta % | 15,505 | 0.0146 | 0.0694 | -0.0733 | unclear | none | -1.1pp |
| 16 | **IV Percentile** | 13,975 | -0.0479 | 0.0000 | -0.0627 | lower_is_better | none | -10.5pp |
| 17 | RSI (14) | 395 | 0.0097 | 0.8474 | 0.0621 | unclear | none | 26.5pp |
| 18 | **Rel Strength 20d** | 15,505 | -0.0371 | 0.0000 | -0.0598 | lower_is_better | none | -5.3pp |
| 19 | **Return 20d** | 15,505 | -0.0339 | 0.0000 | -0.0541 | lower_is_better | none | 0.0pp |
| 20 | Theta-Adjusted Edge | 15,505 | 0.0067 | 0.4016 | 0.0464 | unclear | none | 5.0pp |
| 21 | **IV 10d Change** | 14,987 | 0.0176 | 0.0311 | 0.0333 | unclear | increasing | 16.5pp |
| 22 | **IV/RV Ratio** | 15,505 | -0.0336 | 0.0000 | -0.0278 | lower_is_better | none | -6.3pp |
| 23 | SPY Return 20d | 15,505 | 0.0137 | 0.0891 | 0.0254 | unclear | none | 0.4pp |
| 24 | Expected Move % | 15,505 | -0.0073 | 0.3621 | 0.0187 | unclear | none | 6.7pp |
| 25 | Underlying Close | 15,505 | -0.0007 | 0.9299 | 0.0122 | unclear | none | 5.7pp |
| 26 | Spread % | 15,505 | -0.0107 | 0.1835 | -0.0119 | unclear | none | 4.1pp |
| 27 | Required Move % | 15,505 | -0.0030 | 0.7074 | 0.0086 | unclear | none | 9.9pp |
| 28 | OI 5d Change % | 4,126 | -0.0045 | 0.7702 | -0.0026 | unclear | none | 5.0pp |
| 29 | **Option Mid Price** | 15,505 | -0.0162 | 0.0442 | 0.0011 | unclear | none | -1.3pp |

---
## 2. Quintile Analysis (Significant Features)

### Entry Delta (signed) (r=-0.2015, d=-0.4247, none)

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

### |Entry Delta| (r=0.0383, d=0.1189, none)

| Quintile | Range | n | Win Rate | Avg Return | Median Return |
|----------|-------|---|----------|------------|---------------|
| Q1 | 0.0099 — 0.3303 | 2366 | 36.5% | -7.32% | -53.00% |
| Q2 | 0.3304 — 0.4261 | 2366 | 40.9% | -1.88% | -51.85% |
| Q3 | 0.4261 — 0.4974 | 2366 | 43.3% | -0.01% | -51.03% |
| Q4 | 0.4974 — 0.6115 | 2366 | 41.2% | -4.11% | -50.96% |
| Q5 | 0.6116 — 1.0000 | 2370 | 45.8% | 2.25% | -10.55% |

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

### FVT Implied Vol (r=-0.0987, d=-0.2233, none)

| Quintile | Range | n | Win Rate | Avg Return | Median Return |
|----------|-------|---|----------|------------|---------------|
| Q1 | 0.0004 — 0.2676 | 3101 | 42.3% | -1.43% | -50.89% |
| Q2 | 0.2676 — 0.3268 | 3101 | 47.3% | 5.55% | -39.26% |
| Q3 | 0.3269 — 0.3967 | 3101 | 46.4% | 4.33% | -37.50% |
| Q4 | 0.3967 — 0.5154 | 3101 | 44.0% | 0.20% | -50.24% |
| Q5 | 0.5154 — 1.5320 | 3101 | 28.7% | -19.71% | -53.10% |

### Realized Vol 20d (r=-0.0763, d=-0.1919, none)

| Quintile | Range | n | Win Rate | Avg Return | Median Return |
|----------|-------|---|----------|------------|---------------|
| Q1 | 0.0198 — 0.2864 | 3101 | 42.2% | -3.04% | -51.25% |
| Q2 | 0.2864 — 0.3538 | 3101 | 43.5% | 0.32% | -50.13% |
| Q3 | 0.3538 — 0.4531 | 3101 | 48.0% | 5.75% | -28.90% |
| Q4 | 0.4533 — 0.6039 | 3101 | 42.7% | 0.82% | -50.37% |
| Q5 | 0.6039 — 1.8738 | 3101 | 32.3% | -14.93% | -52.64% |

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

### Volume (r=-0.0469, d=-0.1154, none)

| Quintile | Range | n | Win Rate | Avg Return | Median Return |
|----------|-------|---|----------|------------|---------------|
| Q1 | 50.0000 — 126.0000 | 3101 | 43.4% | -1.32% | -50.67% |
| Q2 | 126.0000 — 220.0000 | 3101 | 45.7% | 3.08% | -50.00% |
| Q3 | 220.0000 — 418.0000 | 3101 | 43.6% | 0.96% | -50.53% |
| Q4 | 419.0000 — 974.0000 | 3101 | 40.0% | -4.40% | -50.96% |
| Q5 | 974.0000 — 74098.0000 | 3101 | 36.1% | -9.38% | -51.79% |

### Return 5d (r=-0.0545, d=-0.1082, none)

| Quintile | Range | n | Win Rate | Avg Return | Median Return |
|----------|-------|---|----------|------------|---------------|
| Q1 | -32.8626 — -4.1365 | 3101 | 44.0% | 1.63% | -50.08% |
| Q2 | -4.1365 — -1.3057 | 3101 | 45.0% | 2.62% | -50.00% |
| Q3 | -1.3048 — 1.1287 | 3101 | 42.5% | -0.10% | -50.80% |
| Q4 | 1.1287 — 4.7746 | 3101 | 35.1% | -12.79% | -52.49% |
| Q5 | 4.7746 — 28.8039 | 3101 | 42.0% | -2.43% | -51.32% |

### Feasibility Ratio (r=0.0404, d=0.1028, none)

| Quintile | Range | n | Win Rate | Avg Return | Median Return |
|----------|-------|---|----------|------------|---------------|
| Q1 | 0.0000 — 0.0000 | 3101 | 31.2% | -15.02% | -54.25% |
| Q2 | 0.0000 — 0.0000 | 3101 | 40.2% | -3.85% | -50.63% |
| Q3 | 0.0000 — 0.4142 | 3101 | 49.9% | 8.33% | -2.96% |
| Q4 | 0.4143 — 0.6366 | 3101 | 44.1% | -1.08% | -50.96% |
| Q5 | 0.6367 — 1.8681 | 3101 | 43.3% | 0.56% | -51.26% |

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
| Entry Delta (signed) | Entry IV | 57.6% | 22.79% | 44.3% | 30.8% | 33.8% | +13.3pp |
| Entry Delta (signed) | Days Held | 57.3% | 24.22% | 45.4% | 35.6% | 29.6% | +11.9pp |
| Days Held | Pillar: Volatility | 50.5% | 13.12% | 40.9% | 35.9% | 39.6% | +9.6pp |
| |Entry Delta| | Pillar: Volatility | 46.9% | 5.43% | 39.8% | 38.7% | 40.5% | +7.1pp |
| Entry Delta (signed) | |Entry Delta| | 54.2% | 15.38% | 47.4% | 32.8% | 31.8% | +6.8pp |
| Entry Delta (signed) | Entry Price | 53.6% | 12.80% | 47.0% | 27.1% | 37.6% | +6.6pp |
| Entry IV | |Entry Delta| | 46.4% | 3.48% | 41.5% | 40.7% | 37.6% | +4.8pp |
| Entry IV | Entry Price | 46.6% | 2.94% | 41.8% | 37.5% | 42.0% | +4.7pp |
| Days Held | Entry Price | 48.7% | 6.28% | 44.1% | 38.4% | 37.2% | +4.5pp |
| Days Held | |Entry Delta| | 48.2% | 7.86% | 45.0% | 39.8% | 35.0% | +3.2pp |
| Pillar: Volatility | DTE at Entry | 44.3% | 5.94% | 41.1% | 40.4% | 40.0% | +3.2pp |
| Pillar: Volatility | Entry Price | 44.8% | 1.03% | 41.8% | 40.0% | 40.4% | +3.0pp |
| Entry Delta (signed) | DTE at Entry | 51.4% | 16.65% | 50.1% | 33.2% | 31.5% | +1.3pp |
| Entry IV | DTE at Entry | 44.5% | 5.17% | 43.3% | 40.4% | 38.1% | +1.3pp |
| Days Held | Entry IV | 47.0% | 9.43% | 46.1% | 41.2% | 33.9% | +0.9pp |
| Entry IV | Pillar: Volatility | 44.2% | 4.24% | 43.6% | 41.2% | 37.3% | +0.6pp |
| Pillar: Volatility | Pillar: Directional | 43.6% | -0.44% | 43.1% | 37.8% | 43.7% | +0.5pp |
| Entry Delta (signed) | Pillar: Volatility | 51.0% | 13.95% | 50.6% | 35.1% | 29.1% | +0.4pp |
| Days Held | DTE at Entry | 46.4% | 8.27% | 46.9% | 37.1% | 37.7% | -0.6pp |
| |Entry Delta| | DTE at Entry | 43.3% | 0.58% | 43.9% | 41.5% | 38.5% | -0.6pp |

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
| Entry Delta (signed) | -0.2343 | -0.4844 | lower_is_better |
| Days Held | -0.1483 | -0.2890 | lower_is_better |
| Pillar: Directional | -0.0838 | -0.1706 | lower_is_better |
| |Entry Delta| | 0.0351 | 0.0972 | higher_is_better |
| Entry IV | -0.0277 | -0.0947 | unclear |

### COMPRESSION_EXPANSION (n=538, WR=40.5%, Avg=-4.59%)

| Feature | r | Cohen's d | Direction |
|---------|---|-----------|-----------|
| Days Held | -0.2024 | -0.4390 | lower_is_better |
| Entry Delta (signed) | -0.1926 | -0.4249 | lower_is_better |
| Entry Price | -0.1787 | -0.3560 | lower_is_better |
| Entry IV | -0.1287 | -0.2886 | lower_is_better |
| Pillar: Directional | 0.1208 | 0.2492 | higher_is_better |

### REVALIDATION (n=29, WR=79.3%, Avg=70.28%)

| Feature | r | Cohen's d | Direction |
|---------|---|-----------|-----------|
| DTE at Entry | 0.5507 | 1.8636 | higher_is_better |
| |Entry Delta| | -0.3833 | -0.5597 | lower_is_better |

### UNUSUAL_VOLUME (n=6,949, WR=37.6%, Avg=-6.39%)

| Feature | r | Cohen's d | Direction |
|---------|---|-----------|-----------|
| Entry Delta (signed) | -0.1901 | -0.4081 | lower_is_better |
| |Entry Delta| | 0.0541 | 0.1723 | higher_is_better |
| DTE at Entry | -0.0800 | -0.1250 | lower_is_better |
| Entry IV | -0.0597 | -0.1085 | lower_is_better |
| Pillar: Volatility | 0.0597 | 0.0780 | higher_is_better |

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
| Entry Delta (signed) | 5 | 60.0% | [-0.2015, 0.1255] | -0.0620 |
| |Entry Delta| | 5 | 60.0% | [-0.1394, 0.1315] | 0.0022 |
| Pillar: Directional | 5 | 60.0% | [-0.0398, 0.1176] | 0.0089 |

---
## 7. Proposed New Pillar System

### Premium Leverage (Weight: 26.0%)

Importance: 0.4327, Subscores: 4

| Subscore | Feature | Weight | r | Cohen's d | Direction | Monotonic | Tier |
|----------|---------|--------|---|-----------|-----------|-----------|------|
| FVT Implied Vol | `fvt_iv` | 51.6% | -0.0987 | -0.2233 | lower_is_better | none | tier2 |
| |Entry Delta| | `abs_entry_delta` | 27.5% | 0.0383 | 0.1189 | higher_is_better | none | tier1 |
| IV Percentile | `fvt_iv_percentile` | 14.5% | -0.0479 | -0.0627 | lower_is_better | none | tier2 |
| IV/RV Ratio | `fvt_iv_rv_ratio` | 6.4% | -0.0336 | -0.0278 | lower_is_better | none | tier2 |

**FVT Implied Vol breakpoints:**

| Value | Score | Quintile WR | Quintile Avg Return |
|-------|-------|-------------|---------------------|
| 0.1340 | 67.9 | 42.3% | -1.43% |
| 0.2972 | 90.0 | 47.3% | 5.55% |
| 0.3618 | 86.1 | 46.4% | 4.33% |
| 0.4560 | 73.1 | 44.0% | 0.20% |
| 1.0237 | 10.0 | 28.7% | -19.71% |

**|Entry Delta| breakpoints:**

| Value | Score | Quintile WR | Quintile Avg Return |
|-------|-------|-------------|---------------------|
| 0.1701 | 10.0 | 36.5% | -7.32% |
| 0.3782 | 55.5 | 40.9% | -1.88% |
| 0.4617 | 71.1 | 43.3% | -0.01% |
| 0.5544 | 36.8 | 41.2% | -4.11% |
| 0.8058 | 90.0 | 45.8% | 2.25% |

**IV Percentile breakpoints:**

| Value | Score | Quintile WR | Quintile Avg Return |
|-------|-------|-------------|---------------------|
| 5.5550 | 90.0 | 47.5% | 7.74% |
| 21.1800 | 47.6 | 42.0% | -0.51% |
| 40.2500 | 10.0 | 38.2% | -7.83% |
| 59.2400 | 33.5 | 40.9% | -3.25% |
| 77.1150 | 35.8 | 43.0% | -2.80% |

### Underlying Behavior (Weight: 53.9%)

Importance: 0.8969, Subscores: 5

| Subscore | Feature | Weight | r | Cohen's d | Direction | Monotonic | Tier |
|----------|---------|--------|---|-----------|-----------|-----------|------|
| ADX (14) | `fvt_adx_14` | 45.6% | -0.2266 | -0.4087 | lower_is_better | none | tier2 |
| Realized Vol 20d | `fvt_rv20` | 21.4% | -0.0763 | -0.1919 | lower_is_better | none | tier2 |
| Feasibility Ratio | `fvt_feasibility_ratio` | 11.5% | 0.0404 | 0.1028 | higher_is_better | none | tier2 |
| Time-Adj Feasibility | `fvt_time_adjusted_feasibility` | 10.8% | 0.0452 | 0.0972 | higher_is_better | none | tier2 |
| ATR % (14) | `fvt_atr14_pct` | 10.7% | -0.0329 | -0.0963 | lower_is_better | none | tier2 |

**ADX (14) breakpoints:**

| Value | Score | Quintile WR | Quintile Avg Return |
|-------|-------|-------------|---------------------|
| 11.6582 | 90.0 | 81.0% | 72.01% |
| 15.8619 | 84.7 | 72.2% | 67.37% |
| 19.1364 | 10.0 | 39.2% | 2.17% |
| 23.0479 | 58.9 | 73.4% | 44.83% |
| 45.8886 | 40.5 | 60.8% | 28.78% |

**Realized Vol 20d breakpoints:**

| Value | Score | Quintile WR | Quintile Avg Return |
|-------|-------|-------------|---------------------|
| 0.1531 | 56.0 | 42.2% | -3.04% |
| 0.3201 | 69.0 | 43.5% | 0.32% |
| 0.4034 | 90.0 | 48.0% | 5.75% |
| 0.5286 | 70.9 | 42.7% | 0.82% |
| 1.2389 | 10.0 | 32.3% | -14.93% |

**Feasibility Ratio breakpoints:**

| Value | Score | Quintile WR | Quintile Avg Return |
|-------|-------|-------------|---------------------|
| 0.0000 | 10.0 | 31.2% | -15.02% |
| 0.0000 | 48.3 | 40.2% | -3.85% |
| 0.2071 | 90.0 | 49.9% | 8.33% |
| 0.5254 | 57.8 | 44.1% | -1.08% |
| 1.2524 | 63.4 | 43.3% | 0.56% |

### Setup Quality (Weight: 20.1%)

Importance: 0.3342, Subscores: 4

| Subscore | Feature | Weight | r | Cohen's d | Direction | Monotonic | Tier |
|----------|---------|--------|---|-----------|-----------|-----------|------|
| Volume | `fvt_volume` | 34.5% | -0.0469 | -0.1154 | lower_is_better | none | tier2 |
| Open Interest | `fvt_open_interest` | 25.1% | -0.0410 | -0.0840 | lower_is_better | none | tier2 |
| Scanner Convergence | `convergence_count` | 15.0% | 0.0226 | 0.0501 | unclear | none | tier1 |
| Dte Bucket | `dte_bucket` | 25.3% | 0.0000 | 0.0000 | categorical | categorical | tier1 |

**Volume breakpoints:**

| Value | Score | Quintile WR | Quintile Avg Return |
|-------|-------|-------------|---------------------|
| 88.0000 | 61.7 | 43.4% | -1.32% |
| 173.0000 | 90.0 | 45.7% | 3.08% |
| 319.0000 | 76.4 | 43.6% | 0.96% |
| 696.5000 | 42.0 | 40.0% | -4.40% |
| 37536.0000 | 10.0 | 36.1% | -9.38% |

**Open Interest breakpoints:**

| Value | Score | Quintile WR | Quintile Avg Return |
|-------|-------|-------------|---------------------|
| 295.5000 | 90.0 | 45.0% | 3.26% |
| 792.5000 | 31.9 | 39.3% | -5.25% |
| 1534.0000 | 69.3 | 43.5% | 0.23% |
| 3350.5000 | 61.9 | 43.0% | -0.85% |
| 91380.0000 | 10.0 | 37.8% | -8.46% |

**Scanner Convergence breakpoints:**

| Value | Score | Quintile WR | Quintile Avg Return |
|-------|-------|-------------|---------------------|
| 1.0000 | 10.0 | 38.5% | -8.13% |
| 1.0000 | 30.6 | 38.9% | -3.98% |
| 1.0000 | 38.1 | 42.0% | -2.47% |
| 1.0000 | 27.1 | 40.1% | -4.68% |
| 1.5000 | 90.0 | 48.1% | 7.98% |

### Composite Score Formula

```
composite = 0.26 x Premium Leverage + 0.54 x Underlying Behavior + 0.20 x Setup Quality
```

---
## 8. Backtest Comparison

### Old vs New Pillar Correlations

| Metric | Old r | New r | Improvement |
|--------|-------|-------|-------------|
| Old Directional | -0.0296 | — | — |
| Old Volatility | 0.0492 | — | — |
| Old Structure | 0.0005 | — | — |
| Old Conviction | 0.0029 | — | — |
| New Premium Leverage | — | 0.1407 | — |
| New Underlying Behavior | — | 0.1156 | — |
| New Setup Quality | — | 0.0918 | — |
| **Old Conviction (composite)** | **0.0029** | — | — |
| **New Composite** | — | **0.1553** | **+0.1524** |

### New Composite Quintile Performance

| Quintile | Range | n | Win Rate | Avg Return | Median Return |
|----------|-------|---|----------|------------|---------------|
| Q1 | 25.7 — 50.4 | 3101 | 30.1% | -18.61% | -53.70% |
| Q2 | 50.4 — 56.0 | 3101 | 35.7% | -9.96% | -52.14% |
| Q3 | 56.0 — 62.1 | 3101 | 41.0% | -2.26% | -50.48% |
| Q4 | 62.1 — 67.8 | 3101 | 47.6% | 5.67% | -50.00% |
| Q5 | 67.8 — 84.5 | 3101 | 54.2% | 14.09% | 51.54% |

### Premium Leverage Quintile Performance

| Quintile | Range | n | Win Rate | Avg Return |
|----------|-------|---|----------|------------|
| Q1 | 10.0 — 55.0 | 3101 | 28.2% | -20.87% |
| Q2 | 55.0 — 62.9 | 3101 | 40.1% | -4.89% |
| Q3 | 62.9 — 67.9 | 3101 | 43.5% | 1.26% |
| Q4 | 67.9 — 73.0 | 3101 | 48.9% | 6.19% |
| Q5 | 73.0 — 89.4 | 3101 | 48.0% | 7.24% |

### Underlying Behavior Quintile Performance

| Quintile | Range | n | Win Rate | Avg Return |
|----------|-------|---|----------|------------|
| Q1 | 15.6 — 46.4 | 3101 | 36.4% | -9.97% |
| Q2 | 46.4 — 53.0 | 3101 | 32.6% | -12.95% |
| Q3 | 53.0 — 64.8 | 3101 | 39.3% | -4.70% |
| Q4 | 64.9 — 71.7 | 3101 | 47.5% | 5.14% |
| Q5 | 71.7 — 89.0 | 3101 | 53.0% | 11.41% |

### Setup Quality Quintile Performance

| Quintile | Range | n | Win Rate | Avg Return |
|----------|-------|---|----------|------------|
| Q1 | 12.9 — 43.1 | 3101 | 36.4% | -10.99% |
| Q2 | 43.1 — 49.1 | 3101 | 38.0% | -8.55% |
| Q3 | 49.1 — 55.3 | 3101 | 42.4% | -0.58% |
| Q4 | 55.3 — 63.4 | 3101 | 43.6% | 2.01% |
| Q5 | 63.4 — 89.1 | 3101 | 48.2% | 7.05% |

### Top/Bottom Decile Analysis

| Segment | n | Win Rate | Avg Return | Median Return | Avg Composite |
|---------|---|----------|------------|---------------|---------------|
| top_10pct | 1550 | 58.9% | 18.98% | 52.96% | 73.6 |
| bottom_10pct | 1550 | 29.7% | -19.27% | -53.80% | 42.9 |

### Scanner-Specific Comparison

| Scanner | n | Old Conviction r | New Composite r | Improvement |
|---------|---|------------------|-----------------|-------------|
| BREAKDOWN | 219 | -0.3523 | 0.2869 | -0.0654 |
| BREAKOUT | 771 | 0.3199 | 0.6995 | +0.3797 |
| CHEAP_OPTIONS | 6,996 | -0.0162 | 0.1366 | +0.1204 |
| COMPRESSION_EXPANSION | 538 | 0.1508 | 0.0961 | -0.0546 |
| REVALIDATION | 29 | 0.2261 | 0.5529 | +0.3268 |
| UNUSUAL_VOLUME | 6,949 | -0.0158 | 0.1145 | +0.0987 |
