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
import pwd
import time
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
                writer.writerow(['timestamp', 'event_type', 'event_subtype', 'session_id', 'details'])

    def log_event(self, event_type: str, event_subtype: str, session_id: str = '', details: str = ''):
        """Log an event to the CSV file."""
        timestamp = datetime.now(timezone.utc).isoformat()
        
        with self.lock:
            try:
                with open(self.log_file, 'a', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow([timestamp, event_type, event_subtype, session_id, details])
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

    try:
        user = getattr(session, 'User')
        if user:
            if isinstance(user, (tuple, list)) and len(user) > 1 and user[1]:
                return str(user[1])
            if hasattr(user, '__getitem__'):
                candidate = user[1]
                if candidate:
                    return str(candidate)
    except Exception:
        pass

    try:
        return str(session.Get('org.freedesktop.login1.Session', 'Name'))
    except Exception:
        pass

    return ''


def _get_current_username() -> str:
    try:
        return pwd.getpwuid(os.getuid()).pw_name
    except Exception:
        return os.environ.get('USER', '')


def _session_details(session_id: str, session) -> str:
    details = []
    username = _get_session_username(session)
    if username:
        details.append(f"user={username}")
    return ','.join(details)


def _get_current_session(self_system_bus, username: str = '') -> Optional[tuple[str, object]]:
    try:
        login1 = self_system_bus.get('org.freedesktop.login1')
        pid = os.getpid()
        try:
            session_path = login1.GetSessionByPID(pid)
            session_id = session_path.rsplit('/', 1)[-1]
            session = self_system_bus.get('org.freedesktop.login1', session_path)
            return session_id, session
        except Exception:
            pass

        uid = os.getuid()
        sessions = login1.ListSessions()
        fallback = None
        for entry in sessions:
            if len(entry) < 5:
                continue

            session_id, session_uid, session_user, _seat_id, session_path = entry[:5]
            if session_uid != uid:
                continue
            if username and session_user and str(session_user) != username:
                continue

            session = self_system_bus.get('org.freedesktop.login1', session_path)
            if getattr(session, 'Active', False):
                return session_id, session
            if fallback is None:
                fallback = (session_id, session)

        if fallback is not None:
            return fallback

        return None
    except Exception:
        return None


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
        self.current_session_id = None
        self.current_username = _get_current_username()
        
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
            self.logger.log_event('system', 'suspend', self.current_session_id or '', '')
        else:
            self.logger.log_event('system', 'resume', self.current_session_id or '', '')
    
    def _on_prepare_for_shutdown(self, shutting_down: bool):
        """Handle system shutdown events."""
        if shutting_down:
            self.logger.log_event('system', 'shutdown', self.current_session_id or '', '')
    
    def _on_screensaver_active_changed(self, active: bool):
        """Handle screensaver activate/deactivate events."""
        if active:
            self.logger.log_event('screensaver', 'activate', self.current_session_id or '', '')
        else:
            self.logger.log_event('screensaver', 'deactivate', self.current_session_id or '', '')
    
    def _on_session_properties_changed(self, interface, changed, invalidated):
        """Handle session property changes (for session state tracking)."""
        if 'Active' in changed:
            active = changed['Active']
            if active:
                session_id = self.current_session_id or ''
                self.logger.log_event('session', 'activate', session_id, '')
            else:
                session_id = self.current_session_id or ''
                self.logger.log_event('session', 'deactivate', session_id, '')

    def _on_session_new(self, session_id: str, session_path: str):
        """Handle new logind sessions."""
        try:
            session = self.system_bus.get('org.freedesktop.login1', session_path)
            username = _get_session_username(session)
            if self.current_username and username and username != self.current_username:
                return
            details = f"session_id={session_id}"
            if username:
                details += f",user={username}"
            self.logger.log_event('session', 'login', session_id, details)
            self.current_session_id = session_id
        except Exception as e:
            print(f"Warning: Could not log new session {session_id}: {e}", file=sys.stderr)

    def _on_session_removed(self, session_id: str, session_path: str):
        """Handle removed logind sessions."""
        try:
            if self.current_session_id and session_id != self.current_session_id:
                return
            self.logger.log_event('session', 'logout', session_id, '')
            self.current_session_id = None
        except Exception as e:
            print(f"Warning: Could not log removed session {session_id}: {e}", file=sys.stderr)

    def _log_current_session_state(self):
        """Reconcile current logind session state at startup."""
        try:
            username = _get_current_username()
            current = _get_current_session(self.system_bus, username)
            if not current:
                details = f"startup=true"
                if username:
                    details += f",user={username}"
                self.logger.log_event('session', 'active', '', details)
                return

            session_id, session = current
            active = getattr(session, 'Active', None)
            if active:
                self.current_session_id = session_id
                username = _get_session_username(session) or username
                details = _session_details(session_id, session)
                if username and 'user=' not in details:
                    details += f",user={username}"
                self.logger.log_event('session', 'active', session_id, details)
            else:
                details = f"session_id={session_id},startup=true"
                if username:
                    details += f",user={username}"
                self.logger.log_event('session', 'active', session_id, details)
                self.current_session_id = session_id
        except Exception as e:
            username = _get_current_username()
            details = f"startup=true"
            if username:
                details += f",user={username}"
            self.logger.log_event('session', 'active', self.current_session_id or '', details)
            print(f"Warning: Could not reconcile current session state: {e}", file=sys.stderr)
    
    def start(self):
        """Start monitoring session activity."""
        self.running = True

        attempt = 0
        while self.running:
            attempt += 1
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
                self.logger.log_event('tracker', 'start', self.current_session_id or '', '')
                print("Session activity tracker started", file=sys.stderr)

                # Start GLib main loop
                self.loop = GLib.MainLoop()
                self.loop.run()
                return

            except Exception as e:
                print(f"Warning: tracker startup failed, retrying: {e}", file=sys.stderr)
                time.sleep(2)

        self.running = False
        sys.exit(1)
    
    def stop(self):
        """Stop monitoring and exit gracefully."""
        if self.running:
            self.running = False
            self.logger.log_event('tracker', 'stop', self.current_session_id or '', '')
            if self.loop:
                self.loop.quit()


def main():
    """Main entry point."""
    tracker = SessionActivityTracker()
    tracker.start()


if __name__ == '__main__':
    main()
