#!/usr/bin/env python3
"""Generate a session report from the timetrack activity log."""

import argparse
import csv
import sys
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import List, Optional


@dataclass(frozen=True)
class ActivityEvent:
    timestamp: datetime
    event_type: str
    event_subtype: str
    row_number: int


@dataclass(frozen=True)
class Interval:
    start: datetime
    end: datetime

    @property
    def duration_seconds(self) -> float:
        return (self.end - self.start).total_seconds()


@dataclass
class SessionReport:
    start: datetime
    end: datetime
    screensaver_spans: List[Interval]
    source: str
    capped: bool = False

    @property
    def session_hours(self) -> float:
        return (self.end - self.start).total_seconds() / 3600.0

    @property
    def screensaver_hours(self) -> float:
        return sum(span.duration_seconds for span in self.screensaver_spans) / 3600.0

    @property
    def active_hours(self) -> float:
        return max(0.0, self.session_hours - self.screensaver_hours)


def default_log_file() -> Path:
    return Path.home() / ".local" / "share" / "timetrack" / "activity.csv"


MAX_SESSION_HOURS = 18


def parse_path(value: str) -> Path:
    return Path(value).expanduser()


def parse_timestamp(value: str) -> datetime:
    value = value.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"

    timestamp = datetime.fromisoformat(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc)


def load_events(log_file: Path) -> List[ActivityEvent]:
    events: List[ActivityEvent] = []

    with log_file.open(newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("CSV log is missing a header row")

        for row_number, row in enumerate(reader, start=2):
            timestamp_raw = (row.get("timestamp") or "").strip()
            event_type = (row.get("event_type") or "").strip()
            event_subtype = (row.get("event_subtype") or "").strip()

            if not timestamp_raw or not event_type or not event_subtype:
                print(f"Warning: skipping malformed row {row_number}", file=sys.stderr)
                continue

            try:
                timestamp = parse_timestamp(timestamp_raw)
            except ValueError as exc:
                print(f"Warning: skipping row {row_number}: {exc}", file=sys.stderr)
                continue

            events.append(ActivityEvent(timestamp, event_type, event_subtype, row_number))

    events.sort(key=lambda event: (event.timestamp, event.row_number))
    return events


def overlap_seconds(start: datetime, end: datetime, other_start: datetime, other_end: datetime) -> float:
    overlap_start = max(start, other_start)
    overlap_end = min(end, other_end)
    if overlap_end <= overlap_start:
        return 0.0
    return (overlap_end - overlap_start).total_seconds()


def build_sessions(events: List[ActivityEvent], as_of: datetime) -> List[SessionReport]:
    sessions: List[SessionReport] = []
    session_start: Optional[datetime] = None
    tracker_start: Optional[datetime] = None
    screensaver_start: Optional[datetime] = None
    screensaver_spans: List[Interval] = []

    def anchor() -> Optional[datetime]:
        return session_start or tracker_start

    def anchor_source() -> Optional[str]:
        if session_start is not None:
            return "session"
        if tracker_start is not None:
            return "tracker"
        return None

    def session_limit(start: datetime) -> datetime:
        return start + timedelta(hours=MAX_SESSION_HOURS)

    def close_session(end: datetime, capped: bool = False) -> None:
        nonlocal session_start, tracker_start, screensaver_start, screensaver_spans

        start = anchor()
        source = anchor_source()
        if start is None:
            return

        if end > start:
            if screensaver_start is not None:
                screensaver_spans.append(Interval(screensaver_start, end))
                screensaver_start = None

            sessions.append(SessionReport(start, end, list(screensaver_spans), source or "unknown", capped=capped))

        session_start = None
        tracker_start = None
        screensaver_start = None
        screensaver_spans = []

    for event in events:
        current_anchor = anchor()
        if current_anchor is not None:
            limit = session_limit(current_anchor)
            if event.timestamp >= limit:
                close_session(limit, capped=True)

        if event.event_type == "tracker" and event.event_subtype == "start":
            if session_start is None and tracker_start is None:
                tracker_start = event.timestamp
        elif event.event_type == "tracker" and event.event_subtype == "stop":
            close_session(event.timestamp)
        elif event.event_type == "session":
            if event.event_subtype == "activate":
                if session_start is None:
                    session_start = event.timestamp
                    tracker_start = None
                    screensaver_start = None
                    screensaver_spans = []
            elif event.event_subtype == "deactivate":
                close_session(event.timestamp)
        elif event.event_type == "screensaver" and anchor() is not None:
            if event.event_subtype == "activate":
                if screensaver_start is None:
                    screensaver_start = event.timestamp
            elif event.event_subtype == "deactivate" and screensaver_start is not None:
                if event.timestamp > screensaver_start:
                    screensaver_spans.append(Interval(screensaver_start, event.timestamp))
                screensaver_start = None

    current_anchor = anchor()
    if current_anchor is not None:
        limit = session_limit(current_anchor)
        end = min(as_of, limit)
        close_session(end, capped=end == limit and as_of > limit)

    return sessions


def format_debug_timestamp(timestamp: datetime) -> str:
    local = timestamp.astimezone()
    return f"{timestamp.isoformat()} | local={local.isoformat(timespec='seconds')}"


def debug_print_events(events: List[ActivityEvent]) -> None:
    print("DEBUG events:", file=sys.stderr)
    for event in events:
        print(
            f"  #{event.row_number} {format_debug_timestamp(event.timestamp)} "
            f"{event.event_type}/{event.event_subtype}",
            file=sys.stderr,
        )


def debug_print_sessions(sessions: List[SessionReport]) -> None:
    print("DEBUG sessions:", file=sys.stderr)
    for index, session in enumerate(sessions, start=1):
        print(
            f"  {index}. {format_debug_timestamp(session.start)} -> {format_debug_timestamp(session.end)} "
            f"source={session.source} capped={'yes' if session.capped else 'no'} "
            f"session_hours={session.session_hours:.2f} screensaver_hours={session.screensaver_hours:.2f} "
            f"active_hours={session.active_hours:.2f}",
            file=sys.stderr,
        )
        for span_index, span in enumerate(session.screensaver_spans, start=1):
            print(
                f"     screensaver {span_index}: {format_debug_timestamp(span.start)} -> {format_debug_timestamp(span.end)}",
                file=sys.stderr,
            )


def split_session_by_midnight(session: SessionReport) -> List[Interval]:
    start_local = session.start.astimezone()
    end_local = session.end.astimezone()
    segments: List[Interval] = []
    cursor = start_local

    while cursor.date() < end_local.date():
        next_midnight = datetime.combine(cursor.date() + timedelta(days=1), time.min, tzinfo=cursor.tzinfo)
        segments.append(Interval(cursor, next_midnight))
        cursor = next_midnight

    segments.append(Interval(cursor, end_local))
    return segments


def segment_screensaver_seconds(session: SessionReport, segment: Interval) -> float:
    segment_start = segment.start.astimezone(timezone.utc)
    segment_end = segment.end.astimezone(timezone.utc)
    return sum(
        overlap_seconds(segment_start, segment_end, span.start, span.end)
        for span in session.screensaver_spans
    )


def format_local_date(timestamp: datetime) -> str:
    return timestamp.astimezone().strftime("%Y-%m-%d")


def format_local_time(timestamp: datetime) -> str:
    return timestamp.astimezone().strftime("%H:%M:%S")


def print_report(sessions: List[SessionReport], debug: bool = False) -> None:
    if not sessions:
        print("No completed sessions found.")
        return

    if debug:
        debug_print_sessions(sessions)

    rows = []
    total_hours = 0.0

    for session in sessions:
        for segment in split_session_by_midnight(session):
            start_local = segment.start
            end_local = segment.end
            session_hours = (segment.end - segment.start).total_seconds() / 3600.0
            screensaver_hours = segment_screensaver_seconds(session, segment) / 3600.0
            active_hours = max(0.0, session_hours - screensaver_hours)
            total_hours += active_hours
            capped = session.capped and segment.end == session.end
            rows.append(
                [
                    format_local_date(start_local),
                    format_local_time(start_local),
                    format_local_time(end_local),
                    f"{session_hours:.2f}",
                    f"{screensaver_hours:.2f}",
                    f"{active_hours:.2f}",
                    "yes" if capped else "",
                ]
            )

    headers = ["Date", "Start", "End", "Duration", "Screensaver", "Active", "Capped"]
    widths = [len(header) for header in headers]
    for row in rows:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(value))

    header_line = (
        f"{headers[0]:<{widths[0]}}  "
        f"{headers[1]:<{widths[1]}}  "
        f"{headers[2]:<{widths[2]}}  "
        f"{headers[3]:>{widths[3]}}  "
        f"{headers[4]:>{widths[4]}}  "
        f"{headers[5]:>{widths[5]}}  "
        f"{headers[6]:<{widths[6]}}"
    )
    separator_line = (
        f"{'-' * widths[0]:<{widths[0]}}  "
        f"{'-' * widths[1]:<{widths[1]}}  "
        f"{'-' * widths[2]:<{widths[2]}}  "
        f"{'-' * widths[3]:>{widths[3]}}  "
        f"{'-' * widths[4]:>{widths[4]}}  "
        f"{'-' * widths[5]:>{widths[5]}}  "
        f"{'-' * widths[6]:<{widths[6]}}"
    )

    print(header_line)
    print(separator_line)
    for row in rows:
        print(
            f"{row[0]:<{widths[0]}}  "
            f"{row[1]:<{widths[1]}}  "
            f"{row[2]:<{widths[2]}}  "
            f"{row[3]:>{widths[3]}}  "
            f"{row[4]:>{widths[4]}}  "
            f"{row[5]:>{widths[5]}}  "
            f"{row[6]:<{widths[6]}}"
        )

    print()
    print(f"Total active hours: {total_hours:.2f}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a session report from timetrack activity logs.")
    parser.add_argument(
        "--log-file",
        type=parse_path,
        default=default_log_file(),
        help="Path to activity.csv (defaults to ~/.local/share/timetrack/activity.csv)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print parsed events and derived sessions to stderr",
    )
    args = parser.parse_args()

    log_file = args.log_file.expanduser()
    if not log_file.exists():
        print(f"Error: log file not found: {log_file}", file=sys.stderr)
        return 1

    try:
        events = load_events(log_file)
    except (OSError, ValueError) as exc:
        print(f"Error reading log file: {exc}", file=sys.stderr)
        return 1

    if args.debug:
        debug_print_events(events)

    sessions = build_sessions(events, datetime.now(timezone.utc))
    print_report(sessions, debug=args.debug)
    return 0


if __name__ == "__main__":
    sys.exit(main())
