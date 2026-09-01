#!/usr/bin/env bash
# cleanup-tailscale.sh
# Safe cleanup of leftover/disabled Tailscale tunnels and interfaces on macOS.
#
# IMPORTANT:
# - This script only performs SAFE actions: stop data-plane, exit the GUI,
#   and CHECK status. It does NOT delete the app or unload the SIP-protected
#   network extension (those steps need System Settings + your password and
#   are left as commented out instructions below).
# - It does NOT touch unrelated macOS utun interfaces (Handoff, private VPN,
#   opencode sandbox) - only Tailscale's own data plane.

set -euo pipefail

TS="/Applications/Tailscale.app/Contents/MacOS/Tailscale"

echo "== [1/4] Tailscale data-plane (CLI) =="
if [ -x "$TS" ]; then
  "$TS" down || echo "   (already stopped / noop)"
else
  echo "   Tailscale.app not found; skipping CLI stop."
fi

# Optional companion CLI (tailscale/usr-local). Down it too if present.
for t in /usr/local/bin/tailscale "$HOME/.local/bin/tailscale"; do
  if [ -x "$t" ]; then "$t" down 2>/dev/null || true; fi
done

echo
echo "== [2/4] Quit Tailscale GUI (idempotent) =="
killall Tailscale 2>/dev/null && echo "   Tailscale GUI quit." || echo "   No Tailscale GUI process (ok)."

echo
echo "== [3/4] Verify: helper / extension process still running? =="
if pgrep -f "io.tailscale.ipn.macsys.network-extension" >/dev/null; then
  PID=$(pgrep -f "io.tailscale.ipn.macsys.network-extension" | head -1)
  echo "   [still running] SIP-protected network extension PID=$PID"
  echo "   -> This extension is managed by launchd; it will NOT be unloaded by"
  echo "      'kill' (SIP restarts it). Use System Settings to remove it, then reboot."
else
  echo "   No network extension process (clean)."
fi

echo
echo "== [4/4] Routes: Tailscale subnets must be GONE =="
TS6=$(netstat -rn -f inet6 2>/dev/null | grep -cE "fd7a:115c" || true)
TS4=$(netstat -rn 2>/dev/null | grep -Ec "100\.(64|74|87|69|119)\." || true)
if [ "$TS6" -eq 0 ] && [ "$TS4" -eq 0 ]; then
  echo "   OK: no Tailscale routes remain (fd7a:115c / 100.x)."
else
  echo "   WARN: still found Tailscale routes (v6=$TS6 v4=$TS4)."
  echo "   If Tailscale GUI is off, reboot to flush."
fi

echo
echo "== NOTES (manual, require System Settings / password - NOT run here) =="
cat <<'EOF'
If you no longer need Tailscale, from Terminal:
  1) osascript -e 'tell app "System Settings" to activate'   # open System Settings
     # Manual: General > Login Items & Extensions > Network Extensions > remove Tailscale
  2) Remove app + leftovers when fully uninstalled:
       rm -rf "/Applications/Tailscale.app"
       rm -f  /usr/local/bin/tailscale
       rm -rf "$HOME/Library/Application Support/Tailscale"
       rm -rf "$HOME/Library/Caches/com.tailscale.ipn.macsys"
  3) REBOOT to clear leftover utun interfaces.
EOF
echo
echo "Done. After the manual removal + reboot, utun count should drop back to a"
echo "few system ones (Handoff / private VPN), not a dozen."
