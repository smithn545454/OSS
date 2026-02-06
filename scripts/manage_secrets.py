#!/usr/bin/env python3
"""Manage API keys in AWS Secrets Manager.

Usage:
    # Create or update the Anthropic API key
    python scripts/manage_secrets.py create-anthropic YOUR_API_KEY
    
    # Verify all secrets are configured
    python scripts/manage_secrets.py verify
    
    # List all OSS secrets
    python scripts/manage_secrets.py list
"""

import argparse
import json
import os
import sys
from typing import Optional

try:
    import boto3
    from botocore.exceptions import ClientError
except ImportError:
    print("boto3 is required. Install with: pip install boto3")
    sys.exit(1)


# Default region
DEFAULT_REGION = os.environ.get("AWS_REGION", "us-east-1")

# Secret names
SECRETS = {
    "polygon": "oss/polygon-api-key",
    "finnhub": "oss/finnhub-api-key", 
    "anthropic": "oss/anthropic-api-key",
    "openai": "oss/openai-api-key",
}


def get_client(region: str = DEFAULT_REGION):
    """Get Secrets Manager client."""
    return boto3.client("secretsmanager", region_name=region)


def create_or_update_secret(
    name: str,
    value: str,
    description: str,
    region: str = DEFAULT_REGION,
) -> dict:
    """Create or update a secret in Secrets Manager.
    
    Args:
        name: Secret name
        value: Secret value (API key)
        description: Description for the secret
        region: AWS region
        
    Returns:
        Response from AWS
    """
    client = get_client(region)
    
    try:
        # Try to update existing secret
        response = client.put_secret_value(
            SecretId=name,
            SecretString=value,
        )
        print(f"✓ Updated secret: {name}")
        return response
        
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            # Create new secret
            response = client.create_secret(
                Name=name,
                Description=description,
                SecretString=value,
                Tags=[
                    {"Key": "Project", "Value": "OSS"},
                    {"Key": "Environment", "Value": "production"},
                ],
            )
            print(f"✓ Created secret: {name}")
            print(f"  ARN: {response['ARN']}")
            return response
        else:
            raise


def get_secret_arn(name: str, region: str = DEFAULT_REGION) -> Optional[str]:
    """Get the ARN of a secret.
    
    Args:
        name: Secret name
        region: AWS region
        
    Returns:
        Secret ARN or None if not found
    """
    client = get_client(region)
    
    try:
        response = client.describe_secret(SecretId=name)
        return response.get("ARN")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            return None
        raise


def verify_secret(name: str, region: str = DEFAULT_REGION) -> bool:
    """Verify a secret exists and has a value.
    
    Args:
        name: Secret name
        region: AWS region
        
    Returns:
        True if secret exists and has a value
    """
    client = get_client(region)
    
    try:
        response = client.get_secret_value(SecretId=name)
        value = response.get("SecretString", "")
        return bool(value and len(value) > 10)  # At least 10 chars
    except ClientError:
        return False


def list_oss_secrets(region: str = DEFAULT_REGION) -> list[dict]:
    """List all OSS-related secrets.
    
    Args:
        region: AWS region
        
    Returns:
        List of secret metadata dicts
    """
    client = get_client(region)
    
    secrets = []
    try:
        paginator = client.get_paginator("list_secrets")
        for page in paginator.paginate(
            Filters=[{"Key": "name", "Values": ["oss/"]}]
        ):
            for secret in page.get("SecretList", []):
                secrets.append({
                    "name": secret["Name"],
                    "arn": secret["ARN"],
                    "created": str(secret.get("CreatedDate", "")),
                    "last_changed": str(secret.get("LastChangedDate", "")),
                })
    except ClientError as e:
        print(f"Error listing secrets: {e}")
    
    return secrets


def cmd_create_anthropic(args):
    """Create or update Anthropic API key."""
    if not args.api_key:
        print("Error: API key is required")
        sys.exit(1)
    
    # Validate API key format (Anthropic keys start with 'sk-ant-')
    if not args.api_key.startswith("sk-ant-"):
        print("Warning: Anthropic API keys typically start with 'sk-ant-'")
        if not args.force:
            confirm = input("Continue anyway? [y/N]: ")
            if confirm.lower() != "y":
                sys.exit(1)
    
    response = create_or_update_secret(
        name=SECRETS["anthropic"],
        value=args.api_key,
        description="Anthropic Claude API key for OSS trade thesis generation",
        region=args.region,
    )
    
    arn = response.get("ARN") or get_secret_arn(SECRETS["anthropic"], args.region)
    print()
    print("To use this secret, set the following environment variable:")
    print(f"  export ANTHROPIC_SECRET_ARN={arn}")
    print()
    print("Or add to your Lambda environment variables in AWS Console.")


def cmd_create_polygon(args):
    """Create or update Polygon API key."""
    if not args.api_key:
        print("Error: API key is required")
        sys.exit(1)
    
    response = create_or_update_secret(
        name=SECRETS["polygon"],
        value=args.api_key,
        description="Polygon.io API key for OSS market data",
        region=args.region,
    )
    
    arn = response.get("ARN") or get_secret_arn(SECRETS["polygon"], args.region)
    print()
    print("To use this secret, set the following environment variable:")
    print(f"  export POLYGON_SECRET_ARN={arn}")


def cmd_create_finnhub(args):
    """Create or update Finnhub API key."""
    if not args.api_key:
        print("Error: API key is required")
        sys.exit(1)
    
    response = create_or_update_secret(
        name=SECRETS["finnhub"],
        value=args.api_key,
        description="Finnhub API key for OSS earnings calendar",
        region=args.region,
    )
    
    arn = response.get("ARN") or get_secret_arn(SECRETS["finnhub"], args.region)
    print()
    print("To use this secret, set the following environment variable:")
    print(f"  export FINNHUB_SECRET_ARN={arn}")


def cmd_create_openai(args):
    """Create or update OpenAI API key."""
    if not args.api_key:
        print("Error: API key is required")
        sys.exit(1)
    
    # Validate API key format (OpenAI keys start with 'sk-')
    if not args.api_key.startswith("sk-"):
        print("Warning: OpenAI API keys typically start with 'sk-'")
        if not args.force:
            confirm = input("Continue anyway? [y/N]: ")
            if confirm.lower() != "y":
                sys.exit(1)
    
    response = create_or_update_secret(
        name=SECRETS["openai"],
        value=args.api_key,
        description="OpenAI API key for OSS trade thesis generation",
        region=args.region,
    )
    
    arn = response.get("ARN") or get_secret_arn(SECRETS["openai"], args.region)
    print()
    print("To use this secret, set the following environment variable:")
    print(f"  export OPENAI_SECRET_ARN={arn}")
    print()
    print("Or add to your Lambda environment variables in AWS Console.")


def cmd_verify(args):
    """Verify all secrets are configured."""
    print(f"Verifying secrets in region: {args.region}")
    print()
    
    all_ok = True
    for service, name in SECRETS.items():
        exists = verify_secret(name, args.region)
        status = "✓" if exists else "✗"
        arn = get_secret_arn(name, args.region) if exists else None
        
        if exists:
            print(f"  {status} {service}: {name}")
            if arn:
                print(f"      ARN: {arn}")
        else:
            print(f"  {status} {service}: NOT CONFIGURED")
            all_ok = False
    
    print()
    if all_ok:
        print("All secrets are configured!")
    else:
        print("Some secrets are missing. Use 'create-*' commands to add them.")
    
    return 0 if all_ok else 1


def cmd_list(args):
    """List all OSS secrets."""
    print(f"Listing OSS secrets in region: {args.region}")
    print()
    
    secrets = list_oss_secrets(args.region)
    
    if not secrets:
        print("No OSS secrets found.")
        return
    
    for secret in secrets:
        print(f"  Name: {secret['name']}")
        print(f"    ARN: {secret['arn']}")
        print(f"    Created: {secret['created']}")
        print(f"    Last Changed: {secret['last_changed']}")
        print()


def cmd_test_llm(args):
    """Test LLM thesis generation with a sample input."""
    print("Testing LLM providers...")
    print()
    
    # Add backend to path
    sys.path.insert(0, str(__file__.replace("/scripts/manage_secrets.py", "/backend")))
    
    import asyncio
    
    # Check both providers
    from app.llm.provider import AnthropicProvider, OpenAIProvider
    
    anthropic = AnthropicProvider()
    openai = OpenAIProvider()
    
    print("Provider availability:")
    print(f"  Anthropic: {'✓ configured' if anthropic.is_available() else '✗ not configured'}")
    print(f"  OpenAI:    {'✓ configured' if openai.is_available() else '✗ not configured'}")
    print()
    
    # Pick available provider
    provider = None
    provider_name = None
    if anthropic.is_available():
        provider = anthropic
        provider_name = "Anthropic"
    elif openai.is_available():
        provider = openai
        provider_name = "OpenAI"
    
    if not provider:
        print("✗ No LLM provider configured!")
        print()
        print("Set one of:")
        print("  - OPENAI_API_KEY or OPENAI_SECRET_ARN")
        print("  - ANTHROPIC_API_KEY or ANTHROPIC_SECRET_ARN")
        return 1
    
    # Test with a simple prompt
    print(f"Testing {provider_name} API connection...")
    
    async def test_api():
        response = await provider.generate(
            prompt="Reply with exactly the text: 'LLM connection successful' and nothing else.",
            max_tokens=50,
        )
        return response
    
    response = asyncio.run(test_api())
    
    if response.success:
        print(f"✓ API Response: {response.content.strip()}")
        print(f"  Model: {response.model}")
        print(f"  Tokens: {response.tokens_used}")
        return 0
    else:
        print(f"✗ API Error: {response.error}")
        return 1


def main():
    parser = argparse.ArgumentParser(
        description="Manage OSS API keys in AWS Secrets Manager",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--region",
        default=DEFAULT_REGION,
        help=f"AWS region (default: {DEFAULT_REGION})",
    )
    
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    # create-anthropic
    p_anthropic = subparsers.add_parser(
        "create-anthropic",
        help="Create/update Anthropic API key",
    )
    p_anthropic.add_argument("api_key", help="Anthropic API key")
    p_anthropic.add_argument("--force", "-f", action="store_true", help="Skip validation")
    p_anthropic.set_defaults(func=cmd_create_anthropic)
    
    # create-polygon
    p_polygon = subparsers.add_parser(
        "create-polygon",
        help="Create/update Polygon API key",
    )
    p_polygon.add_argument("api_key", help="Polygon API key")
    p_polygon.set_defaults(func=cmd_create_polygon)
    
    # create-finnhub
    p_finnhub = subparsers.add_parser(
        "create-finnhub",
        help="Create/update Finnhub API key",
    )
    p_finnhub.add_argument("api_key", help="Finnhub API key")
    p_finnhub.set_defaults(func=cmd_create_finnhub)
    
    # create-openai
    p_openai = subparsers.add_parser(
        "create-openai",
        help="Create/update OpenAI API key",
    )
    p_openai.add_argument("api_key", help="OpenAI API key")
    p_openai.add_argument("--force", "-f", action="store_true", help="Skip validation")
    p_openai.set_defaults(func=cmd_create_openai)
    
    # verify
    p_verify = subparsers.add_parser(
        "verify",
        help="Verify all secrets are configured",
    )
    p_verify.set_defaults(func=cmd_verify)
    
    # list
    p_list = subparsers.add_parser(
        "list",
        help="List all OSS secrets",
    )
    p_list.set_defaults(func=cmd_list)
    
    # test-llm
    p_test = subparsers.add_parser(
        "test-llm",
        help="Test LLM thesis generation",
    )
    p_test.set_defaults(func=cmd_test_llm)
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    result = args.func(args)
    sys.exit(result if result else 0)


if __name__ == "__main__":
    main()
