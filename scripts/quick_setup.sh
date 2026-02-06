#!/bin/bash
# Quick setup script for OSS - Option A (AWS DynamoDB)
# This script automates the setup process

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "=========================================="
echo "OSS Quick Setup - Option A (AWS DynamoDB)"
echo "=========================================="
echo ""

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Check if backend/.env exists
if [ ! -f "$PROJECT_ROOT/backend/.env" ]; then
    echo -e "${RED}❌ Error: backend/.env file not found${NC}"
    echo ""
    echo "Please create backend/.env with:"
    echo ""
    echo "DEBUG=true"
    echo "AWS_REGION=us-east-1"
    echo "DYNAMODB_TABLE_PREFIX=oss"
    echo "POLYGON_API_KEY=your_key_here"
    echo "CORS_ORIGINS=http://localhost:5173,http://localhost:3000"
    echo ""
    echo "Note: Do NOT include DYNAMODB_ENDPOINT to use AWS DynamoDB"
    exit 1
fi

echo -e "${BLUE}Step 1/3: Creating DynamoDB Tables in AWS...${NC}"
echo ""

cd "$PROJECT_ROOT"
if python3 infrastructure/dynamodb_tables.py; then
    echo -e "${GREEN}✓ Tables created successfully${NC}"
else
    echo -e "${YELLOW}⚠ Tables may already exist or there was an error${NC}"
fi
echo ""

echo -e "${BLUE}Step 2/3: Seeding Default Policy...${NC}"
echo ""

if python3 scripts/seed_default_policy.py --activate; then
    echo -e "${GREEN}✓ Policy seeded and activated${NC}"
else
    echo -e "${YELLOW}⚠ Policy may already exist or there was an error${NC}"
fi
echo ""

echo -e "${BLUE}Step 3/3: Running System Diagnostics...${NC}"
echo ""

python3 scripts/diagnose_system.py || true

echo ""
echo "=========================================="
echo -e "${GREEN}Setup Complete!${NC}"
echo "=========================================="
echo ""
echo "Next steps:"
echo ""
echo -e "${BLUE}1. Start the backend (Terminal 1):${NC}"
echo "   cd $PROJECT_ROOT/backend"
echo "   source venv/bin/activate"
echo "   uvicorn app.main:app --reload"
echo ""
echo -e "${BLUE}2. Start the frontend (Terminal 2):${NC}"
echo "   cd $PROJECT_ROOT/frontend"
echo "   npm run dev"
echo ""
echo -e "${BLUE}3. Trigger a scan (Terminal 3):${NC}"
echo "   curl -X POST http://localhost:8000/api/scanners/run \\"
echo "     -H 'Content-Type: application/json' \\"
echo "     -d '{}'"
echo ""
echo -e "${BLUE}4. Open browser:${NC}"
echo "   http://localhost:5173"
echo ""
echo "=========================================="
echo ""
