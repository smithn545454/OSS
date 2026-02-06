#!/bin/bash
# Local development setup script for OSS

set -e

echo "=========================================="
echo "OSS Local Development Setup"
echo "=========================================="

# Check for Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 is required but not installed."
    exit 1
fi
echo "✓ Python 3 found"

# Check for Node.js
if ! command -v node &> /dev/null; then
    echo "❌ Node.js is required but not installed."
    exit 1
fi
echo "✓ Node.js found"

# Check for Docker (optional, for DynamoDB Local)
if command -v docker &> /dev/null; then
    echo "✓ Docker found (for DynamoDB Local)"
    DOCKER_AVAILABLE=true
else
    echo "⚠ Docker not found - you'll need to use AWS DynamoDB"
    DOCKER_AVAILABLE=false
fi

echo ""
echo "Setting up backend..."
echo "----------------------------------------"

cd "$(dirname "$0")/.."

# Create backend virtual environment
if [ ! -d "backend/venv" ]; then
    echo "Creating Python virtual environment..."
    python3 -m venv backend/venv
fi

# Activate and install dependencies
echo "Installing Python dependencies..."
source backend/venv/bin/activate
pip install -q -r backend/requirements.txt

# Create .env file if it doesn't exist
if [ ! -f "backend/.env" ]; then
    echo "Creating backend/.env from example..."
    cp backend/.env.example backend/.env 2>/dev/null || cat > backend/.env << EOF
# OSS Backend Configuration
DEBUG=true
AWS_REGION=us-east-1
DYNAMODB_ENDPOINT=http://localhost:8000
# POLYGON_API_KEY=your_key_here
EOF
    echo "⚠ Please edit backend/.env with your configuration"
fi

echo ""
echo "Setting up frontend..."
echo "----------------------------------------"

cd frontend

# Install npm dependencies
echo "Installing npm dependencies..."
npm install --silent

cd ..

echo ""
echo "=========================================="
echo "Setup Complete!"
echo "=========================================="
echo ""
echo "Next steps:"
echo ""
echo "1. Start DynamoDB Local (if using Docker):"
echo "   docker run -p 8000:8000 amazon/dynamodb-local"
echo ""
echo "2. Create DynamoDB tables:"
echo "   cd infrastructure && python dynamodb_tables.py --endpoint http://localhost:8000"
echo ""
echo "3. Seed default policy:"
echo "   cd scripts && python seed_default_policy.py --endpoint http://localhost:8000 --activate"
echo ""
echo "4. Start the backend:"
echo "   cd backend && source venv/bin/activate && uvicorn app.main:app --reload"
echo ""
echo "5. Start the frontend (in another terminal):"
echo "   cd frontend && npm run dev"
echo ""
echo "The app will be available at: http://localhost:5173"
echo ""
