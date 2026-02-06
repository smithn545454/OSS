"""Application configuration loaded from environment variables."""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)


def _get_secret_from_secrets_manager(secret_arn_env_var: str, service_name: str) -> Optional[str]:
    """Fetch an API key from AWS Secrets Manager.
    
    Args:
        secret_arn_env_var: Environment variable name containing the secret ARN
        service_name: Name of the service (for logging)
    
    Returns:
        The API key if found, None otherwise
    """
    secret_arn = os.environ.get(secret_arn_env_var)
    if not secret_arn:
        return None
    
    try:
        import boto3
        
        # Get region from ARN or environment
        # ARN format: arn:aws:secretsmanager:region:account:secret:name
        region = secret_arn.split(":")[3] if ":" in secret_arn else os.environ.get("AWS_REGION", "us-east-1")
        
        client = boto3.client("secretsmanager", region_name=region)
        response = client.get_secret_value(SecretId=secret_arn)
        
        secret_value = response.get("SecretString")
        if secret_value:
            logger.info(f"Successfully loaded {service_name} API key from Secrets Manager")
            return secret_value
            
    except Exception as e:
        logger.warning(f"Failed to get {service_name} API key from Secrets Manager: {e}")
    
    return None


def _get_polygon_api_key_from_secrets_manager() -> Optional[str]:
    """Fetch Polygon API key from AWS Secrets Manager."""
    return _get_secret_from_secrets_manager("POLYGON_SECRET_ARN", "Polygon")


def _get_finnhub_api_key_from_secrets_manager() -> Optional[str]:
    """Fetch Finnhub API key from AWS Secrets Manager."""
    return _get_secret_from_secrets_manager("FINNHUB_SECRET_ARN", "Finnhub")


def _get_anthropic_api_key_from_secrets_manager() -> Optional[str]:
    """Fetch Anthropic API key from AWS Secrets Manager."""
    return _get_secret_from_secrets_manager("ANTHROPIC_SECRET_ARN", "Anthropic")


def _get_openai_api_key_from_secrets_manager() -> Optional[str]:
    """Fetch OpenAI API key from AWS Secrets Manager."""
    return _get_secret_from_secrets_manager("OPENAI_SECRET_ARN", "OpenAI")


class Settings(BaseSettings):
    """Application settings loaded from environment."""

    # Application
    app_name: str = "OSS"
    app_version: str = "0.1.0"
    debug: bool = False

    # AWS
    aws_region: str = "us-east-1"
    aws_access_key_id: Optional[str] = None
    aws_secret_access_key: Optional[str] = None

    # DynamoDB
    dynamodb_endpoint: Optional[str] = None  # For local development
    dynamodb_table_prefix: str = "oss"

    # Polygon.io - can be set directly or via POLYGON_SECRET_ARN
    polygon_api_key: Optional[str] = None

    # Finnhub - can be set directly or via FINNHUB_SECRET_ARN
    finnhub_api_key: Optional[str] = None

    # Anthropic - can be set directly or via ANTHROPIC_SECRET_ARN
    anthropic_api_key: Optional[str] = None

    # OpenAI - can be set directly or via OPENAI_SECRET_ARN
    openai_api_key: Optional[str] = None

    # Slack webhook for opportunity alerts (optional)
    slack_webhook_url: Optional[str] = None

    # CORS (comma-separated string, will be split)
    # Default to "*" for simplicity; can be overridden via CORS_ORIGINS env var
    cors_origins: str = "*"

    @property
    def cors_origins_list(self) -> list[str]:
        """Get CORS origins as a list."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance.
    
    Fetches API keys from AWS Secrets Manager if the corresponding
    secret ARN environment variables are set.
    """
    settings = Settings()
    
    # Collect overrides from Secrets Manager
    overrides = {}
    
    # Polygon API key
    if not settings.polygon_api_key:
        polygon_key = _get_polygon_api_key_from_secrets_manager()
        if polygon_key:
            overrides["polygon_api_key"] = polygon_key
    
    # Finnhub API key
    if not settings.finnhub_api_key:
        finnhub_key = _get_finnhub_api_key_from_secrets_manager()
        if finnhub_key:
            overrides["finnhub_api_key"] = finnhub_key
    
    # Anthropic API key
    if not settings.anthropic_api_key:
        anthropic_key = _get_anthropic_api_key_from_secrets_manager()
        if anthropic_key:
            overrides["anthropic_api_key"] = anthropic_key
    
    # OpenAI API key
    if not settings.openai_api_key:
        openai_key = _get_openai_api_key_from_secrets_manager()
        if openai_key:
            overrides["openai_api_key"] = openai_key
    
    # If we have overrides, create new settings with them
    if overrides:
        return Settings(**overrides)
    
    return settings
