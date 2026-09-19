#!/bin/sh
# Open the kiosk UI fullscreen on the Pi's touchscreen. Works from an SSH
# session too. Every flag here fixed a real failure on this Pi:
#   --ozone-platform=wayland   the desktop is Wayland; from SSH, Chromium
#                              assumed X11 and exited ("Missing X server")
#   --password-store=basic     otherwise Chromium waits on a hidden "choose a
#                              password for the new keyring" dialog, and the
#                              page sits loading forever
# Alt+F4 on a keyboard closes it.
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
pkill -x chromium 2>/dev/null; sleep 2
exec chromium --ozone-platform=wayland --password-store=basic --kiosk \
  --noerrdialogs --disable-infobars --no-first-run --disable-session-crashed-bubble \
  "${1:-http://localhost:8080}"
