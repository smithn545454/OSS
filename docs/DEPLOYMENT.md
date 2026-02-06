# OSS Deployment Guide

This guide covers deploying OSS (Option Scanner System) to AWS.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                          CloudFront                              │
│                    (Frontend CDN Distribution)                   │
└─────────────────────┬───────────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────────┐
│                         S3 Bucket                                │
│                    (React Frontend Assets)                       │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                    Application Load Balancer                     │
└─────────────────────┬───────────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────────┐
│                      ECS Fargate Service                         │
│                     (FastAPI Backend)                            │
│  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐        │
│  │   Task 1      │  │   Task 2      │  │   Task N      │        │
│  │   (Auto-      │  │   (Auto-      │  │   (Auto-      │        │
│  │   scaled)     │  │   scaled)     │  │   scaled)     │        │
│  └───────────────┘  └───────────────┘  └───────────────┘        │
└─────────────────────┬───────────────────────────────────────────┘
                      │
        ┌─────────────┴─────────────┐
        ▼                           ▼
┌───────────────┐           ┌───────────────┐
│   DynamoDB    │           │ Secrets Mgr   │
│   (10 tables) │           │ (Polygon Key) │
└───────────────┘           └───────────────┘
```

## Prerequisites

Before deploying, ensure you have:

1. **AWS Account** with appropriate permissions
2. **AWS CLI** installed and configured
3. **Docker** installed and running
4. **Node.js** (v18+) and npm
5. **Python 3.11+**
6. **Polygon.io API Key**

### Install AWS CLI

```bash
# macOS
brew install awscli

# Verify installation
aws --version
```

### Configure AWS Credentials

```bash
aws configure
# Enter your AWS Access Key ID, Secret Access Key, and default region (us-east-1)

# Verify credentials
aws sts get-caller-identity
```

### Install AWS CDK

```bash
npm install -g aws-cdk

# Verify installation
cdk --version
```

## Deployment Steps

### Quick Deploy (All-in-One)

```bash
# Set your Polygon API key
export POLYGON_API_KEY=your_polygon_api_key_here

# Run full deployment
chmod +x scripts/deploy.sh
./scripts/deploy.sh all
```

### Step-by-Step Deployment

#### 1. Bootstrap CDK (First Time Only)

```bash
./scripts/deploy.sh bootstrap
```

This sets up the CDK toolkit stack in your AWS account.

#### 2. Deploy Infrastructure

```bash
./scripts/deploy.sh infra
```

This creates:
- VPC with public/private subnets
- DynamoDB tables (10 tables)
- ECR repository
- ECS cluster
- Application Load Balancer
- S3 bucket for frontend
- CloudFront distribution
- Secrets Manager secret

#### 3. Set Polygon API Key

```bash
export POLYGON_API_KEY=your_polygon_api_key_here
./scripts/deploy.sh secret
```

#### 4. Deploy Backend

```bash
./scripts/deploy.sh backend
```

This builds the Docker image and deploys it to ECS Fargate.

#### 5. Deploy Frontend

```bash
./scripts/deploy.sh frontend
```

This builds the React app and deploys it to S3/CloudFront.

#### 6. Seed Default Policy

After deployment, seed the default policy:

```bash
# Get backend URL
./scripts/deploy.sh info

# Seed policy (replace URL)
curl -X POST http://YOUR_BACKEND_URL/api/policies/seed
```

## Environment Configuration

### Environment Variables

The backend uses these environment variables (set automatically by CDK):

| Variable | Description |
|----------|-------------|
| `APP_NAME` | Application name (OSS) |
| `APP_VERSION` | Application version |
| `DEBUG` | Debug mode (false in prod) |
| `AWS_REGION` | AWS region |
| `DYNAMODB_TABLE_PREFIX` | Prefix for DynamoDB tables |
| `POLYGON_API_KEY` | Polygon.io API key (from Secrets Manager) |

### Multiple Environments

Deploy to different environments:

```bash
# Development (default)
ENV_NAME=dev ./scripts/deploy.sh all

# Production
ENV_NAME=prod ./scripts/deploy.sh all
```

## Costs Estimate

| Service | Dev Environment | Production |
|---------|-----------------|------------|
| ECS Fargate (1 task) | ~$30/month | ~$60/month |
| Application Load Balancer | ~$20/month | ~$20/month |
| DynamoDB (on-demand) | ~$5/month | ~$20/month |
| S3 + CloudFront | ~$2/month | ~$10/month |
| NAT Gateway | ~$35/month | ~$35/month |
| **Total** | **~$90/month** | **~$145/month** |

### Cost Optimization Tips

1. **Reduce NAT Gateway costs**: Use VPC endpoints for AWS services
2. **Reserved capacity**: Use Fargate Spot for dev environments
3. **DynamoDB**: Use provisioned capacity if usage is predictable

## Monitoring & Logs

### CloudWatch Logs

View backend logs:

```bash
aws logs tail /ecs/oss-dev-backend --follow
```

### ECS Service Status

```bash
aws ecs describe-services \
  --cluster oss-dev-cluster \
  --services oss-dev-backend
```

### Health Check

```bash
curl http://YOUR_BACKEND_URL/health
```

## Troubleshooting

### Backend Not Starting

1. Check ECS task logs in CloudWatch
2. Verify Polygon API key is set in Secrets Manager
3. Check security group rules

### Frontend 403 Errors

1. Verify S3 bucket policy allows CloudFront access
2. Check Origin Access Identity configuration
3. Invalidate CloudFront cache

### DynamoDB Access Denied

1. Verify IAM role has correct permissions
2. Check table prefix matches configuration

## Cleanup

To destroy all resources:

```bash
cd infrastructure/cdk
source .venv/bin/activate
cdk destroy --all
```

**Warning**: This will delete all data in DynamoDB tables!

## Security Considerations

1. **Secrets**: API keys stored in Secrets Manager (encrypted)
2. **Network**: Backend runs in private subnets
3. **HTTPS**: CloudFront enforces HTTPS for frontend
4. **IAM**: Least-privilege IAM roles for ECS tasks
5. **S3**: Bucket is private, accessed only via CloudFront OAI
