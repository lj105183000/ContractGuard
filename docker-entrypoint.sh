#!/bin/sh
set -eu

# Named volumes can be created by Docker as root. Fix ownership once at
# container start, then drop to the unprivileged app user.
if [ "$(id -u)" = "0" ]; then
    chown -R appuser:appuser /app/data /app/backend/data 2>/dev/null || true
    exec gosu appuser "$@"
fi

exec "$@"
