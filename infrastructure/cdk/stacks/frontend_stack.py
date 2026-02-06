"""Frontend stack for OSS.

Creates S3 bucket and CloudFront distribution for the React frontend.
"""

from aws_cdk import (
    Stack,
    Duration,
    RemovalPolicy,
    aws_s3 as s3,
    aws_s3_deployment as s3_deployment,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    CfnOutput,
)
from constructs import Construct


class FrontendStack(Stack):
    """S3 + CloudFront frontend stack."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        project_name: str,
        env_name: str,
        backend_url: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.project_name = project_name
        self.env_name = env_name

        # S3 bucket for frontend assets
        self.bucket = s3.Bucket(
            self,
            "FrontendBucket",
            bucket_name=f"{project_name}-{env_name}-frontend-{self.account}",
            removal_policy=RemovalPolicy.DESTROY if env_name != "prod" else RemovalPolicy.RETAIN,
            auto_delete_objects=env_name != "prod",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
        )

        # Origin Access Identity for CloudFront
        origin_access_identity = cloudfront.OriginAccessIdentity(
            self,
            "OAI",
            comment=f"OAI for {project_name}-{env_name} frontend",
        )

        # Grant CloudFront access to the bucket
        self.bucket.grant_read(origin_access_identity)

        # CloudFront distribution
        self.distribution = cloudfront.Distribution(
            self,
            "Distribution",
            comment=f"{project_name}-{env_name} frontend distribution",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3Origin(
                    self.bucket,
                    origin_access_identity=origin_access_identity,
                ),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
                allowed_methods=cloudfront.AllowedMethods.ALLOW_GET_HEAD_OPTIONS,
            ),
            default_root_object="index.html",
            error_responses=[
                # Handle SPA routing - return index.html for 404s
                cloudfront.ErrorResponse(
                    http_status=404,
                    response_http_status=200,
                    response_page_path="/index.html",
                    ttl=Duration.minutes(5),
                ),
                cloudfront.ErrorResponse(
                    http_status=403,
                    response_http_status=200,
                    response_page_path="/index.html",
                    ttl=Duration.minutes(5),
                ),
            ],
            price_class=cloudfront.PriceClass.PRICE_CLASS_100,  # US, Canada, Europe
        )

        # Store URLs for reference
        self.frontend_url = f"https://{self.distribution.distribution_domain_name}"

        # Outputs
        CfnOutput(
            self,
            "FrontendUrl",
            value=self.frontend_url,
            description="Frontend URL (CloudFront)",
            export_name=f"{project_name}-{env_name}-frontend-url",
        )

        CfnOutput(
            self,
            "BucketName",
            value=self.bucket.bucket_name,
            description="S3 bucket for frontend assets",
            export_name=f"{project_name}-{env_name}-frontend-bucket",
        )

        CfnOutput(
            self,
            "DistributionId",
            value=self.distribution.distribution_id,
            description="CloudFront distribution ID (for cache invalidation)",
            export_name=f"{project_name}-{env_name}-distribution-id",
        )

        CfnOutput(
            self,
            "BackendApiUrl",
            value=backend_url,
            description="Backend API URL (configure in frontend)",
        )
