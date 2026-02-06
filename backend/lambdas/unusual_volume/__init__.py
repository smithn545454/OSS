"""Unusual Volume Scanner Lambda functions.

This package contains the serverless implementation of the contract-level
unusual volume scanner using a fan-out architecture.

Lambda Functions:
- publisher: Orchestrates scans, publishes ticker messages to SNS
- worker: Processes single ticker, finds unusual contracts
- aggregator: Updates scan-run metrics after completion
- handoff: Applies Stage 2 filters, forwards to Stage 4
- nightly_stats: Pre-computes volume statistics

Utilities:
- utils.occ_parser: Parse OCC option symbols
- utils.buckets: Moneyness and DTE bucket calculations
- utils.scoring: Priority score calculation
"""
