#!/usr/bin/env python3
"""System diagnostic script for OSS.

Checks all prerequisites and reports on system state.
"""

import os
import sys
import subprocess
import json
from typing import Dict, List, Tuple
from dataclasses import dataclass


@dataclass
class DiagnosticResult:
    """Result of a diagnostic check."""
    name: str
    status: str  # "OK", "WARN", "ERROR"
    message: str
    details: List[str]


class Colors:
    """ANSI color codes for terminal output."""
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BLUE = '\033[94m'
    RESET = '\033[0m'
    BOLD = '\033[1m'


def check_port(port: int, service_name: str) -> DiagnosticResult:
    """Check if a service is listening on a port."""
    try:
        result = subprocess.run(
            ['lsof', '-i', f':{port}', '-sTCP:LISTEN'],
            capture_output=True,
            text=True
        )
        if result.returncode == 0 and result.stdout:
            return DiagnosticResult(
                name=f"{service_name} (Port {port})",
                status="OK",
                message=f"{service_name} is running",
                details=[line.strip() for line in result.stdout.split('\n') if line.strip()][:2]
            )
        else:
            return DiagnosticResult(
                name=f"{service_name} (Port {port})",
                status="ERROR",
                message=f"{service_name} is not running",
                details=[f"No service listening on port {port}"]
            )
    except Exception as e:
        return DiagnosticResult(
            name=f"{service_name} (Port {port})",
            status="ERROR",
            message=f"Failed to check {service_name}",
            details=[str(e)]
        )


def check_env_file() -> DiagnosticResult:
    """Check if .env file exists and has required variables."""
    env_path = "/Users/nicksmith/OSS/backend/.env"
    
    if not os.path.exists(env_path):
        return DiagnosticResult(
            name="Environment File",
            status="ERROR",
            message=".env file not found",
            details=[f"Expected at: {env_path}"]
        )
    
    required_vars = ["AWS_REGION", "DYNAMODB_TABLE_PREFIX"]
    optional_vars = ["POLYGON_API_KEY", "DYNAMODB_ENDPOINT"]
    
    try:
        with open(env_path, 'r') as f:
            content = f.read()
        
        found_vars = []
        missing_vars = []
        
        for var in required_vars:
            if var in content:
                found_vars.append(var)
            else:
                missing_vars.append(var)
        
        for var in optional_vars:
            if var in content:
                found_vars.append(var)
        
        if missing_vars:
            return DiagnosticResult(
                name="Environment File",
                status="WARN",
                message=".env file exists but missing required variables",
                details=[f"Found: {', '.join(found_vars)}", f"Missing: {', '.join(missing_vars)}"]
            )
        else:
            return DiagnosticResult(
                name="Environment File",
                status="OK",
                message=".env file configured",
                details=[f"Variables: {', '.join(found_vars)}"]
            )
    except Exception as e:
        return DiagnosticResult(
            name="Environment File",
            status="ERROR",
            message="Failed to read .env file",
            details=[str(e)]
        )


def check_dynamodb_local() -> DiagnosticResult:
    """Check if DynamoDB Local is running."""
    try:
        import boto3
        client = boto3.client('dynamodb', endpoint_url='http://localhost:8000', region_name='us-east-1')
        response = client.list_tables()
        tables = response.get('TableNames', [])
        oss_tables = [t for t in tables if t.startswith('oss')]
        
        if oss_tables:
            return DiagnosticResult(
                name="DynamoDB Local",
                status="OK",
                message=f"DynamoDB Local running with {len(oss_tables)} OSS tables",
                details=oss_tables[:5]
            )
        else:
            return DiagnosticResult(
                name="DynamoDB Local",
                status="WARN",
                message="DynamoDB Local running but no OSS tables found",
                details=["Run: python infrastructure/dynamodb_tables.py --endpoint http://localhost:8000"]
            )
    except Exception as e:
        return DiagnosticResult(
            name="DynamoDB Local",
            status="ERROR",
            message="DynamoDB Local not accessible",
            details=[
                str(e),
                "Start with: docker run -p 8000:8000 amazon/dynamodb-local",
                "Or configure to use AWS DynamoDB"
            ]
        )


def check_aws_dynamodb() -> DiagnosticResult:
    """Check if AWS DynamoDB has OSS tables."""
    try:
        import boto3
        client = boto3.client('dynamodb', region_name='us-east-1')
        response = client.list_tables()
        tables = response.get('TableNames', [])
        oss_tables = [t for t in tables if t.startswith('oss')]
        
        if oss_tables:
            return DiagnosticResult(
                name="AWS DynamoDB",
                status="OK",
                message=f"AWS DynamoDB has {len(oss_tables)} OSS tables",
                details=oss_tables[:5]
            )
        else:
            return DiagnosticResult(
                name="AWS DynamoDB",
                status="WARN",
                message="AWS DynamoDB accessible but no OSS tables",
                details=["Deploy tables with: python infrastructure/dynamodb_tables.py"]
            )
    except Exception as e:
        return DiagnosticResult(
            name="AWS DynamoDB",
            status="WARN",
            message="AWS DynamoDB not accessible or not configured",
            details=[str(e)]
        )


def check_active_policy() -> DiagnosticResult:
    """Check if an active policy exists."""
    # Try local first
    try:
        import boto3
        client = boto3.client('dynamodb', endpoint_url='http://localhost:8000', region_name='us-east-1')
        response = client.scan(
            TableName='oss-policies',
            FilterExpression='is_active = :val',
            ExpressionAttributeValues={':val': {'BOOL': True}},
            Limit=1
        )
        if response.get('Items'):
            version = response['Items'][0].get('version', {}).get('S', 'unknown')
            return DiagnosticResult(
                name="Active Policy (Local)",
                status="OK",
                message=f"Active policy found: {version}",
                details=[f"Version: {version}"]
            )
    except:
        pass
    
    # Try AWS
    try:
        import boto3
        client = boto3.client('dynamodb', region_name='us-east-1')
        response = client.scan(
            TableName='oss-policies',
            FilterExpression='is_active = :val',
            ExpressionAttributeValues={':val': {'BOOL': True}},
            Limit=1
        )
        if response.get('Items'):
            version = response['Items'][0].get('version', {}).get('S', 'unknown')
            return DiagnosticResult(
                name="Active Policy (AWS)",
                status="OK",
                message=f"Active policy found: {version}",
                details=[f"Version: {version}"]
            )
    except:
        pass
    
    return DiagnosticResult(
        name="Active Policy",
        status="ERROR",
        message="No active policy found",
        details=[
            "Seed policy with: python scripts/seed_default_policy.py --endpoint http://localhost:8000 --activate",
            "Or for AWS: python scripts/seed_default_policy.py --activate"
        ]
    )


def check_venv() -> DiagnosticResult:
    """Check if Python virtual environment exists."""
    venv_path = "/Users/nicksmith/OSS/backend/venv"
    
    if not os.path.exists(venv_path):
        return DiagnosticResult(
            name="Python Virtual Environment",
            status="ERROR",
            message="Virtual environment not found",
            details=[f"Expected at: {venv_path}", "Create with: python3 -m venv backend/venv"]
        )
    
    python_path = os.path.join(venv_path, "bin", "python")
    if os.path.exists(python_path):
        return DiagnosticResult(
            name="Python Virtual Environment",
            status="OK",
            message="Virtual environment exists",
            details=[f"Location: {venv_path}"]
        )
    else:
        return DiagnosticResult(
            name="Python Virtual Environment",
            status="WARN",
            message="Virtual environment directory exists but may be incomplete",
            details=[f"Location: {venv_path}"]
        )


def print_result(result: DiagnosticResult):
    """Print a diagnostic result with colors."""
    status_color = {
        "OK": Colors.GREEN,
        "WARN": Colors.YELLOW,
        "ERROR": Colors.RED
    }
    
    color = status_color.get(result.status, Colors.RESET)
    status_symbol = {
        "OK": "✓",
        "WARN": "⚠",
        "ERROR": "✗"
    }
    
    symbol = status_symbol.get(result.status, "?")
    print(f"\n{color}{symbol} {result.name}{Colors.RESET}")
    print(f"  {result.message}")
    
    if result.details:
        for detail in result.details:
            print(f"    {Colors.BLUE}•{Colors.RESET} {detail}")


def main():
    """Run all diagnostic checks."""
    print(f"{Colors.BOLD}{'='*70}{Colors.RESET}")
    print(f"{Colors.BOLD}OSS System Diagnostics{Colors.RESET}")
    print(f"{Colors.BOLD}{'='*70}{Colors.RESET}")
    
    # Run all checks
    results = [
        check_venv(),
        check_env_file(),
        check_port(8000, "Backend API"),
        check_port(5173, "Frontend Dev Server"),
        check_dynamodb_local(),
        check_aws_dynamodb(),
        check_active_policy(),
    ]
    
    # Print results
    for result in results:
        print_result(result)
    
    # Summary
    ok_count = sum(1 for r in results if r.status == "OK")
    warn_count = sum(1 for r in results if r.status == "WARN")
    error_count = sum(1 for r in results if r.status == "ERROR")
    
    print(f"\n{Colors.BOLD}{'='*70}{Colors.RESET}")
    print(f"{Colors.BOLD}Summary{Colors.RESET}")
    print(f"{Colors.BOLD}{'='*70}{Colors.RESET}")
    print(f"{Colors.GREEN}✓ OK:    {ok_count}{Colors.RESET}")
    print(f"{Colors.YELLOW}⚠ WARN:  {warn_count}{Colors.RESET}")
    print(f"{Colors.RED}✗ ERROR: {error_count}{Colors.RESET}")
    
    # Recommendations
    print(f"\n{Colors.BOLD}Recommendations:{Colors.RESET}")
    
    if error_count > 0:
        print(f"\n{Colors.RED}Critical issues found that prevent the system from working:{Colors.RESET}")
        for result in results:
            if result.status == "ERROR":
                print(f"\n{Colors.BOLD}{result.name}:{Colors.RESET}")
                for detail in result.details:
                    print(f"  {detail}")
    
    if warn_count > 0 and error_count == 0:
        print(f"\n{Colors.YELLOW}Warnings found (system may work but not optimally):{Colors.RESET}")
        for result in results:
            if result.status == "WARN":
                print(f"\n{Colors.BOLD}{result.name}:{Colors.RESET}")
                for detail in result.details:
                    print(f"  {detail}")
    
    if error_count == 0 and warn_count == 0:
        print(f"\n{Colors.GREEN}All checks passed! System is ready.{Colors.RESET}")
        print("\nNext steps:")
        print("  1. Ensure backend is running: cd backend && uvicorn app.main:app --reload")
        print("  2. Ensure frontend is running: cd frontend && npm run dev")
        print("  3. Trigger a scan: curl -X POST http://localhost:8000/api/scanners/run")
    
    print(f"\n{Colors.BOLD}{'='*70}{Colors.RESET}\n")
    
    return 1 if error_count > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
