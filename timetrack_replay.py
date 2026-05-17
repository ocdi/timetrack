#!/usr/bin/env python3
"""Replay the activity log as CSV using local timestamps."""

import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path


def default_log_file() -> Path:
    return Path.home() / ".local" / "share" / "timetrack" / "activity.csv"


def parse_path(value: str) -> Path:
    return Path(value).expanduser()


def parse_timestamp(value: str) -> datetime:
    value = value.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"

    timestamp = datetime.fromisoformat(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone()


def replay(log_file: Path, output_file) -> int:
    with log_file.open(newline="") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames is None:
            raise ValueError("CSV log is missing a header row")

        fieldnames = list(reader.fieldnames)
        writer = csv.DictWriter(output_file, fieldnames=fieldnames)
        writer.writeheader()

        for row_number, row in enumerate(reader, start=2):
            timestamp_raw = (row.get("timestamp") or "").strip()
            if timestamp_raw:
                try:
                    row["timestamp"] = parse_timestamp(timestamp_raw).isoformat()
                except ValueError as exc:
                    print(f"Warning: skipping row {row_number}: {exc}", file=sys.stderr)
                    continue
            writer.writerow(row)

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay activity.csv in local time.")
    parser.add_argument(
        "--log-file",
        type=parse_path,
        default=default_log_file(),
        help="Path to activity.csv (defaults to ~/.local/share/timetrack/activity.csv)",
    )
    parser.add_argument(
        "--output",
        type=parse_path,
        help="Write the local-time CSV to this file instead of stdout",
    )
    args = parser.parse_args()

    log_file = args.log_file.expanduser()
    if not log_file.exists():
        print(f"Error: log file not found: {log_file}", file=sys.stderr)
        return 1

    try:
        if args.output is None:
            return replay(log_file, sys.stdout)

        with args.output.expanduser().open("w", newline="") as output_file:
            return replay(log_file, output_file)
    except (OSError, ValueError) as exc:
        print(f"Error processing log file: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
