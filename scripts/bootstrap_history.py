#!/usr/bin/env python3
"""Bootstrap Historical IV and OI Data.

Processes historical option chain files and loads them into DynamoDB.

Usage:
    python scripts/bootstrap_history.py --data-dir /path/to/historic_data
    
    # With custom settings
    python scripts/bootstrap_history.py \
        --data-dir /Users/nicksmith/Downloads \
        --workers 4 \
        --batch-size 100 \
        --checkpoint ./bootstrap_checkpoint.json
    
    # Resume interrupted run
    python scripts/bootstrap_history.py \
        --data-dir /Users/nicksmith/Downloads \
        --resume

What this script does:
1. Recursively finds all *_option_chain.txt files in the data directory
2. Extracts ticker from filename (e.g., AAPL_2025_q1_option_chain.txt -> AAPL)
3. For each file:
   - Parses option chain data
   - Extracts daily ATM IV using Delta ≈ ±0.50
   - Extracts daily OI per contract
4. Writes IVHistory and OIHistory records to DynamoDB in batches
5. Tracks progress in checkpoint file for resumability
"""

import argparse
import asyncio
import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime
from typing import Optional

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from app.core.schemas import IVHistory, OIHistory
from app.features.history_loader import (
    find_option_chain_files,
    process_option_chain_file,
    count_option_chain_files,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("bootstrap_history.log"),
    ],
)
logger = logging.getLogger(__name__)


class BootstrapCheckpoint:
    """Manages checkpoint state for resumable processing."""
    
    def __init__(self, checkpoint_path: Path):
        self.checkpoint_path = checkpoint_path
        self.processed_files: set[str] = set()
        self.stats = {
            "files_processed": 0,
            "iv_records_created": 0,
            "oi_records_created": 0,
            "errors": 0,
            "started_at": None,
            "last_updated": None,
        }
        self._load()
    
    def _load(self) -> None:
        """Load checkpoint from file if exists."""
        if self.checkpoint_path.exists():
            try:
                with open(self.checkpoint_path, "r") as f:
                    data = json.load(f)
                    self.processed_files = set(data.get("processed_files", []))
                    self.stats = data.get("stats", self.stats)
                logger.info(f"Loaded checkpoint: {len(self.processed_files)} files already processed")
            except Exception as e:
                logger.warning(f"Could not load checkpoint: {e}")
    
    def save(self) -> None:
        """Save checkpoint to file."""
        self.stats["last_updated"] = datetime.now().isoformat()
        data = {
            "processed_files": list(self.processed_files),
            "stats": self.stats,
        }
        with open(self.checkpoint_path, "w") as f:
            json.dump(data, f, indent=2)
    
    def mark_processed(self, file_path: str) -> None:
        """Mark a file as processed."""
        self.processed_files.add(file_path)
        self.stats["files_processed"] = len(self.processed_files)
    
    def is_processed(self, file_path: str) -> bool:
        """Check if a file has been processed."""
        return file_path in self.processed_files
    
    def add_iv_records(self, count: int) -> None:
        """Add to IV record count."""
        self.stats["iv_records_created"] += count
    
    def add_oi_records(self, count: int) -> None:
        """Add to OI record count."""
        self.stats["oi_records_created"] += count
    
    def add_error(self) -> None:
        """Increment error count."""
        self.stats["errors"] += 1


def process_file_wrapper(
    file_path: Path,
    min_dte: int,
    max_dte: int,
) -> tuple[Path, list[IVHistory], list[OIHistory], Optional[Exception]]:
    """Wrapper for processing a single file (for thread pool).
    
    Returns:
        Tuple of (file_path, iv_records, oi_records, error)
    """
    try:
        iv_records, oi_records = process_option_chain_file(file_path, min_dte, max_dte)
        return file_path, iv_records, oi_records, None
    except Exception as e:
        return file_path, [], [], e


async def write_to_dynamodb_batch(
    iv_records: list[IVHistory],
    oi_records: list[OIHistory],
    batch_size: int = 25,
    dry_run: bool = False,
) -> tuple[int, int]:
    """Write records to DynamoDB in batches.
    
    Args:
        iv_records: IVHistory records to write
        oi_records: OIHistory records to write
        batch_size: DynamoDB batch write limit (max 25)
        dry_run: If True, don't actually write to DynamoDB
        
    Returns:
        Tuple of (iv_records_written, oi_records_written)
    """
    if dry_run:
        logger.info(f"DRY RUN: Would write {len(iv_records)} IV and {len(oi_records)} OI records")
        return len(iv_records), len(oi_records)
    
    from app.db.tables import IVHistoryTable, OIHistoryTable
    
    # Write IV records
    iv_written = 0
    for i in range(0, len(iv_records), batch_size):
        batch = iv_records[i:i + batch_size]
        try:
            await IVHistoryTable.put_batch(batch)
            iv_written += len(batch)
        except Exception as e:
            logger.error(f"Error writing IV batch: {e}")
    
    # Write OI records
    oi_written = 0
    for i in range(0, len(oi_records), batch_size):
        batch = oi_records[i:i + batch_size]
        try:
            await OIHistoryTable.put_batch(batch)
            oi_written += len(batch)
        except Exception as e:
            logger.error(f"Error writing OI batch: {e}")
    
    return iv_written, oi_written


async def run_bootstrap(
    data_dirs: list[Path],
    checkpoint: BootstrapCheckpoint,
    workers: int = 4,
    batch_size: int = 100,
    min_dte: int = 30,
    max_dte: int = 45,
    dry_run: bool = False,
    iv_only: bool = False,
    limit: Optional[int] = None,
) -> None:
    """Run the bootstrap process.
    
    Args:
        data_dirs: Directories containing option chain files
        checkpoint: Checkpoint manager
        workers: Number of parallel workers for file processing
        batch_size: Records to accumulate before writing to DynamoDB
        min_dte: Minimum DTE for ATM IV extraction
        max_dte: Maximum DTE for ATM IV extraction
        dry_run: If True, don't write to DynamoDB
        iv_only: If True, only extract IV records (skip OI for speed)
        limit: Max files to process (for testing)
    """
    checkpoint.stats["started_at"] = datetime.now().isoformat()
    
    # Find all files
    logger.info("Scanning for option chain files...")
    all_files: list[Path] = []
    for data_dir in data_dirs:
        if data_dir.exists():
            all_files.extend(find_option_chain_files(data_dir))
    
    logger.info(f"Found {len(all_files)} option chain files")
    
    # Filter out already processed files
    files_to_process = [f for f in all_files if not checkpoint.is_processed(str(f))]
    logger.info(f"{len(files_to_process)} files remaining to process")
    
    if limit:
        files_to_process = files_to_process[:limit]
        logger.info(f"Limited to {limit} files for this run")
    
    if not files_to_process:
        logger.info("No files to process!")
        return
    
    # Process files in parallel
    iv_buffer: list[IVHistory] = []
    oi_buffer: list[OIHistory] = []
    files_processed = 0
    
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(process_file_wrapper, f, min_dte, max_dte): f
            for f in files_to_process
        }
        
        for future in as_completed(futures):
            file_path, iv_records, oi_records, error = future.result()
            
            if error:
                logger.error(f"Error processing {file_path.name}: {error}")
                checkpoint.add_error()
            else:
                iv_buffer.extend(iv_records)
                if not iv_only:
                    oi_buffer.extend(oi_records)
                
                checkpoint.add_iv_records(len(iv_records))
                if not iv_only:
                    checkpoint.add_oi_records(len(oi_records))
            
            checkpoint.mark_processed(str(file_path))
            files_processed += 1
            
            # Write to DynamoDB when buffer is full
            if len(iv_buffer) >= batch_size or len(oi_buffer) >= batch_size:
                logger.info(f"Writing batch: {len(iv_buffer)} IV, {len(oi_buffer)} OI records")
                await write_to_dynamodb_batch(
                    iv_buffer, 
                    oi_buffer if not iv_only else [], 
                    dry_run=dry_run
                )
                iv_buffer.clear()
                oi_buffer.clear()
                checkpoint.save()
            
            # Progress update
            if files_processed % 100 == 0:
                logger.info(
                    f"Progress: {files_processed}/{len(files_to_process)} files "
                    f"({checkpoint.stats['iv_records_created']} IV, "
                    f"{checkpoint.stats['oi_records_created']} OI records)"
                )
    
    # Write remaining records
    if iv_buffer or oi_buffer:
        logger.info(f"Writing final batch: {len(iv_buffer)} IV, {len(oi_buffer)} OI records")
        await write_to_dynamodb_batch(
            iv_buffer, 
            oi_buffer if not iv_only else [], 
            dry_run=dry_run
        )
    
    checkpoint.save()
    
    # Print summary
    logger.info("=" * 60)
    logger.info("BOOTSTRAP COMPLETE")
    logger.info(f"Files processed: {checkpoint.stats['files_processed']}")
    logger.info(f"IV records created: {checkpoint.stats['iv_records_created']}")
    logger.info(f"OI records created: {checkpoint.stats['oi_records_created']}")
    logger.info(f"Errors: {checkpoint.stats['errors']}")
    logger.info("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="Bootstrap historical IV and OI data into DynamoDB",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Process all files in Downloads folder
  python scripts/bootstrap_history.py --data-dir ~/Downloads
  
  # Dry run (no DynamoDB writes)
  python scripts/bootstrap_history.py --data-dir ~/Downloads --dry-run
  
  # Resume interrupted run
  python scripts/bootstrap_history.py --data-dir ~/Downloads --resume
  
  # IV only (faster, skip OI records)
  python scripts/bootstrap_history.py --data-dir ~/Downloads --iv-only
  
  # Test with limited files
  python scripts/bootstrap_history.py --data-dir ~/Downloads --limit 10 --dry-run
        """,
    )
    
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path.home() / "Downloads",
        help="Directory containing option chain files (default: ~/Downloads)",
    )
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=Path("bootstrap_checkpoint.json"),
        help="Checkpoint file path (default: ./bootstrap_checkpoint.json)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Number of parallel file processing workers (default: 4)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Records to accumulate before writing to DynamoDB (default: 100)",
    )
    parser.add_argument(
        "--min-dte",
        type=int,
        default=30,
        help="Minimum DTE for ATM IV extraction (default: 30)",
    )
    parser.add_argument(
        "--max-dte",
        type=int,
        default=45,
        help="Maximum DTE for ATM IV extraction (default: 45)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't write to DynamoDB, just process and count",
    )
    parser.add_argument(
        "--iv-only",
        action="store_true",
        help="Only extract IV records (skip OI for faster processing)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from checkpoint (skip already processed files)",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete checkpoint and start fresh",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Limit number of files to process (for testing)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # Handle reset
    if args.reset and args.checkpoint.exists():
        args.checkpoint.unlink()
        logger.info("Checkpoint deleted")
    
    # Initialize checkpoint
    checkpoint = BootstrapCheckpoint(args.checkpoint)
    
    if not args.resume:
        # Starting fresh - clear checkpoint
        checkpoint.processed_files.clear()
        checkpoint.stats = {
            "files_processed": 0,
            "iv_records_created": 0,
            "oi_records_created": 0,
            "errors": 0,
            "started_at": None,
            "last_updated": None,
        }
    
    # Find data directories
    data_dir = args.data_dir.expanduser()
    
    # Look for quarterly folders in the data directory
    data_dirs = [data_dir]
    for subdir in data_dir.iterdir():
        if subdir.is_dir() and "option_chain" in subdir.name.lower():
            data_dirs.append(subdir)
    
    logger.info(f"Searching in directories: {[str(d) for d in data_dirs]}")
    
    # Run bootstrap
    try:
        asyncio.run(run_bootstrap(
            data_dirs=data_dirs,
            checkpoint=checkpoint,
            workers=args.workers,
            batch_size=args.batch_size,
            min_dte=args.min_dte,
            max_dte=args.max_dte,
            dry_run=args.dry_run,
            iv_only=args.iv_only,
            limit=args.limit,
        ))
    except KeyboardInterrupt:
        logger.info("\nInterrupted! Saving checkpoint...")
        checkpoint.save()
        logger.info(f"Progress saved. Resume with --resume flag.")
        sys.exit(1)


if __name__ == "__main__":
    main()
