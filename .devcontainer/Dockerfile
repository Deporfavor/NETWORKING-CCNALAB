# CCNA Lab Kit - one Ubuntu machine that pretends to be a whole network.
# Each router, switch and PC is a Linux "network namespace" (a sealed room
# with its own interfaces, addresses and routing table). Routers run FRRouting,
# whose CLI follows Cisco IOS closely (configure terminal, router ospf, show ip route).
FROM mcr.microsoft.com/devcontainers/base:ubuntu-24.04

ENV DEBIAN_FRONTEND=noninteractive

# Core packages: if any of these were missing the build would fail, so this
# list only contains packages that ship in Ubuntu 24.04 main/universe.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      frr frr-pythontools \
      iproute2 iputils-ping iputils-tracepath traceroute \
      tcpdump tshark \
      dnsmasq-base iptables conntrack \
      dnsutils netcat-openbsd ethtool curl \
      python3 jq less nano \
 && rm -rf /var/lib/apt/lists/*

# Optional DHCP client (lab 9). Installed one by one so a missing package can
# never break the whole build; labctl doctor reports which one is present.
RUN apt-get update \
 && for p in udhcpc busybox; do apt-get install -y --no-install-recommends "$p" || echo "optional package $p skipped"; done \
 && rm -rf /var/lib/apt/lists/*

# FRR must never start as a normal service here: netlab starts one private copy
# per router, inside that router's namespace.
RUN sed -i 's/=yes/=no/' /etc/frr/daemons || true \
 && mkdir -p /var/log/frr /run/netlab \
 && chown frr:frr /var/log/frr
