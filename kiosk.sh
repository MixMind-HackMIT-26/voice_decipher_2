#!/bin/sh
# Start the MixMind kiosk on the Pi -- by hand or at boot.
#   ./kiosk.sh                  mic + pretend pumps (the UNO Q is not wired yet)
#   BOARD=serial ./kiosk.sh     real UNO Q over the UART
# The mic is picked by name: card numbers can change after a reboot.
cd "$(dirname "$0")"
export MIXMIND_MIC="${MIXMIND_MIC:-UACDemo}"
exec .venv/bin/python -u server.py --ui "$HOME/mixmind-ui" --board "${BOARD:-mock}" "$@"
