#!/usr/bin/env bash
# labctl - your lab assistant.
#
#   labctl doctor        check that this codespace can run the labs
#   labctl hello N       sign the attendance book for lab N (do this first!)
#   labctl start-lab N   build a fresh network for lab N (1-10)
#   labctl resume        rebuild the current lab and restore your saved work
#   labctl save          save every device's config into my-work/saved/
#   labctl check N       mark your lab N work and write the evidence file
#   labctl submit N      check N, then upload my-work/ to your GitHub repo
#   labctl status        which lab is running and which devices are up
#   labctl stop          remove the virtual network
#   labctl update        pull the newest lab sheets from your lecturer
set -u
HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
HOME_DIR="${CCNA_HOME:-$(dirname "$HERE")}"
cd "$HOME_DIR" || exit 1
# shellcheck disable=SC1091
. "$HERE/kit.conf"
CUR="$HOME/.ccna/current-lab"
WORK="$HOME_DIR/my-work"
USER_NAME="${GITHUB_USER:-$(whoami)}"
mkdir -p "$HOME/.ccna" "$WORK/evidence" "$WORK/answers" "$WORK/saved" "$WORK/captures"
touch "$WORK/attendance.log"

ok()   { printf '  \e[32m✔\e[0m %s\n' "$*"; }
bad()  { printf '  \e[31m✘\e[0m %s\n' "$*"; }
info() { printf '\e[1m%s\e[0m\n' "$*"; }
die()  { printf '\e[31mlabctl: %s\e[0m\n' "$*" >&2; exit 1; }

lab_num() {
  [[ "${1:-}" =~ ^[0-9]+$ ]] || die "give a lab number, e.g. labctl $2 3"
  local n=$((10#$1))
  [ -f "$HOME_DIR/topologies/lab$(printf %02d "$n").json" ] || die "there is no lab $n yet (labs 1-10)."
  echo "$n"
}

current() { cat "$CUR" 2>/dev/null; }

lab_sheet() { ls "$HOME_DIR"/labs/lab"$(printf %02d "$1")"-*.md 2>/dev/null | head -1; }

push_work() {
  local msg="$1"
  git add my-work >/dev/null 2>&1
  git commit -qm "$msg" >/dev/null 2>&1 || true
  if ! git remote get-url origin >/dev/null 2>&1; then
    bad "This codespace is not linked to a repository, so nothing was uploaded."
    echo "    Fix: Source Control panel -> Publish Branch -> Publish to GitHub private repository,"
    echo "    or create your own repo from the template (see labs/00-getting-started.md)."
    return 1
  fi
  git pull -q --rebase --autostash >/dev/null 2>&1 || true
  if git push -q 2>/tmp/labctl-push.err || git push -q -u origin HEAD 2>>/tmp/labctl-push.err; then
    ok "Uploaded to $(git remote get-url origin | sed 's#.*github.com[:/]##; s#\.git$##')"
  else
    bad "Upload failed:"; sed 's/^/    /' /tmp/labctl-push.err | tail -5
    if grep -q "git-lfs" /tmp/labctl-push.err; then
      echo "    Fix: rm -f .git/hooks/pre-push .git/hooks/post-checkout .git/hooks/post-commit .git/hooks/post-merge"
    fi
    return 1
  fi
}

attend() {   # attend EVENT LAB
  printf '%s\t%s\tlab=%s\tuser=%s\tcodespace=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" "$2" \
    "$USER_NAME" "${CODESPACE_NAME:-local}" >> "$WORK/attendance.log"
}

cmd="${1:-help}"; shift || true
case "$cmd" in
  help|-h|--help)
    sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//' ;;

  doctor)
    info "CCNA Lab Kit health check for $USER_NAME"
    fails=0
    if [ -n "${CODESPACES:-}" ]; then ok "Running in GitHub Codespaces"; else bad "Not in Codespaces (fine for testing)"; fi
    origin="$(git remote get-url origin 2>/dev/null || true)"
    if [ -z "$origin" ]; then bad "No linked repository: labctl submit will not work"; fails=$((fails+1))
    elif echo "$origin" | grep -qi "$TEMPLATE_REPO"; then bad "You are inside the TEMPLATE, not your own copy. Create your own repo (getting-started 1.2)"; fails=$((fails+1))
    else ok "Linked to your repo: $(echo "$origin" | sed 's#.*github.com[:/]##; s#\.git$##')"; fi
    repo_name="$(basename -s .git "$origin" 2>/dev/null)"
    if [ -n "$origin" ] && ! echo "$repo_name" | grep -qi "$REPO_HINT"; then
      bad "Repo name '$repo_name' must contain '$REPO_HINT' or your lecturer cannot see it"; fails=$((fails+1)); fi
    if sudo -n true 2>/dev/null; then ok "sudo works"; else bad "sudo is not available"; fails=$((fails+1)); fi
    for b in vtysh /usr/lib/frr/zebra /usr/lib/frr/ospfd /usr/lib/frr/staticd tcpdump dnsmasq iptables python3; do
      if command -v "$b" >/dev/null 2>&1 || [ -x "$b" ]; then ok "$(basename "$b") installed"; else bad "$(basename "$b") missing"; fails=$((fails+1)); fi
    done
    if command -v udhcpc >/dev/null || busybox udhcpc --help >/dev/null 2>&1; then ok "DHCP client installed"
    else bad "DHCP client missing (only lab 9 needs it)"; fi
    if command -v vtysh >/dev/null; then ok "FRRouting $(vtysh --version 2>/dev/null | head -1 | awk '{print $NF}')"; fi
    # Real test: two rooms, one cable, one ping.
    sudo ip netns del doc1 2>/dev/null; sudo ip netns del doc2 2>/dev/null
    if sudo ip netns add doc1 2>/dev/null && sudo ip netns add doc2 \
       && sudo ip link add dva type veth peer name dvb \
       && sudo ip link set dva netns doc1 && sudo ip link set dvb netns doc2 \
       && sudo ip -n doc1 addr add 10.99.0.1/30 dev dva && sudo ip -n doc2 addr add 10.99.0.2/30 dev dvb \
       && sudo ip -n doc1 link set dva up && sudo ip -n doc2 link set dvb up \
       && sudo ip netns exec doc1 ping -c 1 -W 2 10.99.0.2 >/dev/null; then
      ok "Virtual cables work (test ping between two virtual devices succeeded)"
    else bad "Cannot build virtual networks. The codespace may be using the wrong image: rebuild it"; fails=$((fails+1)); fi
    sudo ip netns del doc1 2>/dev/null; sudo ip netns del doc2 2>/dev/null
    echo
    if [ "$fails" -eq 0 ]; then info "All good. Take a screenshot of this for your lecturer, then: labctl hello 1"
    else info "$fails problem(s) found. See labs/00-getting-started.md section 5 (Troubleshooting)."; fi
    ;;

  hello)
    n="$(lab_num "${1:-}" hello)" || exit 1
    attend hello "$n"
    info "Signed in for lab $n as $USER_NAME at $(date -u +%H:%M) UTC."
    push_work "Attendance: lab $n" ;;

  start-lab)
    n="$(lab_num "${1:-}" start-lab)" || exit 1
    info "Building the network for lab $n..."
    sudo -E python3 "$HERE/netlab.py" up "$n" || die "could not build lab $n (try: labctl doctor)"
    echo "$n" > "$CUR"
    ans="$WORK/answers/lab$(printf %02d "$n").md"
    tmpl="$HOME_DIR/labs/answers/lab$(printf %02d "$n").md"
    [ -f "$ans" ] || { [ -f "$tmpl" ] && cp "$tmpl" "$ans"; }
    files="$HOME_DIR/labs/files/lab$(printf %02d "$n")"
    if [ -d "$files" ]; then            # starter files for this lab (never overwrites yours)
      mkdir -p "$WORK/lab$(printf %02d "$n")"
      cp -rn "$files"/. "$WORK/lab$(printf %02d "$n")/" 2>/dev/null
      echo "Starter files: my-work/lab$(printf %02d "$n")/"
    fi
    [ -d "$WORK/saved/lab$(printf %02d "$n")" ] && \
      echo "Note: you have saved work for lab $n from before. This is a FRESH network. To get your saved work back instead: labctl resume"
    echo
    echo "Lab sheet:     $(lab_sheet "$n" | sed "s#$HOME_DIR/##")"
    [ -f "$ans" ] && echo "Your answers:  my-work/answers/$(basename "$ans")"
    echo "Topology:      net topo        Devices: net list"
    ;;

  resume)
    n="$(current)"; [ -n "$n" ] || die "no lab in progress. Use: labctl start-lab N"
    info "Rebuilding lab $n and restoring my-work/saved/lab$(printf %02d "$n")..."
    sudo -E python3 "$HERE/netlab.py" resume "$n" ;;

  save)
    sudo -E python3 "$HERE/netlab.py" save ;;

  check|submit)
    n="$(lab_num "${1:-}" "$cmd")" || exit 1
    cur="$(current)"
    if [ "$cur" != "$n" ] || [ ! -f /run/netlab/state.json ]; then
      die "lab $n is not the running lab (running: ${cur:-none}). Run: labctl start-lab $n  (or labctl resume)"
    fi
    sudo -E python3 "$HERE/netlab.py" save "$n" >/dev/null
    sudo -E USER_NAME="$USER_NAME" python3 "$HERE/checks.py" "$n"
    rc=$?
    sudo chown -R "$(id -u):$(id -g)" "$WORK" 2>/dev/null
    if [ "$cmd" = "submit" ]; then
      attend submit "$n"
      push_work "Submit lab $n" && info "Lab $n submitted. You can keep working and submit again; the last upload counts."
    else
      echo "Not uploaded yet. When you are happy: labctl submit $n"
    fi
    exit $rc ;;

  status)
    echo "Current lab: $(current || echo none)"
    sudo python3 "$HERE/netlab.py" status ;;

  stop)
    sudo python3 "$HERE/netlab.py" down && rm -f "$CUR" ;;

  update)
    info "Fetching the newest lab sheets from $TEMPLATE_REPO..."
    git fetch -q "https://github.com/$TEMPLATE_REPO.git" main || die "could not reach $TEMPLATE_REPO"
    git checkout FETCH_HEAD -- labs scripts topologies docs .devcontainer README.md 2>/dev/null \
      || die "update failed"
    chmod +x scripts/*.sh scripts/*.py scripts/*.script 2>/dev/null
    git add labs scripts topologies docs .devcontainer README.md
    git commit -qm "Update lab kit from $TEMPLATE_REPO" && ok "Updated. Your my-work/ folder was not touched." \
      || ok "Already up to date."
    push_work "Update lab kit" >/dev/null 2>&1 || true ;;

  *)
    die "unknown command '$cmd'. Run: labctl help" ;;
esac
