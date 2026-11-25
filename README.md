# CCNA Lab Kit

Hands-on CCNA 200-301 labs that run entirely in the browser. Each student gets real routers (FRRouting, Cisco-style CLI), real VLAN switches (Cisco-style CLI) and Linux PCs inside one GitHub Codespace. There's nothing to install.

**Students: start here → [labs/00-getting-started.md](labs/00-getting-started.md)**

| | |
|---|---|
| Labs | [`labs/`](labs/): 10 labs, including 2 mock exams. Course map: [`labs/later/course-map.md`](labs/later/course-map.md) |
| Cisco vs lab commands | [`docs/ios-vs-lab-cheatsheet.md`](docs/ios-vs-lab-cheatsheet.md) |
| Your work | `my-work/` (answers, evidence, captures, saved configs) |
| Lecturer | [`instructor/INSTRUCTOR-GUIDE.md`](instructor/INSTRUCTOR-GUIDE.md) |

```bash
labctl doctor          # health check
labctl hello 1         # sign in
labctl start-lab 1     # build the network
net topo               # see it
labctl submit 1        # hand in
```
