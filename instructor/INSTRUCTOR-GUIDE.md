# Instructor guide: CCNA Lab Kit

This kit uses the same system as your DBA401 CampusDB kit: a GitHub template repo, a codespace per student, `labctl` for attendance, auto-marking and submission, and `roster.py` to see the whole class. What's new is the lab engine: each codespace builds a small virtual network out of Linux network namespaces.

> **Analogy:** in the DBA kit, every student got their own MySQL server. Here every student gets their own **equipment rack**: routers, switches and PCs cabled together, rebuilt in seconds by `labctl start-lab N`.

## 1. What's in the kit

```text
.devcontainer/        Ubuntu 24.04 + FRRouting + tcpdump/tshark + dnsmasq (privileged, for namespaces)
scripts/
  labctl.sh           student commands: doctor, hello, start-lab, check, submit, save, resume, update
  net.sh              "walk into" a device: net R1 / net SW1 / net PC1 / net capture ...
  netlab.py           builds the virtual network from topologies/labNN.json
  swsh.py             Cisco-style switch CLI on top of a Linux VLAN bridge
  checks.py           auto-marker: real pings + config/table checks -> evidence file with SCORE
  kit.conf            TEMPLATE_REPO and REPO_HINT (edit if you rename anything)
topologies/labNN.json devices, cables, starting configs (and the exam faults)
labs/                 00-getting-started + 10 lab sheets, img/ (19 diagrams), answers/ templates, files/
docs/                 Cisco IOS vs lab cheat sheet
instructor/           this guide + roster.py
```

**Answer keys** for every lab question and both mock exams are in your **private** Claude Docs page, not in this repository. The template must be public so students can use it.

## 2. Publish the template (once, about 15 minutes)

1. On GitHub, create a **new public repository** called **`ccna-lab-kit`** under `Kofi-dba`. Tick *Add a README* so it isn't empty.
2. In that repo: **Code → Codespaces → Create codespace on main**. (This first codespace uses GitHub's generic image. That's fine: it's only used to install the files.)
3. Drag **`ccna-lab-kit.zip`** from your computer into the codespace **Explorer** panel.
4. In the codespace terminal:
   ```bash
   unzip -o ccna-lab-kit.zip -d /tmp/kit && bash /tmp/kit/install.sh
   ```
   The script copies everything (including the hidden `.devcontainer` folder, which drag-and-drop tends to lose), sets permissions, commits and pushes. It finishes with **INSTALL OK**.
5. On github.com: repo **Settings → General → tick "Template repository"**.
6. Rebuild so you're on the real image: `Ctrl+Shift+P` → **Codespaces: Rebuild Container**. Then run `labctl doctor`. Inside the template it will warn *"You are inside the TEMPLATE"*, which is expected here. Every other line should be ✔.

## 3. Dry-run before the students (please do this)

The kit was built and checked in an environment **without** FRRouting or network namespaces. Every script passed syntax, simulation and unit tests, but none has run against a live network yet. Before Lab 1, work through labs 1-10 yourself in a codespace on the template. Use the answer-key page as your solution. Test these four first, because they carry the most risk:

| # | Test | Command | Pass looks like |
|---|---|---|---|
| 1 | Namespaces work | `labctl doctor` | ✔ *Virtual cables work* |
| 2 | FRR starts per router | `labctl start-lab 1` then `net R1 show interface brief` | e0 up with 192.168.1.1/24 |
| 3 | VLAN tagging | lab 4 solution, then `net PC1 ping -c2 10.1.1.13` and the isolation pings | STAFF works, cross-VLAN fails |
| 4 | DHCP client | lab 9 Part A | `DHCP lease: 192.168.1.1xx/24` |

Use `labctl check N` in the template, but **not `labctl submit`**: submit would push your test evidence into the template, and every student would inherit it. If that happens, delete `my-work/evidence/` and `my-work/answers/` in the template and push.

After each lab's solution, `labctl check N` should show **10/10**. If any check fails even though the network works, send me the evidence file from `my-work/evidence/` and I'll fix the marker.

**If test 2 fails** (the router CLI says it can't connect), look at `/var/log/frr/R1-zebra.log`. The most likely cause is a difference in how this FRRouting version handles per-namespace instances (`-N`). Send me the log.

Everything you change on the template reaches students through `labctl update`, which pulls `labs/ scripts/ topologies/ docs/ .devcontainer/` and never touches `my-work/`. A change to `.devcontainer/` also needs a container rebuild on each student's side.

## 4. Running an online lab session (2 hours)

| Time | What happens |
|---|---|
| Before | Homework: `labctl doctor` screenshot 48 h before Lab 1 (getting-started 1.5) |
| 0:00 | Everyone runs `labctl hello N` and `labctl start-lab N`. You start `python3 instructor/roster.py --watch 120` |
| 0:05 | 10-minute recap using the lab's know-how cards and diagrams. Share your screen on `net topo` |
| 0:15 | Students work Parts A-C. Breakout rooms of 4-5 with a **lab captain** each (rotate captains) |
| 1:15 | Regroup: walk through the trickiest "What you should see" outputs together |
| 1:30 | Remaining parts + questions in `my-work/answers/labNN.md` |
| 1:50 | `labctl check N` → `labctl submit N`. Check the roster: `--lab N` lists who hasn't submitted |

Mock exams (labs 5 and 10): Section A theory first (45 min, keep cameras on), then the practical (75 min). The practical is marked live by the auto-marker. Section A is marked by hand against your private key.

## 5. Marking

| Part | Where | How |
|---|---|---|
| Practical score | `my-work/evidence/labNN_<user>.txt` → `SCORE: x/10` | Automatic, by real tests (pings, configs, tables). Shown in the roster |
| Written answers | `my-work/answers/labNN.md` | By hand against the private answer key. Labs 1-4 and 6-9 also give 1-2 auto-marks for having answered at all |
| Exam theory | `my-work/answers/lab05.md`, `lab10.md`, Section A | By hand, 20 marks |
| Attendance | `my-work/attendance.log` | Roster "Last sign-in" and "Here now" columns |

The evidence file also contains every device's running config and routing table at the moment of submission. That's useful for spotting copied work: identical descriptions, typos or timestamps across students.

## 6. Roster: one-time setup

1. Students add **Kofi-dba** as a collaborator (getting-started 1.3). The roster accepts the invitations for you.
2. Create a token: github.com → your photo → **Settings → Developer settings → Personal access tokens → Tokens (classic) → Generate new token (classic)**. Tick **`repo`**, set an expiry, and copy it.
3. In a codespace on `ccna-lab-kit`: `python3 instructor/roster.py --setup` and paste it.

```bash
python3 instructor/roster.py              # class table + ~/ccna-roster.csv
python3 instructor/roster.py --watch 120  # live during a lab
python3 instructor/roster.py --lab 5      # who has / hasn't submitted lab 5, with scores
```

GitHub allows 5,000 API calls an hour. One refresh costs about 2-4 calls per student, so `--watch 120` is comfortable for a class of up to about 40.

## 7. Codespaces hours

The kit asks for a 2-core machine. A free personal account's monthly allowance gives roughly 60 hours on 2 cores, and the Student Developer Pack raises it. One lab is 2-2.5 hours, so a student doing 2 labs a week plus homework stays well inside, **provided they stop their codespace** after each session (getting-started section 4). Codespaces also stop automatically after 30 idle minutes.

## 8. Things worth knowing

- **Router interfaces start up**, unlike Cisco. The labs deliberately shut some (labs 1 and 5) to teach `no shutdown`.
- **The switch CLI is a teaching tool.** It covers the VLAN, trunk and MAC-table commands of labs 1-10, rejects ambiguous abbreviations like IOS does, and has no STP or DTP. Lab 12 (STP) will extend it.
- **The exam faults are visible in `topologies/lab05.json` and `lab10.json`** to anyone who goes looking. They show no more than `show running-config` would, but if you prefer, keep the exam topologies out of the template and copy them into it just before each exam.
- **`labctl start-lab` always gives a clean network.** Students who break everything can start again in 15 seconds, so encourage experimenting.
