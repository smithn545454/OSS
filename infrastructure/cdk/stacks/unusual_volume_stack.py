"""Unusual Volume Scanner stack for OSS.

Creates serverless infrastructure for the contract-level unusual volume scanner:
- DynamoDB tables (sp500-tickers, volume-stats, unusual-volume-candidates, scan-runs, underlying-stats)
- SNS topic for fan-out
- SQS queue with dead letter queue for worker processing
- Lambda functions (Publisher, Worker, Aggregator, Handoff, Nightly Stats)
- EventBridge rules for scheduled triggers
- DynamoDB Streams for handoff processing
"""

from aws_cdk import (
    Stack,
    Duration,
    RemovalPolicy,
    aws_dynamodb as dynamodb,
    aws_events as events,
    aws_events_targets as targets,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_lambda_event_sources as lambda_event_sources,
    aws_logs as logs,
    aws_secretsmanager as secretsmanager,
    aws_sns as sns,
    aws_sns_subscriptions as sns_subscriptions,
    aws_sqs as sqs,
    CfnOutput,
)
from constructs import Construct


class UnusualVolumeStack(Stack):
    """Unusual Volume Scanner serverless stack."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        project_name: str,
        env_name: str,
        table_prefix: str,
        polygon_secret: secretsmanager.ISecret,
        evaluations_table: dynamodb.ITable,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.project_name = project_name
        self.env_name = env_name
        self.table_prefix = table_prefix

        # Removal policy: RETAIN for prod, DESTROY for dev
        removal_policy = (
            RemovalPolicy.RETAIN if env_name == "prod" else RemovalPolicy.DESTROY
        )
        billing_mode = dynamodb.BillingMode.PAY_PER_REQUEST

        # ========================================================================
        # DynamoDB Tables
        # ========================================================================

        # 1. SP500 Tickers table
        self.sp500_tickers_table = dynamodb.Table(
            self,
            "SP500TickersTable",
            table_name=f"{table_prefix}-sp500-tickers",
            partition_key=dynamodb.Attribute(
                name="PK", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="SK", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=billing_mode,
            removal_policy=removal_policy,
        )

        # 2. Volume Stats table (with GSI for underlying-date queries)
        self.volume_stats_table = dynamodb.Table(
            self,
            "VolumeStatsTable",
            table_name=f"{table_prefix}-volume-stats",
            partition_key=dynamodb.Attribute(
                name="PK", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="SK", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=billing_mode,
            removal_policy=removal_policy,
            time_to_live_attribute="ttl",
        )
        self.volume_stats_table.add_global_secondary_index(
            index_name="underlying-date-index",
            partition_key=dynamodb.Attribute(
                name="underlying_ticker", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="as_of_date", type=dynamodb.AttributeType.STRING
            ),
        )

        # 3. Unusual Volume Candidates table (with GSI and DynamoDB Streams)
        self.candidates_table = dynamodb.Table(
            self,
            "UnusualVolumeCandidatesTable",
            table_name=f"{table_prefix}-unusual-volume-candidates",
            partition_key=dynamodb.Attribute(
                name="PK", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="SK", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=billing_mode,
            removal_policy=removal_policy,
            time_to_live_attribute="ttl",
            stream=dynamodb.StreamViewType.NEW_IMAGE,  # Enable streams for handoff
        )
        self.candidates_table.add_global_secondary_index(
            index_name="handoff-status-index",
            partition_key=dynamodb.Attribute(
                name="handoff_status", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="created_at", type=dynamodb.AttributeType.STRING
            ),
        )

        # 4. Scan Runs table
        self.scan_runs_table = dynamodb.Table(
            self,
            "ScanRunsTable",
            table_name=f"{table_prefix}-scan-runs",
            partition_key=dynamodb.Attribute(
                name="PK", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="SK", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=billing_mode,
            removal_policy=removal_policy,
            time_to_live_attribute="ttl",
        )

        # 5. Underlying Stats table
        self.underlying_stats_table = dynamodb.Table(
            self,
            "UnderlyingStatsTable",
            table_name=f"{table_prefix}-underlying-stats",
            partition_key=dynamodb.Attribute(
                name="PK", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="SK", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=billing_mode,
            removal_policy=removal_policy,
        )

        # ========================================================================
        # SNS Topic for Fan-Out
        # ========================================================================

        self.scan_topic = sns.Topic(
            self,
            "UnusualVolumeScanTopic",
            topic_name=f"{project_name}-{env_name}-unusual-volume-scan-requests",
            display_name="Unusual Volume Scan Requests",
        )

        # ========================================================================
        # SQS Queue with Dead Letter Queue
        # ========================================================================

        # Dead letter queue
        self.dlq = sqs.Queue(
            self,
            "UnusualVolumeDLQ",
            queue_name=f"{project_name}-{env_name}-unusual-volume-dlq",
            retention_period=Duration.days(7),
        )

        # Main processing queue
        self.scan_queue = sqs.Queue(
            self,
            "UnusualVolumeScanQueue",
            queue_name=f"{project_name}-{env_name}-unusual-volume-scan-queue",
            visibility_timeout=Duration.seconds(90),  # Match worker timeout
            retention_period=Duration.hours(1),
            dead_letter_queue=sqs.DeadLetterQueue(
                max_receive_count=3,
                queue=self.dlq,
            ),
        )

        # Subscribe queue to SNS topic
        self.scan_topic.add_subscription(
            sns_subscriptions.SqsSubscription(
                self.scan_queue,
                raw_message_delivery=True,
            )
        )

        # ========================================================================
        # Lambda Functions
        # ========================================================================

        # Common Lambda configuration
        lambda_runtime = lambda_.Runtime.PYTHON_3_12
        lambda_code_path = "../../backend/lambdas/unusual_volume"

        # Common environment variables
        common_env = {
            "DYNAMODB_TABLE_PREFIX": table_prefix,
            "POLYGON_SECRET_ARN": polygon_secret.secret_arn,
        }

        # Publisher Lambda
        self.publisher_lambda = self._create_lambda(
            "PublisherLambda",
            f"{project_name}-{env_name}-uv-publisher",
            "publisher.lambda_handler",
            lambda_code_path,
            lambda_runtime,
            timeout=Duration.seconds(30),
            memory_size=256,
            environment={
                **common_env,
                "SNS_TOPIC_ARN": self.scan_topic.topic_arn,
            },
            removal_policy=removal_policy,
        )
        self.scan_topic.grant_publish(self.publisher_lambda)
        self.sp500_tickers_table.grant_read_data(self.publisher_lambda)
        self.scan_runs_table.grant_read_write_data(self.publisher_lambda)
        polygon_secret.grant_read(self.publisher_lambda)

        # Worker Lambda
        self.worker_lambda = self._create_lambda(
            "WorkerLambda",
            f"{project_name}-{env_name}-uv-worker",
            "worker.lambda_handler",
            lambda_code_path,
            lambda_runtime,
            timeout=Duration.seconds(90),
            memory_size=512,
            reserved_concurrent_executions=500,  # Match ticker count
            environment={
                **common_env,
                "VOLUME_RATIO_THRESHOLD": "2.0",
                "OI_CHANGE_THRESHOLD_PCT": "15.0",
            },
            removal_policy=removal_policy,
        )
        # Add SQS trigger
        self.worker_lambda.add_event_source(
            lambda_event_sources.SqsEventSource(
                self.scan_queue,
                batch_size=1,
            )
        )
        self.volume_stats_table.grant_read_data(self.worker_lambda)
        self.candidates_table.grant_read_write_data(self.worker_lambda)
        polygon_secret.grant_read(self.worker_lambda)

        # Aggregator Lambda
        self.aggregator_lambda = self._create_lambda(
            "AggregatorLambda",
            f"{project_name}-{env_name}-uv-aggregator",
            "aggregator.lambda_handler",
            lambda_code_path,
            lambda_runtime,
            timeout=Duration.seconds(60),
            memory_size=256,
            environment=common_env,
            removal_policy=removal_policy,
        )
        self.scan_runs_table.grant_read_write_data(self.aggregator_lambda)
        self.candidates_table.grant_read_data(self.aggregator_lambda)

        # Handoff Lambda (triggered by DynamoDB Streams)
        self.handoff_lambda = self._create_lambda(
            "HandoffLambda",
            f"{project_name}-{env_name}-uv-handoff",
            "handoff.lambda_handler",
            lambda_code_path,
            lambda_runtime,
            timeout=Duration.seconds(30),
            memory_size=256,
            reserved_concurrent_executions=100,
            environment={
                **common_env,
                "MIN_UNDERLYING_PRICE": "5.0",
                "MIN_AVG_DOLLAR_VOLUME": "20000000",
                "MAX_MISSING_BARS": "2",
            },
            removal_policy=removal_policy,
        )
        # Add DynamoDB Stream trigger
        self.handoff_lambda.add_event_source(
            lambda_event_sources.DynamoEventSource(
                self.candidates_table,
                starting_position=lambda_.StartingPosition.TRIM_HORIZON,
                batch_size=10,
                retry_attempts=3,
            )
        )
        self.candidates_table.grant_read_write_data(self.handoff_lambda)
        self.underlying_stats_table.grant_read_data(self.handoff_lambda)
        evaluations_table.grant_read_write_data(self.handoff_lambda)

        # Nightly Stats Lambda
        self.nightly_stats_lambda = self._create_lambda(
            "NightlyStatsLambda",
            f"{project_name}-{env_name}-uv-nightly-stats",
            "nightly_stats.lambda_handler",
            lambda_code_path,
            lambda_runtime,
            timeout=Duration.minutes(15),
            memory_size=1024,
            environment=common_env,
            removal_policy=removal_policy,
        )
        self.sp500_tickers_table.grant_read_data(self.nightly_stats_lambda)
        self.volume_stats_table.grant_read_write_data(self.nightly_stats_lambda)
        polygon_secret.grant_read(self.nightly_stats_lambda)
        # Grant access to OI history table
        self.nightly_stats_lambda.role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "dynamodb:Query",
                    "dynamodb:GetItem",
                ],
                resources=[
                    f"arn:aws:dynamodb:{self.region}:{self.account}:table/{table_prefix}-oi-history",
                    f"arn:aws:dynamodb:{self.region}:{self.account}:table/{table_prefix}-oi-history/index/*",
                ],
            )
        )

        # ========================================================================
        # EventBridge Rules
        # ========================================================================

        # Scanner schedule: Every 15 minutes during market hours (9:30 AM - 4:00 PM ET)
        # Using UTC times that cover EST and EDT
        self.scan_schedule_rule = events.Rule(
            self,
            "UnusualVolumeScanSchedule",
            rule_name=f"{project_name}-{env_name}-uv-scan-schedule",
            description="Trigger unusual volume scanner every 15 minutes during market hours",
            schedule=events.Schedule.cron(
                minute="0/15",
                hour="13-21",  # 13:00-21:00 UTC covers market hours
                week_day="MON-FRI",
            ),
            enabled=True,
        )
        self.scan_schedule_rule.add_target(
            targets.LambdaFunction(self.publisher_lambda)
        )

        # Aggregator schedule: 2 minutes after each scan
        self.aggregator_schedule_rule = events.Rule(
            self,
            "UnusualVolumeAggregatorSchedule",
            rule_name=f"{project_name}-{env_name}-uv-aggregator-schedule",
            description="Trigger aggregator 2 minutes after each scan",
            schedule=events.Schedule.cron(
                minute="2/15",
                hour="13-21",
                week_day="MON-FRI",
            ),
            enabled=True,
        )
        self.aggregator_schedule_rule.add_target(
            targets.LambdaFunction(self.aggregator_lambda)
        )

        # Nightly stats job: 8:00 PM ET (01:00 UTC next day during EST, 00:00 UTC during EDT)
        self.nightly_schedule_rule = events.Rule(
            self,
            "UnusualVolumeNightlySchedule",
            rule_name=f"{project_name}-{env_name}-uv-nightly-schedule",
            description="Trigger nightly volume stats computation at 8 PM ET",
            schedule=events.Schedule.cron(
                minute="0",
                hour="1",  # 1:00 UTC = 8:00 PM EST / 9:00 PM EDT
                week_day="MON-FRI",
            ),
            enabled=True,
        )
        self.nightly_schedule_rule.add_target(
            targets.LambdaFunction(self.nightly_stats_lambda)
        )

        # ========================================================================
        # Outputs
        # ========================================================================

        CfnOutput(
            self,
            "ScanTopicArn",
            value=self.scan_topic.topic_arn,
            description="SNS Topic ARN for unusual volume scan requests",
        )

        CfnOutput(
            self,
            "ScanQueueUrl",
            value=self.scan_queue.queue_url,
            description="SQS Queue URL for unusual volume scan processing",
        )

        CfnOutput(
            self,
            "PublisherLambdaArn",
            value=self.publisher_lambda.function_arn,
            description="Publisher Lambda ARN",
        )

        CfnOutput(
            self,
            "WorkerLambdaArn",
            value=self.worker_lambda.function_arn,
            description="Worker Lambda ARN",
        )

    def _create_lambda(
        self,
        id: str,
        function_name: str,
        handler: str,
        code_path: str,
        runtime: lambda_.Runtime,
        timeout: Duration,
        memory_size: int,
        environment: dict,
        removal_policy: RemovalPolicy,
        reserved_concurrent_executions: int = None,
    ) -> lambda_.Function:
        """Create a Lambda function with standard configuration."""

        log_group = logs.LogGroup(
            self,
            f"{id}LogGroup",
            log_group_name=f"/aws/lambda/{function_name}",
            retention=logs.RetentionDays.TWO_WEEKS,
            removal_policy=removal_policy,
        )

        lambda_role = iam.Role(
            self,
            f"{id}Role",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                )
            ],
        )

        function = lambda_.Function(
            self,
            id,
            function_name=function_name,
            runtime=runtime,
            handler=handler,
            code=lambda_.Code.from_asset(
                code_path,
                exclude=[
                    "__pycache__",
                    "*.pyc",
                    ".pytest_cache",
                    "tests",
                    "tests/*",
                ],
            ),
            role=lambda_role,
            timeout=timeout,
            memory_size=memory_size,
            reserved_concurrent_executions=reserved_concurrent_executions,
            environment=environment,
            log_group=log_group,
        )

        return function
