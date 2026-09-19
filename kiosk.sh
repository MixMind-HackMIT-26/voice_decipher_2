#!/bin/sh
# Start the MixMind kiosk on the Pi -- by hand or at boot.
#   ./kiosk.sh                  mic + the real pumps, via the UNO Q over Wi-Fi
#   BOARD=mock ./kiosk.sh       pretend pumps (prints instead of pouring)
#   MIXMIND_UNOQ=http://<ip>:8081 ./kiosk.sh    when the UNO Q's address changes
# The mic is picked by name: card numbers can change after a reboot.
cd "$(dirname "$0")"
export MIXMIND_MIC="${MIXMIND_MIC:-UACDemo}"
exec .venv/bin/python -u server.py --ui "$HOME/mixmind-ui" --board "${BOARD:-http}" "$@"
