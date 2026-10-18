#!/bin/sh
# Writes runtime config for the dashboard. DEVICELAB_DEMO_KEY is only ever set in demo mode;
# with it unset the dashboard asks for an API key instead.
set -eu
if [ -n "${DEVICELAB_DEMO_KEY:-}" ]; then
  printf '{"demoKey":"%s"}' "$DEVICELAB_DEMO_KEY" > /usr/share/nginx/html/config.json
else
  printf '{}' > /usr/share/nginx/html/config.json
fi
