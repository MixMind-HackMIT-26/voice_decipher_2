#!/bin/sh
# Start the MixMind kiosk on the Pi -- by hand or at boot.
#   ./kiosk.sh                  mic + the real pumps, via the UNO Q over Wi-Fi
#   BOARD=mock ./kiosk.sh       pretend pumps (prints instead of pouring)
#   MIXMIND_UNOQ=http://<ip>:8081 ./kiosk.sh    when the UNO Q's address changes
# The mic is picked by name: card numbers can change after a reboot.
cd "$(dirname "$0")"

# Everything that changes between venues lives OUTSIDE the repo, in a file git
# never sees. All of it is optional: with none of it the machine still pours,
# it just talks through espeak and uses the template lines.
#   ELEVENLABS_API_KEY=...   the voice, directed by how the guest sounded
#   DEEPGRAM_API_KEY=...     nova-3 (words), and Aura if ElevenLabs is absent
#   ANTHROPIC_API_KEY=...    or OPENAI_API_KEY -- writes each guest's line
#   MIXMIND_UNOQ=...         the board's address; DHCP and hotspots move it
# Both spellings are read because both are in use on the Pis. This is also why
# the kiosk works when systemd starts it at boot with none of your shell's
# environment.
for f in "$HOME/.mixmind.env" "$HOME/mixmind.env" "./.env"; do
  [ -f "$f" ] && . "$f"
done
export ELEVENLABS_API_KEY DEEPGRAM_API_KEY ANTHROPIC_API_KEY OPENAI_API_KEY MIXMIND_UNOQ
export MIXMIND_MIC="${MIXMIND_MIC:-UACDemo}"
export MIXMIND_SPK="${MIXMIND_SPK:-UACDemo}"   # the speaker, same reason
# MIXMIND_UNOQ, DEEPGRAM_API_KEY, ANTHROPIC_API_KEY / OPENAI_API_KEY if set in the
# environment are passed straight through: the board's address moves on
# DHCP renewal, and without a key the machine talks with espeak instead.
exec .venv/bin/python -u server.py --ui "$HOME/mixmind-ui" --board "${BOARD:-http}" "$@"
