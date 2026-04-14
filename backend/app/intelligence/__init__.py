"""Intelligence agents — scheduled narrative generators for OSS.

Phase 1: Nightly Scribe — writes a dated markdown journal entry after each
trading day's pipeline activity, stored as (DynamoDB index row + S3 markdown).
"""
