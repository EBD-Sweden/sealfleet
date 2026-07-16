#!/bin/bash
echo "Stopping Sealfleet services..."
for pidfile in /tmp/mcpfinder/*.pid; do
    [ -f "$pidfile" ] || continue
    name=$(basename "$pidfile" .pid)
    pid=$(cat "$pidfile")
    if kill -0 "$pid" 2>/dev/null; then
        kill "$pid"
        echo "  Stopped $name (pid $pid)"
    fi
    rm "$pidfile"
done
echo "Done."
