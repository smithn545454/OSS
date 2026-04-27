"""Backend stack for OSS.

Creates Lambda-based deployment for the FastAPI backend:
- Lambda function with Python 3.12 runtime
- API Gateway HTTP API
- Secrets Manager for API keys
- IAM roles with DynamoDB access
- CloudWatch Logs
- EventBridge scheduler for market hours scans
"""

from aws_cdk import (
    Stack,
    Duration,
    RemovalPolicy,
    aws_apigatewayv2 as apigw,
    aws_apigatewayv2_integrations as apigw_integrations,
    aws_events as events,
    aws_events_targets as targets,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_logs as logs,
    aws_secretsmanager as secretsmanager,
    CfnOutput,
)
from constructs import Construct


class BackendStack(Stack):
    """Lambda-based backend stack."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        project_name: str,
        env_name: str,
        table_prefix: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.project_name = project_name
        self.env_name = env_name

        # Secret for Polygon API key
        self.polygon_secret = secretsmanager.Secret(
            self,
            "PolygonApiKey",
            secret_name=f"{project_name}/{env_name}/polygon-api-key",
            description="Polygon.io API key for market data",
        )

        # Import existing Finnhub secret (created manually)
        self.finnhub_secret = secretsmanager.Secret.from_secret_name_v2(
            self,
            "FinnhubApiKey",
            secret_name=f"{project_name}/{env_name}/finnhub-api-key",
        )

        # Import existing Anthropic secret (for LLM thesis generation)
        self.anthropic_secret = secretsmanager.Secret.from_secret_name_v2(
            self,
            "AnthropicApiKey",
            secret_name="anthropic",
        )

        # CloudWatch Log Group for Lambda
        log_group = logs.LogGroup(
            self,
            "BackendLogGroup",
            log_group_name=f"/aws/lambda/{project_name}-{env_name}-backend",
            retention=logs.RetentionDays.TWO_WEEKS,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # Lambda execution role
        lambda_role = iam.Role(
            self,
            "LambdaExecutionRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                )
            ],
        )

        # Grant DynamoDB permissions
        lambda_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "dynamodb:GetItem",
                    "dynamodb:PutItem",
                    "dynamodb:UpdateItem",
                    "dynamodb:DeleteItem",
                    "dynamodb:Query",
                    "dynamodb:Scan",
                    "dynamodb:BatchGetItem",
                    "dynamodb:BatchWriteItem",
                ],
                resources=[
                    f"arn:aws:dynamodb:{self.region}:{self.account}:table/{table_prefix}-*",
                    f"arn:aws:dynamodb:{self.region}:{self.account}:table/{table_prefix}-*/index/*",
                ],
            )
        )

        # Grant Secrets Manager access
        self.polygon_secret.grant_read(lambda_role)
        self.finnhub_secret.grant_read(lambda_role)
        self.anthropic_secret.grant_read(lambda_role)

        # Grant S3 permissions for backtest data bucket
        lambda_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "s3:GetObject",
                    "s3:PutObject",
                    "s3:ListBucket",
                    "s3:DeleteObject",
                ],
                resources=[
                    f"arn:aws:s3:::{project_name}-{env_name}-backtest-{self.account}",
                    f"arn:aws:s3:::{project_name}-{env_name}-backtest-{self.account}/*",
                ],
            )
        )

        # Grant Lambda invoke permission (for chunked processing - coordinator invokes workers)
        lambda_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["lambda:InvokeFunction"],
                resources=[
                    f"arn:aws:lambda:{self.region}:{self.account}:function:{project_name}-{env_name}-backend",
                ],
            )
        )

        # Lambda function
        # Code will be deployed separately via deploy.sh
        self.lambda_function = lambda_.Function(
            self,
            "BackendFunction",
            function_name=f"{project_name}-{env_name}-backend",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="app.main.handler",
            code=lambda_.Code.from_asset(
                "../../backend",
                exclude=[
                    "venv",
                    "venv/*",
                    "__pycache__",
                    "*.pyc",
                    ".pytest_cache",
                    "tests",
                    "tests/*",
                    ".git",
                    ".gitignore",
                    "*.md",
                ],
            ),
            role=lambda_role,
            timeout=Duration.minutes(15),  # 15 min for backtest window processing (3 days × ~3 min)
            memory_size=3008,  # Account max — backtest uses cache eviction to stay within
            environment={
                "APP_NAME": "OSS",
                "APP_VERSION": "0.1.0",
                "DEBUG": "false" if env_name == "prod" else "true",
                "DYNAMODB_TABLE_PREFIX": table_prefix,
                "POLYGON_SECRET_ARN": self.polygon_secret.secret_arn,
                "FINNHUB_SECRET_ARN": self.finnhub_secret.secret_arn,
                "ANTHROPIC_SECRET_ARN": self.anthropic_secret.secret_arn,
                # Chunked processing configuration
                "USE_CHUNKED_PROCESSING": "true",
                "SCANNER_CHUNK_SIZE": "100",  # Process 100 tickers per worker
                "BACKTEST_S3_BUCKET": f"{project_name}-{env_name}-backtest-{self.account}",
            },
            log_group=log_group,
        )

        # API Gateway HTTP API
        self.http_api = apigw.HttpApi(
            self,
            "BackendApi",
            api_name=f"{project_name}-{env_name}-backend-api",
            cors_preflight=apigw.CorsPreflightOptions(
                allow_origins=["*"],
                allow_methods=[apigw.CorsHttpMethod.ANY],
                allow_headers=["*"],
            ),
        )

        # Lambda integration
        lambda_integration = apigw_integrations.HttpLambdaIntegration(
            "LambdaIntegration",
            self.lambda_function,
        )

        # Add route to handle all requests
        self.http_api.add_routes(
            path="/{proxy+}",
            methods=[apigw.HttpMethod.ANY],
            integration=lambda_integration,
        )

        # Add root route
        self.http_api.add_routes(
            path="/",
            methods=[apigw.HttpMethod.ANY],
            integration=lambda_integration,
        )

        # Backend URL
        self.backend_url = self.http_api.url

        # EventBridge rule for scheduled scans during market hours
        # Market hours: 9:30 AM - 4:00 PM ET = ~13:30 - 21:00 UTC (varies with DST)
        # Using 13:00 - 21:00 UTC to cover both EST and EDT
        # Runs every 10 minutes on weekdays
        self.scan_schedule_rule = events.Rule(
            self,
            "ScanScheduleRule",
            rule_name=f"{project_name}-{env_name}-scan-schedule",
            description="Trigger option scans every 10 minutes during market hours",
            schedule=events.Schedule.cron(
                minute="0/10",
                hour="13-21",
                week_day="MON-FRI",
            ),
            enabled=True,
        )

        # Add Lambda as target with scheduled scan event
        self.scan_schedule_rule.add_target(
            targets.LambdaFunction(
                self.lambda_function,
                event=events.RuleTargetInput.from_object({
                    "source": "oss.scheduler",
                    "action": "run_scan",
                }),
            )
        )

        # EventBridge rule for weekly v5 archetype rate recomputation.
        # Runs Monday 07:00 UTC — needs >= 5 business days of closes.
        # NOTE: This resource is documentation only. Per CLAUDE.md the
        # backend stack must never be `cdk deploy`ed (it re-uploads an
        # unpackaged Lambda zip that lacks dependencies). The actual
        # EventBridge rule is created via AWS CLI during Phase 2 rollout
        # and lives here so a future legitimate CDK deploy picks it up.
        self.calibration_weekly_rule = events.Rule(
            self,
            "CalibrationWeeklyRule",
            rule_name=f"{project_name}-{env_name}-calibration-weekly",
            description="Recompute v5 archetype HR/P rate lookups weekly",
            schedule=events.Schedule.cron(
                minute="0",
                hour="7",
                week_day="MON",
            ),
            enabled=True,
        )
        self.calibration_weekly_rule.add_target(
            targets.LambdaFunction(
                self.lambda_function,
                event=events.RuleTargetInput.from_object({
                    "source": "oss.scheduler",
                    "action": "calibration_weekly",
                }),
            )
        )

        # EventBridge rule for daily paper trading position updates
        # Runs once daily at 4:15 PM ET (21:15 UTC) after market close on weekdays
        self.paper_update_rule = events.Rule(
            self,
            "PaperTradingUpdateRule",
            rule_name=f"{project_name}-{env_name}-paper-trading-update",
            description="Update paper trading positions daily after market close",
            schedule=events.Schedule.cron(
                minute="15",
                hour="21",
                week_day="MON-FRI",
            ),
            enabled=True,
        )

        self.paper_update_rule.add_target(
            targets.LambdaFunction(
                self.lambda_function,
                event=events.RuleTargetInput.from_object({
                    "source": "oss.scheduler",
                    "action": "paper_trading_update",
                }),
            )
        )

        # EventBridge rule for daily earnings cache refresh
        # Runs once daily at 7:00 AM ET (12:00 UTC) before market open on weekdays
        self.earnings_refresh_rule = events.Rule(
            self,
            "EarningsRefreshRule",
            rule_name=f"{project_name}-{env_name}-earnings-refresh",
            description="Refresh earnings cache from Finnhub daily before market open",
            schedule=events.Schedule.cron(
                minute="0",
                hour="12",
                week_day="MON-FRI",
            ),
            enabled=True,
        )

        self.earnings_refresh_rule.add_target(
            targets.LambdaFunction(
                self.lambda_function,
                event=events.RuleTargetInput.from_object({
                    "source": "oss.scheduler",
                    "action": "earnings_refresh",
                }),
            )
        )

        # EventBridge rule for Pillar v4 daily price-history refresh.
        # Appends yesterday's bar for every S&P 500 + Russell 1000 ticker
        # (plus SPY and SPDR sector ETFs) via Polygon's grouped endpoint.
        # Runs at 05:00 UTC Tue-Sat so Friday's close is available before
        # the next pipeline scan on Monday.
        self.price_history_refresh_rule = events.Rule(
            self,
            "PriceHistoryRefreshRule",
            rule_name=f"{project_name}-{env_name}-price-history-refresh",
            description="Append yesterday's daily bar for Pillar v4 price-history table",
            schedule=events.Schedule.cron(
                minute="0",
                hour="5",
                week_day="TUE-SAT",
            ),
            enabled=True,
        )
        self.price_history_refresh_rule.add_target(
            targets.LambdaFunction(
                self.lambda_function,
                event=events.RuleTargetInput.from_object({
                    "source": "oss.scheduler",
                    "action": "price_history_refresh",
                }),
            )
        )

        # EventBridge rule for Convex Mode kinetic-universe construction.
        # Runs monthly on the 1st at 02:00 UTC (well outside market hours)
        # to refresh the eligible universe from price-history + Polygon
        # ticker reference data. Disabled by default; enabled at cutover.
        self.convex_universe_refresh_rule = events.Rule(
            self,
            "ConvexUniverseRefreshRule",
            rule_name=f"{project_name}-{env_name}-convex-universe-refresh",
            description="Monthly Convex Mode kinetic-universe snapshot rebuild",
            schedule=events.Schedule.cron(
                minute="0",
                hour="2",
                day="1",
                month="*",
                year="*",
            ),
            enabled=False,
        )
        self.convex_universe_refresh_rule.add_target(
            targets.LambdaFunction(
                self.lambda_function,
                event=events.RuleTargetInput.from_object({
                    "source": "oss.scheduler",
                    "action": "convex_universe_refresh",
                }),
            )
        )

        # EventBridge rule for daily Convex pipeline run.
        # Runs weekdays at 22:30 UTC — after the 22:00 daily data-capture
        # settles and before pre-market alerts. Disabled by default; the
        # cutover plan flips it on once Phase 8 backtest validation is done.
        self.convex_daily_rule = events.Rule(
            self,
            "ConvexDailyRule",
            rule_name=f"{project_name}-{env_name}-convex-daily-run",
            description="Daily Convex Mode four-stage pipeline run",
            schedule=events.Schedule.cron(
                minute="30",
                hour="22",
                week_day="MON-FRI",
            ),
            enabled=False,
        )
        self.convex_daily_rule.add_target(
            targets.LambdaFunction(
                self.lambda_function,
                event=events.RuleTargetInput.from_object({
                    "source": "oss.scheduler",
                    "action": "convex_daily_run",
                }),
            )
        )

        # EventBridge rule for Pillar v4 earnings-history 1-day-move
        # backfill. Runs 60 minutes after the price-history refresh so
        # yesterday's bars are guaranteed present when we compute moves.
        self.earnings_history_refresh_rule = events.Rule(
            self,
            "EarningsHistoryRefreshRule",
            rule_name=f"{project_name}-{env_name}-earnings-history-refresh",
            description="Recompute 1-day moves for recently-concluded earnings events",
            schedule=events.Schedule.cron(
                minute="0",
                hour="6",
                week_day="TUE-SAT",
            ),
            enabled=True,
        )
        self.earnings_history_refresh_rule.add_target(
            targets.LambdaFunction(
                self.lambda_function,
                event=events.RuleTargetInput.from_object({
                    "source": "oss.scheduler",
                    "action": "earnings_history_refresh",
                }),
            )
        )

        # EventBridge rule for daily market data capture for backtesting
        # Runs once daily at 5:00 PM ET (22:00 UTC) after market close on weekdays
        # Captures stock OHLCV, options chains, IV history, and market context to S3
        self.data_capture_rule = events.Rule(
            self,
            "DailyDataCaptureRule",
            rule_name=f"{project_name}-{env_name}-daily-data-capture",
            description="Capture daily market data for backtesting after market close",
            schedule=events.Schedule.cron(
                minute="0",
                hour="22",
                week_day="MON-FRI",
            ),
            enabled=True,
        )

        self.data_capture_rule.add_target(
            targets.LambdaFunction(
                self.lambda_function,
                event=events.RuleTargetInput.from_object({
                    "source": "oss.scheduler",
                    "action": "daily_data_capture",
                }),
            )
        )

        # Outputs
        CfnOutput(
            self,
            "BackendUrl",
            value=self.backend_url or "",
            description="Backend API URL",
            export_name=f"{project_name}-{env_name}-backend-url",
        )

        CfnOutput(
            self,
            "LambdaFunctionName",
            value=self.lambda_function.function_name,
            description="Lambda Function Name",
            export_name=f"{project_name}-{env_name}-lambda-name",
        )

        CfnOutput(
            self,
            "LambdaFunctionArn",
            value=self.lambda_function.function_arn,
            description="Lambda Function ARN",
            export_name=f"{project_name}-{env_name}-lambda-arn",
        )

        CfnOutput(
            self,
            "PolygonSecretArn",
            value=self.polygon_secret.secret_arn,
            description="Polygon API Key Secret ARN (set value in AWS Console)",
            export_name=f"{project_name}-{env_name}-polygon-secret-arn",
        )
