# OSS - Option Scanner System

A **cost-controlled, deterministic, fully observable** system that identifies single-leg long options trades with a higher probability of profitability.

## Architecture

- **Backend**: Python + FastAPI
- **Frontend**: React + TypeScript + Vite
- **Database**: DynamoDB

## Project Structure

```
OSS/
├── backend/           # FastAPI backend
│   ├── app/
│   │   ├── api/       # API routes
│   │   ├── core/      # Business logic (policy, pipeline)
│   │   ├── db/        # DynamoDB operations
│   │   └── services/  # External service clients
│   └── tests/
├── frontend/          # React frontend
│   └── src/
│       ├── components/
│       ├── pages/
│       ├── lib/
│       └── hooks/
├── infrastructure/    # Setup scripts
└── scripts/           # Utility scripts
```

## Pipeline Stages

1. **Opportunity Discovery** - Four scanners identify ticker-level opportunities
2. **Underlying Quality Filters** - Remove low-quality underlyings
3. **Contract Selection** - Select multiple contracts per ticker
4. **Feature Computation** - Calculate all scoring inputs
5. **Pillar Scoring** - Score Directional, Volatility, Structure
6. **Hard Gates** - Binary pass/fail checks
7. **Decision Logic** - Final verdict (APPROVE/WATCH/REJECT)
8. **Paper Trading** - Track performance

## Getting Started

### Backend

```bash
cd backend
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

### DynamoDB Local (Optional)

```bash
# Using Docker
docker run -p 8000:8000 amazon/dynamodb-local

# Create tables
python infrastructure/dynamodb_tables.py
```

## Environment Variables

Create a `.env` file in the backend directory:

```env
AWS_REGION=us-east-1
DYNAMODB_ENDPOINT=http://localhost:8000  # For local development
POLYGON_API_KEY=your_polygon_key
```

## Non-Negotiable Principles

1. **Single-leg long options only** - No spreads, no combos, no short positions
2. **Deterministic decisions** - Same inputs + same policy → identical outputs
3. **No LLM in decision logic** - LLM used only for post-decision trade thesis
4. **Hard gates dominate** - Any failed gate → REJECT
5. **Everything is explainable** - Every score emits reason codes
6. **Config over code** - All thresholds editable in UI

## License

Private - Internal Use Only
