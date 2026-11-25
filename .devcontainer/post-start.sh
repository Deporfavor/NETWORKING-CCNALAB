#!/usr/bin/env bash
# Runs every time the codespace starts (including after it was stopped).
# A stopped codespace loses its virtual network, like a building after a power
# cut. If a lab was in progress, rebuild it and restore the student's saved work.
set -u
cd "${CCNA_HOME:-$(pwd)}" || exit 0
sudo mkdir -p /run/netns /run/netlab
sudo ln -sf "$(pwd)/scripts/labctl.sh" /usr/local/bin/labctl
sudo ln -sf "$(pwd)/scripts/net.sh"    /usr/local/bin/net

if [ -f "$HOME/.ccna/current-lab" ]; then
  lab="$(cat "$HOME/.ccna/current-lab")"
  echo "Restoring lab $lab after restart..."
  bash scripts/labctl.sh resume >/tmp/ccna-resume.log 2>&1 \
    && echo "Lab $lab restored (details: /tmp/ccna-resume.log)." \
    || echo "Could not restore lab $lab automatically. Run: labctl resume"
fi
exit 0
