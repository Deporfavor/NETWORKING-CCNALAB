#!/usr/bin/env bash
# Runs once, when the codespace is first built.
set -u
cd "${CCNA_HOME:-$(pwd)}" || exit 1
HOME_DIR="$(pwd)"

chmod +x scripts/*.sh scripts/*.py scripts/*.script 2>/dev/null || true
sudo ln -sf "$HOME_DIR/scripts/labctl.sh" /usr/local/bin/labctl
sudo ln -sf "$HOME_DIR/scripts/net.sh"    /usr/local/bin/net

mkdir -p my-work/evidence my-work/answers my-work/saved my-work/captures
touch my-work/attendance.log

# Commits made by labctl carry the student's GitHub name.
if [ -z "$(git config --global user.name 2>/dev/null)" ] && [ -n "${GITHUB_USER:-}" ]; then
  git config --global user.name  "$GITHUB_USER"
  git config --global user.email "${GITHUB_USER}@users.noreply.github.com"
fi
git config --global pull.rebase true

# Prompt reminder for new terminals.
if ! grep -q "CCNA Lab Kit" ~/.bashrc 2>/dev/null; then
  cat >> ~/.bashrc <<'EOF'

# --- CCNA Lab Kit ---
echo "CCNA Lab Kit ready. Try:  labctl doctor   |   labctl start-lab 1   |   net topo"
EOF
fi

echo "post-create finished."
