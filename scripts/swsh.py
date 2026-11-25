#!/usr/bin/env python3
"""swsh - a Cisco IOS-style command line for a Linux bridge switch.

Analogy: the Linux bridge is the switch's hardware; swsh is the faceplate and
console port. You type Cisco commands, swsh translates them into Linux bridge
commands, so what you configure really changes how frames are forwarded.

Supported (abbreviations work: "conf t", "sh vl br", "int e1", "sw mo acc"):
  enable / disable / configure terminal / end / exit / do <cmd>
  hostname NAME
  vlan N  ->  name NAME           no vlan N
  interface eN   |   interface range e1 - 4
     switchport mode access|trunk
     switchport access vlan N
     switchport trunk allowed vlan LIST | add LIST | remove LIST | all | none
     switchport trunk native vlan N
     shutdown / no shutdown / description TEXT
  mac address-table aging-time SECONDS
  show vlan brief | show interfaces status | show interfaces trunk
  show interfaces eN switchport | show mac address-table [vlan N | interface eN]
  show running-config | show version
  clear mac address-table dynamic
  write memory | copy running-config startup-config

Run inside the switch's namespace:  swsh.py SW1  (net SW1 does this for you)
Non-interactive:  swsh.py SW1 --init | --batch FILE|- | --load-state FILE|-
"""
import json
import os
import re
import subprocess
import sys

STATE_DIR = os.environ.get("SWSH_STATE_DIR", "/run/netlab/sw")
DRYRUN = os.environ.get("SWSH_DRYRUN") == "1"
DRY_LOG = []


# ===================================================================== kernel
def sh(args, input=None):
    if DRYRUN:
        DRY_LOG.append(" ".join(args) + (f" <<<{input!r}" if input else ""))
        return ""
    p = subprocess.run(args, input=input, capture_output=True, text=True)
    return p.stdout if p.returncode == 0 else ""


def kernel_ports():
    if DRYRUN:
        return [p for p in os.environ.get("SWSH_PORTS", "e1,e2,e3,e4").split(",") if p]
    try:
        data = json.loads(sh(["ip", "-j", "link", "show", "master", "br0"]) or "[]")
    except ValueError:
        data = []
    return sorted([d["ifname"] for d in data], key=port_key)


def port_key(p):
    m = re.match(r"([a-z]+)(\d+)", p)
    return (m.group(1), int(m.group(2))) if m else (p, 0)


def link_info(port):
    """-> (admin_up, oper_up)"""
    if DRYRUN:
        return (True, True)
    try:
        d = json.loads(sh(["ip", "-j", "link", "show", "dev", port]) or "[]")[0]
    except (ValueError, IndexError):
        return (False, False)
    flags = d.get("flags", [])
    return ("UP" in flags, d.get("operstate") == "UP" or "LOWER_UP" in flags)


def kernel_vlans(port):
    """Current VLAN membership of a port: {vid: (pvid, untagged)}"""
    if DRYRUN:
        return {}
    try:
        data = json.loads(sh(["bridge", "-j", "vlan", "show", "dev", port]) or "[]")
    except ValueError:
        return {}
    out = {}
    for entry in data:
        if entry.get("ifname") != port:
            continue
        for v in entry.get("vlans", []):
            flags = v.get("flags", [])
            for vid in range(v["vlan"], v.get("vlanEnd", v["vlan"]) + 1):
                out[vid] = ("PVID" in flags, "Egress Untagged" in flags)
    return out


def kernel_fdb():
    if DRYRUN:
        return []
    try:
        data = json.loads(sh(["bridge", "-j", "fdb", "show", "br", "br0"]) or "[]")
    except ValueError:
        return []
    out = []
    for e in data:
        if e.get("master") != "br0" and "master" in e:
            continue
        if e.get("state") == "permanent" or "self" in e.get("flags", []) or "vlan" not in e:
            continue
        if e.get("ifname") == "br0":
            continue
        out.append((e["vlan"], e["mac"], e["ifname"]))
    return sorted(out)


# ===================================================================== state
def default_port():
    return {"mode": "access", "access_vlan": 1, "native": 1, "allowed": "all",
            "shutdown": False, "description": ""}


class Switch:
    def __init__(self, name):
        self.name = name
        self.path = os.path.join(STATE_DIR, f"{name}.json")
        self.state = None

    def load(self):
        try:
            with open(self.path) as fh:
                self.state = json.load(fh)
        except (OSError, ValueError):
            self.reset()
        for p in kernel_ports():
            self.state["ports"].setdefault(p, default_port())
        return self

    def reset(self):
        self.state = {"hostname": self.name, "vlans": {"1": "default"}, "aging": 300,
                      "ports": {p: default_port() for p in kernel_ports()}}

    def save(self, path=None):
        path = path or self.path
        if DRYRUN and not os.environ.get("SWSH_STATE_DIR"):
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            json.dump(self.state, fh, indent=2)

    # ---- helpers
    @property
    def vlans(self):
        return {int(k): v for k, v in self.state["vlans"].items()}

    def ports(self):
        return sorted(self.state["ports"], key=port_key)

    def allowed(self, port):
        a = self.state["ports"][port]["allowed"]
        return set(range(1, 4095)) if a == "all" else set(a)

    # ---- push config into the Linux bridge
    def apply_port(self, port):
        cfg = self.state["ports"][port]
        db = set(self.vlans)
        desired = {}
        if cfg["mode"] == "access":
            if cfg["access_vlan"] in db:
                desired[cfg["access_vlan"]] = (True, True)
        else:
            active = self.allowed(port) & db
            for v in active:
                desired[v] = (False, False)
            if cfg["native"] in active:
                desired[cfg["native"]] = (True, True)
        current = kernel_vlans(port)
        batch = []
        for vid, flags in current.items():
            if desired.get(vid) != flags:
                batch.append(f"vlan del dev {port} vid {vid}")
        for vid, (pvid, untagged) in sorted(desired.items()):
            if current.get(vid) != (pvid, untagged):
                extra = (" pvid" if pvid else "") + (" untagged" if untagged else "")
                batch.append(f"vlan add dev {port} vid {vid}{extra}")
        if batch:
            sh(["bridge", "-batch", "-"], input="\n".join(batch) + "\n")
        sh(["ip", "link", "set", port, "down" if cfg["shutdown"] else "up"])

    def apply_all(self):
        for p in self.ports():
            self.apply_port(p)
        sh(["ip", "link", "set", "br0", "type", "bridge", "ageing_time", str(int(self.state.get("aging", 300)) * 100)])


# ===================================================================== parsing helpers
IFTYPE_WORDS = {"e", "eth", "ethernet", "f", "fa", "fastethernet", "g", "gi", "gig",
                "gigabitethernet", "et", "port"}


def norm_iface(tok):
    """'e1', 'eth1', 'fa0/1', 'Gi0/1', 'ethernet1' -> 'e1' (or None)."""
    m = re.fullmatch(r"([a-zA-Z]*)([\d/]+)", tok.strip())
    if not m:
        return None
    letters, digits = m.group(1).lower(), m.group(2)
    if letters and not any(w.startswith(letters) or letters.startswith(w) for w in IFTYPE_WORDS):
        return None
    last = digits.split("/")[-1]
    return f"e{int(last)}" if last.isdigit() else None


def parse_vlan_list(text):
    out = set()
    for part in text.replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            if not (a.isdigit() and b.isdigit()):
                raise ValueError
            out.update(range(int(a), int(b) + 1))
        elif part.isdigit():
            out.add(int(part))
        else:
            raise ValueError
    if not out or min(out) < 1 or max(out) > 4094:
        raise ValueError
    return out


def fmt_vlans(vs):
    vs = sorted(vs)
    if not vs:
        return "none"
    if vs == list(range(1, 4095)):
        return "1-4094"
    parts, start, prev = [], vs[0], vs[0]
    for v in vs[1:] + [None]:
        if v is not None and v == prev + 1:
            prev = v
            continue
        parts.append(str(start) if start == prev else f"{start}-{prev}")
        if v is not None:
            start = prev = v
    return ",".join(parts)


def expand_range(text):
    """'e1 - 4', 'e1-4,e6', 'fa0/1 - 4' -> ['e1','e2','e3','e4',...]"""
    out = []
    for part in text.replace(" ", "").split(","):
        m = re.fullmatch(r"([a-zA-Z]*[\d/]*?)(\d+)(?:-(\d+))?", part)
        if not m:
            return None
        prefix, a, b = m.group(1), int(m.group(2)), int(m.group(3) or m.group(2))
        for n in range(a, b + 1):
            p = norm_iface(f"{prefix}{n}")
            if p is None:
                return None
            out.append(p)
    return out


def cisco_mac(mac):
    h = mac.replace(":", "").lower()
    return f"{h[0:4]}.{h[4:8]}.{h[8:12]}"


def pre_tokens(line):
    """Split a line and glue 'ethernet 1' / 'fa 0/1' into one token."""
    toks = line.split()
    out, i = [], 0
    while i < len(toks):
        t = toks[i]
        if (t.lower() in IFTYPE_WORDS and i + 1 < len(toks) and toks[i + 1][:1].isdigit()):
            out.append(t + toks[i + 1])
            i += 2
            continue
        out.append(t)
        i += 1
    return out


# ===================================================================== CLI
EXEC, PRIV, CONF, IF, VLAN = "exec", "priv", "config", "config-if", "config-vlan"
PH_HELP = {"<vlan>": "<1-4094>  VLAN ID", "<vlanlist>": "WORD  VLAN list, e.g. 10,20,30-35",
           "<iface>": "e<N>  Interface (e1, e2 ... also fa0/1 style)",
           "<ifrange>": "LINE  Interface range, e.g. e1 - 4", "<word>": "WORD",
           "<line>": "LINE  Text", "<num>": "<0-1000000>  Number"}


class CLI:
    def __init__(self, sw):
        self.sw = sw
        self.mode = EXEC
        self.ifaces = []       # selected interface(s)
        self.vlan = None       # selected vlan
        self.errors = 0
        self.out = []
        self.cmds = self._build()

    def p(self, text=""):
        self.out.append(text)

    def flush(self):
        if self.out:
            print("\n".join(self.out))
        self.out = []

    # ------------------------------------------------------------ prompt
    def prompt(self):
        h = self.sw.state["hostname"]
        return {EXEC: f"{h}>", PRIV: f"{h}#", CONF: f"{h}(config)#",
                IF: f"{h}(config-if{'-range' if len(self.ifaces) > 1 else ''})#",
                VLAN: f"{h}(config-vlan)#"}[self.mode]

    # ------------------------------------------------------------ command table
    def _build(self):
        S = self
        show = [
            ("show vlan brief", S.show_vlan), ("show vlan", S.show_vlan),
            ("show interfaces status", S.show_status),
            ("show interfaces trunk", S.show_trunk),
            ("show interfaces <iface> switchport", S.show_switchport),
            ("show mac address-table", S.show_mac),
            ("show mac address-table dynamic", S.show_mac),
            ("show mac address-table vlan <vlan>", S.show_mac),
            ("show mac address-table dynamic vlan <vlan>", S.show_mac),
            ("show mac address-table interface <iface>", S.show_mac),
            ("show mac address-table dynamic interface <iface>", S.show_mac),
            ("show mac address-table aging-time", S.show_aging),
            ("show running-config", S.show_run),
            ("show startup-config", S.show_run),
            ("show version", S.show_version),
            ("show spanning-tree", S.show_stp),
        ]
        priv = show + [
            ("configure terminal", S.conf_t), ("disable", S.disable),
            ("write memory", S.write), ("write", S.write),
            ("copy running-config startup-config", S.write),
            ("clear mac address-table dynamic", S.clear_mac),
            ("clear mac address-table", S.clear_mac),
            ("exit", S.quit), ("logout", S.quit), ("quit", S.quit),
        ]
        conf = [
            ("hostname <word>", S.hostname),
            ("vlan <vlanlist>", S.vlan_enter), ("no vlan <vlanlist>", S.vlan_del),
            ("interface range <ifrange>", S.if_range), ("interface <iface>", S.if_enter),
            ("mac address-table aging-time <num>", S.aging),
            ("end", S.end), ("exit", S.exit_conf), ("do <line>", S.do),
        ]
        ifc = [
            ("switchport mode access", S.sw_mode_access), ("switchport mode trunk", S.sw_mode_trunk),
            ("switchport access vlan <vlan>", S.sw_access),
            ("no switchport access vlan", S.sw_access_default),
            ("switchport trunk native vlan <vlan>", S.sw_native),
            ("no switchport trunk native vlan", S.sw_native_default),
            ("switchport trunk allowed vlan all", S.sw_allowed_all),
            ("switchport trunk allowed vlan none", S.sw_allowed_none),
            ("switchport trunk allowed vlan add <vlanlist>", S.sw_allowed_add),
            ("switchport trunk allowed vlan remove <vlanlist>", S.sw_allowed_remove),
            ("switchport trunk allowed vlan <vlanlist>", S.sw_allowed_set),
            ("no switchport trunk allowed vlan", S.sw_allowed_all),
            ("shutdown", S.shut), ("no shutdown", S.noshut),
            ("description <line>", S.descr), ("no description", S.nodescr),
            ("end", S.end), ("exit", S.exit_sub), ("do <line>", S.do),
        ]
        vl = [("name <word>", S.vlan_name), ("end", S.end), ("exit", S.exit_sub), ("do <line>", S.do)]
        ex = show + [("enable", S.enable), ("exit", S.quit), ("logout", S.quit), ("quit", S.quit)]
        t = {EXEC: ex, PRIV: priv, CONF: conf, IF: ifc, VLAN: vl}
        return {m: [(pat.split(), fn) for pat, fn in lst] for m, lst in t.items()}

    # ------------------------------------------------------------ matcher
    @staticmethod
    def _match(pattern, toks):
        """-> (args, exact_count) or None"""
        args, exact = [], 0
        i = 0
        for j, pt in enumerate(pattern):
            if pt == "<line>":
                if i >= len(toks):
                    return None
                args.append(" ".join(toks[i:]))
                return args, exact
            if pt == "<ifrange>":
                if i >= len(toks):
                    return None
                args.append(" ".join(toks[i:]))
                return args, exact
            if i >= len(toks):
                return None
            tok = toks[i]
            if pt.startswith("<"):
                if pt == "<vlan>" and not (tok.isdigit() and 1 <= int(tok) <= 4094):
                    return None
                if pt == "<num>" and not tok.isdigit():
                    return None
                if pt == "<iface>" and norm_iface(tok) is None:
                    return None
                if pt == "<vlanlist>" and not re.fullmatch(r"[\d,\-]+", tok):
                    return None
                args.append(tok)
            else:
                if not pt.startswith(tok.lower()):
                    return None
                exact += pt == tok.lower()
            i += 1
        if i != len(toks):
            return None
        return args, exact

    def execute(self, line):
        line = line.strip()
        if not line or line.startswith("!") or line.startswith("#"):
            return
        if line.endswith("?"):
            self.help(line[:-1])
            return
        toks = pre_tokens(line)
        res = self._dispatch(self.mode, toks)
        if res is None and self.mode in (IF, VLAN):
            res = self._dispatch(CONF, toks, fallback=True)
        if res is None:
            self.errors += 1
            self.p(f"% Invalid input detected at '^' marker.\n  ({line})")

    def _ambiguous(self, mode, toks):
        """Cisco rule: an abbreviation that fits two different keywords at the
        same position is ambiguous ('s' could be shutdown or switchport)."""
        cands = [pt for pt, _ in self.cmds[mode]]
        for i, tok in enumerate(toks):
            t = tok.lower()
            kws = {pt[i] for pt in cands if len(pt) > i and not pt[i].startswith("<")
                   and pt[i].startswith(t)}
            if t in kws:
                kws = {t}
            if len(kws) > 1:
                return True
            keep = []
            for pt in cands:
                if len(pt) <= i:
                    continue
                if pt[i] in ("<line>", "<ifrange>"):
                    continue          # swallows the rest of the line: stop checking it
                if pt[i] in kws or pt[i].startswith("<"):
                    keep.append(pt)
            cands = keep
        return False

    def _dispatch(self, mode, toks, fallback=False):
        hits = []
        for pattern, fn in self.cmds[mode]:
            m = self._match(pattern, toks)
            if m:
                hits.append((m[1], pattern, fn, m[0]))
        if not hits:
            return None
        if fallback and hits[0][1][0] in ("end", "exit", "do"):
            return None
        if self._ambiguous(mode, toks):
            self.errors += 1
            self.p(f"% Ambiguous command:  \"{' '.join(toks)}\"")
            return True
        hits.sort(key=lambda h: -h[0])
        _, _, fn, args = hits[0]
        if fallback:
            self.mode = CONF
        fn(*args)
        return True

    def help(self, text):
        toks = pre_tokens(text)
        partial = "" if text.endswith(" ") or not text else toks.pop()
        seen = {}
        for pattern, _ in self.cmds[self.mode]:
            if len(pattern) <= len(toks):
                continue
            ok = all(pt.startswith("<") or pt.startswith(t.lower()) for pt, t in zip(pattern, toks))
            if not ok:
                continue
            nxt = pattern[len(toks)]
            if nxt.startswith("<") or nxt.startswith(partial.lower()):
                seen[nxt] = PH_HELP.get(nxt, nxt)
        if not seen:
            self.p("% Unrecognized command")
        for k in sorted(seen):
            self.p(f"  {seen[k]}")

    def completions(self, text_before, word):
        toks = pre_tokens(text_before)
        out = set()
        for pattern, _ in self.cmds[self.mode]:
            if len(pattern) <= len(toks):
                continue
            if all(pt.startswith("<") or pt.startswith(t.lower()) for pt, t in zip(pattern, toks)):
                nxt = pattern[len(toks)]
                if not nxt.startswith("<") and nxt.startswith(word.lower()):
                    out.add(nxt + " ")
        return sorted(out)

    # ------------------------------------------------------------ mode commands
    def enable(self):
        self.mode = PRIV

    def disable(self):
        self.mode = EXEC

    def conf_t(self):
        self.p("Enter configuration commands, one per line.  End with CNTL/Z.")
        self.mode = CONF

    def end(self):
        self.mode = PRIV
        self.ifaces, self.vlan = [], None

    def exit_conf(self):
        self.mode = PRIV

    def exit_sub(self):
        self.mode = CONF
        self.ifaces, self.vlan = [], None

    def quit(self):
        raise EOFError

    def do(self, line):
        saved = self.mode
        self.mode = PRIV
        self.execute(line)
        if self.mode == PRIV:
            self.mode = saved

    # ------------------------------------------------------------ config commands
    def hostname(self, name):
        self.sw.state["hostname"] = name
        self.sw.save()

    def vlan_enter(self, text):
        try:
            vids = parse_vlan_list(text)
        except ValueError:
            self.errors += 1
            self.p("% Bad VLAN list")
            return
        for v in vids:
            self.sw.state["vlans"].setdefault(str(v), f"VLAN{v:04d}")
        self.vlan = sorted(vids)
        self.mode = VLAN
        self.sw.save()
        self.sw.apply_all()

    def vlan_del(self, text):
        try:
            vids = parse_vlan_list(text)
        except ValueError:
            self.errors += 1
            self.p("% Bad VLAN list")
            return
        for v in vids:
            if v == 1:
                self.p("% Default VLAN 1 may not be deleted.")
                continue
            self.sw.state["vlans"].pop(str(v), None)
        self.sw.save()
        self.sw.apply_all()

    def vlan_name(self, name):
        for v in self.vlan or []:
            if v == 1:
                self.p("% Default VLAN 1 may not have its name changed.")
                continue
            self.sw.state["vlans"][str(v)] = name
        self.sw.save()

    def if_enter(self, tok):
        p = norm_iface(tok)
        if p not in self.sw.state["ports"]:
            self.errors += 1
            self.p(f"% Interface {tok} does not exist on {self.sw.state['hostname']}. "
                   f"Ports: {', '.join(self.sw.ports())}")
            return
        self.ifaces = [p]
        self.mode = IF

    def if_range(self, text):
        ports = expand_range(text)
        if not ports:
            self.errors += 1
            self.p("% Bad interface range. Example: interface range e1 - 4")
            return
        missing = [p for p in ports if p not in self.sw.state["ports"]]
        ports = [p for p in ports if p in self.sw.state["ports"]]
        if missing:
            self.p(f"% Skipping ports not cabled in this lab: {', '.join(missing)}")
        if not ports:
            self.errors += 1
            return
        self.ifaces = ports
        self.mode = IF

    def aging(self, n):
        self.sw.state["aging"] = int(n)
        self.sw.save()
        self.sw.apply_all()

    # ------------------------------------------------------------ interface commands
    def _each(self, fn):
        for p in self.ifaces:
            fn(self.sw.state["ports"][p])
        self.sw.save()
        for p in self.ifaces:
            self.sw.apply_port(p)

    def sw_mode_access(self):
        self._each(lambda c: c.update(mode="access"))

    def sw_mode_trunk(self):
        self._each(lambda c: c.update(mode="trunk"))

    def sw_access(self, vid):
        vid = int(vid)
        if str(vid) not in self.sw.state["vlans"]:
            self.p(f"% Access VLAN does not exist. Creating vlan {vid}")
            self.sw.state["vlans"][str(vid)] = f"VLAN{vid:04d}"
            self.sw.save()
            self._each(lambda c: c.update(access_vlan=vid))
            self.sw.apply_all()
            return
        self._each(lambda c: c.update(access_vlan=vid))

    def sw_access_default(self):
        self._each(lambda c: c.update(access_vlan=1))

    def sw_native(self, vid):
        self._each(lambda c: c.update(native=int(vid)))

    def sw_native_default(self):
        self._each(lambda c: c.update(native=1))

    def sw_allowed_all(self):
        self._each(lambda c: c.update(allowed="all"))

    def sw_allowed_none(self):
        self._each(lambda c: c.update(allowed=[]))

    def _vl(self, text):
        try:
            return parse_vlan_list(text)
        except ValueError:
            self.errors += 1
            self.p("% Bad VLAN list. Example: 10,20,30-35")
            return None

    def sw_allowed_set(self, text):
        vs = self._vl(text)
        if vs is not None:
            self._each(lambda c: c.update(allowed=sorted(vs)))

    def sw_allowed_add(self, text):
        vs = self._vl(text)
        if vs is not None:
            def f(c):
                if c["allowed"] != "all":
                    c["allowed"] = sorted(set(c["allowed"]) | vs)
            self._each(f)

    def sw_allowed_remove(self, text):
        vs = self._vl(text)
        if vs is not None:
            def f(c):
                cur = set(range(1, 4095)) if c["allowed"] == "all" else set(c["allowed"])
                c["allowed"] = sorted(cur - vs)
            self._each(f)

    def shut(self):
        self._each(lambda c: c.update(shutdown=True))

    def noshut(self):
        self._each(lambda c: c.update(shutdown=False))

    def descr(self, text):
        self._each(lambda c: c.update(description=text[:40]))

    def nodescr(self):
        self._each(lambda c: c.update(description=""))

    # ------------------------------------------------------------ exec commands
    def write(self):
        base = os.path.join(STATE_DIR, f"{self.sw.name}.startup.json")
        self.sw.save(base)
        self.p("Building configuration...\n[OK]")

    def clear_mac(self):
        sh(["ip", "link", "set", "dev", "br0", "type", "bridge", "fdb_flush"])

    # ------------------------------------------------------------ show commands
    def _status(self, port):
        cfg = self.sw.state["ports"][port]
        admin, oper = link_info(port)
        if cfg["shutdown"] or not admin:
            return "disabled"
        if not oper:
            return "notconnect"
        if cfg["mode"] == "access" and cfg["access_vlan"] not in self.sw.vlans:
            return "inactive"
        return "connected"

    def show_vlan(self):
        self.p(f"\n{'VLAN':<4} {'Name':<32} {'Status':<9} Ports")
        self.p(f"{'-'*4} {'-'*32} {'-'*9} {'-'*31}")
        for v, name in sorted(self.sw.vlans.items()):
            ports = [p for p in self.sw.ports()
                     if self.sw.state["ports"][p]["mode"] == "access"
                     and self.sw.state["ports"][p]["access_vlan"] == v]
            self.p(f"{v:<4} {name:<32} {'active':<9} {', '.join(ports)}")
        self.p("")

    def show_status(self):
        self.p(f"\n{'Port':<9} {'Name':<18} {'Status':<12} {'Vlan':<10} {'Duplex':>6} {'Speed':>6} Type")
        for p in self.sw.ports():
            c = self.sw.state["ports"][p]
            vlan = "trunk" if c["mode"] == "trunk" else str(c["access_vlan"])
            self.p(f"{p:<9} {c['description'][:18]:<18} {self._status(p):<12} {vlan:<10} "
                   f"{'full':>6} {'1000':>6} virtual")
        self.p("")

    def show_trunk(self):
        trunks = [p for p in self.sw.ports() if self.sw.state["ports"][p]["mode"] == "trunk"
                  and self._status(p) == "connected"]
        if not trunks:
            self.p("(no interfaces are trunking)")
            return
        self.p(f"\n{'Port':<11} {'Mode':<12} {'Encapsulation':<14} {'Status':<13} Native vlan")
        for p in trunks:
            self.p(f"{p:<11} {'on':<12} {'802.1q':<14} {'trunking':<13} {self.sw.state['ports'][p]['native']}")
        self.p(f"\n{'Port':<11} Vlans allowed on trunk")
        for p in trunks:
            self.p(f"{p:<11} {fmt_vlans(self.sw.allowed(p))}")
        self.p(f"\n{'Port':<11} Vlans allowed and active in management domain")
        for p in trunks:
            self.p(f"{p:<11} {fmt_vlans(self.sw.allowed(p) & set(self.sw.vlans))}")
        self.p("")

    def show_switchport(self, tok):
        p = norm_iface(tok)
        if p not in self.sw.state["ports"]:
            self.p(f"% Interface {tok} does not exist")
            return
        c = self.sw.state["ports"][p]
        st = self._status(p)
        oper = "down" if st in ("disabled", "notconnect") else ("trunk" if c["mode"] == "trunk" else "static access")
        vname = lambda v: self.sw.vlans.get(v, "Inactive")
        self.p(f"Name: {p}\nSwitchport: Enabled\n"
               f"Administrative Mode: {'trunk' if c['mode'] == 'trunk' else 'static access'}\n"
               f"Operational Mode: {oper}\n"
               f"Administrative Trunking Encapsulation: dot1q\n"
               f"Access Mode VLAN: {c['access_vlan']} ({vname(c['access_vlan'])})\n"
               f"Trunking Native Mode VLAN: {c['native']} ({vname(c['native'])})\n"
               f"Trunking VLANs Enabled: {'ALL' if c['allowed'] == 'all' else fmt_vlans(c['allowed'])}")

    def show_mac(self, *args):
        rows = kernel_fdb()
        if args:
            a = args[0]
            if a.isdigit():
                rows = [r for r in rows if r[0] == int(a)]
            else:
                rows = [r for r in rows if r[2] == norm_iface(a)]
        self.p("          Mac Address Table\n-------------------------------------------\n")
        self.p(f"{'Vlan':<7} {'Mac Address':<17} {'Type':<11} Ports")
        self.p(f"{'----':<7} {'-----------':<17} {'--------':<11} -----")
        for vlan, mac, port in rows:
            self.p(f"{vlan:>4}    {cisco_mac(mac):<17} {'DYNAMIC':<11} {port}")
        self.p(f"Total Mac Addresses for this criterion: {len(rows)}")

    def show_aging(self):
        self.p(f"Global Aging Time: {self.sw.state.get('aging', 300):>4}")

    def running_config(self):
        st = self.sw.state
        lines = ["Building configuration...", "", "Current configuration:", "!",
                 f"hostname {st['hostname']}", "!"]
        if st.get("aging", 300) != 300:
            lines += [f"mac address-table aging-time {st['aging']}", "!"]
        for v, name in sorted(self.sw.vlans.items()):
            if v == 1:
                continue
            lines += [f"vlan {v}"] + ([f" name {name}"] if name != f"VLAN{v:04d}" else []) + ["!"]
        for p in self.sw.ports():
            c = st["ports"][p]
            lines.append(f"interface {p}")
            if c["description"]:
                lines.append(f" description {c['description']}")
            if c["mode"] == "access":
                if c["access_vlan"] != 1:
                    lines.append(f" switchport access vlan {c['access_vlan']}")
                lines.append(" switchport mode access")
            else:
                if c["native"] != 1:
                    lines.append(f" switchport trunk native vlan {c['native']}")
                if c["allowed"] != "all":
                    lines.append(f" switchport trunk allowed vlan {fmt_vlans(c['allowed'])}")
                lines.append(" switchport mode trunk")
            if c["shutdown"]:
                lines.append(" shutdown")
            lines.append("!")
        lines.append("end")
        return "\n".join(lines)

    def show_run(self):
        self.p(self.running_config())

    def show_version(self):
        self.p(f"swsh Cisco-style switch shell 1.0 for the CCNA Lab Kit\n"
               f"Hardware: Linux bridge with 802.1Q VLAN filtering\n"
               f"{self.sw.state['hostname']} has {len(self.sw.ports())} cabled ports: {', '.join(self.sw.ports())}")

    def show_stp(self):
        self.p("Spanning tree is switched off in these labs (no loops in the topologies).\n"
               "STP gets its own lab later in the course.")


# ===================================================================== entry points
def interactive(cli):
    try:
        import readline
        def complete(text, state):
            buf = readline.get_line_buffer()
            before = buf[:readline.get_begidx()]
            opts = cli.completions(before, text)
            return opts[state] if state < len(opts) else None
        readline.set_completer(complete)
        readline.set_completer_delims(" ")
        readline.parse_and_bind("tab: complete")
    except ImportError:
        pass
    print(f"\n{cli.sw.state['hostname']} - Cisco-style switch CLI (type ? for help, exit to leave)\n")
    while True:
        try:
            line = input(cli.prompt() + " ")
        except KeyboardInterrupt:
            print("^C")
            if cli.mode not in (EXEC, PRIV):
                cli.end()
            continue
        except EOFError:
            print()
            break
        try:
            if line.strip() in ("\x1a",):
                cli.end()
                continue
            cli.execute(line)
        except EOFError:
            cli.flush()
            break
        cli.flush()


def read_src(src):
    return sys.stdin.read() if src == "-" else open(src).read()


def main(argv):
    if len(argv) < 2 or argv[1] in ("-h", "--help"):
        print(__doc__)
        return 0
    sw = Switch(argv[1])
    opt = argv[2] if len(argv) > 2 else None
    if opt == "--init":
        sw.reset()
        sw.save()
        sw.apply_all()
        return 0
    sw.load()
    if opt == "--load-state":
        data = json.loads(read_src(argv[3]))
        ports = sw.state["ports"]
        sw.state = data
        for p in ports:
            sw.state["ports"].setdefault(p, default_port())
        sw.save()
        sw.apply_all()
        return 0
    cli = CLI(sw)
    if opt == "--batch":
        cli.mode = PRIV
        for line in read_src(argv[3]).splitlines():
            try:
                cli.execute(line)
            except EOFError:
                break
        cli.flush()
        return 1 if cli.errors else 0
    if opt == "--show":
        cli.mode = PRIV
        cli.execute(" ".join(argv[3:]))
        cli.flush()
        return 0
    interactive(cli)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
