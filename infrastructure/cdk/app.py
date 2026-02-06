#!/usr/bin/env python3
"""AWS CDK App for OSS (Option Scanner System).

Deploys:
- DynamoDB tables for data persistence
- Lambda function for backend API
- API Gateway HTTP API
- S3 + CloudFront for frontend hosting
- Supporting infrastructure (IAM, Secrets)
"""

import os

import aws_cdk as cdk

from stacks.database_stack import DatabaseStack
from stacks.backend_stack import BackendStack
from stacks.frontend_stack import FrontendStack
from stacks.unusual_volume_stack import UnusualVolumeStack

# Get environment from context or default
app = cdk.App()
env_name = app.node.try_get_context("environment") or "dev"
project_name = app.node.try_get_context("project_name") or "oss"

# AWS environment (account and region)
env = cdk.Environment(
    account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
    region=os.environ.get("CDK_DEFAULT_REGION", "us-east-1"),
)

# Common tags for all resources
tags = {
    "Project": project_name,
    "Environment": env_name,
    "ManagedBy": "CDK",
}

# Create stacks
# 1. Database stack (DynamoDB tables)
database_stack = DatabaseStack(
    app,
    f"{project_name}-{env_name}-database",
    env=env,
    project_name=project_name,
    env_name=env_name,
)

# 3. Backend stack (Lambda, API Gateway)
backend_stack = BackendStack(
    app,
    f"{project_name}-{env_name}-backend",
    env=env,
    project_name=project_name,
    env_name=env_name,
    table_prefix=database_stack.table_prefix,
)
backend_stack.add_dependency(database_stack)

# 4. Frontend stack (S3, CloudFront)
frontend_stack = FrontendStack(
    app,
    f"{project_name}-{env_name}-frontend",
    env=env,
    project_name=project_name,
    env_name=env_name,
    backend_url=backend_stack.backend_url,
)
frontend_stack.add_dependency(backend_stack)

# 5. Unusual Volume Scanner stack (serverless fan-out architecture)
unusual_volume_stack = UnusualVolumeStack(
    app,
    f"{project_name}-{env_name}-unusual-volume",
    env=env,
    project_name=project_name,
    env_name=env_name,
    table_prefix=database_stack.table_prefix,
    polygon_secret=backend_stack.polygon_secret,
    evaluations_table=database_stack.evaluations_table,
)
unusual_volume_stack.add_dependency(database_stack)
unusual_volume_stack.add_dependency(backend_stack)

# Apply tags to all stacks
for stack in [database_stack, backend_stack, frontend_stack, unusual_volume_stack]:
    for key, value in tags.items():
        cdk.Tags.of(stack).add(key, value)

app.synth()
