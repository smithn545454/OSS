#!/usr/bin/env python3
"""Seed S&P 500 tickers into DynamoDB for the Unusual Volume Scanner.

This script populates the sp500-tickers table with the current S&P 500 constituents.
It can be run:
- Initially to populate the table
- Quarterly to update for rebalancing changes

Usage:
    python scripts/seed_sp500_tickers.py [--endpoint URL] [--prefix PREFIX]

Example (local development):
    python scripts/seed_sp500_tickers.py --endpoint http://localhost:8000

Example (AWS):
    python scripts/seed_sp500_tickers.py --prefix oss-prod

Data Source:
    Uses a curated list of S&P 500 tickers. For production, consider:
    - Wikipedia S&P 500 page scraping
    - Financial data provider API (Alpha Vantage, IEX Cloud)
    - Manual quarterly updates from S&P Dow Jones Indices
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError


# Current S&P 500 constituents (as of late 2025)
# This list should be updated quarterly for rebalancing
SP500_TICKERS = [
    # A
    "A", "AAL", "AAPL", "ABBV", "ABNB", "ABT", "ACGL", "ACN", "ADBE", "ADI",
    "ADM", "ADP", "ADSK", "AEE", "AEP", "AES", "AFL", "AIG", "AIZ", "AJG",
    "AKAM", "ALB", "ALGN", "ALL", "ALLE", "AMAT", "AMCR", "AMD", "AME", "AMGN",
    "AMP", "AMT", "AMZN", "ANET", "ANSS", "AON", "AOS", "APA", "APD", "APH",
    "APTV", "ARE", "ATO", "AVB", "AVGO", "AVY", "AWK", "AXON", "AXP", "AZO",
    # B
    "BA", "BAC", "BALL", "BAX", "BBWI", "BBY", "BDX", "BEN", "BF.B", "BG",
    "BIIB", "BIO", "BK", "BKNG", "BKR", "BLDR", "BLK", "BMY", "BR", "BRK.B",
    "BRO", "BSX", "BWA", "BX", "BXP",
    # C
    "C", "CAG", "CAH", "CARR", "CAT", "CB", "CBOE", "CBRE", "CCI", "CCL",
    "CDNS", "CDW", "CE", "CEG", "CF", "CFG", "CHD", "CHRW", "CHTR", "CI",
    "CINF", "CL", "CLX", "CMA", "CMCSA", "CME", "CMG", "CMI", "CMS", "CNC",
    "CNP", "COF", "COO", "COP", "COR", "COST", "CPAY", "CPB", "CPRT", "CPT",
    "CRL", "CRM", "CSCO", "CSGP", "CSX", "CTAS", "CTLT", "CTRA", "CTSH", "CTVA",
    "CVS", "CVX",
    # D
    "D", "DAL", "DD", "DE", "DECK", "DFS", "DG", "DGX", "DHI", "DHR",
    "DIS", "DLR", "DLTR", "DOC", "DOV", "DOW", "DPZ", "DRI", "DTE", "DUK",
    "DVA", "DVN",
    # E
    "EA", "EBAY", "ECL", "ED", "EFX", "EG", "EIX", "EL", "ELV", "EMN",
    "EMR", "ENPH", "EOG", "EPAM", "EQIX", "EQR", "EQT", "ES", "ESS", "ETN",
    "ETR", "EVRG", "EW", "EXC", "EXPD", "EXPE", "EXR",
    # F
    "F", "FANG", "FAST", "FCX", "FDS", "FDX", "FE", "FFIV", "FI", "FICO",
    "FIS", "FITB", "FMC", "FOX", "FOXA", "FRT", "FSLR", "FTNT", "FTV",
    # G
    "GD", "GDDY", "GE", "GEHC", "GEN", "GEV", "GILD", "GIS", "GL", "GLW",
    "GM", "GNRC", "GOOG", "GOOGL", "GPC", "GPN", "GRMN", "GS", "GWW",
    # H
    "HAL", "HAS", "HBAN", "HCA", "HD", "HES", "HIG", "HII", "HLT", "HOLX",
    "HON", "HPE", "HPQ", "HRL", "HSIC", "HST", "HSY", "HUBB", "HUM", "HWM",
    # I
    "IBM", "ICE", "IDXX", "IEX", "IFF", "ILMN", "INCY", "INTC", "INTU", "INVH",
    "IP", "IPG", "IQV", "IR", "IRM", "ISRG", "IT", "ITW", "IVZ",
    # J
    "J", "JBHT", "JBL", "JCI", "JKHY", "JNJ", "JNPR", "JPM",
    # K
    "K", "KDP", "KEY", "KEYS", "KHC", "KIM", "KKR", "KLAC", "KMB", "KMI",
    "KMX", "KO", "KR",
    # L
    "L", "LDOS", "LEN", "LH", "LHX", "LIN", "LKQ", "LLY", "LMT", "LNT",
    "LOW", "LRCX", "LULU", "LUV", "LVS", "LW", "LYB", "LYV",
    # M
    "MA", "MAA", "MAR", "MAS", "MCD", "MCHP", "MCK", "MCO", "MDLZ", "MDT",
    "MET", "META", "MGM", "MHK", "MKC", "MKTX", "MLM", "MMC", "MMM", "MNST",
    "MO", "MOH", "MOS", "MPC", "MPWR", "MRK", "MRNA", "MS", "MSCI", "MSFT",
    "MSI", "MTB", "MTCH", "MTD", "MU",
    # N
    "NCLH", "NDAQ", "NDSN", "NEE", "NEM", "NFLX", "NI", "NKE", "NOC", "NOW",
    "NRG", "NSC", "NTAP", "NTRS", "NUE", "NVDA", "NVR", "NWS", "NWSA", "NXPI",
    # O
    "O", "ODFL", "OKE", "OMC", "ON", "ORCL", "ORLY", "OTIS", "OXY",
    # P
    "PANW", "PARA", "PAYC", "PAYX", "PCAR", "PCG", "PEG", "PEP", "PFE", "PFG",
    "PG", "PGR", "PH", "PHM", "PKG", "PLD", "PLTR", "PM", "PNC", "PNR",
    "PNW", "POOL", "PPG", "PPL", "PRU", "PSA", "PSX", "PTC", "PWR", "PXD",
    # Q-R
    "QCOM", "QRVO", "RCL", "REG", "REGN", "RF", "RJF", "RL", "RMD", "ROK",
    "ROL", "ROP", "ROST", "RSG", "RTX",
    # S
    "SBAC", "SBUX", "SCHW", "SHW", "SJM", "SLB", "SMCI", "SNA", "SNPS", "SO",
    "SOLV", "SPG", "SPGI", "SRE", "STE", "STLD", "STT", "STX", "STZ", "SWK",
    "SWKS", "SYF", "SYK", "SYY",
    # T
    "T", "TAP", "TDG", "TDY", "TECH", "TEL", "TER", "TFC", "TFX", "TGT",
    "TJX", "TMO", "TMUS", "TPR", "TRGP", "TRMB", "TROW", "TRV", "TSCO", "TSLA",
    "TSN", "TT", "TTWO", "TXN", "TXT", "TYL",
    # U
    "UAL", "UBER", "UDR", "UHS", "ULTA", "UNH", "UNP", "UPS", "URI", "USB",
    # V
    "V", "VICI", "VLO", "VLTO", "VMC", "VRSK", "VRSN", "VRTX", "VST", "VTR",
    "VTRS", "VZ",
    # W
    "WAB", "WAT", "WBA", "WBD", "WDAY", "WDC", "WEC", "WELL", "WFC", "WM",
    "WMB", "WMT", "WRB", "WRK", "WST", "WTW", "WY", "WYNN",
    # X-Z
    "XEL", "XOM", "XYL", "YUM", "ZBH", "ZBRA", "ZTS",
]

# Sector mappings (simplified - would be expanded for production)
SECTOR_MAPPINGS = {
    "AAPL": "Technology", "MSFT": "Technology", "GOOGL": "Technology", "AMZN": "Consumer Discretionary",
    "META": "Technology", "NVDA": "Technology", "TSLA": "Consumer Discretionary", "BRK.B": "Financials",
    "JPM": "Financials", "V": "Financials", "JNJ": "Healthcare", "UNH": "Healthcare",
    "XOM": "Energy", "CVX": "Energy", "PG": "Consumer Staples", "HD": "Consumer Discretionary",
    "MA": "Financials", "LLY": "Healthcare", "ABBV": "Healthcare", "PFE": "Healthcare",
    "KO": "Consumer Staples", "PEP": "Consumer Staples", "COST": "Consumer Staples",
    "MRK": "Healthcare", "TMO": "Healthcare", "ABT": "Healthcare",
    "WMT": "Consumer Staples", "BAC": "Financials", "DIS": "Communication Services",
    "CSCO": "Technology", "ACN": "Technology", "VZ": "Communication Services",
    "NFLX": "Communication Services", "ADBE": "Technology", "CRM": "Technology",
    "INTC": "Technology", "AMD": "Technology", "TXN": "Technology",
    "NEE": "Utilities", "DUK": "Utilities", "SO": "Utilities",
    "UNP": "Industrials", "CAT": "Industrials", "BA": "Industrials",
    "GE": "Industrials", "MMM": "Industrials", "HON": "Industrials",
}


def seed_tickers(
    dynamodb: Any,
    table_name: str,
    tickers: list[str],
) -> tuple[int, int]:
    """Seed tickers into the sp500-tickers table.

    Args:
        dynamodb: Boto3 DynamoDB client
        table_name: Name of the sp500-tickers table
        tickers: List of ticker symbols

    Returns:
        Tuple of (success_count, error_count)
    """
    success_count = 0
    error_count = 0
    now = datetime.now(timezone.utc).isoformat()

    for ticker in tickers:
        try:
            # Get sector (default to "Unknown")
            sector = SECTOR_MAPPINGS.get(ticker, "Unknown")

            # Build item
            item = {
                "PK": {"S": "TICKER_LIST"},
                "SK": {"S": f"TICKER#{ticker}"},
                "ticker": {"S": ticker},
                "company_name": {"S": f"{ticker} Inc."},  # Placeholder
                "sector": {"S": sector},
                "added_date": {"S": now[:10]},
                "is_active": {"BOOL": True},
            }

            dynamodb.put_item(
                TableName=table_name,
                Item=item,
            )
            success_count += 1

        except ClientError as e:
            print(f"✗ Error adding {ticker}: {e}")
            error_count += 1

    return success_count, error_count


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Seed S&P 500 tickers into DynamoDB"
    )
    parser.add_argument(
        "--endpoint",
        help="DynamoDB endpoint URL (for local development)",
        default=None,
    )
    parser.add_argument(
        "--prefix",
        help="Table name prefix",
        default="oss",
    )
    parser.add_argument(
        "--region",
        help="AWS region",
        default="us-east-1",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print tickers without writing to DynamoDB",
    )
    args = parser.parse_args()

    if args.dry_run:
        print(f"S&P 500 Tickers ({len(SP500_TICKERS)} total):")
        for i, ticker in enumerate(sorted(SP500_TICKERS), 1):
            print(f"  {i:3d}. {ticker}")
        return 0

    table_name = f"{args.prefix}-sp500-tickers"
    print(f"Seeding S&P 500 tickers into table: {table_name}")
    print(f"Total tickers: {len(SP500_TICKERS)}")

    if args.endpoint:
        print(f"Using endpoint: {args.endpoint}")

    # Create DynamoDB client
    client_kwargs = {"region_name": args.region}
    if args.endpoint:
        client_kwargs["endpoint_url"] = args.endpoint

    dynamodb = boto3.client("dynamodb", **client_kwargs)

    # Seed tickers
    success, errors = seed_tickers(dynamodb, table_name, SP500_TICKERS)

    print(f"\n✓ Successfully seeded: {success} tickers")
    if errors:
        print(f"✗ Errors: {errors} tickers")

    return 0 if errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
