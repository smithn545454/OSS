"""Historical Data Loader for IV and OI History.

Processes historical option chain files (.txt) to extract:
- Daily ATM IV proxy for IV percentile calculation
- Daily OI per contract for OI 5-day change calculation

File format expected:
- Filename: {TICKER}_{YEAR}_q{QUARTER}_option_chain.txt
- Contents: CSV with columns:
  Trade Date, Strike, Expiry Date, Call/Put, Last Trade Price, Bid Price, Ask Price,
  Bid Implied Volatility, Ask Implied Volatility, Open Interest, Volume,
  Delta, Gamma, Vega, Theta, Rho
"""

from __future__ import annotations

import csv
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

from app.core.schemas import IVHistory, OIHistory

logger = logging.getLogger(__name__)

# Regex to extract ticker from filename
# Pattern: {TICKER}_{YEAR}_q{QUARTER}_option_chain.txt
FILENAME_PATTERN = re.compile(r"^([A-Z]+)_\d{4}_q\d_option_chain\.txt$", re.IGNORECASE)


@dataclass
class OptionChainRow:
    """Parsed row from option chain file."""
    
    trade_date: str  # YYYY-MM-DD
    strike: float
    expiry_date: str  # YYYY-MM-DD
    call_put: str  # 'c' or 'p'
    last_trade_price: Optional[float]
    bid_price: Optional[float]
    ask_price: Optional[float]
    bid_iv: Optional[float]
    ask_iv: Optional[float]
    open_interest: int
    volume: int
    delta: Optional[float]
    gamma: Optional[float]
    vega: Optional[float]
    theta: Optional[float]
    rho: Optional[float]


@dataclass
class DailyATMIV:
    """Daily ATM IV data extracted from option chain."""
    
    ticker: str
    date: str
    atm_iv: float
    atm_call_iv: Optional[float]
    atm_put_iv: Optional[float]


def extract_ticker_from_filename(filename: str) -> Optional[str]:
    """Extract ticker symbol from filename.
    
    Args:
        filename: Filename like "AAPL_2025_q1_option_chain.txt"
        
    Returns:
        Ticker symbol or None if pattern doesn't match
    """
    match = FILENAME_PATTERN.match(filename)
    if match:
        return match.group(1).upper()
    return None


def parse_float(value: str) -> Optional[float]:
    """Parse float value, returning None for empty/invalid."""
    try:
        if value.strip() == "" or value.strip().lower() == "nan":
            return None
        return float(value)
    except (ValueError, TypeError):
        return None


def parse_int(value: str) -> int:
    """Parse int value, returning 0 for empty/invalid."""
    try:
        if value.strip() == "":
            return 0
        return int(float(value))  # Handle "123.0" format
    except (ValueError, TypeError):
        return 0


def parse_date(value: str) -> Optional[str]:
    """Parse date value to YYYY-MM-DD format."""
    try:
        value = value.strip()
        # Try common formats
        for fmt in ["%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d", "%d-%m-%Y"]:
            try:
                dt = datetime.strptime(value, fmt)
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                continue
        return None
    except Exception:
        return None


def parse_option_chain_row(row: dict[str, str]) -> Optional[OptionChainRow]:
    """Parse a single row from the option chain CSV.
    
    Args:
        row: Dictionary with column names as keys
        
    Returns:
        OptionChainRow or None if parsing fails
    """
    try:
        trade_date = parse_date(row.get("Trade Date", ""))
        if not trade_date:
            return None
        
        expiry_date = parse_date(row.get("Expiry Date", ""))
        if not expiry_date:
            return None
        
        strike = parse_float(row.get("Strike", ""))
        if strike is None:
            return None
        
        call_put = row.get("Call/Put", "").strip().lower()
        if call_put not in ("c", "p", "call", "put"):
            return None
        call_put = "c" if call_put in ("c", "call") else "p"
        
        return OptionChainRow(
            trade_date=trade_date,
            strike=strike,
            expiry_date=expiry_date,
            call_put=call_put,
            last_trade_price=parse_float(row.get("Last Trade Price", "")),
            bid_price=parse_float(row.get("Bid Price", "")),
            ask_price=parse_float(row.get("Ask Price", "")),
            bid_iv=parse_float(row.get("Bid Implied Volatility", "")),
            ask_iv=parse_float(row.get("Ask Implied Volatility", "")),
            open_interest=parse_int(row.get("Open Interest", "")),
            volume=parse_int(row.get("Volume", "")),
            delta=parse_float(row.get("Delta", "")),
            gamma=parse_float(row.get("Gamma", "")),
            vega=parse_float(row.get("Vega", "")),
            theta=parse_float(row.get("Theta", "")),
            rho=parse_float(row.get("Rho", "")),
        )
    except Exception as e:
        logger.debug(f"Error parsing row: {e}")
        return None


def calculate_dte(trade_date: str, expiry_date: str) -> int:
    """Calculate days to expiration."""
    try:
        trade = datetime.strptime(trade_date, "%Y-%m-%d")
        expiry = datetime.strptime(expiry_date, "%Y-%m-%d")
        return (expiry - trade).days
    except Exception:
        return 0


def extract_daily_atm_iv(
    rows: list[OptionChainRow],
    ticker: str,
    trade_date: str,
    min_dte: int = 30,
    max_dte: int = 45,
    target_call_delta: float = 0.50,
    target_put_delta: float = -0.50,
) -> Optional[DailyATMIV]:
    """Extract ATM IV for a single trading day.
    
    Uses Delta to identify ATM contracts (delta closest to ±0.50).
    
    Args:
        rows: All rows for this trade date
        ticker: Ticker symbol
        trade_date: Trade date (YYYY-MM-DD)
        min_dte: Minimum DTE for ATM contracts
        max_dte: Maximum DTE for ATM contracts
        target_call_delta: Target delta for ATM call (0.50)
        target_put_delta: Target delta for ATM put (-0.50)
        
    Returns:
        DailyATMIV or None if no valid ATM contracts found
    """
    # Filter to target DTE range
    filtered = []
    for row in rows:
        dte = calculate_dte(trade_date, row.expiry_date)
        if min_dte <= dte <= max_dte:
            filtered.append(row)
    
    if not filtered:
        return None
    
    # Separate calls and puts with valid delta and IV
    calls = [r for r in filtered if r.call_put == "c" and r.delta is not None and r.bid_iv is not None and r.ask_iv is not None]
    puts = [r for r in filtered if r.call_put == "p" and r.delta is not None and r.bid_iv is not None and r.ask_iv is not None]
    
    # Find ATM call (delta closest to 0.50)
    atm_call = None
    atm_call_iv = None
    if calls:
        atm_call = min(calls, key=lambda r: abs(r.delta - target_call_delta))
        atm_call_iv = (atm_call.bid_iv + atm_call.ask_iv) / 2
    
    # Find ATM put (delta closest to -0.50)
    atm_put = None
    atm_put_iv = None
    if puts:
        atm_put = min(puts, key=lambda r: abs(r.delta - target_put_delta))
        atm_put_iv = (atm_put.bid_iv + atm_put.ask_iv) / 2
    
    # Calculate ATM IV proxy
    if atm_call_iv is not None and atm_put_iv is not None:
        atm_iv = (atm_call_iv + atm_put_iv) / 2
    elif atm_call_iv is not None:
        atm_iv = atm_call_iv
    elif atm_put_iv is not None:
        atm_iv = atm_put_iv
    else:
        return None
    
    return DailyATMIV(
        ticker=ticker,
        date=trade_date,
        atm_iv=atm_iv,
        atm_call_iv=atm_call_iv,
        atm_put_iv=atm_put_iv,
    )


def process_option_chain_file(
    file_path: Path,
    min_dte: int = 30,
    max_dte: int = 45,
) -> tuple[list[IVHistory], list[OIHistory]]:
    """Process a single option chain file.
    
    Args:
        file_path: Path to the .txt file
        min_dte: Minimum DTE for ATM contracts
        max_dte: Maximum DTE for ATM contracts
        
    Returns:
        Tuple of (iv_history_records, oi_history_records)
    """
    ticker = extract_ticker_from_filename(file_path.name)
    if not ticker:
        logger.warning(f"Could not extract ticker from filename: {file_path.name}")
        return [], []
    
    logger.debug(f"Processing {file_path.name} for ticker {ticker}")
    
    # Parse all rows
    rows_by_date: dict[str, list[OptionChainRow]] = {}
    oi_records: list[OIHistory] = []
    
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f)
            for row_dict in reader:
                parsed = parse_option_chain_row(row_dict)
                if parsed:
                    # Group by trade date for IV calculation
                    if parsed.trade_date not in rows_by_date:
                        rows_by_date[parsed.trade_date] = []
                    rows_by_date[parsed.trade_date].append(parsed)
                    
                    # Create OI history record for each contract
                    # Build option ticker from components
                    option_ticker = build_option_ticker(
                        ticker, parsed.expiry_date, parsed.call_put, parsed.strike
                    )
                    oi_records.append(OIHistory(
                        option_ticker=option_ticker,
                        date=parsed.trade_date,
                        open_interest=parsed.open_interest,
                        volume=parsed.volume,
                    ))
    except Exception as e:
        logger.error(f"Error reading file {file_path}: {e}")
        return [], []
    
    # Extract daily ATM IV
    iv_records: list[IVHistory] = []
    for trade_date, day_rows in rows_by_date.items():
        daily_iv = extract_daily_atm_iv(
            rows=day_rows,
            ticker=ticker,
            trade_date=trade_date,
            min_dte=min_dte,
            max_dte=max_dte,
        )
        if daily_iv:
            iv_records.append(IVHistory(
                ticker=daily_iv.ticker,
                date=daily_iv.date,
                atm_iv=daily_iv.atm_iv,
                atm_call_iv=daily_iv.atm_call_iv,
                atm_put_iv=daily_iv.atm_put_iv,
            ))
    
    logger.debug(f"Extracted {len(iv_records)} IV records and {len(oi_records)} OI records from {file_path.name}")
    return iv_records, oi_records


def build_option_ticker(
    underlying: str,
    expiry_date: str,
    call_put: str,
    strike: float,
) -> str:
    """Build OCC-style option ticker.
    
    Format: O:{UNDERLYING}{YYMMDD}{C/P}{STRIKE*1000}
    Example: O:AAPL240119C00190000
    
    Args:
        underlying: Underlying ticker
        expiry_date: Expiry date (YYYY-MM-DD)
        call_put: 'c' or 'p'
        strike: Strike price
        
    Returns:
        Option ticker string
    """
    try:
        dt = datetime.strptime(expiry_date, "%Y-%m-%d")
        date_str = dt.strftime("%y%m%d")
        cp = "C" if call_put.lower() == "c" else "P"
        strike_str = f"{int(strike * 1000):08d}"
        return f"O:{underlying}{date_str}{cp}{strike_str}"
    except Exception:
        return f"O:{underlying}_{expiry_date}_{call_put}_{strike}"


def find_option_chain_files(data_dir: Path) -> Iterator[Path]:
    """Find all option chain files in a directory (recursively).
    
    Args:
        data_dir: Root directory to search
        
    Yields:
        Path objects for each matching file
    """
    for file_path in data_dir.rglob("*_option_chain.txt"):
        if FILENAME_PATTERN.match(file_path.name):
            yield file_path


def count_option_chain_files(data_dir: Path) -> int:
    """Count option chain files in a directory."""
    return sum(1 for _ in find_option_chain_files(data_dir))
