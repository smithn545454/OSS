#!/usr/bin/env python3
"""Check options ingestion progress from the background task log."""
import re
import sys

LOG_FILE = "/private/tmp/claude-501/-Users-nicksmith-OSS--claude-worktrees-xenodochial-joliot/tasks/bf0aa6e.output"

def main():
    try:
        with open(LOG_FILE) as f:
            lines = f.readlines()
    except FileNotFoundError:
        print("No ingestion log found. Is the ingestion running?")
        sys.exit(1)

    quarters = [
        "2023_q4", "2024_q1", "2024_q2", "2024_q3", "2024_q4",
        "2025_q1", "2025_q2", "2025_q3", "2025_q4", "2026_q1",
    ]
    quarter_status = {}
    current_quarter = None

    for line in lines:
        m = re.search(r"QUARTER (\d+)/10: (\d{4}_q\d)", line)
        if m:
            current_quarter = m.group(2)
            quarter_status[current_quarter] = {
                "status": "parsing", "tickers": "0/?", "rows": 0, "dates": 0,
            }

        m = re.search(r"Found (\d+) ticker files", line)
        if m and current_quarter:
            quarter_status[current_quarter]["total_files"] = int(m.group(1))

        m = re.search(r"\[(\d+)/(\d+)\] Parsed \w+ \(([\d,]+) rows total", line)
        if m and current_quarter:
            quarter_status[current_quarter]["tickers"] = f"{m.group(1)}/{m.group(2)}"
            quarter_status[current_quarter]["rows"] = int(m.group(3).replace(",", ""))
            quarter_status[current_quarter]["status"] = "parsing"

        if "Phase 1 complete" in line and current_quarter:
            m2 = re.search(r"([\d,]+) rows from (\d+) files", line)
            if m2:
                quarter_status[current_quarter]["rows"] = int(m2.group(1).replace(",", ""))
                quarter_status[current_quarter]["status"] = "uploading"

        m = re.search(r"Uploaded (\d+)/(\d+) dates", line)
        if m and current_quarter:
            quarter_status[current_quarter]["dates"] = f"{m.group(1)}/{m.group(2)}"
            quarter_status[current_quarter]["status"] = "uploading"

        m = re.search(
            r"Quarter (\d{4}_q\d) done: .* (\d+) dates, ([\d,]+) rows in (\d+)s", line
        )
        if m:
            q = m.group(1)
            if q in quarter_status:
                quarter_status[q]["status"] = "done"
                quarter_status[q]["dates"] = m.group(2)
                quarter_status[q]["rows"] = int(m.group(3).replace(",", ""))
                quarter_status[q]["time"] = m.group(4)

    print()
    print("╔══════════════════════════════════════════════════════════════╗")
    print("║           OPTIONS DATA INGESTION PROGRESS                   ║")
    print("╠══════════╦═══════════╦════════════╦════════════╦═════════════╣")
    print("║ Quarter  ║  Status   ║  Tickers   ║    Rows    ║    Dates    ║")
    print("╠══════════╬═══════════╬════════════╬════════════╬═════════════╣")

    for q in quarters:
        if q in quarter_status:
            s = quarter_status[q]
            status = s["status"]
            if status == "done":
                icon, status_str = "✅", "DONE"
            elif status == "uploading":
                icon, status_str = "📤", "UPLOAD"
            elif status == "parsing":
                icon, status_str = "📝", "PARSE"
            else:
                icon, status_str = "⏳", "WAIT"

            tickers = s.get("tickers", "?")
            rows = f"{s.get('rows', 0):,}"
            dates = s.get("dates", "-")
            time_str = s.get("time", "")
            if time_str:
                status_str += f" {time_str}s"

            print(
                f"║ {q:8s} ║ {icon} {status_str:7s} ║ {tickers:>10s} "
                f"║ {rows:>10s} ║ {str(dates):>11s} ║"
            )
        else:
            print(
                f"║ {q:8s} ║ ⬜ PENDING ║        -   ║          - ║           - ║"
            )

    print("╚══════════╩═══════════╩════════════╩════════════╩═════════════╝")

    total_rows = sum(s.get("rows", 0) for s in quarter_status.values())
    done_count = sum(1 for s in quarter_status.values() if s["status"] == "done")
    print(f"  Total rows so far: {total_rows:,}")
    print(f"  Quarters complete: {done_count}/10")
    print()


if __name__ == "__main__":
    main()
