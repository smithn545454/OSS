# 🚀 START HERE - OSS Scanner System Setup

**Your system is not running. Follow this guide to get it operational in 10 minutes.**

---

## 🔍 What's Wrong?

Your Dashboard and Pipeline pages show **zero activity** because:

1. ❌ **Backend server** is not running
2. ❌ **Frontend server** is not running
3. ❌ **Database tables** don't exist
4. ❌ **No active policy** configured
5. ❌ **No scans** have been triggered

This is a **local development environment** - nothing starts automatically.

---

## ✅ What's Working?

Your environment is **properly configured**:
- ✓ `.env` file exists
- ✓ Python virtual environment set up
- ✓ AWS credentials configured
- ✓ All required scripts present

You just need to **start the services** and **create the database**.

---

## 🎯 Quick Fix (10 Minutes)

### Option A: AWS DynamoDB (Recommended)

**Fastest path - no Docker needed**

#### 1. Update Configuration (1 min)

Edit `backend/.env` and comment out this line:
```bash
# DYNAMODB_ENDPOINT=http://localhost:8000
```

Or just remove it entirely. This tells the backend to use AWS DynamoDB instead of local.

#### 2. Run Automated Setup (2 min)

```bash
cd /Users/nicksmith/OSS
./scripts/quick_setup.sh
```

This will:
- Create 10 DynamoDB tables in AWS
- Seed default policy (v2.0.0)
- Run system diagnostics

#### 3. Start Backend (Terminal 1)

```bash
cd /Users/nicksmith/OSS/backend
source venv/bin/activate
uvicorn app.main:app --reload
```

Keep this running. Backend will be at: http://localhost:8000

#### 4. Start Frontend (Terminal 2)

```bash
cd /Users/nicksmith/OSS/frontend
npm run dev
```

Keep this running. Frontend will be at: http://localhost:5173

#### 5. Trigger Your First Scan (Terminal 3)

```bash
curl -X POST http://localhost:8000/api/scanners/run \
  -H "Content-Type: application/json" \
  -d '{}'
```

This takes 2-5 minutes. Watch the backend logs for progress.

#### 6. View Results

Open browser: **http://localhost:5173**

- **Dashboard** - Shows pipeline run statistics
- **Pipeline** - Stage-by-stage breakdown  
- **Opportunities** - Approved trade setups

---

### Option B: Full Local Setup (If You Want Docker)

**Requires Docker installation**

#### 1. Install Docker

```bash
brew install --cask docker
# Or download from docker.com
```

#### 2. Start DynamoDB Local

```bash
docker run -d -p 8000:8000 amazon/dynamodb-local
```

#### 3. Create Tables Locally

```bash
cd /Users/nicksmith/OSS
python3 infrastructure/dynamodb_tables.py --endpoint http://localhost:8000
```

#### 4. Seed Policy Locally

```bash
python3 scripts/seed_default_policy.py --endpoint http://localhost:8000 --activate
```

#### 5-6. Follow Steps 3-6 from Option A

---

## 🔧 Verify Setup

After setup, run diagnostics:

```bash
cd /Users/nicksmith/OSS
python3 scripts/diagnose_system.py
```

Expected output:
```
✓ Python Virtual Environment
✓ Environment File
✓ Backend API (Port 8000)
✓ Frontend Dev Server (Port 5173)
✓ DynamoDB (Local or AWS)
✓ Active Policy
```

---

## ⚠️ Important: Manual Triggering Required

**Scanners do NOT run automatically in local development.**

You must trigger each scan manually:

```bash
curl -X POST http://localhost:8000/api/scanners/run \
  -H "Content-Type: application/json" \
  -d '{}'
```

(AWS Lambda deployment runs automatically every 10 minutes during market hours)

---

## 📚 Full Documentation

We've created comprehensive documentation for you:

### Quick Reference
- **[QUICK_START.md](QUICK_START.md)** - Detailed setup guide with troubleshooting
- **[DIAGNOSIS_SUMMARY.md](DIAGNOSIS_SUMMARY.md)** - Overview of issues and solutions
- **[DIAGNOSTIC_REPORT.md](DIAGNOSTIC_REPORT.md)** - Deep dive analysis

### Tools
- **[scripts/diagnose_system.py](scripts/diagnose_system.py)** - Automated system checker
- **[scripts/quick_setup.sh](scripts/quick_setup.sh)** - One-command setup

### Existing Docs
- **[README.md](README.md)** - Project overview
- **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)** - AWS deployment guide

---

## 🎬 What Happens During a Scan?

1. **Load Policy** - Gets active policy from DynamoDB
2. **Get Watchlist** - Loads ticker list from policy (default: ~50 tickers)
3. **Run Scanners** - 4 scanners analyze each ticker
   - Unusual Volume Scanner
   - Breakout Scanner
   - Compression Scanner
   - Cheap Options Scanner
4. **Filter** - Removes low-quality underlyings
5. **Select Contracts** - Finds best contracts per DTE/delta bucket
6. **Evaluate** - Computes features, scores, gates
7. **Decide** - Assigns APPROVE/WATCH/REJECT verdict
8. **Store** - Saves to DynamoDB
9. **Dashboard Updates** - Shows results

**Duration:** 2-5 minutes per scan

---

## 📊 Expected Results

### After First Scan

Your Dashboard should show:
- **Pipeline Runs:** 1 completed
- **Opportunities:** 10-50 found
- **Evaluations:** 5-30 created
- **Approvals:** 1-10 (varies by market conditions)

### If You Get 0 Approvals

This is **normal** and can happen due to:
- Market is closed (needs recent data)
- Low volatility period
- Conservative default thresholds
- Small watchlist

**Not a system error** - just market conditions.

---

## 🆘 Troubleshooting

### Backend Won't Start

```bash
# Install dependencies
cd backend
pip install -r requirements.txt
```

### "No Active Policy Found"

```bash
# Seed policy
python3 scripts/seed_default_policy.py --activate
```

### "ResourceNotFoundException"

```bash
# Create tables
python3 infrastructure/dynamodb_tables.py
```

### "Cannot Connect to DynamoDB"

**If using local:**
```bash
# Start DynamoDB Local
docker run -p 8000:8000 amazon/dynamodb-local
```

**If using AWS:**
```bash
# Remove DYNAMODB_ENDPOINT from .env
nano backend/.env
```

---

## 🎯 Quick Commands Reference

```bash
# Setup
./scripts/quick_setup.sh

# Diagnostics
python3 scripts/diagnose_system.py

# Start backend
cd backend && source venv/bin/activate && uvicorn app.main:app --reload

# Start frontend
cd frontend && npm run dev

# Trigger scan
curl -X POST http://localhost:8000/api/scanners/run -H "Content-Type: application/json" -d '{}'

# Check health
curl http://localhost:8000/health

# View policy
curl http://localhost:8000/api/policies/active

# View stats
curl http://localhost:8000/api/pipeline/stats
```

---

## 🚀 Ready to Start?

1. **Choose setup option** (A or B above)
2. **Follow the numbered steps**
3. **Run diagnostics** to verify
4. **Trigger first scan**
5. **Open browser** to view results

**Estimated time:** 10 minutes (Option A) or 20 minutes (Option B)

---

## 💡 Pro Tips

- **Keep terminals open** - Backend and frontend must stay running
- **Check backend logs** - Shows scan progress and any errors
- **Trigger during market hours** - Better results with fresh data
- **Adjust policy** - Use UI to customize thresholds
- **Run diagnostics first** - Identifies issues before starting

---

## 📞 Still Having Issues?

1. **Run diagnostics:**
   ```bash
   python3 scripts/diagnose_system.py
   ```

2. **Read detailed guide:**
   ```bash
   cat QUICK_START.md
   ```

3. **Check comprehensive report:**
   ```bash
   cat DIAGNOSTIC_REPORT.md
   ```

All common issues and solutions are documented.

---

## ✨ Summary

**Problem:** System not running (nothing started)  
**Solution:** Run setup script, start services, trigger scan  
**Time:** 10 minutes  
**Result:** Dashboard shows activity, opportunities appear

**Start now with:**
```bash
./scripts/quick_setup.sh
```

---

**Ready? Open QUICK_START.md for detailed step-by-step instructions!**
