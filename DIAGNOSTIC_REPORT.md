# OSS System Diagnostic Report

**Generated:** 2026-02-03  
**System:** Local Development Environment

---

## Executive Summary

🔴 **CRITICAL: System is not operational**

The OSS scanner system is showing no activity because **none of the required services are running**. This is a local development environment that requires manual setup and startup.

### Quick Fix Summary

You need to:
1. ✗ Start DynamoDB Local (or use AWS DynamoDB)
2. ✗ Create database tables
3. ✗ Seed an active policy
4. ✗ Start the backend server
5. ✗ Start the frontend dev server
6. ✗ Manually trigger a scanner run

---

## Detailed Findings

### ✓ Configuration Files

**Status:** OK (2/2 checks passed)

- ✓ Python virtual environment exists at `/Users/nicksmith/OSS/backend/venv`
- ✓ Environment file `.env` exists with required variables:
  - `AWS_REGION=us-east-1`
  - `DYNAMODB_TABLE_PREFIX=oss`
  - `DYNAMODB_ENDPOINT=http://localhost:8000`
  - `CORS_ORIGINS=http://localhost:5173,http://localhost:3000`

**Missing from .env:**
- ⚠️ `POLYGON_API_KEY` - Not visible (may be present but not shown in scan)

### ✗ Running Services

**Status:** CRITICAL (0/2 services running)

- ✗ **Backend API** - Not running on port 8000
- ✗ **Frontend Dev Server** - Not running on port 5173

**Impact:** Without these services, the dashboard cannot load or display any data.

### ✗ Database Services

**Status:** CRITICAL (0/1 database accessible)

#### DynamoDB Local
- ✗ Not running on port 8000
- ✗ Cannot connect to `http://localhost:8000`
- **Note:** Docker is not installed, so DynamoDB Local cannot be run via Docker

#### AWS DynamoDB
- ⚠️ Accessible but no OSS tables exist
- AWS credentials are configured (region: us-west-1)
- **Option:** You can use AWS DynamoDB instead of local

### ✗ Database Tables & Policy

**Status:** CRITICAL

- ✗ No DynamoDB tables created (neither local nor AWS)
- ✗ No active policy seeded
- **Impact:** Even if backend starts, scanners cannot run without an active policy

---

## Root Cause Analysis

### Why No Pipeline Activity?

The dashboard shows zero activity because of this dependency chain:

```
Scanner Activity Requires:
  ↓
Backend Running (port 8000)
  ↓
DynamoDB Accessible (tables exist)
  ↓
Active Policy Exists
  ↓
Manual Trigger OR EventBridge Schedule
  ↓
Polygon API Key for market data
```

**Current Status:** All prerequisites are missing ❌

### Local vs AWS Deployment

Your `.env` file is configured for **LOCAL DEVELOPMENT**:
- `DYNAMODB_ENDPOINT=http://localhost:8000` points to local DynamoDB
- But DynamoDB Local is not running
- And Docker is not installed to run it

**You have two options:**

1. **Option A: Full Local Setup** (Requires Docker)
   - Install Docker
   - Run DynamoDB Local
   - Create tables locally
   - Seed policy locally
   - Start backend/frontend

2. **Option B: Hybrid Setup** (Easier - recommended)
   - Remove/comment out `DYNAMODB_ENDPOINT` from `.env`
   - Create tables in AWS DynamoDB
   - Seed policy in AWS
   - Start backend/frontend locally
   - Backend connects to AWS DynamoDB

---

## Step-by-Step Fix Guide

### Option B: Hybrid Setup (Recommended)

This uses AWS DynamoDB with local backend/frontend servers.

#### Step 1: Update Configuration

Edit `/Users/nicksmith/OSS/backend/.env`:

```bash
DEBUG=true
AWS_REGION=us-east-1
DYNAMODB_TABLE_PREFIX=oss
# DYNAMODB_ENDPOINT=http://localhost:8000  # Comment this out to use AWS
POLYGON_API_KEY=your_actual_polygon_key_here
CORS_ORIGINS=http://localhost:5173,http://localhost:3000
```

#### Step 2: Create DynamoDB Tables in AWS

```bash
cd /Users/nicksmith/OSS
python3 infrastructure/dynamodb_tables.py
```

This creates 10 tables in AWS DynamoDB:
- oss-policies
- oss-pipeline-runs
- oss-opportunities
- oss-evaluations
- oss-stage-events
- oss-paper-positions
- oss-feature-values
- oss-pillar-scores
- oss-gate-results
- oss-iv-history

#### Step 3: Seed Default Policy

```bash
cd /Users/nicksmith/OSS
python3 scripts/seed_default_policy.py --activate
```

This creates and activates policy version v2.0.0 with default configuration.

#### Step 4: Start Backend Server

```bash
cd /Users/nicksmith/OSS/backend
source venv/bin/activate
uvicorn app.main:app --reload
```

Backend will be available at: `http://localhost:8000`

#### Step 5: Start Frontend Dev Server

In a new terminal:

```bash
cd /Users/nicksmith/OSS/frontend
npm run dev
```

Frontend will be available at: `http://localhost:5173`

#### Step 6: Verify System is Running

```bash
# Check backend health
curl http://localhost:8000/health

# Check active policy
curl http://localhost:8000/api/policies/active

# Check for pipeline runs (should be empty initially)
curl http://localhost:8000/api/pipeline/stats
```

#### Step 7: Trigger Your First Scanner Run

Scanners in local development must be triggered manually:

```bash
curl -X POST http://localhost:8000/api/scanners/run \
  -H "Content-Type: application/json" \
  -d '{}'
```

This will:
1. Load the active policy
2. Get the watchlist tickers
3. Run all 4 scanners (Unusual Volume, Breakout, Compression, Cheap Options)
4. Filter opportunities
5. Select and evaluate contracts
6. Store results in DynamoDB

**Expected Duration:** 2-5 minutes for first run (depending on watchlist size)

#### Step 8: View Results

1. Open browser to `http://localhost:5173`
2. Navigate to Dashboard - should show pipeline run statistics
3. Navigate to Pipeline - should show the run details
4. Navigate to Opportunities - should show approved evaluations

---

### Option A: Full Local Setup (If You Want Docker)

#### Step 1: Install Docker

```bash
# On macOS with Homebrew
brew install --cask docker

# Or download from: https://www.docker.com/products/docker-desktop
```

#### Step 2: Start DynamoDB Local

```bash
docker run -d -p 8000:8000 amazon/dynamodb-local
```

#### Step 3: Create Local Tables

```bash
cd /Users/nicksmith/OSS
python3 infrastructure/dynamodb_tables.py --endpoint http://localhost:8000
```

#### Step 4: Seed Policy Locally

```bash
cd /Users/nicksmith/OSS
python3 scripts/seed_default_policy.py --endpoint http://localhost:8000 --activate
```

#### Step 5-8: Same as Option B (Steps 4-8)

---

## Automated Diagnostics

A diagnostic script has been created for you:

```bash
cd /Users/nicksmith/OSS
python3 scripts/diagnose_system.py
```

This will check:
- ✓ Virtual environment
- ✓ .env configuration
- ✓ Backend running
- ✓ Frontend running
- ✓ DynamoDB accessible
- ✓ Tables exist
- ✓ Active policy exists

Run this after setup to verify everything is working.

---

## Understanding Scanner Execution

### Local Development (Your Setup)
- **Trigger:** Manual API call only
- **Frequency:** Whenever you trigger it
- **Command:** `curl -X POST http://localhost:8000/api/scanners/run`

### AWS Lambda Deployment
- **Trigger:** EventBridge schedule
- **Frequency:** Every 10 minutes during market hours
- **Hours:** 13:00-21:00 UTC, Monday-Friday (covers both EST and EDT)
- **Automatic:** Yes, no manual trigger needed

**Important:** Your local development environment does NOT automatically run scanners. You must trigger them manually each time.

---

## Common Issues & Solutions

### Issue: "No active policy found"
**Cause:** Policy not seeded or not activated  
**Fix:** `python3 scripts/seed_default_policy.py --activate`

### Issue: "ResourceNotFoundException" on DynamoDB
**Cause:** Tables don't exist  
**Fix:** `python3 infrastructure/dynamodb_tables.py`

### Issue: Backend won't start
**Cause:** Missing dependencies  
**Fix:** `cd backend && pip install -r requirements.txt`

### Issue: "POLYGON_API_KEY not set"
**Cause:** Missing API key in .env  
**Fix:** Add `POLYGON_API_KEY=your_key` to backend/.env

### Issue: Scanner runs but shows 0 opportunities
**Possible Causes:**
1. Market is closed (scanners need live/recent data)
2. Polygon API key is invalid
3. Watchlist is empty or tickers are invalid
4. Scanner thresholds are too strict

**Debug:** Check backend logs for errors during scan

---

## Next Steps After Setup

1. **Verify Setup:**
   ```bash
   python3 scripts/diagnose_system.py
   ```

2. **Run First Scan:**
   ```bash
   curl -X POST http://localhost:8000/api/scanners/run
   ```

3. **Monitor Progress:**
   - Watch backend logs for scan progress
   - Check dashboard at `http://localhost:5173`
   - View pipeline page for detailed breakdown

4. **Explore Results:**
   - Dashboard: Overview and statistics
   - Pipeline: Stage-by-stage breakdown
   - Opportunities: Approved trades
   - Calibration: Performance analysis

5. **Customize Configuration:**
   - Policy page: Adjust scanner thresholds
   - Create new policy versions
   - Compare policy performance

---

## Architecture Reference

### System Components

```
┌─────────────────────────────────────────────────────────┐
│                     Frontend (React)                     │
│                  http://localhost:5173                   │
└────────────────────────┬────────────────────────────────┘
                         │ HTTP API Calls
                         ▼
┌─────────────────────────────────────────────────────────┐
│              Backend (FastAPI + Uvicorn)                 │
│                  http://localhost:8000                   │
│  ┌──────────────────────────────────────────────────┐   │
│  │  Scanner Orchestrator                             │   │
│  │  ├─ Unusual Volume Scanner                        │   │
│  │  ├─ Breakout Scanner                              │   │
│  │  ├─ Compression Scanner                           │   │
│  │  └─ Cheap Options Scanner                         │   │
│  └──────────────────────────────────────────────────┘   │
└────────────┬────────────────────────┬───────────────────┘
             │                        │
             ▼                        ▼
┌─────────────────────┐    ┌──────────────────────┐
│   DynamoDB (AWS)    │    │   Polygon.io API     │
│   10 Tables         │    │   Market Data        │
│   - policies        │    │   - Quotes           │
│   - pipeline-runs   │    │   - Options Chain    │
│   - opportunities   │    │   - IV Data          │
│   - evaluations     │    └──────────────────────┘
│   - etc.            │
└─────────────────────┘
```

### Data Flow

```
1. User/EventBridge → POST /api/scanners/run
2. Backend → Load active policy from DynamoDB
3. Backend → Get watchlist tickers from policy
4. For each ticker:
   - Backend → Fetch data from Polygon API
   - Scanners → Identify opportunities
5. Backend → Filter opportunities (underlying quality)
6. Backend → Select contracts (DTE, delta, liquidity)
7. Backend → Evaluate contracts (features, pillars, gates)
8. Backend → Apply decision logic (APPROVE/WATCH/REJECT)
9. Backend → Store results in DynamoDB
10. Frontend → Fetch results from backend
11. Dashboard → Display statistics and recent runs
```

---

## File Reference

### Configuration
- [`backend/.env`](backend/.env) - Environment variables
- [`backend/app/config.py`](backend/app/config.py) - Settings loader

### Setup Scripts
- [`infrastructure/dynamodb_tables.py`](infrastructure/dynamodb_tables.py) - Create DB tables
- [`scripts/seed_default_policy.py`](scripts/seed_default_policy.py) - Seed default policy
- [`scripts/diagnose_system.py`](scripts/diagnose_system.py) - System diagnostics
- [`infrastructure/local_setup.sh`](infrastructure/local_setup.sh) - Automated local setup

### Core Application
- [`backend/app/main.py`](backend/app/main.py) - FastAPI app entry point
- [`backend/app/scanners/orchestrator.py`](backend/app/scanners/orchestrator.py) - Scanner coordinator
- [`backend/app/api/routes/scanners.py`](backend/app/api/routes/scanners.py) - Scanner API endpoints
- [`backend/app/api/routes/pipeline.py`](backend/app/api/routes/pipeline.py) - Pipeline monitoring API

### Frontend
- [`frontend/src/pages/Dashboard.tsx`](frontend/src/pages/Dashboard.tsx) - Dashboard page
- [`frontend/src/lib/api.ts`](frontend/src/lib/api.ts) - API client

---

## Support

If you encounter issues after following this guide:

1. Run diagnostics: `python3 scripts/diagnose_system.py`
2. Check backend logs for error messages
3. Verify Polygon API key is valid
4. Ensure AWS credentials have DynamoDB permissions
5. Check that market is open (if testing with live data)

**Remember:** Local development scanners must be triggered manually. They do not run automatically.
