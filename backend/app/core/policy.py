"""Policy versioning service.

Handles policy creation, activation, and comparison.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional

from app.core.schemas import Policy, PolicyChangelog, PolicyConfig, PolicyDiff
from app.db.tables import PolicyTable

logger = logging.getLogger(__name__)


class PolicyService:
    """Service for managing policy versions."""

    async def create_version(
        self,
        config: PolicyConfig,
        user: str,
        base_version: Optional[str] = None,
    ) -> Policy:
        """Create a new policy version.

        Args:
            config: The policy configuration
            user: User creating the policy
            base_version: Optional version to base changelog on

        Returns:
            The created Policy
        """
        # Compute hash for the config
        policy_hash = Policy.compute_hash(config)

        # Determine new version number
        new_version = await self._compute_next_version(base_version)

        # Generate changelog if base version provided
        changelog: list[PolicyChangelog] = []
        if base_version:
            base_policy = await self.get_version(base_version)
            if base_policy:
                changelog = self._generate_changelog(
                    base_policy.config, config, user
                )

        # Create policy
        policy = Policy(
            version=new_version,
            policy_hash=policy_hash,
            config=config,
            created_by=user,
            is_active=False,
            changelog=changelog,
        )

        # Store policy
        await PolicyTable.put(policy)
        logger.info(f"Created policy version {new_version} by {user}")

        return policy

    async def get_version(self, version: str) -> Optional[Policy]:
        """Get a specific policy version.

        Args:
            version: Version string (e.g., "v2.0.0")

        Returns:
            The Policy if found, None otherwise
        """
        return await PolicyTable.get(version)

    async def get_active(self) -> Optional[Policy]:
        """Get the currently active policy.

        Returns:
            The active Policy if one exists, None otherwise
        """
        return await PolicyTable.get_active()

    async def list_versions(self, limit: int = 20) -> list[Policy]:
        """List policy versions.

        Args:
            limit: Maximum number of versions to return

        Returns:
            List of Policy objects (most recent first)
        """
        return await PolicyTable.list(limit=limit)

    async def activate_version(self, version: str) -> Optional[Policy]:
        """Activate a specific policy version.

        Args:
            version: Version string to activate

        Returns:
            The activated Policy if found, None otherwise
        """
        # Verify version exists
        policy = await self.get_version(version)
        if not policy:
            logger.warning(f"Cannot activate non-existent policy version: {version}")
            return None

        # Set as active (this deactivates others)
        await PolicyTable.set_active(version)
        logger.info(f"Activated policy version {version}")

        # Return updated policy
        return await self.get_version(version)

    async def diff_versions(self, v1: str, v2: str) -> Optional[PolicyDiff]:
        """Compare two policy versions.

        Args:
            v1: First version string
            v2: Second version string

        Returns:
            PolicyDiff with changes, or None if either version not found
        """
        policy1 = await self.get_version(v1)
        policy2 = await self.get_version(v2)

        if not policy1 or not policy2:
            return None

        changes = self._generate_changelog(policy1.config, policy2.config, "system")

        return PolicyDiff(
            version_1=v1,
            version_2=v2,
            changes=changes,
            identical=len(changes) == 0,
        )

    async def _compute_next_version(self, base_version: Optional[str] = None) -> str:
        """Compute the next version number.

        Args:
            base_version: Optional base version to increment from

        Returns:
            New version string (e.g., "v2.0.1")
        """
        if base_version:
            # Parse existing version and increment patch
            match = re.match(r"v(\d+)\.(\d+)\.(\d+)", base_version)
            if match:
                major, minor, patch = map(int, match.groups())
                return f"v{major}.{minor}.{patch + 1}"

        # Get latest version and increment
        policies = await self.list_versions(limit=1)
        if policies:
            latest = policies[0].version
            match = re.match(r"v(\d+)\.(\d+)\.(\d+)", latest)
            if match:
                major, minor, patch = map(int, match.groups())
                return f"v{major}.{minor}.{patch + 1}"

        # Default to v2.0.0 (per requirements spec)
        return "v2.0.0"

    def _generate_changelog(
        self,
        old_config: PolicyConfig,
        new_config: PolicyConfig,
        user: str,
    ) -> list[PolicyChangelog]:
        """Generate changelog between two configs.

        Args:
            old_config: Previous configuration
            new_config: New configuration
            user: User making the change

        Returns:
            List of PolicyChangelog entries
        """
        changes: list[PolicyChangelog] = []
        now = datetime.now(timezone.utc).isoformat()

        # Convert to dicts for comparison
        old_dict = old_config.model_dump()
        new_dict = new_config.model_dump()

        # Recursively compare
        self._compare_dicts(old_dict, new_dict, "", changes, user, now)

        return changes

    def _compare_dicts(
        self,
        old: dict[str, Any],
        new: dict[str, Any],
        path: str,
        changes: list[PolicyChangelog],
        user: str,
        timestamp: str,
    ) -> None:
        """Recursively compare two dictionaries and record changes.

        Args:
            old: Old dictionary
            new: New dictionary
            path: Current path in the config
            changes: List to append changes to
            user: User making changes
            timestamp: Timestamp for changes
        """
        all_keys = set(old.keys()) | set(new.keys())

        for key in all_keys:
            current_path = f"{path}.{key}" if path else key
            old_value = old.get(key)
            new_value = new.get(key)

            if old_value == new_value:
                continue

            # If both are dicts, recurse
            if isinstance(old_value, dict) and isinstance(new_value, dict):
                self._compare_dicts(
                    old_value, new_value, current_path, changes, user, timestamp
                )
            else:
                changes.append(
                    PolicyChangelog(
                        field_path=current_path,
                        old_value=old_value,
                        new_value=new_value,
                        changed_at=timestamp,
                        changed_by=user,
                    )
                )
