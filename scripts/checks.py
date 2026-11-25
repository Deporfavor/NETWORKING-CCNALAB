#!/usr/bin/env python3
"""checks - marks a lab by testing the live network, like an examiner walking
round the room with a laptop: it pings, reads configs and looks at tables, then
writes an evidence file into my-work/evidence/.

  sudo checks.py N          (labctl check N runs this for you)
  CHECKS_MOCK=1 checks.py N (developer test: no network needed, everything fails)
"""
import datetime as dt
import ipaddress
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.environ.get("CCNA_HOME") or os.path.dirname(HERE)
WORK = os.path.join(HOME, "my-work")
SW_DIR = "/run/netlab/sw"
MOCK = os.environ.get("CHECKS_MOCK") == "1"


# ===================================================================== probes
def sh(cmd, ns=None, timeout=20):
    if MOCK:
        return 1, ""
    if ns:
        cmd = ["ip", "netns", "exec", ns] + cmd
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return 1, ""


def ping(src, ip, count=3):
    rc, _ = sh(["ping", "-c", str(count), "-W", "1", "-i", "0.3", ip], ns=src)
    return rc == 0


def vtysh(router, command):
    return sh(["vtysh", "-N", router, "-c", command])[1]


def run_conf(router):
    return vtysh(router, "show running-config")


def route_json(router):
    try:
        return json.loads(vtysh(router, "show ip route json") or "{}")
    except ValueError:
        return {}


def has_route(router, prefix, protocol=None):
    for r in route_json(router).get(prefix, []):
        if protocol is None or r.get("protocol") == protocol:
            if r.get("selected") or r.get("installed") or protocol is None:
                return True
    return False


def sw_state(name):
    if MOCK:
        return {"hostname": name, "vlans": {"1": "default"}, "aging": 300, "ports": {}}
    try:
        with open(os.path.join(SW_DIR, f"{name}.json")) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {"vlans": {}, "ports": {}}


def sw_port(name, port):
    return sw_state(name)["ports"].get(port, {})


def sw_allowed(cfg):
    a = cfg.get("allowed", "all")
    return set(range(1, 4095)) if a == "all" else set(a)


def addrs(ns, iface=None):
    cmd = ["ip", "-j", "-4", "addr", "show"] + (["dev", iface] if iface else [])
    rc, out = sh(cmd, ns=ns)
    try:
        data = json.loads(out or "[]")
    except ValueError:
        return []
    return [f"{a['local']}/{a['prefixlen']}" for i in data if i.get("ifname") != "lo"
            for a in i.get("addr_info", [])]


def default_gw(ns):
    rc, out = sh(["ip", "-j", "-4", "route", "show", "default"], ns=ns)
    try:
        data = json.loads(out or "[]")
    except ValueError:
        return None
    return data[0].get("gateway") if data else None


def link_up(ns, iface):
    rc, out = sh(["ip", "-j", "link", "show", "dev", iface], ns=ns)
    try:
        d = json.loads(out or "[]")[0]
    except (ValueError, IndexError):
        return False
    return "UP" in d.get("flags", []) and d.get("operstate") in ("UP", "UNKNOWN")


def static_routes(conf):
    """-> list of (prefix, nexthop, distance) from 'ip route ...' config lines."""
    out = []
    for line in conf.splitlines():
        t = line.split()
        if len(t) < 4 or t[0] != "ip" or t[1] != "route":
            continue
        try:
            if "/" in t[2]:
                prefix, rest = str(ipaddress.ip_network(t[2], strict=False)), t[3:]
            else:
                prefix, rest = str(ipaddress.ip_network(f"{t[2]}/{t[3]}", strict=False)), t[4:]
        except ValueError:
            continue
        if not rest:
            continue
        nh = rest[0]
        dist = next((int(x) for x in rest[1:] if x.isdigit()), 1)
        out.append((prefix, nh, dist))
    return out


def in_subnet(addr, net):
    try:
        return ipaddress.ip_interface(addr).ip in ipaddress.ip_network(net)
    except ValueError:
        return False


def tshark_count(path, display_filter):
    if not os.path.exists(path):
        return 0
    rc, out = sh(["tshark", "-r", path, "-Y", display_filter, "-T", "fields", "-e", "frame.number"])
    return len([l for l in out.splitlines() if l.strip()])


def answers_written(n):
    mine = os.path.join(WORK, "answers", f"lab{n:02d}.md")
    tmpl = os.path.join(HOME, "labs", "answers", f"lab{n:02d}.md")
    if not os.path.exists(mine):
        return False
    a = open(mine).read()
    b = open(tmpl).read() if os.path.exists(tmpl) else ""
    return a != b and len(a.strip()) - len(b.strip()) >= 150


def ospf_full_count(router):
    return vtysh(router, "show ip ospf neighbor").count("Full/")


# ===================================================================== check list
class Marker:
    def __init__(self):
        self.items = []

    def add(self, desc, points, passed, hint=""):
        self.items.append((desc, points, bool(passed), hint))

    @property
    def score(self):
        return sum(p for _, p, ok, _ in self.items if ok), sum(p for _, p, _, _ in self.items)


# ===================================================================== labs
def lab1(m):
    conf = run_conf("R1")
    m.add("R1's hostname is R1", 2, re.search(r"^hostname R1$", conf, re.M),
          "net R1 -> configure terminal -> hostname R1")
    m.add("R1 e1 is up (no shutdown)", 2, link_up("R1", "e1") and not re.search(r"interface e1\n(?: .*\n)*? shutdown", conf),
          "net R1 -> conf t -> interface e1 -> no shutdown")
    m.add("PC1 can ping PC3 (across the router)", 2, ping("PC1", "192.168.2.13"))
    m.add("PC2 can ping PC3", 1, ping("PC2", "192.168.2.13"))
    saved = ""
    try:
        saved = open("/etc/frr/R1/frr.conf").read() if not MOCK else ""
    except OSError:
        pass
    m.add("R1's config was saved with write memory", 2, "hostname R1" in saved,
          "In R1: end, then write memory")
    m.add("Questions answered in my-work/answers/lab01.md", 1, answers_written(1))


def lab2(m):
    want = {"e0": "192.168.50.1/26", "e1": "192.168.50.65/27", "e2": "192.168.50.97/28"}
    got = addrs("R1")
    for iface, a in want.items():
        m.add(f"R1 {iface} has {a} (first usable address of its subnet)", 1, a in addrs("R1", iface),
              f"net R1 -> conf t -> interface {iface} -> ip address {a}")
    hosts = {"PC1": ("192.168.50.0/26", "192.168.50.1", 26), "PC2": ("192.168.50.0/26", "192.168.50.1", 26),
             "PC3": ("192.168.50.64/27", "192.168.50.65", 27), "PC4": ("192.168.50.96/28", "192.168.50.97", 28)}
    for h, (net, gw, plen) in hosts.items():
        a = addrs(h, "e0")
        good = any(in_subnet(x, net) and x.endswith(f"/{plen}") for x in a) and default_gw(h) == gw
        m.add(f"{h} has an address in {net} with mask /{plen} and gateway {gw}", 1, good,
              f"net {h} ip addr  (fix with ip addr del / ip addr add, then ip route add default via {gw})")
    m.add("PC1 can ping PC3 and PC4", 1, ping("PC1", "192.168.50.70") and ping("PC1", "192.168.50.100"),
          "PC3 must be 192.168.50.70 and PC4 192.168.50.100 (see the addressing table)")
    m.add("PC2 (the one with the wrong mask) can ping PC3", 1, ping("PC2", "192.168.50.70"))
    m.add("Subnetting table and questions answered", 1, answers_written(2))


def lab3(m):
    cap = os.path.join(WORK, "captures")
    arp = os.path.join(cap, "lab03-arp.pcap")
    flood = os.path.join(cap, "lab03-flood.pcap")
    m.add("Capture lab03-arp.pcap exists", 1, os.path.exists(arp), "net capture PC3 e0 lab03-arp")
    m.add("It shows PC1's ARP broadcast asking for 10.0.0.12", 2,
          tshark_count(arp, "arp.opcode == 1 && arp.dst.proto_ipv4 == 10.0.0.12 && arp.src.proto_ipv4 == 10.0.0.11") > 0)
    m.add("Capture lab03-flood.pcap shows a PC1->PC2 ping flooded to PC3 (unknown unicast)", 3,
          tshark_count(flood, "icmp.type == 8 && ip.src == 10.0.0.11 && ip.dst == 10.0.0.12") > 0,
          "Part D: clear the MAC table on SW1, then ping from PC1 while capturing on PC3")
    m.add("SW1 MAC aging time is 30 seconds", 2, sw_state("SW1").get("aging") == 30,
          "net SW1 -> conf t -> mac address-table aging-time 30")
    m.add("Questions answered", 2, answers_written(3))


def lab4(m):
    for s in ("SW1", "SW2"):
        v = sw_state(s)["vlans"]
        m.add(f"{s} has VLAN 10 STAFF, 20 STUDENTS, 99 NATIVE", 1,
              v.get("10") == "STAFF" and v.get("20") == "STUDENTS" and v.get("99") == "NATIVE")
    ok_access = all(sw_port(s, "e1").get("mode") == "access" and sw_port(s, "e1").get("access_vlan") == 10 and
                    sw_port(s, "e2").get("mode") == "access" and sw_port(s, "e2").get("access_vlan") == 20
                    for s in ("SW1", "SW2"))
    m.add("e1 is in VLAN 10 and e2 in VLAN 20 on both switches", 2, ok_access)
    t_ok = all(sw_port(s, "e8").get("mode") == "trunk" and sw_port(s, "e8").get("native") == 99 and
               sw_allowed(sw_port(s, "e8")) == {10, 20, 99} for s in ("SW1", "SW2"))
    m.add("e8 is a trunk on both switches: native VLAN 99, allowed 10,20,99", 2, t_ok)
    p13, p24 = ping("PC1", "10.1.1.13"), ping("PC2", "10.1.1.14")
    m.add("PC1 reaches PC3 (STAFF across the trunk)", 1, p13)
    m.add("PC2 reaches PC4 (STUDENTS across the trunk)", 1, p24)
    iso = p13 and p24 and not ping("PC1", "10.1.1.12", 2) and not ping("PC1", "10.1.1.14", 2)
    m.add("PC1 cannot reach PC2 or PC4 (VLANs keep them apart)", 2, iso)


def lab5(m):
    m.add("Fault: PC2's switch port is in the STUDENTS VLAN", 2, sw_port("SW1", "e2").get("access_vlan") == 20)
    m.add("Fault: STUDENTS traffic crosses the trunk (PC2 reaches PC4)", 2,
          20 in sw_allowed(sw_port("SW2", "e8")) and ping("PC2", "10.20.20.14"))
    m.add("Fault: R1's server interface is up and R1 reaches SRV", 2,
          link_up("R1", "e1") and ping("R1", "172.16.0.10"))
    m.add("Fault: PC3 is 10.10.10.13/24 with gateway 10.10.10.1 and reaches PC1", 2,
          "10.10.10.13/24" in addrs("PC3", "e0") and default_gw("PC3") == "10.10.10.1" and ping("PC3", "10.10.10.11"))
    rc, page = sh(["curl", "-s", "-m", "4", "http://172.16.0.10/"], ns="PC1")
    m.add("Fault: SRV has gateway 172.16.0.1 and PC1 can open the file server page", 2,
          default_gw("SRV") == "172.16.0.1" and "Faculty file server" in page)


def lab6(m):
    m.add("SW1 e8 is a trunk carrying VLANs 10, 20 and 30", 2,
          sw_port("SW1", "e8").get("mode") == "trunk" and {10, 20, 30} <= sw_allowed(sw_port("SW1", "e8")))
    for v in (10, 20, 30):
        m.add(f"R1 e0.{v} has 192.168.{v}.1/24", 1, f"192.168.{v}.1/24" in addrs("R1", f"e0.{v}"),
              f"net R1 -> conf t -> interface e0.{v} -> ip address 192.168.{v}.1/24")
    m.add("PC1 (STAFF) reaches PC2 (STUDENTS)", 1, ping("PC1", "192.168.20.12"))
    m.add("PC1 (STAFF) reaches PC3 (GUESTS)", 1, ping("PC1", "192.168.30.13"))
    m.add("PC2 reaches PC3", 1, ping("PC2", "192.168.30.13"))
    rc, out = sh(["traceroute", "-n", "-w", "1", "-q", "1", "-m", "3", "192.168.20.12"], ns="PC1")
    m.add("traceroute from PC1 to PC2 goes through 192.168.10.1", 1, "192.168.10.1" in out)
    m.add("Questions answered", 1, answers_written(6))


def lab7(m):
    r = {n: static_routes(run_conf(n)) for n in ("R1", "R2", "R3")}
    m.add("R1: route to 10.3.3.0/24 via 10.0.12.2", 1, ("10.3.3.0/24", "10.0.12.2", 1) in r["R1"])
    m.add("R3: route to 10.1.1.0/24 via 10.0.23.1", 1, ("10.1.1.0/24", "10.0.23.1", 1) in r["R3"])
    m.add("R2: routes to both LANs", 1, ("10.1.1.0/24", "10.0.12.1", 1) in r["R2"]
          and ("10.3.3.0/24", "10.0.23.2", 1) in r["R2"])
    m.add("R1 and R3 have default routes to R2", 1, ("0.0.0.0/0", "10.0.12.2", 1) in r["R1"]
          and ("0.0.0.0/0", "10.0.23.1", 1) in r["R3"])
    fl1 = any(p == "10.3.3.0/24" and nh == "10.0.13.2" and d > 1 for p, nh, d in r["R1"])
    fl3 = any(p == "10.1.1.0/24" and nh == "10.0.13.1" and d > 1 for p, nh, d in r["R3"])
    m.add("Floating static routes over the backup link (distance above 1) on R1 and R3", 2, fl1 and fl3)
    m.add("R1: host route 10.3.3.13/32 via 10.0.13.2", 1, any(p == "10.3.3.13/32" and nh == "10.0.13.2"
                                                              for p, nh, d in r["R1"]))
    m.add("PC1 <-> PC3 and both reach SRV", 1, ping("PC1", "10.3.3.13") and ping("PC1", "203.0.113.10")
          and ping("PC3", "203.0.113.10"))
    # Failover test: switch R2 off (both its router-facing ports), ping, switch it back on.
    if not MOCK:
        sh(["ip", "link", "set", "e0", "down"], ns="R2")
        sh(["ip", "link", "set", "e1", "down"], ns="R2")
        time.sleep(3)
    survived = ping("PC1", "10.3.3.13", 4)
    if not MOCK:
        sh(["ip", "link", "set", "e0", "up"], ns="R2")
        sh(["ip", "link", "set", "e1", "up"], ns="R2")
        time.sleep(2)
    m.add("Failover: with R2 switched off, PC1 still reaches PC3 over the backup link", 2, survived,
          "Both floating routes are needed: the reply must come back the same way")


def lab8(m):
    m.add("R1 has two Full OSPF neighbours", 1, ospf_full_count("R1") >= 2, "net R1 show ip ospf neighbor")
    m.add("R2 and R3 each have two Full neighbours", 1, ospf_full_count("R2") >= 2 and ospf_full_count("R3") >= 2)
    m.add("R1 learned 192.168.2.0/24 and 192.168.3.0/24 by OSPF", 1,
          has_route("R1", "192.168.2.0/24", "ospf") and has_route("R1", "192.168.3.0/24", "ospf"))
    m.add("R3 learned 192.168.1.0/24 by OSPF", 1, has_route("R3", "192.168.1.0/24", "ospf"))
    ids = all(re.search(rf"Router ID: {i}\.{i}\.{i}\.{i}\b", vtysh(r, "show ip ospf"))
              for i, r in ((1, "R1"), (2, "R2"), (3, "R3")))
    m.add("Router IDs are 1.1.1.1, 2.2.2.2 and 3.3.3.3", 1, ids)
    passive = all(re.search(r"interface e1\n(?: .*\n)*? ip ospf passive", run_conf(r)) or
                  re.search(r"passive-interface e1", run_conf(r)) for r in ("R1", "R2", "R3"))
    m.add("The PC-facing interface (e1) is passive on all three routers", 1, passive)
    m.add("R2 and R3 learned the default route from R1 by OSPF", 1,
          has_route("R2", "0.0.0.0/0", "ospf") and has_route("R3", "0.0.0.0/0", "ospf"))
    dr = re.search(r"Designated Router \(ID\) (\S+)", vtysh("R2", "show ip ospf interface e0"))
    m.add("R1 (1.1.1.1) is the DR on the backbone LAN", 1, bool(dr) and dr.group(1).rstrip(",") == "1.1.1.1",
          "Set ip ospf priority 100 on R1 e0, then clear ip ospf process on all routers")
    m.add("PC1 reaches PC2 and PC3", 1, ping("PC1", "192.168.2.12") and ping("PC1", "192.168.3.13"))
    m.add("PC3 reaches the Internet server", 1, ping("PC3", "203.0.113.10"))


def lab9(m):
    rc, ps = sh(["ps", "-eo", "pid,args"])
    rc2, pids = sh(["ip", "netns", "pids", "R1"])
    dns_running = any(l.split(None, 1)[0] in pids.split() and "dnsmasq" in l for l in ps.splitlines()[1:] if l.strip())
    m.add("A DHCP/DNS server (dnsmasq) is running on R1", 1, dns_running,
          "net R1 dhcp-server my-work/lab09/dnsmasq.conf")
    pool = ipaddress.ip_network("192.168.1.0/24")
    def leased(h):
        return any(in_subnet(a, str(pool)) and 100 <= int(a.split("/")[0].split(".")[-1]) <= 150
                   for a in addrs(h, "e0"))
    m.add("PC1 and PC2 got addresses from the pool .100-.150", 2, leased("PC1") and leased("PC2"),
          "net PC1 dhcp   and   net PC2 dhcp")
    leases = ""
    if not MOCK and os.path.exists("/run/netlab/R1.leases"):
        leases = open("/run/netlab/R1.leases").read()
    m.add("R1's lease file shows two clients", 1, len([l for l in leases.splitlines() if l.strip()]) >= 2)
    resolv = open("/etc/netns/PC1/resolv.conf").read() if os.path.exists("/etc/netns/PC1/resolv.conf") else ""
    m.add("PC1 was told to use 192.168.1.1 as its DNS server", 1, "nameserver 192.168.1.1" in resolv)
    rc, ans = sh(["dig", "+short", "+time=2", "+tries=1", "@192.168.1.1", "web.isp.example"], ns="PC1")
    m.add("DNS: web.isp.example resolves to 203.0.113.10", 1, "203.0.113.10" in ans)
    m.add("R1 has a default route to the ISP (198.51.100.1)", 1,
          ("0.0.0.0/0", "198.51.100.1", 1) in static_routes(run_conf("R1")))
    rc, rules = sh(["iptables-save", "-t", "nat"], ns="R1")
    m.add("R1 translates inside addresses on its outside interface (PAT)", 1,
          bool(re.search(r"-A POSTROUTING .*-o e1 .*-j (MASQUERADE|SNAT)", rules)))
    rc, page = sh(["curl", "-s", "-m", "4", "http://web.isp.example/"], ns="PC1")
    m.add("PC1 opens http://web.isp.example (DHCP + DNS + routing + NAT together)", 1, "through NAT" in page)
    m.add("No shortcut: the ISP still has no route to your private network", 1,
          not MOCK and not any(p in ("192.168.1.0/24", "192.168.0.0/16") for p in route_json("ISP")))


def lab10(m):
    m.add("Fault: the trunk to R1 carries VLANs 10 and 20", 2,
          sw_port("SW1", "e8").get("mode") == "trunk" and {10, 20} <= sw_allowed(sw_port("SW1", "e8")))
    m.add("Fault: R1 e0.20 is 192.168.20.1/24", 2, "192.168.20.1/24" in addrs("R1", "e0.20"))
    m.add("Fault: R1 and R2 are Full OSPF neighbours", 2, ospf_full_count("R1") >= 1)
    m.add("Fault: R1 learned 172.16.2.0/24 by OSPF and PC1 reaches SRV", 2,
          has_route("R1", "172.16.2.0/24", "ospf") and ping("PC1", "172.16.2.10"))
    leases = ""
    if not MOCK and os.path.exists("/run/netlab/R1.leases"):
        leases = open("/run/netlab/R1.leases").read()
    pc2 = addrs("PC2", "e0")
    m.add("Fault: PC2 got a DHCP lease in 192.168.20.0/24 with gateway 192.168.20.1 and reaches SRV", 2,
          bool(leases.strip()) and any(in_subnet(a, "192.168.20.0/24") for a in pc2)
          and default_gw("PC2") == "192.168.20.1" and ping("PC2", "172.16.2.10"))


LABS = {1: lab1, 2: lab2, 3: lab3, 4: lab4, 5: lab5, 6: lab6, 7: lab7, 8: lab8, 9: lab9, 10: lab10}


# ===================================================================== evidence
def device_dump():
    try:
        st = json.load(open("/run/netlab/state.json"))
    except (OSError, ValueError):
        return "(no running lab)"
    parts = []
    for name, kind in st.get("nodes", {}).items():
        if kind == "router":
            parts.append(f"### {name}: show running-config\n{run_conf(name)}")
            parts.append(f"### {name}: show ip route\n{vtysh(name, 'show ip route')}")
        elif kind == "switch":
            rc, out = sh(["python3", os.path.join(HERE, "swsh.py"), name, "--show", "show", "running-config"], ns=name)
            parts.append(f"### {name}: show running-config\n{out}")
        else:
            parts.append(f"### {name}: ip addr / ip route\n{sh(['ip', '-4', '-br', 'addr'], ns=name)[1]}"
                         f"{sh(['ip', '-4', 'route'], ns=name)[1]}")
    return "\n".join(parts)


def main(argv):
    if len(argv) < 2 or not argv[1].isdigit() or int(argv[1]) not in LABS:
        print(__doc__)
        return 2
    n = int(argv[1])
    m = Marker()
    print(f"Marking lab {n}... (this takes up to a minute: the marker really tests your network)")
    LABS[n](m)
    got, total = m.score
    user = os.environ.get("USER_NAME") or os.environ.get("GITHUB_USER") or "student"
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = []
    for desc, pts, ok, hint in m.items:
        mark = "PASS" if ok else "FAIL"
        lines.append(f"[{mark} {pts if ok else 0}/{pts}] {desc}")
        colour = "\033[32m✔" if ok else "\033[31m✘"
        print(f"  {colour}\033[0m {desc}  ({pts if ok else 0}/{pts})")
        if not ok and hint:
            print(f"      hint: {hint}")
    print(f"\n  SCORE: {got}/{total}")
    os.makedirs(os.path.join(WORK, "evidence"), exist_ok=True)
    path = os.path.join(WORK, "evidence", f"lab{n:02d}_{user}.txt")
    with open(path, "w") as fh:
        fh.write(f"CCNA Lab Kit evidence\nLab: {n:02d}\nStudent: {user}\n"
                 f"Codespace: {os.environ.get('CODESPACE_NAME', 'local')}\nGenerated: {now}\n"
                 f"SCORE: {got}/{total}\n\n--- checks ---\n" + "\n".join(lines) +
                 "\n\n--- device state ---\n" + device_dump() + "\n")
    print(f"  Evidence written to my-work/evidence/{os.path.basename(path)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
