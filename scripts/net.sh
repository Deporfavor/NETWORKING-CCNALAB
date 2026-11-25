#!/usr/bin/env bash
# net - walk into any device of the running lab.
#
# Analogy: the lab is an office floor of sealed rooms. "net R1" opens the door
# of room R1 and hands you its console cable.
#
#   net topo                     draw the topology of the running lab
#   net list                     devices and whether they are running
#   net R1                       router CLI (FRRouting vtysh, Cisco-style)
#   net R1 show ip route         one router command, then come back
#   net R1 shell                 Linux shell inside the router (iptables, dnsmasq)
#   net SW1                      switch CLI (swsh, Cisco-style)
#   net SW1 show vlan brief      one switch command
#   net PC1                      shell on PC1 (type exit to leave)
#   net PC1 ping -c 3 10.1.1.1   one command on PC1
#   net PC1 dhcp                 PC1 asks for an address by DHCP (lab 9)
#   net R1 dhcp-server FILE      start a DHCP/DNS server on R1 using FILE (lab 9)
#   net capture PC3 e0 [NAME]    watch (and record) frames on PC3's e0
set -u
HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
HOME_DIR="${CCNA_HOME:-$(dirname "$HERE")}"
STATE=/run/netlab/state.json

die() { echo "net: $*" >&2; exit 1; }

node_type() {
  [ -f "$STATE" ] || die "no lab is running. Start one with: labctl start-lab N"
  python3 -c "import json,sys; print(json.load(open('$STATE'))['nodes'].get(sys.argv[1],''))" "$1"
}

host_shell() {
  local n="$1"
  local rc=/run/netlab/hostrc
  sudo tee "$rc" >/dev/null <<EOF
PS1='\[\e[1;32m\]$n\[\e[0m\]:\w\$ '
alias ipconfig='ip -4 addr show; echo; ip route'
alias ll='ls -l'
echo "You are now on $n. Try: ip addr  |  ip route  |  ping -c 3 <address>  |  exit"
EOF
  sudo ip netns exec "$n" env HOME="$HOME" TERM="${TERM:-xterm}" bash --rcfile "$rc" -i
}

case "${1:-help}" in
  help|-h|--help)
    sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  topo)
    [ -f "$STATE" ] || die "no lab is running."
    lab=$(python3 -c "import json; print(json.load(open('$STATE'))['lab'])")
    python3 - "$HOME_DIR/topologies/lab$(printf %02d "$lab").json" <<'EOF'
import json, sys
t = json.load(open(sys.argv[1]))
print(f"\nLab {t['lab']}: {t['title']}\n")
print(t.get("ascii", "").rstrip("\n"))
print(f"\nPicture: {t.get('image', '')}   (open it in the Explorer, or see the lab sheet)\n")
EOF
    exit 0 ;;
  list|status)
    sudo python3 "$HERE/netlab.py" status; exit $? ;;
  capture)
    [ $# -ge 3 ] || die "usage: net capture NODE IFACE [NAME] [-- tcpdump filter]"
    n="$2"; ifc="$3"; shift 3
    name="$n-$ifc"
    if [ $# -gt 0 ] && [ "$1" != "--" ]; then name="$1"; shift; fi
    [ "${1:-}" = "--" ] && shift
    mkdir -p "$HOME_DIR/my-work/captures"
    out="$HOME_DIR/my-work/captures/$name.pcap"
    echo "Capturing on $n $ifc (Ctrl+C to stop). Recording to my-work/captures/$name.pcap"
    sudo ip netns exec "$n" tcpdump -Z root -eni "$ifc" -U -w "$out" --print "$@"
    sudo chown "$(id -u):$(id -g)" "$out" 2>/dev/null
    exit 0 ;;
esac

n="$1"; shift
t="$(node_type "$n")"
[ -n "$t" ] || die "no device called '$n' in this lab. Run: net list"

case "$t" in
  router)
    if [ $# -eq 0 ]; then
      echo "Connected to $n (FRRouting, Cisco-style). Type 'exit' to leave."
      exec sudo vtysh -N "$n"
    fi
    case "$1" in
      shell) host_shell "$n" ;;
      dhcp-server)
        f="${2:-}"; [ -f "$f" ] || die "usage: net $n dhcp-server my-work/lab09/dnsmasq.conf"
        pidf=/run/netlab/dnsmasq-$n.pid
        if [ -f "$pidf" ]; then sudo kill "$(cat "$pidf")" 2>/dev/null; sleep 0.5; fi
        sudo ip netns exec "$n" dnsmasq --conf-file="$(readlink -f "$f")" \
             --pid-file="$pidf" --dhcp-leasefile=/run/netlab/$n.leases \
             --log-facility=/run/netlab/dnsmasq-$n.log --user=root --group=root \
          && echo "DHCP/DNS server running on $n. Log: /run/netlab/dnsmasq-$n.log" \
          || echo "dnsmasq refused to start. Read the error above: usually a typo in $f" ;;
      show|sh|clear|write|wr)
        exec sudo vtysh -N "$n" -c "$*" ;;
      *) exec sudo ip netns exec "$n" "$@" ;;
    esac ;;
  switch)
    if [ $# -eq 0 ]; then
      exec sudo ip netns exec "$n" python3 "$HERE/swsh.py" "$n"
    fi
    exec sudo ip netns exec "$n" python3 "$HERE/swsh.py" "$n" --show "$@" ;;
  *)
    if [ $# -eq 0 ]; then host_shell "$n"; exit $?; fi
    if [ "$1" = "dhcp" ]; then
      ifc="${2:-e0}"
      if command -v udhcpc >/dev/null; then client=(udhcpc)
      elif busybox udhcpc --help >/dev/null 2>&1; then client=(busybox udhcpc)
      else die "no DHCP client installed (run labctl doctor)"; fi
      sudo ip netns exec "$n" ip -4 addr flush dev "$ifc"
      sudo ip netns exec "$n" "${client[@]}" -i "$ifc" -n -q -t 5 -s "$HERE/udhcpc.script"
      exit $?
    fi
    exec sudo ip netns exec "$n" "$@" ;;
esac
