#!/usr/bin/env python3
"""
Session Activity Tracker
Monitors and logs session events on Gnome/Linux systems.
"""

import os
import sys
import signal
import csv
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    from pydbus import SystemBus, SessionBus
    from gi.repository import GLib
except ImportError:
    print("Error: Required dependencies not found.", file=sys.stderr)
    print("Please install: python3-pydbus python3-gi", file=sys.stderr)
    sys.exit(1)


class ActivityLogger:
    """Handles writing activity events to CSV log file."""
    
    def __init__(self, log_file: Path):
        self.log_file = log_file
        self.lock = threading.Lock()
        self._ensure_log_directory()
        self._ensure_log_header()
    
    def _ensure_log_directory(self):
        """Create log directory if it doesn't exist."""
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
    
    def _ensure_log_header(self):
        """Write CSV header if file doesn't exist."""
        if not self.log_file.exists():
            with open(self.log_file, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['timestamp', 'event_type', 'event_subtype', 'details'])
    
    def log_event(self, event_type: str, event_subtype: str, details: str = ''):
        """Log an event to the CSV file."""
        timestamp = datetime.now(timezone.utc).isoformat()
        
        with self.lock:
            try:
                with open(self.log_file, 'a', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow([timestamp, event_type, event_subtype, details])
            except Exception as e:
                print(f"Error writing to log: {e}", file=sys.stderr)


def _get_session_username(session) -> str:
    for attr in ('UserName', 'Name'):
        try:
            value = getattr(session, attr)
            if value:
                return str(value)
        except Exception:
            continue
    return ''


def _session_details(session_id: str, session) -> str:
    details = [f"session_id={session_id}"]
    username = _get_session_username(session)
    if username:
        details.append(f"user={username}")
    return ','.join(details)


class SessionActivityTracker:
    """Monitors session activity via D-Bus and logs events."""
    
    def __init__(self, log_file: Optional[Path] = None):
        if log_file is None:
            log_dir = Path.home() / '.local' / 'share' / 'timetrack'
            log_file = log_dir / 'activity.csv'
        
        self.logger = ActivityLogger(log_file)
        self.system_bus = None
        self.session_bus = None
        self.loop = None
        self.running = False
        
        # Set up signal handlers
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully."""
        print(f"\nReceived signal {signum}, shutting down...", file=sys.stderr)
        self.stop()
    
    def _on_prepare_for_sleep(self, sleeping: bool):
        """Handle system suspend/resume events."""
        if sleeping:
            self.logger.log_event('system', 'suspend', '')
        else:
            self.logger.log_event('system', 'resume', '')
    
    def _on_prepare_for_shutdown(self, shutting_down: bool):
        """Handle system shutdown events."""
        if shutting_down:
            self.logger.log_event('system', 'shutdown', '')
    
    def _on_screensaver_active_changed(self, active: bool):
        """Handle screensaver activate/deactivate events."""
        if active:
            self.logger.log_event('screensaver', 'activate', '')
        else:
            self.logger.log_event('screensaver', 'deactivate', '')
    
    def _on_session_properties_changed(self, interface, changed, invalidated):
        """Handle session property changes (for session state tracking)."""
        if 'Active' in changed:
            active = changed['Active']
            if active:
                self.logger.log_event('session', 'activate', '')
            else:
                self.logger.log_event('session', 'deactivate', '')

    def _on_session_new(self, session_id: str, session_path: str):
        """Handle new logind sessions."""
        try:
            session = self.system_bus.get('org.freedesktop.login1', session_path)
            username = _get_session_username(session)
            details = f"session_id={session_id}"
            if username:
                details += f",user={username}"
            self.logger.log_event('session', 'login', details)
        except Exception as e:
            print(f"Warning: Could not log new session {session_id}: {e}", file=sys.stderr)

    def _on_session_removed(self, session_id: str, session_path: str):
        """Handle removed logind sessions."""
        try:
            self.logger.log_event('session', 'logout', f"session_id={session_id}")
        except Exception as e:
            print(f"Warning: Could not log removed session {session_id}: {e}", file=sys.stderr)

    def _log_current_session_state(self):
        """Reconcile current logind session state at startup."""
        try:
            session_id = os.environ.get('XDG_SESSION_ID')
            if not session_id:
                self.logger.log_event('session', 'logout', 'startup=no-session-id')
                return

            session_path = f'/org/freedesktop/login1/session/{session_id}'
            session = self.system_bus.get('org.freedesktop.login1', session_path)
            active = getattr(session, 'Active', None)
            if active:
                self.logger.log_event('session', 'login', _session_details(session_id, session))
            else:
                self.logger.log_event('session', 'logout', _session_details(session_id, session))
        except Exception as e:
            print(f"Warning: Could not reconcile current session state: {e}", file=sys.stderr)
    
    def start(self):
        """Start monitoring session activity."""
        self.running = True
        
        try:
            # Connect to D-Bus
            self.system_bus = SystemBus()
            self.session_bus = SessionBus()
            
            # Monitor systemd-logind for suspend/resume and shutdown
            login1 = self.system_bus.get('org.freedesktop.login1')
            login1.PrepareForSleep.connect(self._on_prepare_for_sleep)
            login1.PrepareForShutdown.connect(self._on_prepare_for_shutdown)
            try:
                login1.SessionNew.connect(self._on_session_new)
                login1.SessionRemoved.connect(self._on_session_removed)
            except Exception as e:
                print(f"Warning: Could not connect to session login events: {e}", file=sys.stderr)
            
            # Monitor Gnome Screensaver
            try:
                screensaver = self.session_bus.get('org.gnome.ScreenSaver')
                screensaver.ActiveChanged.connect(self._on_screensaver_active_changed)
            except Exception as e:
                print(f"Warning: Could not connect to screensaver: {e}", file=sys.stderr)
            
            # Monitor current session state changes
            try:
                session_id = os.environ.get('XDG_SESSION_ID')
                if session_id:
                    session_path = f'/org/freedesktop/login1/session/{session_id}'
                    session = self.system_bus.get('org.freedesktop.login1', session_path)
                    session.onPropertiesChanged = self._on_session_properties_changed
            except Exception as e:
                print(f"Warning: Could not monitor session state: {e}", file=sys.stderr)

            self._log_current_session_state()
            
            # Log startup
            self.logger.log_event('tracker', 'start', '')
            print("Session activity tracker started", file=sys.stderr)
            
            # Start GLib main loop
            self.loop = GLib.MainLoop()
            self.loop.run()
            
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            self.running = False
            sys.exit(1)
    
    def stop(self):
        """Stop monitoring and exit gracefully."""
        if self.running:
            self.running = False
            self.logger.log_event('tracker', 'stop', '')
            if self.loop:
                self.loop.quit()


def main():
    """Main entry point."""
    tracker = SessionActivityTracker()
    tracker.start()


if __name__ == '__main__':
    main()
