#!/bin/bash
# Pantheon override of the cua image's start script: listen on the container network
# (the image binds 127.0.0.1, which other containers cannot reach). The host
# port stays bound to 127.0.0.1 in docker-compose.yml.
set -e
while ! xdpyinfo -display :1 >/dev/null 2>&1; do sleep 1; done
export DISPLAY=:1
exec /opt/venv/bin/python3 -m computer_server --host 0.0.0.0 --port ${API_PORT:-8000}
