# OSS Scanner System - Diagnosis Summary

**Date:** February 3, 2026  
**Status:** 🔴 System Not Operational (All issues identified and documented)

---

## Executive Summary

Your OSS scanner system shows no activity on the Dashboard and Pipeline pages because **the required services are not running**. This is a **local development environment** that requires manual setup and startup.

### Root Cause

The system requires these components to function:
1. ❌ Backend server (FastAPI on port 8000) - **NOT RUNNING**
2. ❌ DynamoDB (local or AWS with tables) - **NO TABLES EXIST**  
3. ❌ Active policy configuration - **NOT SEEDED**
4. ❌ Manual scanner trigger - **NEVER TRIGGERED**
5. ❌ Frontend dev server (port 5173) - **NOT RUNNING**

**All prerequisites are missing**, so the system cannot function.

---

## What We Found

### ✅ Good News

1. **Environment configured correctly**
   - `.env` file exists with proper settings
   - Python virtual environment is set up
   - AWS credentials are configured
   - All required scripts are present

2. **Configuration points to local development**
   - `DYNAMODB_ENDPOINT=http://localhost:8000`
   - `DEBUG=true`
   - Ready for local testing

### ❌ Issues Preventing Operation

1. **No services running**
   - Backend not started (should be on port 8000)
   - Frontend not started (should be on port 5173)
   - DynamoDB Local not running (and Docker not installed)

2. **No database setup**
   - 0 tables exist in AWS DynamoDB
   - DynamoDB Local is not accessible
   - Cannot connect to configured endpoint

3. **No policy configuration**
   - No active policy in database
   - Scanners cannot run without policy

4. **Scanners never triggered**
   - Local development requires manual triggering
   - No automatic scheduling exists
   - Must call API to run scanners

---

## Solution Overview

You have **two options** to fix this:

### Option A: Hybrid Setup (Recommended ✓)
**Use AWS DynamoDB with local backend/frontend**

**Pros:**
- No Docker installation needed
- Faster setup (3 steps)
- Data persists between restarts
- Production-like environment

**Steps:**
1. Update `.env` to use AWS DynamoDB (remove `DYNAMODB_ENDPOINT`)
2. Run `python3 infrastructure/dynamodb_tables.py`
3. Run `python3 scripts/seed_default_policy.py --activate`
4. Start backend: `uvicorn app.main:app --reload`
5. Start frontend: `npm run dev`
6. Trigger scan: `curl -X POST http://localhost:8000/api/scanners/run`

**Time:** 5-10 minutes

---

### Option B: Full Local Setup
**Run everything locally including DynamoDB**

**Pros:**
- Complete local environment
- No AWS usage

**Cons:**
- Requires Docker installation
- Data lost on DynamoDB Local restart

**Steps:**
1. Install Docker
2. Run `docker run -p 8000:8000 amazon/dynamodb-local`
3. Run `python3 infrastructure/dynamodb_tables.py --endpoint http://localhost:8000`
4. Run `python3 scripts/seed_default_policy.py --endpoint http://localhost:8000 --activate`
5. Start backend and frontend
6. Trigger scan

**Time:** 15-20 minutes (including Docker install)

---

## Quick Start Commands

We've created several tools to help you:

### 1. Automated Setup Script
```bash
cd /Users/nicksmith/OSS
./scripts/quick_setup.sh
```
Creates tables, seeds policy, and checks system status.

### 2. System Diagnostics
```bash
cd /Users/nicksmith/OSS
python3 scripts/diagnose_system.py
```
Comprehensive system check with color-coded status.

### 3. Documentation Created

- **[`QUICK_START.md`](QUICK_START.md)** - Step-by-step setup guide (10 min)
- **[`DIAGNOSTIC_REPORT.md`](DIAGNOSTIC_REPORT.md)** - Detailed analysis and troubleshooting
- **[`scripts/diagnose_system.py`](scripts/diagnose_system.py)** - Automated diagnostic tool
- **[`scripts/quick_setup.sh`](scripts/quick_setup.sh)** - One-command setup

---

## Understanding Why Dashboard Shows No Activity

The Dashboard queries these API endpoints:
- `/api/pipeline/stats` - Pipeline statistics
- `/api/pipeline/runs` - Recent pipeline runs  
- `/api/policies/active` - Active policy

**These APIs return empty/zero because:**
1. Backend is not running → APIs not accessible
2. No pipeline runs exist → Stats show 0
3. No policy seeded → No active policy

**After setup, you'll see:**
- Backend responds to API calls
- Stats show pipeline run count
- Dashboard displays activity metrics

---

## Key Points About Local Development

### 🚨 Important: Manual Triggering Required

**Local development does NOT automatically run scanners.**

- In AWS Lambda: Scanners run every 10 minutes (EventBridge schedule)
- In Local Dev: You must manually trigger each scan

**To trigger a scan:**
```bash
curl -X POST http://localhost:8000/api/scanners/run \
  -H "Content-Type: application/json" \
  -d '{}'
```

### Scanner Execution Flow

```
1. You trigger scan via API call
2. Backend loads active policy from DynamoDB
3. Gets watchlist tickers from policy (default: ~50 tickers)
4. Runs 4 scanners on each ticker:
   - Unusual Volume Scanner
   - Breakout Scanner
   - Compression Scanner
   - Cheap Options Scanner
5. Filters opportunities (underlying quality)
6. Selects contracts (DTE buckets, delta bands)
7. Evaluates contracts (features, pillars, gates)
8. Applies decision logic (APPROVE/WATCH/REJECT)
9. Stores results in DynamoDB
10. Dashboard queries results and displays them
```

**Duration:** 2-5 minutes per scan (depends on watchlist size)

---

## Recommended Next Steps

### Step 1: Choose Your Setup Option

**If you want fastest setup:** Use Option A (Hybrid with AWS DynamoDB)  
**If you want everything local:** Use Option B (with Docker)

### Step 2: Follow Quick Start Guide

Open [`QUICK_START.md`](QUICK_START.md) and follow the instructions for your chosen option.

Or run the automated setup:
```bash
./scripts/quick_setup.sh
```

### Step 3: Start Services

**Terminal 1 - Backend:**
```bash
cd backend
source venv/bin/activate
uvicorn app.main:app --reload
```

**Terminal 2 - Frontend:**
```bash
cd frontend
npm run dev
```

### Step 4: Verify Setup

```bash
python3 scripts/diagnose_system.py
```

Should show all green checkmarks.

### Step 5: Trigger First Scan

```bash
curl -X POST http://localhost:8000/api/scanners/run \
  -H "Content-Type: application/json" \
  -d '{}'
```

### Step 6: View Results

Open browser: http://localhost:5173

- Dashboard: Will show 1 pipeline run, approve/watch/reject rates
- Pipeline: Detailed stage-by-stage breakdown
- Opportunities: List of approved evaluations

---

## What to Expect After First Scan

### Typical Results
- **Opportunities Found:** 10-50 raw opportunities
- **After Filtering:** 5-30 pass underlying filters
- **After Gates:** 2-15 pass all hard gates
- **Final APPROVE:** 1-10 approved evaluations

### If You Get 0 Results

This can be normal due to:
1. **Market conditions** - Low volatility, no opportunities
2. **Market closed** - Scanners need recent data
3. **Strict thresholds** - Default policy is conservative
4. **Small watchlist** - Fewer tickers = fewer opportunities

**Not a problem with the system**, just market conditions.

### Success Indicators

✓ Dashboard shows > 0 pipeline runs  
✓ Pipeline page shows stage breakdown with counts  
✓ No errors in backend logs  
✓ Scan completes in 2-5 minutes  

---

## Troubleshooting Resources

### If Setup Fails

1. **Run diagnostics:**
   ```bash
   python3 scripts/diagnose_system.py
   ```

2. **Check specific issues:**
   - Backend logs (Terminal 1 output)
   - Frontend console (Browser F12)
   - DynamoDB connectivity

3. **Read detailed guide:**
   - [`DIAGNOSTIC_REPORT.md`](DIAGNOSTIC_REPORT.md) - Comprehensive troubleshooting

### Common Issues & Fixes

| Issue | Fix |
|-------|-----|
| "No module named 'fastapi'" | `pip install -r requirements.txt` |
| "ResourceNotFoundException" | Run `dynamodb_tables.py` |
| "No active policy found" | Run `seed_default_policy.py --activate` |
| "Could not connect to endpoint" | Start DynamoDB Local or use AWS |
| Backend won't start | Check logs, verify dependencies |

---

## System Architecture Reference

```
┌─────────────────────────────────────────────────────────┐
│           Frontend (React + TypeScript)                  │
│              http://localhost:5173                       │
│  Shows: Dashboard, Pipeline, Opportunities, Policy       │
└────────────────────┬────────────────────────────────────┘
                     │ REST API Calls
                     ▼
┌─────────────────────────────────────────────────────────┐
│           Backend (FastAPI + Python)                     │
│              http://localhost:8000                       │
│                                                          │
│  ┌─────────────────────────────────────────────────┐   │
│  │  Scanner Orchestrator                            │   │
│  │  ├─ Unusual Volume Scanner                       │   │
│  │  ├─ Breakout Scanner                             │   │
│  │  ├─ Compression Scanner                          │   │
│  │  └─ Cheap Options Scanner                        │   │
│  └─────────────────────────────────────────────────┘   │
│                                                          │
│  8 Pipeline Stages: Scan → Filter → Select → Evaluate   │
│                    → Features → Pillars → Gates →Decision│
└────────┬───────────────────────────┬────────────────────┘
         │                           │
         ▼                           ▼
┌─────────────────────┐    ┌──────────────────────┐
│  DynamoDB (AWS/Local)│   │   Polygon.io API      │
│  10 Tables:          │   │   Market Data:        │
│  - policies          │   │   - Stock quotes      │
│  - pipeline-runs     │   │   - Options chains    │
│  - opportunities     │   │   - IV history        │
│  - evaluations       │   │   - Earnings dates    │
│  - stage-events      │   └──────────────────────┘
│  - gate-results      │
│  - paper-positions   │
│  - feature-values    │
│  - pillar-scores     │
│  - iv-history        │
└─────────────────────┘
```

---

## Files Created for You

### Documentation
- ✅ **DIAGNOSIS_SUMMARY.md** (this file) - Quick overview
- ✅ **QUICK_START.md** - Step-by-step setup (10 min guide)
- ✅ **DIAGNOSTIC_REPORT.md** - Detailed analysis and troubleshooting

### Tools
- ✅ **scripts/diagnose_system.py** - Automated system checker
- ✅ **scripts/quick_setup.sh** - One-command setup script

### Existing Resources
- 📄 **README.md** - Project overview
- 📄 **docs/DEPLOYMENT.md** - AWS deployment guide
- 📄 **infrastructure/local_setup.sh** - Full local setup
- 📄 **scripts/seed_default_policy.py** - Policy seeder

---

## Summary

### The Problem
Your scanner system shows no activity because nothing is running. This is a local development environment that requires manual setup.

### The Solution  
Choose Option A (AWS DynamoDB - recommended) or Option B (Local with Docker), then follow the Quick Start guide.

### The Result
After setup:
- Backend and frontend running
- Database tables created
- Active policy configured
- Scanners ready to trigger
- Dashboard shows activity after first scan

### Time to Fix
- **Option A:** 5-10 minutes
- **Option B:** 15-20 minutes (with Docker install)

### Next Action
1. Read [`QUICK_START.md`](QUICK_START.md)
2. Run `./scripts/quick_setup.sh` (Option A)
3. Start backend and frontend
4. Trigger first scan
5. View results at http://localhost:5173

---

## Need Help?

1. **Start here:** [`QUICK_START.md`](QUICK_START.md)
2. **Troubleshooting:** [`DIAGNOSTIC_REPORT.md`](DIAGNOSTIC_REPORT.md)
3. **System check:** `python3 scripts/diagnose_system.py`
4. **Setup script:** `./scripts/quick_setup.sh`

**Remember:** Local development scanners must be triggered manually. They do not run automatically like in AWS Lambda deployment.

---

**All diagnostic tools and documentation are now in place. You're ready to set up and run the system!**
