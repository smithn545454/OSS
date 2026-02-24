"""Database stack for OSS.

Creates DynamoDB tables for all application data:
- Policies
- Opportunities
- Evaluations
- Pipeline runs
- Stage events
- Paper positions
- Feature values
- Pillar scores
- Gate results
- IV history
- OI history
- Backtest runs
- Backtest trades
- Backtest insights

Also creates:
- S3 bucket for backtest historical data
"""

from aws_cdk import (
    Duration,
    Stack,
    RemovalPolicy,
    aws_dynamodb as dynamodb,
    aws_s3 as s3,
    CfnOutput,
)
from constructs import Construct


class DatabaseStack(Stack):
    """DynamoDB tables stack."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        project_name: str,
        env_name: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.project_name = project_name
        self.env_name = env_name
        self.table_prefix = f"{project_name}-{env_name}"

        # Removal policy: RETAIN for prod, DESTROY for dev
        removal_policy = (
            RemovalPolicy.RETAIN if env_name == "prod" else RemovalPolicy.DESTROY
        )

        # Common table settings
        billing_mode = dynamodb.BillingMode.PAY_PER_REQUEST

        # 1. Policies table
        self.policies_table = dynamodb.Table(
            self,
            "PoliciesTable",
            table_name=f"{self.table_prefix}-policies",
            partition_key=dynamodb.Attribute(
                name="PK", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="SK", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=billing_mode,
            removal_policy=removal_policy,
        )

        # 2. Opportunities table (with GSI for date queries)
        self.opportunities_table = dynamodb.Table(
            self,
            "OpportunitiesTable",
            table_name=f"{self.table_prefix}-opportunities",
            partition_key=dynamodb.Attribute(
                name="PK", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="SK", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=billing_mode,
            removal_policy=removal_policy,
        )
        self.opportunities_table.add_global_secondary_index(
            index_name="GSI1",
            partition_key=dynamodb.Attribute(
                name="GSI1PK", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="GSI1SK", type=dynamodb.AttributeType.STRING
            ),
        )

        # 3. Evaluations table (with GSIs for verdict and date queries)
        self.evaluations_table = dynamodb.Table(
            self,
            "EvaluationsTable",
            table_name=f"{self.table_prefix}-evaluations",
            partition_key=dynamodb.Attribute(
                name="PK", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="SK", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=billing_mode,
            removal_policy=removal_policy,
        )
        self.evaluations_table.add_global_secondary_index(
            index_name="GSI1",
            partition_key=dynamodb.Attribute(
                name="GSI1PK", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="GSI1SK", type=dynamodb.AttributeType.STRING
            ),
        )
        self.evaluations_table.add_global_secondary_index(
            index_name="GSI2",
            partition_key=dynamodb.Attribute(
                name="GSI2PK", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="GSI2SK", type=dynamodb.AttributeType.STRING
            ),
        )

        # 4. Pipeline runs table
        self.pipeline_runs_table = dynamodb.Table(
            self,
            "PipelineRunsTable",
            table_name=f"{self.table_prefix}-pipeline-runs",
            partition_key=dynamodb.Attribute(
                name="PK", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="SK", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=billing_mode,
            removal_policy=removal_policy,
        )

        # 5. Stage events table
        self.stage_events_table = dynamodb.Table(
            self,
            "StageEventsTable",
            table_name=f"{self.table_prefix}-stage-events",
            partition_key=dynamodb.Attribute(
                name="PK", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="SK", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=billing_mode,
            removal_policy=removal_policy,
        )

        # 6. Paper positions table
        # NOTE: GSI1 and GSI2 exist on the live table but were added outside CloudFormation.
        # Do NOT declare them here — CF would try to re-create them and fail with
        # "Cannot perform more than one GSI creation or deletion in a single update".
        # The GSIs are: GSI1 (evaluation_id lookup), GSI2 (option_ticker dedup).
        self.paper_positions_table = dynamodb.Table(
            self,
            "PaperPositionsTable",
            table_name=f"{self.table_prefix}-paper-positions",
            partition_key=dynamodb.Attribute(
                name="PK", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="SK", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=billing_mode,
            removal_policy=removal_policy,
        )

        # 7. Feature values table
        self.feature_values_table = dynamodb.Table(
            self,
            "FeatureValuesTable",
            table_name=f"{self.table_prefix}-feature-values",
            partition_key=dynamodb.Attribute(
                name="PK", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="SK", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=billing_mode,
            removal_policy=removal_policy,
        )

        # 8. Pillar scores table
        self.pillar_scores_table = dynamodb.Table(
            self,
            "PillarScoresTable",
            table_name=f"{self.table_prefix}-pillar-scores",
            partition_key=dynamodb.Attribute(
                name="PK", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="SK", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=billing_mode,
            removal_policy=removal_policy,
        )

        # 9. Gate results table
        self.gate_results_table = dynamodb.Table(
            self,
            "GateResultsTable",
            table_name=f"{self.table_prefix}-gate-results",
            partition_key=dynamodb.Attribute(
                name="PK", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="SK", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=billing_mode,
            removal_policy=removal_policy,
        )

        # 10. IV history table (with TTL for 365-day retention)
        self.iv_history_table = dynamodb.Table(
            self,
            "IVHistoryTable",
            table_name=f"{self.table_prefix}-iv-history",
            partition_key=dynamodb.Attribute(
                name="PK", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="SK", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=billing_mode,
            removal_policy=removal_policy,
            time_to_live_attribute="ttl",  # Enable TTL
        )

        # 11. OI history table (with TTL for 90-day retention)
        self.oi_history_table = dynamodb.Table(
            self,
            "OIHistoryTable",
            table_name=f"{self.table_prefix}-oi-history",
            partition_key=dynamodb.Attribute(
                name="PK", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="SK", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=billing_mode,
            removal_policy=removal_policy,
            time_to_live_attribute="ttl",  # Enable TTL
        )

        # 12. Earnings cache table (with TTL for automatic cleanup)
        self.earnings_cache_table = dynamodb.Table(
            self,
            "EarningsCacheTable",
            table_name=f"{self.table_prefix}-earnings-cache",
            partition_key=dynamodb.Attribute(
                name="ticker", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=billing_mode,
            removal_policy=removal_policy,
            time_to_live_attribute="ttl",  # Enable TTL for automatic cleanup
        )

        # ============================================================
        # Backtest S3 Bucket
        # ============================================================
        self.backtest_bucket = s3.Bucket(
            self,
            "BacktestDataBucket",
            bucket_name=f"{self.table_prefix}-backtest-{self.account}",
            removal_policy=removal_policy,
            auto_delete_objects=env_name != "prod",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            lifecycle_rules=[
                s3.LifecycleRule(
                    transitions=[
                        s3.Transition(
                            storage_class=s3.StorageClass.INFREQUENT_ACCESS,
                            transition_after=Duration.days(90),
                        )
                    ]
                )
            ],
        )

        # ============================================================
        # Backtest DynamoDB Tables
        # ============================================================

        # 13. Backtest runs table
        self.backtest_runs_table = dynamodb.Table(
            self,
            "BacktestRunsTable",
            table_name=f"{self.table_prefix}-backtest-runs",
            partition_key=dynamodb.Attribute(
                name="PK", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="SK", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=billing_mode,
            removal_policy=removal_policy,
        )
        self.backtest_runs_table.add_global_secondary_index(
            index_name="GSI1",
            partition_key=dynamodb.Attribute(
                name="GSI1PK", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="GSI1SK", type=dynamodb.AttributeType.STRING
            ),
        )

        # 14. Backtest trades table
        self.backtest_trades_table = dynamodb.Table(
            self,
            "BacktestTradesTable",
            table_name=f"{self.table_prefix}-backtest-trades",
            partition_key=dynamodb.Attribute(
                name="PK", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="SK", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=billing_mode,
            removal_policy=removal_policy,
        )
        self.backtest_trades_table.add_global_secondary_index(
            index_name="GSI1",
            partition_key=dynamodb.Attribute(
                name="GSI1PK", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="GSI1SK", type=dynamodb.AttributeType.STRING
            ),
        )
        self.backtest_trades_table.add_global_secondary_index(
            index_name="GSI2",
            partition_key=dynamodb.Attribute(
                name="GSI2PK", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="GSI2SK", type=dynamodb.AttributeType.STRING
            ),
        )

        # 15. Backtest insights table
        self.backtest_insights_table = dynamodb.Table(
            self,
            "BacktestInsightsTable",
            table_name=f"{self.table_prefix}-backtest-insights",
            partition_key=dynamodb.Attribute(
                name="PK", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="SK", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=billing_mode,
            removal_policy=removal_policy,
        )

        # Collect all tables for permissions
        self.all_tables = [
            self.policies_table,
            self.opportunities_table,
            self.evaluations_table,
            self.pipeline_runs_table,
            self.stage_events_table,
            self.paper_positions_table,
            self.feature_values_table,
            self.pillar_scores_table,
            self.gate_results_table,
            self.iv_history_table,
            self.oi_history_table,
            self.earnings_cache_table,
            self.backtest_runs_table,
            self.backtest_trades_table,
            self.backtest_insights_table,
        ]

        # Outputs
        CfnOutput(
            self,
            "TablePrefix",
            value=self.table_prefix,
            description="DynamoDB table prefix",
            export_name=f"{project_name}-{env_name}-table-prefix",
        )

        CfnOutput(
            self,
            "BacktestBucketName",
            value=self.backtest_bucket.bucket_name,
            description="Backtest data S3 bucket name",
            export_name=f"{project_name}-{env_name}-backtest-bucket",
        )
