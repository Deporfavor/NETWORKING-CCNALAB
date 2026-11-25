#!/usr/bin/env python3
"""netlab - builds a virtual network inside the codespace.

Analogy: the codespace is an empty office floor. netlab puts up walls (network
namespaces) to make one sealed room per device, runs virtual cables (veth pairs)
between rooms, and puts the right equipment in each room:
  router  -> its own private copy of FRRouting (Cisco-like CLI via vtysh)
  switch  -> a Linux bridge with VLAN filtering, driven by swsh (Cisco-like CLI)
  host    -> a plain PC with one network card (e0)

Usage (run as root; labctl does this for you):
  netlab.py up N          build lab N from topologies/labNN.json
  netlab.py resume        rebuild the current lab and re-apply my-work/saved/labNN
  netlab.py save          save every device's config to my-work/saved/labNN
  netlab.py down          remove the whole virtual network
  netlab.py status        list devices of the running lab

Set NETLAB_DRYRUN=1 to print the commands instead of running them.
"""
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.environ.get("CCNA_HOME") or os.path.dirname(HERE)
TOPO_DIR = os.path.join(HOME, "topologies")
SAVE_DIR = os.path.join(HOME, "my-work", "saved")
RUN = "/run/netlab"
STATE = os.path.join(RUN, "state.json")
SWSH = os.path.join(HERE, "swsh.py")
FRR_BIN = "/usr/lib/frr"
FRR_DAEMONS = ["zebra", "mgmtd", "staticd", "ospfd"]   # mgmtd only exists on FRR 9+
DRYRUN = os.environ.get("NETLAB_DRYRUN") == "1"


# --------------------------------------------------------------- helpers
def run(cmd, ns=None, check=True, input=None, quiet=False):
    """Run a command (list), optionally inside namespace ns. Returns stdout."""
    if ns:
        cmd = ["ip", "netns", "exec", ns] + cmd
    if DRYRUN:
        print("+ " + " ".join(cmd) + (f"   <<< {input!r}" if input else ""))
        return ""
    p = subprocess.run(cmd, input=input, capture_output=True, text=True)
    if check and p.returncode != 0:
        msg = (p.stderr or p.stdout).strip()
        raise RuntimeError(f"command failed: {' '.join(cmd)}\n  {msg}")
    if not quiet and p.stderr.strip() and p.returncode != 0:
        print(p.stderr.strip(), file=sys.stderr)
    return p.stdout


def background(cmd, ns, log):
    """Start a long-running program inside a namespace (e.g. a web server)."""
    full = ["ip", "netns", "exec", ns, "sh", "-c", cmd]
    if DRYRUN:
        print("+ (background) " + " ".join(full))
        return
    with open(log, "a") as fh:
        subprocess.Popen(full, stdout=fh, stderr=fh, stdin=subprocess.DEVNULL,
                         start_new_session=True)


def lab_file(n):
    return os.path.join(TOPO_DIR, f"lab{int(n):02d}.json")


def load_topo(n):
    with open(lab_file(n)) as fh:
        return json.load(fh)


def read_state():
    try:
        with open(STATE) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def write_state(st):
    if DRYRUN:
        return
    os.makedirs(RUN, exist_ok=True)
    with open(STATE, "w") as fh:
        json.dump(st, fh, indent=2)


def existing_namespaces():
    if DRYRUN:
        return []
    out = run(["ip", "netns", "list"], check=False)
    return [line.split()[0] for line in out.splitlines() if line.strip()]


def wait_for(path, seconds=8.0):
    if DRYRUN:
        return True
    end = time.time() + seconds
    while time.time() < end:
        if os.path.exists(path):
            return True
        time.sleep(0.1)
    return False


def chown_frr(path):
    if DRYRUN:
        return
    try:
        shutil.chown(path, "frr", "frr")
    except (LookupError, OSError):
        pass


# --------------------------------------------------------------- teardown
def down(verbose=True):
    st = read_state()
    nodes = list(st.get("nodes", {}).keys())
    for ns in existing_namespaces():
        if ns not in nodes:
            nodes.append(ns) if ns.startswith(("R", "SW", "PC", "SRV", "ISP")) else None
    for name in nodes:
        # Stop everything running in the room (FRR, dnsmasq, web servers...).
        pids = run(["ip", "netns", "pids", name], check=False, quiet=True).split()
        for pid in pids:
            run(["kill", pid], check=False, quiet=True)
        if pids and not DRYRUN:
            time.sleep(0.3)
            for pid in pids:
                run(["kill", "-9", pid], check=False, quiet=True)
        run(["ip", "netns", "del", name], check=False, quiet=True)
        for d in (f"/etc/frr/{name}", f"/var/run/frr/{name}", f"/etc/netns/{name}"):
            if not DRYRUN:
                shutil.rmtree(d, ignore_errors=True)
    if not DRYRUN:
        for f in os.listdir(RUN) if os.path.isdir(RUN) else []:
            p = os.path.join(RUN, f)
            if os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
            else:
                os.remove(p)
    if verbose:
        print("Virtual network removed.")


# --------------------------------------------------------------- build
def make_namespace(name):
    run(["ip", "netns", "add", name])
    run(["ip", "link", "set", "lo", "up"], ns=name)
    # Each room gets its own resolv.conf so lab DNS never touches the codespace.
    if not DRYRUN:
        os.makedirs(f"/etc/netns/{name}", exist_ok=True)
        open(f"/etc/netns/{name}/resolv.conf", "w").close()


def make_links(links):
    """links: [["R1:e0", "SW1:e8"], ...] -> virtual cables."""
    for i, (a, b) in enumerate(links):
        na, ia = a.split(":")
        nb, ib = b.split(":")
        ta, tb = f"nlv{i}a", f"nlv{i}b"            # temporary names (<15 chars)
        run(["ip", "link", "add", ta, "type", "veth", "peer", "name", tb])
        run(["ip", "link", "set", ta, "netns", na])
        run(["ip", "link", "set", tb, "netns", nb])
        run(["ip", "link", "set", ta, "name", ia], ns=na)
        run(["ip", "link", "set", tb, "name", ib], ns=nb)


def node_ifaces(name, links):
    out = []
    for a, b in links:
        for end in (a, b):
            n, i = end.split(":")
            if n == name:
                out.append(i)
    return sorted(out, key=lambda s: (len(s), s))


def sysctl(ns, key, value):
    run(["sysctl", "-qw", f"{key}={value}"], ns=ns, check=False)


def build_router(name, spec, links, saved_conf):
    for iface in node_ifaces(name, links):
        run(["ip", "link", "set", iface, "up"], ns=name)
        sysctl(name, f"net.ipv4.conf.{iface}.send_redirects", 0)
        sysctl(name, f"net.ipv4.conf.{iface}.rp_filter", 0)
    for parent, vids in spec.get("subifs", {}).items():
        for vid in vids:
            sub = f"{parent}.{vid}"
            run(["ip", "link", "add", "link", parent, "name", sub, "type", "vlan", "id", str(vid)], ns=name)
            run(["ip", "link", "set", sub, "up"], ns=name)
            sysctl(name, f"net.ipv4.conf.{sub}.rp_filter", 0)
    sysctl(name, "net.ipv4.ip_forward", 1)
    for k in ("all", "default"):
        sysctl(name, f"net.ipv4.conf.{k}.rp_filter", 0)
        sysctl(name, f"net.ipv4.conf.{k}.send_redirects", 0)

    conf_dir, run_dir = f"/etc/frr/{name}", f"/var/run/frr/{name}"
    config = saved_conf if saved_conf is not None else spec.get("config", f"hostname {name}\n")
    if not DRYRUN:
        os.makedirs(conf_dir, exist_ok=True)
        os.makedirs(run_dir, exist_ok=True)
        with open(f"{conf_dir}/frr.conf", "w") as fh:
            fh.write(config if config.endswith("\n") else config + "\n")
        with open(f"{conf_dir}/vtysh.conf", "w") as fh:
            fh.write("service integrated-vtysh-config\n")
        for p in (conf_dir, run_dir, f"{conf_dir}/frr.conf", f"{conf_dir}/vtysh.conf"):
            chown_frr(p)
    else:
        print(f"+ write {conf_dir}/frr.conf:\n" + "\n".join("    " + l for l in config.splitlines()))

    for d in FRR_DAEMONS:
        binary = f"{FRR_BIN}/{d}"
        if not DRYRUN and not os.path.exists(binary):
            continue
        run([binary, "-d", "-N", name, "-u", "frr", "-g", "frr", "-P", "0",
             "--log", f"file:/var/log/frr/{name}-{d}.log"], ns=name)
        if not wait_for(f"{run_dir}/{d}.vty"):
            print(f"warning: {d} on {name} did not open its socket", file=sys.stderr)
    # Apply the integrated config (like a router reading startup-config at boot).
    if DRYRUN:
        run(["vtysh", "-N", name, "-b"], check=False)
        return
    p = subprocess.run(["vtysh", "-N", name, "-b"], capture_output=True, text=True)
    if p.returncode != 0:
        # Fallback: feed the same file as commands.
        subprocess.run(["vtysh", "-N", name, "-f", f"{conf_dir}/frr.conf"],
                       capture_output=True, text=True)


def build_switch(name, spec, links, saved_state):
    run(["ip", "link", "add", "br0", "type", "bridge", "vlan_filtering", "1", "stp_state", "0"], ns=name)
    for iface in node_ifaces(name, links):
        run(["ip", "link", "set", iface, "master", "br0"], ns=name)
        run(["ip", "link", "set", iface, "up"], ns=name)
    run(["ip", "link", "set", "br0", "up"], ns=name)
    base = ["python3", SWSH, name]
    run(base + ["--init"], ns=name)
    if saved_state is not None:
        run(base + ["--load-state", "-"], ns=name, input=json.dumps(saved_state))
    elif spec.get("config"):
        run(base + ["--batch", "-"], ns=name, input="\n".join(spec["config"]) + "\n")


def build_host(name, spec, links, saved):
    iface = (node_ifaces(name, links) or ["e0"])[0]
    run(["ip", "link", "set", iface, "up"], ns=name)
    if saved is not None:
        for addr in saved.get("addrs", []):
            run(["ip", "addr", "add", addr, "dev", iface], ns=name, check=False)
        for r in saved.get("routes", []):
            run(["ip", "route", "replace", r["dst"], "via", r["gw"]], ns=name, check=False)
        if saved.get("resolv") and not DRYRUN:
            with open(f"/etc/netns/{name}/resolv.conf", "w") as fh:
                fh.write(saved["resolv"])
    else:
        ips = spec.get("ip", [])
        for addr in ([ips] if isinstance(ips, str) else ips):
            run(["ip", "addr", "add", addr, "dev", iface], ns=name)
        if spec.get("gw"):
            run(["ip", "route", "add", "default", "via", spec["gw"]], ns=name, check=False)
        for dst, gw in spec.get("routes", []):
            run(["ip", "route", "add", dst, "via", gw], ns=name, check=False)
        for cmd in spec.get("cmds", []):
            run(cmd.split(), ns=name, check=False)


def start_services(name, spec):
    if spec.get("www"):
        web = os.path.join(RUN, f"www-{name}")
        if not DRYRUN:
            os.makedirs(web, exist_ok=True)
            with open(os.path.join(web, "index.html"), "w") as fh:
                fh.write(spec["www"] + "\n")
        background(f"exec python3 -m http.server 80 --bind 0.0.0.0 --directory {web}",
                   name, os.path.join(RUN, f"{name}-www.log"))
    for i, cmd in enumerate(spec.get("run", [])):
        background(cmd, name, os.path.join(RUN, f"{name}-run{i}.log"))


def saved_path(lab):
    return os.path.join(SAVE_DIR, f"lab{int(lab):02d}")


def up(lab, resume=False):
    topo = load_topo(lab)
    down(verbose=False)
    if not DRYRUN:
        os.makedirs(RUN, exist_ok=True)
        os.makedirs(os.path.join(RUN, "sw"), exist_ok=True)
    nodes, links = topo["nodes"], topo["links"]
    sdir = saved_path(lab)

    def saved(name, ext):
        p = os.path.join(sdir, f"{name}.{ext}")
        if resume and os.path.exists(p):
            with open(p) as fh:
                return json.load(fh) if ext == "json" else fh.read()
        return None

    for name in nodes:
        make_namespace(name)
    make_links(links)
    # Switches first, so cables to routers and PCs are live when those start.
    order = sorted(nodes, key=lambda n: {"switch": 0, "router": 1}.get(nodes[n]["type"], 2))
    for name in order:
        spec = nodes[name]
        kind = spec["type"]
        if kind == "switch":
            build_switch(name, spec, links, saved(name, "json"))
        elif kind == "router":
            build_router(name, spec, links, saved(name, "conf"))
            rules = saved(name, "iptables")
            if rules:
                run(["iptables-restore"], ns=name, input=rules, check=False)
        else:
            build_host(name, spec, links, saved(name, "json"))
        start_services(name, spec)
    write_state({"lab": int(lab), "title": topo.get("title", ""),
                 "nodes": {n: nodes[n]["type"] for n in nodes},
                 "started": time.strftime("%Y-%m-%d %H:%M:%S")})
    restored = " (your saved work was restored)" if resume and os.path.isdir(sdir) else ""
    print(f"Lab {int(lab)} network is up{restored}: " + ", ".join(nodes))


# --------------------------------------------------------------- save
def save(lab=None):
    st = read_state()
    lab = lab or st.get("lab")
    if not lab:
        print("No lab is running.")
        return
    sdir = saved_path(lab)
    os.makedirs(sdir, exist_ok=True)
    for name, kind in st.get("nodes", {}).items():
        try:
            if kind == "router":
                conf = run(["vtysh", "-N", name, "-c", "show running-config"], check=False)
                if conf.strip():
                    with open(os.path.join(sdir, f"{name}.conf"), "w") as fh:
                        fh.write(conf)
                rules = run(["iptables-save"], ns=name, check=False)
                if "-A " in rules:                     # only keep it if NAT/ACL rules exist
                    with open(os.path.join(sdir, f"{name}.iptables"), "w") as fh:
                        fh.write(rules)
            elif kind == "switch":
                src = os.path.join(RUN, "sw", f"{name}.json")
                if os.path.exists(src):
                    shutil.copy(src, os.path.join(sdir, f"{name}.json"))
            else:
                addrs = json.loads(run(["ip", "-j", "-4", "addr", "show"], ns=name, check=False) or "[]")
                routes = json.loads(run(["ip", "-j", "-4", "route", "show"], ns=name, check=False) or "[]")
                data = {
                    "addrs": [f"{a['local']}/{a['prefixlen']}" for i in addrs if i.get("ifname") != "lo"
                              for a in i.get("addr_info", [])],
                    "routes": [{"dst": r["dst"], "gw": r["gateway"]} for r in routes if r.get("gateway")],
                }
                rc = f"/etc/netns/{name}/resolv.conf"
                if os.path.exists(rc):
                    data["resolv"] = open(rc).read()
                with open(os.path.join(sdir, f"{name}.json"), "w") as fh:
                    json.dump(data, fh, indent=2)
        except Exception as e:  # keep saving the other devices
            print(f"warning: could not save {name}: {e}", file=sys.stderr)
    # Files must stay editable by the student (labctl runs this as root).
    uid = os.stat(HOME).st_uid
    gid = os.stat(HOME).st_gid
    for root, dirs, files in os.walk(SAVE_DIR):
        for p in [root] + [os.path.join(root, f) for f in files]:
            try:
                os.chown(p, uid, gid)
            except OSError:
                pass
    print(f"Saved lab {lab} configs to my-work/saved/lab{int(lab):02d}/")


def status():
    st = read_state()
    if not st:
        print("No lab is running. Start one with: labctl start-lab N")
        return
    alive = set(existing_namespaces())
    print(f"Lab {st['lab']}: {st.get('title', '')}   (started {st.get('started', '?')})")
    for n, kind in st["nodes"].items():
        print(f"  {n:<5} {kind:<7} {'running' if n in alive else 'MISSING (run: labctl resume)'}")


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    cmd = argv[1]
    if os.geteuid() != 0 and not DRYRUN:
        print("netlab must run as root (labctl uses sudo for you).", file=sys.stderr)
        return 1
    try:
        if cmd == "up" and len(argv) > 2:
            up(argv[2])
        elif cmd == "resume":
            lab = argv[2] if len(argv) > 2 else read_state().get("lab")
            if not lab:
                print("No lab to resume.")
                return 1
            up(lab, resume=True)
        elif cmd == "save":
            save(argv[2] if len(argv) > 2 else None)
        elif cmd == "down":
            down()
        elif cmd == "status":
            status()
        else:
            print(__doc__)
            return 1
    except (RuntimeError, FileNotFoundError) as e:
        print(f"netlab: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
