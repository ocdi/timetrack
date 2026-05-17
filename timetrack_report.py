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
    session_id: str
    details: str
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
DEFAULT_TRACKER_RESTART_GAP = timedelta(minutes=5)
DEFAULT_STRAY_TRACKER_START_GAP = timedelta(hours=4)
DEFAULT_SESSION_MERGE_GAP = timedelta(minutes=5)


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
            session_id = (row.get("session_id") or "").strip()
            details = (row.get("details") or "").strip()

            if not timestamp_raw or not event_type or not event_subtype:
                print(f"Warning: skipping malformed row {row_number}", file=sys.stderr)
                continue

            try:
                timestamp = parse_timestamp(timestamp_raw)
            except ValueError as exc:
                print(f"Warning: skipping row {row_number}: {exc}", file=sys.stderr)
                continue

            events.append(ActivityEvent(timestamp, event_type, event_subtype, session_id, details, row_number))

    events.sort(key=lambda event: (event.timestamp, event.row_number))
    return events


def overlap_seconds(start: datetime, end: datetime, other_start: datetime, other_end: datetime) -> float:
    overlap_start = max(start, other_start)
    overlap_end = min(end, other_end)
    if overlap_end <= overlap_start:
        return 0.0
    return (overlap_end - overlap_start).total_seconds()


def details_value(details: str, key: str) -> Optional[str]:
    for part in details.split(','):
        part = part.strip()
        if part.startswith(f"{key}="):
            return part.split('=', 1)[1]
    return None


def event_session_id(event: ActivityEvent) -> Optional[str]:
    if event.session_id:
        return event.session_id
    return details_value(event.details, "session_id")


def merge_sessions(sessions: List[SessionReport], merge_gap: timedelta) -> List[SessionReport]:
    if not sessions:
        return []

    merged: List[SessionReport] = [sessions[0]]
    for session in sessions[1:]:
        previous = merged[-1]
        gap = session.start - previous.end
        if gap <= merge_gap:
            merged[-1] = SessionReport(
                previous.start,
                session.end,
                previous.screensaver_spans + session.screensaver_spans,
                previous.source,
                capped=previous.capped or session.capped,
            )
        else:
            merged.append(session)

    return merged


def build_sessions(
    events: List[ActivityEvent],
    as_of: datetime,
    tracker_restart_gap: timedelta = DEFAULT_TRACKER_RESTART_GAP,
    session_merge_gap: timedelta = DEFAULT_SESSION_MERGE_GAP,
) -> List[SessionReport]:
    sessions: List[SessionReport] = []
    session_start: Optional[datetime] = None
    screensaver_start: Optional[datetime] = None
    screensaver_spans: List[Interval] = []
    current_source: str = "unknown"
    tracker_pending_stop: Optional[datetime] = None
    current_session_id: Optional[str] = None

    def anchor() -> Optional[datetime]:
        return session_start

    def start_session(start: datetime, source: str) -> None:
        nonlocal session_start, screensaver_start, screensaver_spans, current_source, tracker_pending_stop
        session_start = start
        current_source = source
        screensaver_start = None
        screensaver_spans = []
        tracker_pending_stop = None

    def session_limit(start: datetime) -> datetime:
        return start + timedelta(hours=MAX_SESSION_HOURS)

    def close_session(end: datetime, capped: bool = False) -> None:
        nonlocal session_start, screensaver_start, screensaver_spans, current_source, tracker_pending_stop, current_session_id

        start = anchor()
        if start is None:
            return

        if end > start:
            if screensaver_start is not None:
                screensaver_spans.append(Interval(screensaver_start, end))
                screensaver_start = None

            sessions.append(SessionReport(start, end, list(screensaver_spans), current_source, capped=capped))

        session_start = None
        screensaver_start = None
        screensaver_spans = []
        current_source = "unknown"
        tracker_pending_stop = None
        current_session_id = None

    def flush_pending_tracker_stop(before: datetime) -> None:
        nonlocal tracker_pending_stop
        if tracker_pending_stop is None or session_start is None:
            return
        if before >= tracker_pending_stop + tracker_restart_gap:
            close_session(tracker_pending_stop)

    for event in events:
        flush_pending_tracker_stop(event.timestamp)

        current_anchor = anchor()
        if current_anchor is not None:
            limit = session_limit(current_anchor)
            if event.timestamp >= limit:
                close_session(limit, capped=True)

        if event.event_type == "session":
            session_id = event_session_id(event)

            if event.event_subtype in {"login", "active", "activate"}:
                if session_start is None:
                    start_session(event.timestamp, "session-active" if event.event_subtype in {"login", "active"} else "session")
                    current_session_id = session_id
                elif session_id is not None and current_session_id is not None and session_id != current_session_id:
                    close_session(event.timestamp)
                    start_session(event.timestamp, "session-active")
                    current_session_id = session_id
                elif session_id is not None and current_session_id is None:
                    current_session_id = session_id
            elif event.event_subtype in {"logout", "deactivate"}:
                if session_start is not None and (current_session_id is None or session_id is None or session_id == current_session_id):
                    close_session(event.timestamp)
            continue

        if event.event_type == "system" and event.event_subtype == "suspend":
            if session_start is not None:
                close_session(event.timestamp)
            continue

        if event.event_type == "tracker":
            if event.event_subtype == "start":
                if tracker_pending_stop is not None and event.timestamp - tracker_pending_stop <= tracker_restart_gap:
                    tracker_pending_stop = None
                elif session_start is None and current_source == "unknown":
                    start_session(event.timestamp, "tracker")
            elif event.event_subtype == "stop":
                if session_start is not None:
                    tracker_pending_stop = event.timestamp
            continue

        if event.event_type == "screensaver" and anchor() is not None:
            if event.event_subtype == "activate":
                if screensaver_start is None:
                    screensaver_start = event.timestamp
            elif event.event_subtype == "deactivate" and screensaver_start is not None:
                if event.timestamp > screensaver_start:
                    screensaver_spans.append(Interval(screensaver_start, event.timestamp))
                screensaver_start = None

    current_anchor = anchor()
    if current_anchor is not None:
        if tracker_pending_stop is not None and as_of >= tracker_pending_stop + tracker_restart_gap:
            close_session(tracker_pending_stop)
        else:
            limit = session_limit(current_anchor)
            end = min(as_of, limit)
            close_session(end, capped=end == limit and as_of > limit)

    return merge_sessions(sessions, session_merge_gap)


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


def debug_print_tracker_start_anomalies(
    events: List[ActivityEvent],
    stray_gap: timedelta = DEFAULT_STRAY_TRACKER_START_GAP,
) -> None:
    last_tracker_start: Optional[ActivityEvent] = None

    for event in events:
        if event.event_type != "tracker":
            continue

        if event.event_subtype == "start":
            if last_tracker_start is not None:
                gap = event.timestamp - last_tracker_start.timestamp
                if gap >= stray_gap:
                    print(
                        "WARNING: possible stray tracker/start detected "
                        f"at row {last_tracker_start.row_number}. Another tracker/start appears "
                        f"{gap} later at row {event.row_number} with no tracker/stop between. "
                        f"If the earlier start was caused by an accidental wake, remove row {last_tracker_start.row_number} "
                        "from activity.csv while timetrack is stopped.",
                        file=sys.stderr,
                    )
            last_tracker_start = event
        elif event.event_subtype == "stop":
            last_tracker_start = None


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


def format_timedelta_minutes(value: timedelta) -> str:
    return f"{int(value.total_seconds() // 60)}m"


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
                    f"{active_hours:.2f}",
                    format_local_time(end_local),
                    f"{session_hours:.2f}",
                    f"{screensaver_hours:.2f}",
                    "yes" if capped else "",
                ]
            )

    headers = ["Date", "Start", "Active", "End", "Duration", "Screensaver", "Capped"]
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
    parser.add_argument(
        "--tracker-restart-gap",
        type=int,
        default=int(DEFAULT_TRACKER_RESTART_GAP.total_seconds() // 60),
        help="Merge tracker stop/start gaps shorter than this many minutes",
    )
    parser.add_argument(
        "--session-merge-gap",
        type=int,
        default=int(DEFAULT_SESSION_MERGE_GAP.total_seconds() // 60),
        help="Merge adjacent sessions with gaps shorter than this many minutes",
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

    restart_gap = timedelta(minutes=args.tracker_restart_gap)
    session_merge_gap = timedelta(minutes=args.session_merge_gap)
    if args.debug:
        print(f"DEBUG tracker restart gap: {format_timedelta_minutes(restart_gap)}", file=sys.stderr)
        print(
            f"DEBUG session merge gap: {format_timedelta_minutes(session_merge_gap)}",
            file=sys.stderr,
        )
        print(
            f"DEBUG stray tracker/start gap: {format_timedelta_minutes(DEFAULT_STRAY_TRACKER_START_GAP)}",
            file=sys.stderr,
        )

    sessions = build_sessions(
        events,
        datetime.now(timezone.utc),
        tracker_restart_gap=restart_gap,
        session_merge_gap=session_merge_gap,
    )
    if args.debug:
        debug_print_tracker_start_anomalies(events)
    print_report(sessions, debug=args.debug)
    return 0


if __name__ == "__main__":
    sys.exit(main())
