# OSS Quick Start Guide

Get your OSS scanner system running in 10 minutes.

## TL;DR - Fastest Path to Working System

```bash
# 1. Update .env to use AWS DynamoDB (comment out DYNAMODB_ENDPOINT)
nano backend/.env  # Comment out: # DYNAMODB_ENDPOINT=http://localhost:8000

# 2. Create tables in AWS
python3 infrastructure/dynamodb_tables.py

# 3. Seed active policy
python3 scripts/seed_default_policy.py --activate

# 4. Start backend (Terminal 1)
cd backend && source venv/bin/activate && uvicorn app.main:app --reload

# 5. Start frontend (Terminal 2)
cd frontend && npm run dev

# 6. Trigger first scan (Terminal 3)
curl -X POST http://localhost:8000/api/scanners/run -H "Content-Type: application/json" -d '{}'

# 7. Open browser
open http://localhost:5173
```

---

## Current System Status

🔴 **PROBLEM IDENTIFIED**

Your system shows no activity because:
- ❌ Backend server is not running
- ❌ Frontend server is not running  
- ❌ DynamoDB tables don't exist
- ❌ No active policy is configured
- ❌ No scanner runs have been triggered

✅ **WHAT'S WORKING**
- Python environment is set up
- .env file exists
- AWS credentials are configured
- All scripts are in place

---

## Two Setup Options

### Option A: Use AWS DynamoDB (Recommended - Easier)

**Pros:** No Docker needed, data persists, faster setup  
**Cons:** Uses AWS resources (minimal cost for dev)

**Steps:**

1. **Configure for AWS** (1 min)
   ```bash
   cd /Users/nicksmith/OSS/backend
   # Edit .env and comment out or remove this line:
   # DYNAMODB_ENDPOINT=http://localhost:8000
   ```

2. **Create Tables** (2 min)
   ```bash
   cd /Users/nicksmith/OSS
   python3 infrastructure/dynamodb_tables.py
   ```
   Creates 10 tables in us-east-1

3. **Seed Policy** (1 min)
   ```bash
   python3 scripts/seed_default_policy.py --activate
   ```
   Creates v2.0.0 policy

4. **Start Backend** (keep running)
   ```bash
   cd backend
   source venv/bin/activate
   uvicorn app.main:app --reload
   ```
   Runs on http://localhost:8000

5. **Start Frontend** (new terminal, keep running)
   ```bash
   cd frontend
   npm run dev
   ```
   Runs on http://localhost:5173

6. **Trigger Scan** (new terminal)
   ```bash
   curl -X POST http://localhost:8000/api/scanners/run \
     -H "Content-Type: application/json" \
     -d '{}'
   ```
   Takes 2-5 minutes

7. **View Results**
   - Open http://localhost:5173
   - Dashboard will show stats after scan completes

---

### Option B: Use DynamoDB Local (Requires Docker)

**Pros:** Everything runs locally, no AWS usage  
**Cons:** Requires Docker, data lost on restart

**Steps:**

1. **Install Docker** (5 min)
   ```bash
   brew install --cask docker
   # Or download from docker.com
   ```

2. **Start DynamoDB Local** (keep running)
   ```bash
   docker run -d -p 8000:8000 amazon/dynamodb-local
   ```

3. **Create Tables Locally** (2 min)
   ```bash
   cd /Users/nicksmith/OSS
   python3 infrastructure/dynamodb_tables.py --endpoint http://localhost:8000
   ```

4. **Seed Policy Locally** (1 min)
   ```bash
   python3 scripts/seed_default_policy.py --endpoint http://localhost:8000 --activate
   ```

5. **Continue with steps 4-7 from Option A**

---

## Verification Checklist

After setup, verify everything is working:

```bash
# Run automated diagnostics
python3 scripts/diagnose_system.py
```

Should show:
- ✓ Python Virtual Environment
- ✓ Environment File
- ✓ Backend API (Port 8000)
- ✓ Frontend Dev Server (Port 5173)
- ✓ DynamoDB (Local or AWS)
- ✓ Active Policy

### Manual Checks

```bash
# 1. Backend health
curl http://localhost:8000/health
# Expected: {"status":"ok","timestamp":"..."}

# 2. Active policy
curl http://localhost:8000/api/policies/active
# Expected: {"version":"v2.0.0",...}

# 3. Pipeline stats (after running a scan)
curl http://localhost:8000/api/pipeline/stats
# Expected: {"total_runs":1,...}

# 4. Frontend accessible
curl http://localhost:5173
# Expected: HTML response
```

---

## Understanding Scanner Execution

### 🚨 IMPORTANT: Scanners Don't Run Automatically in Local Dev

In local development:
- Scanners must be **manually triggered** via API
- There is **no automatic scheduling**
- You must run `curl -X POST http://localhost:8000/api/scanners/run` each time

In AWS Lambda deployment:
- Scanners run **automatically every 10 minutes**
- During market hours (13:00-21:00 UTC, Mon-Fri)
- Via EventBridge schedule

### How to Trigger a Scan

```bash
# Basic scan (uses active policy, default watchlist)
curl -X POST http://localhost:8000/api/scanners/run \
  -H "Content-Type: application/json" \
  -d '{}'

# Scan specific tickers
curl -X POST http://localhost:8000/api/scanners/run \
  -H "Content-Type: application/json" \
  -d '{"tickers": ["AAPL", "MSFT", "GOOGL"]}'
```

### What Happens During a Scan

1. **Load Policy** - Get active policy configuration
2. **Get Tickers** - Load watchlist from policy
3. **Run Scanners** - 4 scanners analyze each ticker
   - Unusual Volume Scanner
   - Breakout Scanner  
   - Compression Scanner
   - Cheap Options Scanner
4. **Filter** - Remove low-quality underlyings
5. **Select Contracts** - Find best contracts per DTE/delta bucket
6. **Evaluate** - Compute features, scores, gates
7. **Decide** - Assign APPROVE/WATCH/REJECT verdict
8. **Store** - Save to DynamoDB

**Duration:** 2-5 minutes for typical watchlist (20-50 tickers)

---

## Troubleshooting Common Issues

### Backend won't start

**Error:** `ModuleNotFoundError: No module named 'fastapi'`  
**Fix:**
```bash
cd backend
pip install -r requirements.txt
```

### DynamoDB errors

**Error:** `ResourceNotFoundException: Requested resource not found`  
**Fix:** Tables not created
```bash
python3 infrastructure/dynamodb_tables.py [--endpoint http://localhost:8000]
```

**Error:** `Could not connect to the endpoint URL`  
**Fix:** DynamoDB Local not running
```bash
docker run -d -p 8000:8000 amazon/dynamodb-local
```

### No active policy

**Error:** `No active policy found`  
**Fix:**
```bash
python3 scripts/seed_default_policy.py --activate [--endpoint http://localhost:8000]
```

### Scan returns 0 opportunities

**Possible causes:**
1. **Market is closed** - Scanners need recent data
2. **Invalid Polygon API key** - Check backend logs
3. **Empty watchlist** - Check active policy configuration
4. **Strict thresholds** - Adjust policy settings

**Debug:**
```bash
# Check backend logs during scan
# Look for errors from Polygon API or scanner logic
```

### Frontend shows 0 runs

**Causes:**
1. **No scans triggered yet** - Run manual trigger
2. **Backend not running** - Start uvicorn
3. **API connection issue** - Check browser console

**Fix:** Trigger a scan and wait for completion

---

## Development Workflow

### Daily Development Pattern

```bash
# Terminal 1: Backend
cd /Users/nicksmith/OSS/backend
source venv/bin/activate
uvicorn app.main:app --reload  # Auto-reloads on code changes

# Terminal 2: Frontend  
cd /Users/nicksmith/OSS/frontend
npm run dev  # Auto-reloads on code changes

# Terminal 3: Commands
curl -X POST http://localhost:8000/api/scanners/run -H "Content-Type: application/json" -d '{}'
python3 scripts/diagnose_system.py
```

### Making Changes

1. **Edit backend code** → Auto-reloads (Terminal 1 shows restart)
2. **Edit frontend code** → Auto-reloads in browser
3. **Change policy** → Use UI or create new version
4. **Test scanners** → Trigger manual run, check logs

### Useful Commands

```bash
# Check system status
python3 scripts/diagnose_system.py

# Trigger scan
curl -X POST http://localhost:8000/api/scanners/run -H "Content-Type: application/json" -d '{}'

# View recent evaluations
curl http://localhost:8000/api/evaluations/filtered?verdict=APPROVE&limit=10

# Check pipeline stats
curl http://localhost:8000/api/pipeline/stats?days=7

# List policies
curl http://localhost:8000/api/policies?limit=10
```

---

## What to Expect After First Scan

### Dashboard Page
- **Pipeline Runs:** Shows your scan (status: COMPLETED)
- **Approve Rate:** % of evaluations approved
- **Watch Rate:** % of evaluations to watch
- **Reject Rate:** % of evaluations rejected
- **Verdict Distribution:** Visual breakdown

### Pipeline Page
- Stage-by-stage breakdown
- Funnel visualization
- Gate failure analysis
- Representative traces

### Opportunities Page
- List of APPROVE evaluations
- Contract details, pricing
- Trade thesis (if LLM configured)
- Earnings warnings

### Typical First Run Results

- **Opportunities Found:** 10-50 (depends on market conditions)
- **After Filtering:** 20-60% pass underlying filters
- **After Gates:** 10-30% pass all hard gates
- **Final APPROVE:** 5-15% of original opportunities

**If you get 0 approvals on first run:**
- This is normal during low-volatility periods
- Check that market is open/was recently open
- Review scanner thresholds in policy
- Try with different tickers or relaxed thresholds

---

## Next Steps

Once system is running:

1. **Explore the UI**
   - Dashboard: System overview
   - Opportunities: Tradeable setups
   - Pipeline: Detailed breakdown
   - Policy: Configuration management
   - Calibration: Performance tracking

2. **Customize Configuration**
   - Adjust scanner thresholds
   - Modify gate criteria
   - Change DTE buckets
   - Update watchlist

3. **Run Regular Scans**
   - Trigger scans during market hours
   - Compare results across different times
   - Track which scanners find opportunities

4. **Deploy to AWS** (Optional)
   - Automated scanning every 10 minutes
   - No manual triggering needed
   - See [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md)

---

## Need Help?

1. **Run Diagnostics:**
   ```bash
   python3 scripts/diagnose_system.py
   ```

2. **Check Full Report:**
   - Read [`DIAGNOSTIC_REPORT.md`](DIAGNOSTIC_REPORT.md)

3. **View Logs:**
   - Backend: Terminal 1 output
   - Frontend: Browser console (F12)

4. **Common Issues:**
   - Backend logs show detailed errors
   - Most issues are missing prerequisites
   - Diagnostic script identifies problems

---

## Summary

**You need to run 3 things:**

1. Backend server: `cd backend && source venv/bin/activate && uvicorn app.main:app --reload`
2. Frontend server: `cd frontend && npm run dev`
3. Manual scan trigger: `curl -X POST http://localhost:8000/api/scanners/run -H "Content-Type: application/json" -d '{}'`

**Before first run:**
- Create DynamoDB tables
- Seed active policy
- Ensure Polygon API key is set

**Then:**
- Open http://localhost:5173
- Dashboard will show your scan results
- Explore opportunities and pipeline details

**The system is working when:**
- Dashboard shows > 0 pipeline runs
- Pipeline page shows stage breakdown
- Opportunities page shows evaluations
