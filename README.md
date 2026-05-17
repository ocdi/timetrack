# Session Activity Tracker

A lightweight Python daemon that monitors and logs session activity events on Gnome/Linux systems.

## Features

Tracks the following events:
 - **Session active**: GNOME session is active at service start, with username when available
- **Screensaver**: Activate/deactivate
- **System**: Suspend/resume
- **System**: Shutdown
- **Session**: Activate/deactivate (when session becomes active/inactive)
- **Tracker**: Start/stop (when the tracker itself starts or stops)

All events are logged to a CSV file with timestamps for easy analysis.

On startup the tracker also reconciles the current logind session state, so a service restart will emit a login/logout record for the active session if one exists.

## Session Report

To turn the activity log into a session report, run:

```bash
python3 timetrack_report.py
```

This reads `~/.local/share/timetrack/activity.csv`, converts the session timestamps to your system local time, and prints each session with its date, start time, end time, session length, screensaver time, and active hours.

Screensaver time is excluded from active hours but does not end a session. Sessions are split at local midnight so overnight activity stays readable. Any session left open in the log is capped at 18 hours so a broken logout cannot inflate the report.

You can also point it at another log file:

```bash
python3 timetrack_report.py --log-file /path/to/activity.csv
```

Add `--debug` to print the parsed UTC events, their local-time conversions, and the derived sessions to stderr:

```bash
python3 timetrack_report.py --debug
```

Tracker-mode sessions treat short `tracker stop`/`tracker start` gaps as continuous. The default merge window is 5 minutes, and you can change it with `--tracker-restart-gap`.

## Local CSV Replay

To inspect the raw log in local time, run:

```bash
python3 timetrack_replay.py
```

This rewrites the `timestamp` column into local time and streams the CSV back out, which makes it easier to compare with the session report.

You can write the replay to a file:

```bash
python3 timetrack_replay.py --output /tmp/activity-local.csv
```

## Requirements

- Python 3.8 or higher
- Gnome desktop environment
- systemd

## Installation

### 1. Install system dependencies

On Ubuntu/Debian:
```bash
sudo apt install python3-pydbus python3-gi
```

### 2. Install the service

```bash
# Create systemd user directory if it doesn't exist
mkdir -p ~/.config/systemd/user

# Copy the service file
cp timetrack.service ~/.config/systemd/user/

# If you logged out while testing, re-enable/reload after updating the unit
systemctl --user daemon-reload
systemctl --user restart timetrack.service

# Reload systemd
systemctl --user daemon-reload

# Enable the service to start on boot
systemctl --user enable timetrack.service

# Start the service now
systemctl --user start timetrack.service
```

### 3. Verify it's running

The service is configured to restart automatically, so it should come back after logout/login once your user manager is active again.

```bash
# Check service status
systemctl --user status timetrack.service

# View recent log entries
journalctl --user -u timetrack.service -f
```

## Usage

### Check the activity log

The activity log is stored at:
```
~/.local/share/timetrack/activity.csv
```

You can view it with any text editor or spreadsheet application:
```bash
# View with cat
cat ~/.local/share/timetrack/activity.csv

# View with less
less ~/.local/share/timetrack/activity.csv

# Open in LibreOffice Calc
libreoffice --calc ~/.local/share/timetrack/activity.csv
```

### CSV Format

The log file has the following columns:
- **timestamp**: ISO 8601 format timestamp (UTC)
- **event_type**: Type of event (session, screensaver, system, tracker)
- **event_subtype**: Specific event (active, login, logout, activate, deactivate, suspend, resume, shutdown, start, stop)
- **details**: Additional information such as `session_id=...` and `user=...`

Example:
```csv
timestamp,event_type,event_subtype,details
2026-02-08T10:30:45.123456+00:00,tracker,start,
2026-02-08T10:35:12.456789+00:00,screensaver,activate,
2026-02-08T10:37:03.789012+00:00,screensaver,deactivate,
2026-02-08T11:00:00.000000+00:00,system,suspend,
2026-02-08T11:30:15.234567+00:00,system,resume,
```

### Service Management

```bash
# Start the service
systemctl --user start timetrack.service

# Stop the service
systemctl --user stop timetrack.service

# Restart the service
systemctl --user restart timetrack.service

# Disable auto-start on boot
systemctl --user disable timetrack.service

# Re-enable auto-start
systemctl --user enable timetrack.service

# View logs
journalctl --user -u timetrack.service
```

## Manual Testing

You can also run the tracker manually (without systemd) for testing:

```bash
cd /home/t/code/timetrack
python3 timetrack.py
```

Press Ctrl+C to stop it.

## Uninstallation

```bash
# Stop and disable the service
systemctl --user stop timetrack.service
systemctl --user disable timetrack.service

# Remove the service file
rm ~/.config/systemd/user/timetrack.service

# Reload systemd
systemctl --user daemon-reload

# Optionally, remove log files
rm -rf ~/.local/share/timetrack
```

## Troubleshooting

### Service won't start

1. Check the service status:
   ```bash
   systemctl --user status timetrack.service
   ```

2. View detailed logs:
   ```bash
   journalctl --user -u timetrack.service -n 50
   ```

3. Verify dependencies are installed:
   ```bash
   python3 -c "import pydbus; import gi.repository.GLib; print('OK')"
   ```

### No events being logged

1. Check if the log file exists:
   ```bash
   ls -la ~/.local/share/timetrack/activity.csv
   ```

2. Try triggering events manually:
   - Lock your screen (Super+L)
   - Unlock it
   - Check if events were logged

3. Verify the service is running:
   ```bash
   systemctl --user is-active timetrack.service
   ```

### Permission issues

The service runs as your user, so it should have access to your home directory. If you see permission errors:

```bash
# Ensure the log directory is writable
mkdir -p ~/.local/share/timetrack
chmod 755 ~/.local/share/timetrack
```

### D-Bus connection issues

If you see D-Bus errors in the logs:

1. Ensure you're running Gnome:
   ```bash
   echo $XDG_CURRENT_DESKTOP
   ```

2. Check if the session bus is available:
   ```bash
   echo $DBUS_SESSION_BUS_ADDRESS
   ```

3. The service must run in a user session with D-Bus access.

## Architecture Notes

The tracker uses D-Bus to monitor system events:
- **org.freedesktop.login1**: For suspend/resume and shutdown signals
- **org.gnome.ScreenSaver**: For screensaver activation
- **org.freedesktop.login1.Session**: For session state changes

It runs as a user systemd service, which means:
- No root privileges required
- Starts automatically when you log in
- Stops when you log out
- Has access to the session D-Bus

## License

This project is licensed under the MIT License. See `LICENSE`.
