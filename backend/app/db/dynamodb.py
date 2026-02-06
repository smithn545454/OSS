"""DynamoDB client wrapper."""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Optional

import boto3
from botocore.config import Config

from app.config import get_settings

logger = logging.getLogger(__name__)


class DynamoDBClient:
    """Wrapper for DynamoDB operations."""

    _instance: Optional[DynamoDBClient] = None

    def __init__(self) -> None:
        """Initialize DynamoDB client."""
        import os
        settings = get_settings()

        # For local development with explicit endpoint
        if settings.dynamodb_endpoint:
            logger.info(f"Using DynamoDB endpoint: {settings.dynamodb_endpoint}")
            self._client = boto3.client(
                "dynamodb",
                endpoint_url=settings.dynamodb_endpoint,
                region_name=settings.aws_region,
            )
            self._resource = boto3.resource(
                "dynamodb",
                endpoint_url=settings.dynamodb_endpoint,
                region_name=settings.aws_region,
            )
        else:
            # In Lambda/AWS, use configured region with default credentials
            logger.info(f"Using AWS DynamoDB with region={settings.aws_region}")
            self._client = boto3.client("dynamodb", region_name=settings.aws_region)
            self._resource = boto3.resource("dynamodb", region_name=settings.aws_region)

        self._table_prefix = settings.dynamodb_table_prefix
        logger.info(f"DynamoDB table prefix: {self._table_prefix}")

    @classmethod
    def get_instance(cls) -> DynamoDBClient:
        """Get singleton instance of DynamoDB client."""
        if cls._instance is None:
            cls._instance = DynamoDBClient()
        return cls._instance

    def get_table_name(self, table_suffix: str) -> str:
        """Get full table name with prefix."""
        return f"{self._table_prefix}-{table_suffix}"

    def get_table(self, table_suffix: str) -> Any:
        """Get a DynamoDB table resource."""
        return self._resource.Table(self.get_table_name(table_suffix))

    @staticmethod
    def convert_to_dynamodb(data: dict[str, Any]) -> dict[str, Any]:
        """Convert Python dict to DynamoDB format, handling floats."""
        return DynamoDBClient._convert_floats(data)

    @staticmethod
    def _convert_floats(obj: Any) -> Any:
        """Recursively convert floats to Decimal."""
        if isinstance(obj, float):
            return Decimal(str(obj))
        elif isinstance(obj, dict):
            return {k: DynamoDBClient._convert_floats(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [DynamoDBClient._convert_floats(item) for item in obj]
        return obj

    @staticmethod
    def convert_from_dynamodb(data: dict[str, Any]) -> dict[str, Any]:
        """Convert DynamoDB format back to Python dict."""
        return DynamoDBClient._convert_decimals(data)

    @staticmethod
    def _convert_decimals(obj: Any) -> Any:
        """Recursively convert Decimals to floats."""
        if isinstance(obj, Decimal):
            # Check if it's an integer
            if obj % 1 == 0:
                return int(obj)
            return float(obj)
        elif isinstance(obj, dict):
            return {k: DynamoDBClient._convert_decimals(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [DynamoDBClient._convert_decimals(item) for item in obj]
        return obj

    async def put_item(self, table_suffix: str, item: dict[str, Any]) -> None:
        """Put an item into a table."""
        table = self.get_table(table_suffix)
        converted_item = self.convert_to_dynamodb(item)
        table.put_item(Item=converted_item)

    async def get_item(
        self, table_suffix: str, pk: str, sk: str
    ) -> Optional[dict[str, Any]]:
        """Get an item from a table."""
        table = self.get_table(table_suffix)
        response = table.get_item(Key={"PK": pk, "SK": sk})
        item = response.get("Item")
        if item:
            return self.convert_from_dynamodb(item)
        return None

    async def query(
        self,
        table_suffix: str,
        pk: str,
        sk_prefix: Optional[str] = None,
        sk_condition: Optional[dict[str, Any]] = None,
        limit: int = 100,
        scan_forward: bool = False,
        index_name: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Query items from a table.

        Args:
            table_suffix: Table suffix
            pk: Partition key value
            sk_prefix: Optional sort key prefix for begins_with condition
            sk_condition: Optional sort key condition dict with one of:
                - {"gte": value} for SK >= value
                - {"lte": value} for SK <= value
                - {"between": [start, end]} for SK BETWEEN start AND end
            limit: Maximum items to return
            scan_forward: If True, sort ascending; if False, sort descending
            index_name: Optional GSI name

        Returns:
            List of items matching the query
        """
        table = self.get_table(table_suffix)

        key_condition = "PK = :pk"
        expression_values: dict[str, Any] = {":pk": pk}

        # Handle sort key conditions
        if sk_prefix:
            key_condition += " AND begins_with(SK, :sk_prefix)"
            expression_values[":sk_prefix"] = sk_prefix
        elif sk_condition:
            if "gte" in sk_condition:
                key_condition += " AND SK >= :sk_start"
                expression_values[":sk_start"] = sk_condition["gte"]
            elif "lte" in sk_condition:
                key_condition += " AND SK <= :sk_end"
                expression_values[":sk_end"] = sk_condition["lte"]
            elif "between" in sk_condition:
                key_condition += " AND SK BETWEEN :sk_start AND :sk_end"
                expression_values[":sk_start"] = sk_condition["between"][0]
                expression_values[":sk_end"] = sk_condition["between"][1]

        query_kwargs: dict[str, Any] = {
            "KeyConditionExpression": key_condition,
            "ExpressionAttributeValues": expression_values,
            "Limit": limit,
            "ScanIndexForward": scan_forward,
        }

        if index_name:
            query_kwargs["IndexName"] = index_name
            # For GSI, adjust the key names
            if index_name.startswith("GSI1"):
                query_kwargs["KeyConditionExpression"] = query_kwargs[
                    "KeyConditionExpression"
                ].replace("PK", "GSI1PK").replace("SK", "GSI1SK")
            elif index_name.startswith("GSI2"):
                query_kwargs["KeyConditionExpression"] = query_kwargs[
                    "KeyConditionExpression"
                ].replace("PK", "GSI2PK").replace("SK", "GSI2SK")

        response = table.query(**query_kwargs)
        items = response.get("Items", [])
        return [self.convert_from_dynamodb(item) for item in items]

    async def update_item(
        self,
        table_suffix: str,
        pk: str,
        sk: str,
        updates: dict[str, Any],
    ) -> dict[str, Any]:
        """Update an item in a table."""
        table = self.get_table(table_suffix)

        # Build update expression
        update_parts = []
        expression_names = {}
        expression_values = {}

        for i, (key, value) in enumerate(updates.items()):
            attr_name = f"#attr{i}"
            attr_value = f":val{i}"
            update_parts.append(f"{attr_name} = {attr_value}")
            expression_names[attr_name] = key
            expression_values[attr_value] = self._convert_floats(value)

        update_expression = "SET " + ", ".join(update_parts)

        response = table.update_item(
            Key={"PK": pk, "SK": sk},
            UpdateExpression=update_expression,
            ExpressionAttributeNames=expression_names,
            ExpressionAttributeValues=expression_values,
            ReturnValues="ALL_NEW",
        )

        return self.convert_from_dynamodb(response.get("Attributes", {}))

    async def delete_item(self, table_suffix: str, pk: str, sk: str) -> None:
        """Delete an item from a table."""
        table = self.get_table(table_suffix)
        table.delete_item(Key={"PK": pk, "SK": sk})

    async def batch_write(
        self, table_suffix: str, items: list[dict[str, Any]]
    ) -> None:
        """Batch write items to a table."""
        table = self.get_table(table_suffix)

        with table.batch_writer() as batch:
            for item in items:
                converted_item = self.convert_to_dynamodb(item)
                batch.put_item(Item=converted_item)


def get_dynamodb() -> DynamoDBClient:
    """Get DynamoDB client instance."""
    return DynamoDBClient.get_instance()
