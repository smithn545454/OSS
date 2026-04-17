#!/bin/bash
# Create the two Pillar v4 EventBridge daily-refresh rules.
#
# Mirrors what backend_stack.py defines for `price_history_refresh_rule`
# and `earnings_history_refresh_rule`, but runs directly against AWS to
# sidestep the CloudFormation drift on oss-dev-daily-data-capture that
# currently blocks `cdk deploy oss-dev-backend`.
#
# See docs/pillar_v4_execution_plan.md "Phase 1 Known Issues" for the
# drift resolution plan — until that's done, these rules are NOT in any
# CFN template.

set -euo pipefail

REGION="${AWS_REGION:-us-west-1}"
PROJECT="${PROJECT_NAME:-oss}"
ENV="${ENV_NAME:-dev}"
LAMBDA_NAME="${PROJECT}-${ENV}-backend"
LAMBDA_ARN="arn:aws:lambda:${REGION}:$(aws sts get-caller-identity --query Account --output text):function:${LAMBDA_NAME}"

echo "Region:       $REGION"
echo "Lambda:       $LAMBDA_NAME"
echo "Lambda ARN:   $LAMBDA_ARN"
echo

create_rule() {
    local RULE_NAME=$1
    local SCHEDULE=$2
    local DESCRIPTION=$3
    local ACTION=$4
    local STATEMENT_ID=$5

    echo "=== $RULE_NAME ==="
    echo "  schedule: $SCHEDULE"
    echo "  action:   $ACTION"

    aws events put-rule \
        --region "$REGION" \
        --name "$RULE_NAME" \
        --schedule-expression "$SCHEDULE" \
        --description "$DESCRIPTION" \
        --state ENABLED \
        --output text > /dev/null
    echo "  rule created"

    aws events put-targets \
        --region "$REGION" \
        --rule "$RULE_NAME" \
        --targets "[{\"Id\":\"Target0\",\"Arn\":\"${LAMBDA_ARN}\",\"Input\":\"{\\\"source\\\":\\\"oss.scheduler\\\",\\\"action\\\":\\\"${ACTION}\\\"}\"}]" \
        --output text > /dev/null
    echo "  target bound to Lambda"

    # Idempotent: remove existing permission if present, then re-add.
    aws lambda remove-permission \
        --region "$REGION" \
        --function-name "$LAMBDA_NAME" \
        --statement-id "$STATEMENT_ID" \
        2>/dev/null || true

    aws lambda add-permission \
        --region "$REGION" \
        --function-name "$LAMBDA_NAME" \
        --statement-id "$STATEMENT_ID" \
        --action lambda:InvokeFunction \
        --principal events.amazonaws.com \
        --source-arn "arn:aws:events:${REGION}:$(aws sts get-caller-identity --query Account --output text):rule/${RULE_NAME}" \
        --output text > /dev/null
    echo "  Lambda invoke permission granted"
    echo
}

create_rule \
    "oss-${ENV}-price-history-refresh" \
    "cron(0 5 ? * TUE-SAT *)" \
    "Pillar v4: append yesterday's daily bar for S&P 500 + Russell 1000 via Polygon grouped endpoint" \
    "price_history_refresh" \
    "EventsPriceHistoryRefresh"

create_rule \
    "oss-${ENV}-earnings-history-refresh" \
    "cron(0 6 ? * TUE-SAT *)" \
    "Pillar v4: recompute 1-day moves for recently-concluded earnings events" \
    "earnings_history_refresh" \
    "EventsEarningsHistoryRefresh"

echo "=== Verification ==="
aws events list-rules --region "$REGION" --name-prefix "oss-${ENV}-" \
    --query 'Rules[?contains(Name, `-history-refresh`)].[Name, ScheduleExpression, State]' \
    --output table
