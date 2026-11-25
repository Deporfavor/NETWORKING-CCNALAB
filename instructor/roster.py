#!/usr/bin/env python3
"""roster.py - one table showing your whole class.

How it works (the "attendance book" analogy):
  * Each student keeps a private repo made from the template, with "ccna" in its name,
    and adds you as a collaborator.
  * `labctl hello N` writes a line in my-work/attendance.log and uploads it.
  * `labctl submit N` writes a 'submit' line and uploads the evidence file (with SCORE).
  * This script reads every student's book through the GitHub API, accepts pending
    collaborator invitations for you, and prints one table.

Usage (in a codespace on your template repo, or any computer with Python 3):
  python3 instructor/roster.py --setup        # once: paste a GitHub token
  python3 instructor/roster.py                # class table
  python3 instructor/roster.py --watch 120    # refresh every 2 minutes during a lab
  python3 instructor/roster.py --lab 5        # who has / hasn't submitted lab 5, with scores

The token and the CSV copy (~/ccna-roster.csv) stay in your HOME folder, never in the
repository, so student names are never published.
"""
import argparse
import base64
import csv
import datetime as dt
import getpass
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

API = "https://api.github.com"
TOKEN_FILE = os.path.expanduser("~/.ccna/gh_token")
CSV_FILE = os.path.expanduser("~/ccna-roster.csv")
HERE = os.path.dirname(os.path.abspath(__file__))


def kit_conf():
    conf = {"REPO_HINT": "ccna", "TEMPLATE_REPO": ""}
    try:
        for line in open(os.path.join(HERE, "..", "scripts", "kit.conf")):
            if "=" in line and not line.startswith("#"):
                k, v = line.strip().split("=", 1)
                conf[k] = v
    except OSError:
        pass
    return conf


CONF = kit_conf()
HINT = CONF["REPO_HINT"].lower()
TEMPLATE = CONF["TEMPLATE_REPO"].lower()


# --------------------------------------------------------------------- GitHub
def token():
    t = os.environ.get("ROSTER_TOKEN")
    if not t and os.path.exists(TOKEN_FILE):
        t = open(TOKEN_FILE).read().strip()
    if not t:
        sys.exit("No token yet. Run:  python3 instructor/roster.py --setup")
    return t


def gh(path, method="GET", tok=None, raw=False):
    url = path if path.startswith("http") else API + path
    req = urllib.request.Request(url, method=method)
    req.add_header("Authorization", "Bearer " + (tok or token()))
    req.add_header("Accept", "application/vnd.github.raw" if raw else "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read()
            if raw:
                return body.decode("utf-8", "replace")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        if e.code == 401:
            sys.exit("GitHub refused the token (401). Run --setup again with a new token.")
        if e.code == 403:
            sys.exit("GitHub said 403: the token lacks the 'repo' scope, or the hourly API limit was hit.")
        raise


def gh_all(path):
    items, page = [], 1
    sep = "&" if "?" in path else "?"
    while True:
        batch = gh(f"{path}{sep}per_page=100&page={page}") or []
        items += batch
        if len(batch) < 100:
            return items
        page += 1


def setup():
    print("Create a token: github.com > your photo > Settings > Developer settings >\n"
          "Personal access tokens > Tokens (classic) > Generate new token (classic).\n"
          "Tick 'repo', choose an expiry (e.g. 90 days), generate, and copy it.\n"
          "The token is like a staff master key: never paste it into a repository, chat or email.\n")
    t = getpass.getpass("Paste the token here (it stays hidden): ").strip()
    me = gh("/user", tok=t)
    if not me:
        sys.exit("That token didn't work.")
    os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
    with open(TOKEN_FILE, "w") as fh:
        fh.write(t)
    os.chmod(TOKEN_FILE, 0o600)
    print(f"Saved. Logged in as {me['login']}. Now run: python3 instructor/roster.py")


# --------------------------------------------------------------------- data
def accept_invitations():
    n = 0
    for inv in gh_all("/user/repository_invitations"):
        name = inv.get("repository", {}).get("full_name", "")
        if HINT in name.lower():
            gh(f"/user/repository_invitations/{inv['id']}", method="PATCH")
            n += 1
    return n


def student_repos(me):
    repos = gh_all("/user/repos?affiliation=collaborator")
    return [r for r in repos if HINT in r["name"].lower() and r["owner"]["login"] != me
            and r["full_name"].lower() != TEMPLATE]


def parse_log(text):
    rows = []
    for line in (text or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        try:
            ts = dt.datetime.strptime(parts[0], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
        except ValueError:
            continue
        lab = parts[2].replace("lab=", "")
        rows.append((ts, parts[1], int(lab) if lab.isdigit() else None))
    return rows


def evidence_score(repo, lab):
    listing = gh(f"/repos/{repo}/contents/my-work/evidence") or []
    for f in listing if isinstance(listing, list) else []:
        if f["name"].startswith(f"lab{lab:02d}_"):
            text = gh(f"/repos/{repo}/contents/my-work/evidence/{f['name']}", raw=True) or ""
            m = re.search(r"^SCORE: (\d+)/(\d+)", text, re.M)
            g = re.search(r"^Generated: (\S+)", text, re.M)
            return (f"{m.group(1)}/{m.group(2)}" if m else "?"), (g.group(1) if g else "")
    return None, None


def collect(me, exam_labs=(5, 10), only_lab=None):
    now = dt.datetime.now(dt.timezone.utc)
    out = []
    for r in sorted(student_repos(me), key=lambda r: r["owner"]["login"].lower()):
        full = r["full_name"]
        log = parse_log(gh(f"/repos/{full}/contents/my-work/attendance.log", raw=True))
        hellos = [ts for ts, ev, _ in log if ev == "hello"]
        submitted = sorted({lab for _, ev, lab in log if ev == "submit" and lab})
        row = {
            "student": r["owner"]["login"], "repo": full,
            "here_now": "yes" if hellos and (now - max(hellos)).total_seconds() < 3 * 3600 else "",
            "last_signin": max(hellos).strftime("%d %b %H:%M") if hellos else "never",
            "submitted": ",".join(map(str, submitted)),
        }
        labs = [only_lab] if only_lab else [l for l in exam_labs if l in submitted]
        for lab in (exam_labs if not only_lab else [only_lab]):
            row[f"lab{lab}"] = ""
        for lab in labs:
            score, when = evidence_score(full, lab)
            row[f"lab{lab}"] = score or ""
            if only_lab:
                row["when"] = when or ""
                row["did_submit"] = "yes" if lab in submitted else "NO"
        out.append(row)
    return out


# --------------------------------------------------------------------- output
def table(rows, cols, heads):
    widths = [max(len(h), *(len(str(r.get(c, ""))) for r in rows)) if rows else len(h) for c, h in zip(cols, heads)]
    line = "  ".join(h.ljust(w) for h, w in zip(heads, widths))
    print(line)
    print("-" * len(line))
    for r in rows:
        print("  ".join(str(r.get(c, "")).ljust(w) for c, w in zip(cols, widths)))


def save_csv(rows):
    if not rows:
        return
    keys = list(rows[0].keys())
    with open(CSV_FILE, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


def show(only_lab=None):
    me = gh("/user")["login"]
    n = accept_invitations()
    rows = collect(me, only_lab=only_lab)
    stamp = dt.datetime.now().strftime("%H:%M:%S")
    print(f"\nCCNA class roster  ({len(rows)} students, {stamp})" + (f"  - accepted {n} new invitation(s)" if n else ""))
    if only_lab:
        table(rows, ["student", "did_submit", f"lab{only_lab}", "when", "last_signin"],
              ["Student", f"Submitted lab {only_lab}", "Score", "Evidence time (UTC)", "Last sign-in"])
        missing = [r["student"] for r in rows if r.get("did_submit") != "yes"]
        print(f"\nNot yet submitted ({len(missing)}): {', '.join(missing) or 'nobody'}")
    else:
        table(rows, ["student", "here_now", "last_signin", "submitted", "lab5", "lab10"],
              ["Student", "Here now", "Last sign-in (UTC)", "Labs submitted", "Exam 1 practical", "Exam 2 practical"])
    save_csv(rows)
    print(f"\nSpreadsheet copy: {CSV_FILE}")
    if not rows:
        print("No student repos yet. Students must add you as a collaborator and keep "
              f"'{HINT}' in their repo name (getting-started 1.2 and 1.3).")


def main():
    ap = argparse.ArgumentParser(description="CCNA class roster")
    ap.add_argument("--setup", action="store_true")
    ap.add_argument("--watch", type=int, metavar="SECONDS")
    ap.add_argument("--lab", type=int)
    a = ap.parse_args()
    if a.setup:
        return setup()
    if a.watch:
        try:
            while True:
                os.system("clear")
                show(a.lab)
                print(f"\nRefreshing every {a.watch}s. Ctrl+C to stop.")
                time.sleep(max(30, a.watch))
        except KeyboardInterrupt:
            print()
    else:
        show(a.lab)


if __name__ == "__main__":
    main()
