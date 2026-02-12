#!/bin/bash
# OSS Deployment Script
# Deploys the complete OSS stack to AWS using Lambda (no Docker required)
#
# Safety features:
# - Pre-deploy tests: backend deploy runs pytest first (skip with --skip-tests)
# - Lambda versioning: every deploy publishes a numbered version for rollback
# - Git commit tracking: Lambda description records the deployed commit hash
# - Rollback: `./scripts/deploy.sh rollback` reverts to the previous Lambda version

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configuration
PROJECT_NAME="${PROJECT_NAME:-oss}"
ENV_NAME="${ENV_NAME:-dev}"
AWS_REGION="${AWS_REGION:-us-east-1}"
SKIP_TESTS="${SKIP_TESTS:-false}"

# Parse flags — collect non-flag args into ARGS array
ARGS=()
for arg in "$@"; do
    case $arg in
        --skip-tests)
            SKIP_TESTS="true"
            ;;
        *)
            ARGS+=("$arg")
            ;;
    esac
done
set -- "${ARGS[@]}"

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  OSS Deployment Script (Lambda)${NC}"
echo -e "${GREEN}  Environment: ${ENV_NAME}${NC}"
echo -e "${GREEN}  Region: ${AWS_REGION}${NC}"
echo -e "${GREEN}========================================${NC}"

# Get Lambda function name (shared by backend/rollback/versions)
get_lambda_name() {
    LAMBDA_NAME=$(aws cloudformation describe-stacks \
        --stack-name ${PROJECT_NAME}-${ENV_NAME}-backend \
        --query "Stacks[0].Outputs[?ExportName=='${PROJECT_NAME}-${ENV_NAME}-lambda-name'].OutputValue" \
        --output text \
        --region ${AWS_REGION})

    if [ -z "$LAMBDA_NAME" ] || [ "$LAMBDA_NAME" = "None" ]; then
        echo -e "${RED}Error: Could not get Lambda function name. Deploy infrastructure first.${NC}"
        exit 1
    fi
}

# Check prerequisites
check_prerequisites() {
    echo -e "\n${YELLOW}Checking prerequisites...${NC}"

    # Check AWS CLI
    if ! command -v aws &> /dev/null; then
        echo -e "${RED}Error: AWS CLI not found. Please install it first.${NC}"
        exit 1
    fi

    # Check AWS credentials
    if ! aws sts get-caller-identity &> /dev/null; then
        echo -e "${RED}Error: AWS credentials not configured. Run 'aws configure' first.${NC}"
        exit 1
    fi

    # Check CDK
    if ! command -v cdk &> /dev/null; then
        echo -e "${YELLOW}CDK not found. Installing...${NC}"
        npm install -g aws-cdk
    fi

    # Check Node.js for frontend
    if ! command -v npm &> /dev/null; then
        echo -e "${RED}Error: npm not found. Please install Node.js first.${NC}"
        exit 1
    fi

    # Check Python for Lambda packaging
    if ! command -v python3 &> /dev/null; then
        echo -e "${RED}Error: python3 not found. Please install Python first.${NC}"
        exit 1
    fi

    echo -e "${GREEN}All prerequisites met!${NC}"
}

# Bootstrap CDK (first time only)
bootstrap_cdk() {
    echo -e "\n${YELLOW}Bootstrapping CDK...${NC}"
    cd infrastructure/cdk

    # Create virtual environment if it doesn't exist
    if [ ! -d ".venv" ]; then
        python3 -m venv .venv
    fi

    source .venv/bin/activate
    pip install -r requirements.txt

    cdk bootstrap aws://${AWS_ACCOUNT_ID}/${AWS_REGION}

    deactivate
    cd ../..
}

# Deploy infrastructure
deploy_infrastructure() {
    echo -e "\n${YELLOW}Deploying infrastructure with CDK...${NC}"
    cd infrastructure/cdk

    source .venv/bin/activate

    # Synthesize first to check for errors
    cdk synth

    # Deploy all stacks
    cdk deploy --all --require-approval never

    deactivate
    cd ../..

    echo -e "${GREEN}Infrastructure deployed!${NC}"
}

# Run backend tests before deploying
run_backend_tests() {
    if [ "$SKIP_TESTS" = "true" ]; then
        echo -e "${YELLOW}Skipping tests (--skip-tests flag set)${NC}"
        return 0
    fi

    echo -e "\n${YELLOW}Running backend tests before deploy...${NC}"
    cd backend

    if python3 -m pytest tests/ --tb=short -q --no-header 2>&1; then
        echo -e "${GREEN}Tests passed!${NC}"
        cd ..
        return 0
    else
        echo -e "${RED}========================================${NC}"
        echo -e "${RED}  DEPLOY ABORTED: Tests failed!${NC}"
        echo -e "${RED}  Fix the tests or use --skip-tests${NC}"
        echo -e "${RED}========================================${NC}"
        cd ..
        return 1
    fi
}

# Package and deploy Lambda function
deploy_backend() {
    echo -e "\n${YELLOW}Packaging and deploying Lambda function...${NC}"

    # Run tests first
    run_backend_tests

    get_lambda_name

    # Get git commit hash for tracking
    GIT_COMMIT=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
    GIT_BRANCH=$(git branch --show-current 2>/dev/null || echo "unknown")
    DEPLOY_TIME=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    DEPLOY_DESC="commit=${GIT_COMMIT} branch=${GIT_BRANCH} deployed=${DEPLOY_TIME}"

    echo "Lambda Function: $LAMBDA_NAME"
    echo "Git Commit: $GIT_COMMIT ($GIT_BRANCH)"

    # Create packaging directory
    cd backend
    rm -rf package lambda.zip
    mkdir -p package

    # Install dependencies (Linux-compatible for Lambda)
    # Use requirements-lambda.txt which excludes test dependencies for faster resolution
    echo "Installing dependencies for Lambda (Linux x86_64)..."
    REQUIREMENTS_FILE="requirements-lambda.txt"
    if [ ! -f "$REQUIREMENTS_FILE" ]; then
        REQUIREMENTS_FILE="requirements.txt"
    fi
    python3 -m pip install -r $REQUIREMENTS_FILE -t package/ --quiet --upgrade \
        --platform manylinux2014_x86_64 \
        --implementation cp \
        --python-version 3.12 \
        --only-binary=:all:

    # Copy application code
    echo "Copying application code..."
    cp -r app package/

    # Create zip file
    echo "Creating deployment package..."
    cd package
    zip -r ../lambda.zip . -q
    cd ..

    # Get zip file size
    ZIP_SIZE=$(ls -lh lambda.zip | awk '{print $5}')
    echo "Package size: $ZIP_SIZE"

    # Check if zip is too large (50MB limit for direct upload)
    ZIP_BYTES=$(stat -f%z lambda.zip 2>/dev/null || stat -c%s lambda.zip)
    if [ $ZIP_BYTES -gt 52428800 ]; then
        echo -e "${YELLOW}Package too large for direct upload, using S3...${NC}"

        # Upload to S3 first
        BUCKET="${PROJECT_NAME}-${ENV_NAME}-lambda-deploy-${AWS_ACCOUNT_ID}"
        aws s3 mb s3://${BUCKET} --region ${AWS_REGION} 2>/dev/null || true
        aws s3 cp lambda.zip s3://${BUCKET}/lambda.zip --region ${AWS_REGION}

        aws lambda update-function-code \
            --function-name ${LAMBDA_NAME} \
            --s3-bucket ${BUCKET} \
            --s3-key lambda.zip \
            --region ${AWS_REGION}
    else
        # Direct upload
        aws lambda update-function-code \
            --function-name ${LAMBDA_NAME} \
            --zip-file fileb://lambda.zip \
            --region ${AWS_REGION}
    fi

    # Clean up
    rm -rf package lambda.zip
    cd ..

    # Wait for Lambda to finish updating before publishing version
    echo "Waiting for Lambda update to complete..."
    aws lambda wait function-updated \
        --function-name ${LAMBDA_NAME} \
        --region ${AWS_REGION}

    # Update Lambda description with git commit info
    aws lambda update-function-configuration \
        --function-name ${LAMBDA_NAME} \
        --description "${DEPLOY_DESC}" \
        --region ${AWS_REGION} > /dev/null

    # Wait again after config update
    aws lambda wait function-updated \
        --function-name ${LAMBDA_NAME} \
        --region ${AWS_REGION}

    # Publish a numbered version (immutable snapshot for rollback)
    echo -e "\n${YELLOW}Publishing Lambda version...${NC}"
    VERSION_OUTPUT=$(aws lambda publish-version \
        --function-name ${LAMBDA_NAME} \
        --description "${DEPLOY_DESC}" \
        --region ${AWS_REGION})

    VERSION_NUMBER=$(echo "$VERSION_OUTPUT" | python3 -c "import sys,json; print(json.load(sys.stdin)['Version'])")

    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}  Backend deployed!${NC}"
    echo -e "${GREEN}  Lambda version: ${VERSION_NUMBER}${NC}"
    echo -e "${GREEN}  Git commit: ${GIT_COMMIT}${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo -e "${YELLOW}  Rollback with: AWS_REGION=${AWS_REGION} ./scripts/deploy.sh rollback${NC}"
}

# Rollback to a previous Lambda version
rollback_backend() {
    echo -e "\n${YELLOW}Rolling back Lambda function...${NC}"

    get_lambda_name

    # List recent versions
    echo -e "\n${YELLOW}Recent Lambda versions:${NC}"
    aws lambda list-versions-by-function \
        --function-name ${LAMBDA_NAME} \
        --region ${AWS_REGION} \
        --query "Versions[-5:].{Version:Version,Description:Description,Modified:LastModified}" \
        --output table

    # Get the target version (second-to-last, since last is what's currently deployed)
    if [ -n "$2" ]; then
        TARGET_VERSION="$2"
        echo -e "\nRolling back to specified version: ${TARGET_VERSION}"
    else
        # Get all version numbers, sort numerically, take second-to-last
        TARGET_VERSION=$(aws lambda list-versions-by-function \
            --function-name ${LAMBDA_NAME} \
            --region ${AWS_REGION} \
            --query "Versions[?Version!='\$LATEST'].Version" \
            --output text | tr '\t' '\n' | sort -n | tail -2 | head -1)

        if [ -z "$TARGET_VERSION" ] || [ "$TARGET_VERSION" = "None" ]; then
            echo -e "${RED}Error: No previous version found to rollback to.${NC}"
            exit 1
        fi
        echo -e "\nRolling back to previous version: ${TARGET_VERSION}"
    fi

    # Get the version's code and apply it to $LATEST
    # We do this by updating function code from the published version's package
    echo "Applying version ${TARGET_VERSION} code to \$LATEST..."

    # Get the version's code location
    CODE_INFO=$(aws lambda get-function \
        --function-name ${LAMBDA_NAME} \
        --qualifier ${TARGET_VERSION} \
        --region ${AWS_REGION} \
        --query "Code.Location" \
        --output text)

    # Download and re-upload
    TMPFILE=$(mktemp /tmp/lambda-rollback-XXXXXX.zip)
    curl -sL "$CODE_INFO" -o "$TMPFILE"

    aws lambda update-function-code \
        --function-name ${LAMBDA_NAME} \
        --zip-file "fileb://${TMPFILE}" \
        --region ${AWS_REGION} > /dev/null

    rm -f "$TMPFILE"

    # Wait and publish the rollback as a new version
    aws lambda wait function-updated \
        --function-name ${LAMBDA_NAME} \
        --region ${AWS_REGION}

    ROLLBACK_DESC="ROLLBACK to version ${TARGET_VERSION} at $(date -u +"%Y-%m-%dT%H:%M:%SZ")"

    aws lambda update-function-configuration \
        --function-name ${LAMBDA_NAME} \
        --description "${ROLLBACK_DESC}" \
        --region ${AWS_REGION} > /dev/null

    aws lambda wait function-updated \
        --function-name ${LAMBDA_NAME} \
        --region ${AWS_REGION}

    VERSION_OUTPUT=$(aws lambda publish-version \
        --function-name ${LAMBDA_NAME} \
        --description "${ROLLBACK_DESC}" \
        --region ${AWS_REGION})

    NEW_VERSION=$(echo "$VERSION_OUTPUT" | python3 -c "import sys,json; print(json.load(sys.stdin)['Version'])")

    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}  Rollback complete!${NC}"
    echo -e "${GREEN}  Rolled back to version: ${TARGET_VERSION}${NC}"
    echo -e "${GREEN}  New version number: ${NEW_VERSION}${NC}"
    echo -e "${GREEN}========================================${NC}"
}

# List Lambda versions
list_versions() {
    get_lambda_name

    echo -e "\n${YELLOW}Lambda versions for ${LAMBDA_NAME}:${NC}"
    aws lambda list-versions-by-function \
        --function-name ${LAMBDA_NAME} \
        --region ${AWS_REGION} \
        --query "Versions[?Version!='\$LATEST'].{Version:Version,Description:Description,Modified:LastModified}" \
        --output table
}

# Build and deploy frontend
deploy_frontend() {
    echo -e "\n${YELLOW}Building and deploying frontend...${NC}"

    # Get S3 bucket name and CloudFront distribution ID
    BUCKET_NAME=$(aws cloudformation describe-stacks \
        --stack-name ${PROJECT_NAME}-${ENV_NAME}-frontend \
        --query "Stacks[0].Outputs[?ExportName=='${PROJECT_NAME}-${ENV_NAME}-frontend-bucket'].OutputValue" \
        --output text \
        --region ${AWS_REGION})

    DISTRIBUTION_ID=$(aws cloudformation describe-stacks \
        --stack-name ${PROJECT_NAME}-${ENV_NAME}-frontend \
        --query "Stacks[0].Outputs[?ExportName=='${PROJECT_NAME}-${ENV_NAME}-frontend-url'].OutputValue" \
        --output text \
        --region ${AWS_REGION})

    BACKEND_URL=$(aws cloudformation describe-stacks \
        --stack-name ${PROJECT_NAME}-${ENV_NAME}-backend \
        --query "Stacks[0].Outputs[?ExportName=='${PROJECT_NAME}-${ENV_NAME}-backend-url'].OutputValue" \
        --output text \
        --region ${AWS_REGION})

    if [ -z "$BUCKET_NAME" ]; then
        echo -e "${RED}Error: Could not get S3 bucket. Deploy infrastructure first.${NC}"
        exit 1
    fi

    # Build frontend
    cd frontend

    # Set API URL environment variable
    echo "VITE_API_URL=${BACKEND_URL}" > .env.production

    npm ci
    npm run build

    # Sync to S3
    aws s3 sync dist/ s3://${BUCKET_NAME}/ --delete --region ${AWS_REGION}

    # Invalidate CloudFront cache
    if [ -n "$DISTRIBUTION_ID" ] && [ "$DISTRIBUTION_ID" != "None" ]; then
        aws cloudfront create-invalidation \
            --distribution-id ${DISTRIBUTION_ID} \
            --paths "/*" \
            --region ${AWS_REGION}
    fi

    cd ..

    echo -e "${GREEN}Frontend deployed!${NC}"
}

# Set Polygon API key in Secrets Manager
set_polygon_secret() {
    echo -e "\n${YELLOW}Setting Polygon API key...${NC}"

    if [ -z "$POLYGON_API_KEY" ]; then
        echo -e "${RED}Error: POLYGON_API_KEY environment variable not set.${NC}"
        echo "Run: export POLYGON_API_KEY=your_api_key"
        exit 1
    fi

    SECRET_ARN=$(aws cloudformation describe-stacks \
        --stack-name ${PROJECT_NAME}-${ENV_NAME}-backend \
        --query "Stacks[0].Outputs[?ExportName=='${PROJECT_NAME}-${ENV_NAME}-polygon-secret-arn'].OutputValue" \
        --output text \
        --region ${AWS_REGION})

    aws secretsmanager put-secret-value \
        --secret-id ${SECRET_ARN} \
        --secret-string "${POLYGON_API_KEY}" \
        --region ${AWS_REGION}

    echo -e "${GREEN}Polygon API key set!${NC}"
}

# Print deployment info
print_info() {
    echo -e "\n${GREEN}========================================${NC}"
    echo -e "${GREEN}  Deployment Complete!${NC}"
    echo -e "${GREEN}========================================${NC}"

    BACKEND_URL=$(aws cloudformation describe-stacks \
        --stack-name ${PROJECT_NAME}-${ENV_NAME}-backend \
        --query "Stacks[0].Outputs[?ExportName=='${PROJECT_NAME}-${ENV_NAME}-backend-url'].OutputValue" \
        --output text \
        --region ${AWS_REGION} 2>/dev/null)

    FRONTEND_URL=$(aws cloudformation describe-stacks \
        --stack-name ${PROJECT_NAME}-${ENV_NAME}-frontend \
        --query "Stacks[0].Outputs[?ExportName=='${PROJECT_NAME}-${ENV_NAME}-frontend-url'].OutputValue" \
        --output text \
        --region ${AWS_REGION} 2>/dev/null)

    LAMBDA_NAME=$(aws cloudformation describe-stacks \
        --stack-name ${PROJECT_NAME}-${ENV_NAME}-backend \
        --query "Stacks[0].Outputs[?ExportName=='${PROJECT_NAME}-${ENV_NAME}-lambda-name'].OutputValue" \
        --output text \
        --region ${AWS_REGION} 2>/dev/null)

    echo -e "\n${YELLOW}URLs:${NC}"
    echo "  Backend API:  ${BACKEND_URL:-Not yet deployed}"
    echo "  Frontend:     ${FRONTEND_URL:-Not yet deployed}"
    echo "  Lambda:       ${LAMBDA_NAME:-Not yet deployed}"
    echo ""
    echo -e "${YELLOW}Next steps:${NC}"
    echo "  1. Set Polygon API key: export POLYGON_API_KEY=your_key && ./scripts/deploy.sh secret"
    echo "  2. Seed default policy: curl -X POST ${BACKEND_URL}/api/policies/seed"
    echo ""
}

# Main
case "${1:-all}" in
    prereq)
        check_prerequisites
        ;;
    bootstrap)
        check_prerequisites
        AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
        bootstrap_cdk
        ;;
    infra)
        deploy_infrastructure
        ;;
    backend)
        AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
        deploy_backend
        ;;
    frontend)
        deploy_frontend
        ;;
    rollback)
        rollback_backend "$@"
        ;;
    versions)
        list_versions
        ;;
    secret)
        set_polygon_secret
        ;;
    all)
        check_prerequisites
        AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
        bootstrap_cdk
        deploy_infrastructure
        deploy_backend
        deploy_frontend
        print_info
        ;;
    info)
        print_info
        ;;
    *)
        echo "Usage: $0 {prereq|bootstrap|infra|backend|frontend|rollback [VERSION]|versions|secret|all|info} [--skip-tests]"
        echo ""
        echo "Commands:"
        echo "  backend              Deploy backend Lambda (runs tests first)"
        echo "  backend --skip-tests Deploy backend Lambda without running tests"
        echo "  rollback             Rollback to previous Lambda version"
        echo "  rollback N           Rollback to specific Lambda version N"
        echo "  versions             List published Lambda versions"
        echo "  frontend             Build and deploy frontend"
        echo "  all                  Full deployment (infra + backend + frontend)"
        exit 1
        ;;
esac
