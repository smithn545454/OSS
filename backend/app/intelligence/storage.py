"""Shared storage helpers for intelligence artifacts.

Markdown bodies live in S3 at ``s3://{intelligence_bucket}/{key}``.
Index rows live in DynamoDB via ``DiaryTable`` (and future sibling tables).
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import boto3
from botocore.exceptions import ClientError

from app.config import get_settings

logger = logging.getLogger(__name__)


def _s3_client() -> Any:
    """Return a boto3 S3 client bound to the configured AWS region."""
    settings = get_settings()
    return boto3.client("s3", region_name=settings.aws_region)


def _bucket_name() -> Optional[str]:
    return get_settings().intelligence_s3_bucket


def put_markdown(key: str, body: str) -> str:
    """Write a UTF-8 markdown body to the intelligence bucket.

    Returns the s3_key that was written. Raises RuntimeError if the
    bucket is not configured.
    """
    bucket = _bucket_name()
    if not bucket:
        raise RuntimeError(
            "intelligence_s3_bucket not configured "
            "(set INTELLIGENCE_S3_BUCKET env var)"
        )
    _s3_client().put_object(
        Bucket=bucket,
        Key=key,
        Body=body.encode("utf-8"),
        ContentType="text/markdown; charset=utf-8",
    )
    logger.info(f"Wrote intelligence artifact to s3://{bucket}/{key}")
    return key


def get_markdown(key: str) -> Optional[str]:
    """Read a markdown body from the intelligence bucket.

    Returns None if the object does not exist or the bucket is not
    configured.
    """
    bucket = _bucket_name()
    if not bucket:
        logger.warning("intelligence_s3_bucket not configured; returning None")
        return None
    try:
        response = _s3_client().get_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code in ("NoSuchKey", "404", "NoSuchBucket"):
            return None
        raise
    body: Any = response["Body"].read()
    if isinstance(body, bytes):
        return body.decode("utf-8")
    return str(body)
