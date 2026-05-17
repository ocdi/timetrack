#!/bin/bash
# Installation script for Session Activity Tracker

set -e

echo "=== Session Activity Tracker Installation ==="
echo

# Check if we're on a Debian/Ubuntu system
if ! command -v apt &> /dev/null; then
    echo "Warning: apt not found. This script is designed for Ubuntu/Debian."
    echo "Please install python3-pydbus and python3-gi manually."
    exit 1
fi

# Install system dependencies
echo "Installing system dependencies..."
sudo apt update
sudo apt install -y python3-pydbus python3-gi

# Verify installation
echo
echo "Verifying dependencies..."
if python3 -c "import pydbus; import gi.repository.GLib" 2>/dev/null; then
    echo "✓ Dependencies installed successfully"
else
    echo "✗ Dependency verification failed"
    exit 1
fi

# Create systemd user directory
echo
echo "Setting up systemd service..."
mkdir -p ~/.config/systemd/user

# Copy service file
cp "$(dirname "$0")/timetrack.service" ~/.config/systemd/user/

# Update service file with actual path
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
sed -i "s|%h/code/timetrack|$SCRIPT_DIR|g" ~/.config/systemd/user/timetrack.service

# Reload systemd
systemctl --user daemon-reload

# Enable and start service
echo
read -p "Enable service to start on boot? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    systemctl --user enable timetrack.service
    echo "✓ Service enabled"
fi

echo
read -p "Start service now? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    systemctl --user start timetrack.service
    echo "✓ Service started"
    echo
    echo "Checking status..."
    systemctl --user status timetrack.service --no-pager || true
fi

echo
echo "=== Installation Complete ==="
echo
echo "Log file location: ~/.local/share/timetrack/activity.csv"
echo
echo "Useful commands:"
echo "  systemctl --user status timetrack.service   # Check status"
echo "  journalctl --user -u timetrack.service -f   # View logs"
echo "  cat ~/.local/share/timetrack/activity.csv   # View activity log"
