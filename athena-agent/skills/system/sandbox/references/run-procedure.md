# Running untrusted code under watch

Two isolation profiles. Both are `bwrap`; the difference is whether the code is allowed to see a network at all.

## A — no network (default: learn intent, nothing can succeed)

```sh
bwrap \
  --unshare-all --die-with-parent --new-session \
  --ro-bind /usr /usr --ro-bind /lib /lib --ro-bind /lib64 /lib64 --ro-bind /bin /bin --ro-bind /etc /etc \
  --tmpfs /tmp --tmpfs /home --tmpfs /root --tmpfs /run \
  --proc /proc --dev /dev \
  --chdir /work \
  --bind "$CLONE" /work \
  timeout --signal=KILL 120 /work/entrypoint
```

`--unshare-all` includes `--unshare-net`, so a socket call has nowhere to go. Every `connect()` fails immediately —
which is exactly what you record: the attempt, its destination, and its timing.

Capture it:

```sh
strace -f -e trace=network,openat,execve -o /tmp/trace.log timeout --signal=KILL 120 ./entrypoint
grep -E 'connect|bind|listen|execve' /tmp/trace.log
```

## B — one veth to a recording proxy (learn intent *and* whether it would work)

Create a network namespace and a pair, with the host side running a proxy that logs and decides:

```sh
ip netns add untrusted
ip link add veth-host type veth peer name veth-sb
ip link set veth-sb netns untrusted
ip addr add 10.200.0.1/24 dev veth-host && ip link set veth-host up
ip netns exec untrusted ip addr add 10.200.0.2/24 dev veth-sb
ip netns exec untrusted ip link set veth-sb up
ip netns exec untrusted ip route add default via 10.200.0.1
# no NAT and no forwarding rule: nothing leaves the host
```

Then run `bwrap` with `--netns untrusted` (not `--unshare-net`), and on `veth-host` run:

```sh
# what it tried to reach
tcpdump -i veth-host -nn -l 'tcp[tcpflags] & tcp-syn != 0' -w attempted.pcap
# what it tried to resolve
dnsmasq --interface=veth-host --log-queries --no-resolv --address=/#/10.200.0.1
```

Every DNS name and every SYN is now evidence. Answer with the proxy only when the allowlist says so; a refusal that is
recorded is worth more than a silence.

## Inbound

Scan the namespace from the host side while it runs:

```sh
ip netns exec untrusted ss -lntup                     # what it opened
nmap -Pn -p- 10.200.0.2 --min-rate 2000 -oN listeners.txt   # what actually answers
```

A listener on a port nobody asked for is the most important line in the report.

## Filesystem evidence

```sh
mkdir -p upper work
bwrap ... --overlay-src / --overlay upper work / ...
# afterwards
find upper -type f | head -100          # everything it created or changed, including files it could not write
```

## Budget

```sh
systemd-run --scope -p MemoryMax=1G -p CPUQuota=100% -p TasksMax=64 -- bwrap ...
```

Without a pids limit, a fork bomb takes the box down before your wall-clock kill fires.

## Reporting template

```text
run      <entrypoint> in <profile A|B>, <n>s, exit <code>
outbound <count> attempts — <host:port> ×<n>, DNS: <names>
inbound  <ports listening> — <who would answer>
writes   <count> paths under the overlay, notable: <…>
spawned  <execve calls>
verdict  <what the evidence supports, and what it does not>
```
