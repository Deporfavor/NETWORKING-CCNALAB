# Cisco IOS vs the CCNA Lab Kit: command cheat sheet

The exam tests **Cisco IOS** syntax. The labs run **FRRouting** (routers), **swsh** (switches) and **Linux** (PCs). The ideas are identical, and most commands are too. This page lists every difference you'll meet.

> **Rule of thumb:** learn the left-hand column for the exam. Use the right-hand column in the lab.

## Getting around

| Task | Cisco IOS | Lab |
|---|---|---|
| Connect to a device | console cable / SSH | `net R1`, `net SW1`, `net PC1` |
| Privileged mode | `enable` (from `R1>`) | routers start at `R1#`; switches need `enable` |
| Global config | `configure terminal` | same |
| Back one level / straight to `#` | `exit` / `end` (or Ctrl+Z) | same |
| Run a show command from config mode | `do show ...` | same |
| Help / completion | `?` / Tab | same |
| Save | `copy running-config startup-config` / `write memory` | `write memory` (switch: both work) |
| Show config | `show running-config` | same |

## Interfaces

| Task | Cisco IOS | Lab |
|---|---|---|
| Names | `GigabitEthernet0/1`, `g0/1`, `fa0/1` | routers `e0`, `e1`; switches `e1`... (switches also accept `fa0/1`, `g0/1`) |
| Address | `ip address 10.0.0.1 255.255.255.0` | `ip address 10.0.0.1/24` |
| Enable | `no shutdown` (interfaces start shut) | `no shutdown` (interfaces start up unless the lab shuts them) |
| Summary | `show ip interface brief` | `show interface brief` |
| Description | `description TO-R2` | same |

## Switching (swsh)

| Task | Cisco IOS | Lab |
|---|---|---|
| Create/name VLAN | `vlan 10` · `name STAFF` | same |
| Access port | `switchport mode access` · `switchport access vlan 10` | same |
| Trunk | `switchport mode trunk` (some: `switchport trunk encapsulation dot1q` first) | `switchport mode trunk` |
| Allowed list | `switchport trunk allowed vlan 10,20` / `add` / `remove` | same |
| Native VLAN | `switchport trunk native vlan 99` | same |
| DTP off | `switchport nonegotiate` | no DTP in the lab |
| Several ports | `interface range fa0/1 - 4` | `interface range e1 - 4` |
| Verify | `show vlan brief` · `show interfaces trunk` · `show interfaces status` · `show interfaces fa0/1 switchport` | same (with `e1`) |
| MAC table | `show mac address-table` · `clear mac address-table dynamic` | same |
| STP | `show spanning-tree` | not in labs 1-10 |

## Routing

| Task | Cisco IOS | Lab (FRRouting) |
|---|---|---|
| Static route | `ip route 10.3.3.0 255.255.255.0 10.0.12.2` | `ip route 10.3.3.0/24 10.0.12.2` (mask form also accepted) |
| Default route | `ip route 0.0.0.0 0.0.0.0 10.0.12.2` | `ip route 0.0.0.0/0 10.0.12.2` |
| Floating static | `... 10.0.13.2 200` | same |
| Routing table | `show ip route` (codes `C`, `L`, `S`, `O`) | `show ip route` (codes `C`, `S`, `O`, `K`; no `L` routes) |
| Router-on-a-stick | `interface g0/0.10` · `encapsulation dot1Q 10` | `interface e0.10` (tag already set by the lab) |
| OSPF process | `router ospf 1` | `router ospf` |
| Router ID | `router-id 1.1.1.1` | `ospf router-id 1.1.1.1` |
| Network statement | `network 10.0.0.0 0.0.0.255 area 0` (wildcard) | `network 10.0.0.0/24 area 0` (prefix) |
| Passive | `passive-interface g0/1` (router mode) | `ip ospf passive` (interface mode) |
| Priority | `ip ospf priority 100` | same |
| Default into OSPF | `default-information originate` | same |
| Verify | `show ip ospf neighbor` · `show ip ospf interface` · `show ip protocols` | same (no `show ip protocols`: use `show ip ospf`) |

## IP services

| Task | Cisco IOS | Lab |
|---|---|---|
| DHCP pool | `ip dhcp pool LAN` · `network ...` · `default-router ...` · `dns-server ...` | `dnsmasq.conf` + `net R1 dhcp-server FILE` |
| Excluded addresses | `ip dhcp excluded-address A B` | start the pool after them |
| DHCP relay | `ip helper-address X` | - |
| Leases | `show ip dhcp binding` | `sudo cat /run/netlab/R1.leases` |
| PAT | `ip nat inside` / `ip nat outside` / `access-list 1 permit ...` / `ip nat inside source list 1 interface g0/1 overload` | `net R1 iptables -t nat -A POSTROUTING -o e1 -j MASQUERADE` |
| Translations | `show ip nat translations` | `net R1 conntrack -L` |

## Hosts (Windows ↔ Linux)

| Task | Windows | Lab PC (Linux) |
|---|---|---|
| IP settings | `ipconfig` / `ipconfig /all` | `ip addr`, `ip route` (`ipconfig` alias works) |
| Ping | `ping 10.0.0.1` | `ping -c 3 10.0.0.1` |
| Trace | `tracert 10.0.0.1` | `traceroute -n 10.0.0.1` |
| ARP table | `arp -a` | `ip neigh` |
| DNS lookup | `nslookup name` | `nslookup name` / `dig name` |
| Renew DHCP | `ipconfig /renew` | `net PC1 dhcp` |
