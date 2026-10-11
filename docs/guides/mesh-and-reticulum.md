<!--
SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
SPDX-License-Identifier: GPL-3.0-or-later
-->

# Mesh and Reticulum: messaging with no infrastructure

Most of what an operator runs for emergency communications leans on
something that is somebody else's: a repeater, a gateway, an internet
service. Reticulum is a networking stack that assumes none of them. The
`mesh` profile puts it on the laptop, with NomadNet (an encrypted messenger
and page browser for the terminal), LXMF (the message layer under it), the
RNode flasher for LoRa radios, and the two Meshtastic clients the catalog
already carried. Two laptops on one network find each other with no setup;
the same programs then reach further over a LoRa radio, a packet modem or
the internet. The decision record behind it is **D-080** in
`docs/DECISIONS.md`, and the inventory it rests on is
`docs/reference/mesh-inventory.md`.

It is the laptop-in-a-passenger-seat case: no tower, no gateway, a radio or
two and the notes to run them. Daily use and EMCOMM are the same setup. The
way to be ready is to have used it on ordinary days, so the identity you will
need and the habits to go with it already exist.

**Where things are in this page.** Sections: 1 what Reticulum is; 2 install;
3 first run, and two laptops on one network; 4 NomadNet; 5 the internet; 6 LoRa
with an RNode; 7 a packet TNC; 8 a shell on another laptop; 9 a propagation
node; 10 EMCOMM notes; 11 Meshtastic beside it; 12 removing it. The page ends
with what has and has not been measured.

The examples use `N0CALL` for a callsign and `FN31pr` for a grid square, and
a made-up hash where a destination hash goes. Put your own in their place.

---

## What you need first

- **An ordinary account, not root.** Everything here runs as you and keeps
  its files in your home directory.
- **A network connection at install time**, and only then, for the local
  network case. The Reticulum programs come from PyPI against pinned hashes.
- **A second machine on the same network**, for the two-laptop test in
  section 3. One machine can run every command here, but nothing is
  worth messaging until there is somebody on the other end.
- **For radio**, an RNode (section 6) or a packet modem (section 7), and
  membership of the `dialout` group: `docs/hardware/rnode.md` and the
  [serial permission entry](../troubleshooting/running.md#dialout) say how.
- **About 70 MB of disk** for the three Reticulum environments, plus the
  Meshtastic packages. `meshtasticd` pulls about 150 packages on a bare machine
  (most are the SDL and graphics libraries it links) and a third-party
  repository you are asked to trust (section 2).

The profile is marked post-1.0: it is built and its pieces are tested, and no
LoRa link has been run through it. [What is measured, and what is
not](#13-what-is-measured-and-what-is-not) lists exactly what that means.

---

## 1. What Reticulum is

Reticulum is not a service you subscribe to and not one global network you
join. Upstream's manual puts it as "a networking stack; a toolkit for building
communications systems that align with your specific values, requirements, and
operational environment" (the manual for 1.5.5, read on 2026-10-03). Three
things make it different from the networks an operator already knows:

- **Addresses are keys.** A destination is the hash of a public key, 32
  hexadecimal characters such as `c89b4da064bf66d280f0e4d8abfd9806` (the
  example hash from upstream's manual). There is no registry, no callsign
  lookup and nobody to ask for one.
- **Every packet is encrypted**, between the two ends, whatever carries it.
  Section 6 says why that matters on an amateur band.
- **Any medium that can carry a few hundred bytes will do.** The interface
  types in the installed version (listed from `RNS/Interfaces/`, 2026-10-03)
  include `AutoInterface` (a local network with no configuration),
  `TCPClientInterface` and `TCPServerInterface` and `BackboneInterface` (the
  internet or a private network), `RNodeInterface` (LoRa), `KISSInterface` and
  `AX25KISSInterface` (a packet modem), `SerialInterface`, `UDPInterface`,
  `I2PInterface`, `PipeInterface`, `WeaveInterface` and `LocalInterface`.
  One instance can run several at once and route between them.

**What it is not.** It is not Meshtastic: a Meshtastic node and a Reticulum
node do not exchange messages, and a LoRa board runs one firmware or the
other. It is not Winlink or APRS: those are station-to-station on amateur
bands under callsigns, and a Reticulum address is a key. They coexist on one
laptop, and a KISS modem such as Direwolf, which the `packet` profile
installs, can carry Reticulum as well (section 7).

---

## 2. Install

Look before you install:

```
hammunition install mesh --dry-run
```

Among the rest of the plan, the Reticulum programs print their licence on the
line that installs them. For `rns` it reads, in part:

```
Install rns into its venv — every wheel verified against the manifest's sha256 pins; licence: Reticulum License (MIT plus two use restrictions; not OSI-approved) (https://github.com/markqvist/Reticulum/blob/master/LICENSE)
```

and the service the profile will write is listed under *User services*, run
as you and not as root, with the command it will run, the `systemctl --user`
commands that enable it, and a line saying it is not started now but at your
next login. It lists no listening port, because it has none (section 3).

Then install for real:

```
hammunition install mesh
```

Run it as yourself. Nothing needs root unless a Meshtastic package has to
come from the archive or a repository, and then the plan shows the `sudo` step
first.

**One question you must answer: the Meshtastic repository.** `meshtasticd`, the
Meshtastic node daemon (section 11), is in no distribution's archive. It comes
from the Meshtastic project's own repository (the openSUSE Build Service on
Debian 13, Parrot and Kali, a Launchpad PPA on Ubuntu 24.04, Linux Mint 22.3
and Ubuntu 26.04), so the plan lists a *third-party apt repository* with its
signing key's fingerprint and the two files it will write, and the install
stops until you type that fingerprint. At a terminal it asks; in a script, set
the variable the plan names (`HAMMUNITION_ACCEPT_APT_REPO_<NAME>`) to the
fingerprint itself, and note that `--yes` does not answer it (**D-040**,
**D-021**). Check the fingerprint against what Meshtastic publishes before you
accept it. If you want Reticulum and not Meshtastic's daemon, name the units
and the question never comes: `hammunition install rns lxmf nomadnet`.

**What the licence means for you.** Reticulum and LXMF are under the
Reticulum License: the MIT text plus two added conditions, which upstream's
LICENSE states (read 2026-10-03):

> The Software shall not be used in any kind of system which includes amongst
> its functions the ability to purposefully do harm to human beings.
>
> The Software shall not be used, directly or indirectly, in the creation of
> an artificial intelligence, machine learning or language model training
> dataset, including but not limited to any use that contributes to the
> training or development of such a model or algorithm.

It is not on the OSI list. Hammunition installs it from PyPI at your
direction, never mirrors or vendors it, prints the terms and does not judge
your use of them (**D-033**, **D-021**). NomadNet is a different matter: its
wheel's metadata says MIT while the licence text it ships, and the one in its
repository, is the GNU GPL v3; the shipped text governs here, so the manifest
says GPL-3.0-only.

**If you installed Reticulum with pip or pipx before.** The plan stops, naming
each command (`rnsd`, `rnstatus` and the rest) that already exists in
`~/.local/bin` and that Hammunition did not write; a pipx install leaves
symlinks there, and Hammunition never replaces or writes through one (issue
#288, **D-022**). Nothing has been changed when you see it. Remove the old
install (`pipx uninstall rns` or `python3 -m pip uninstall rns`) or move those
files aside, and run the install again. Uninstalling this unit does not bring
the old one back. Hammunition's own earlier wrappers are recognised by a
header line and replaced without a fuss.

**Where the programs went.** Each of `rns`, `lxmf` and `nomadnet` is a
per-user virtualenv under `~/.local/share/hammunition/venvs/`, with a small
wrapper for every command in `~/.local/bin`. If a new shell says `rnstatus:
command not found`, `~/.local/bin` is not on your `PATH` yet: see [A
venv-installed program is "not found"](../troubleshooting/running.md#local-bin).

On Ubuntu 24.04 and Pop!_OS 24.04 the archive has no `python3-meshtastic`
(measured 2026-10-03), so the plan defers that member by name and installs the
rest (**D-039**).

---

## 3. First run: the shared instance and two laptops

By default the first Reticulum program to start owns the interfaces and every
other one attaches to it as a client. That is a trap if the first one is
NomadNet, because when you quit it the others lose the network. The profile
installs a systemd user service, `hammunition-rnsd`, so the owner is always
the same quiet daemon. It is enabled at install and starts at your next
login. To start it now:

```
systemctl --user start hammunition-rnsd
systemctl --user status hammunition-rnsd
```

To turn it off, and stop it starting at login, run `systemctl --user disable
--now hammunition-rnsd`; `hammunition uninstall mesh` does the same and removes
the unit file. (`hammunition services` lists it as `rns` when hammunition-tray's
helper is installed.)

Then ask it what it is doing:

```
rnstatus
```

You should see one block for the shared instance and one for each interface
that is up. If it prints "Could not get RNS status" instead, see [`rnstatus`
says "Could not get RNS status"](../troubleshooting/running.md#reticulum-no-instance).

On a single machine it reads, in part (Debian 13 container, 2026-10-03):

```
 Shared Instance[rns/default]
    Status    : Up
    Serving   : 0 programs

 AutoInterface[Default Interface]
    Status    : Up
    Mode      : Full
    Peers     : 0 reachable
```

On first start `rnsd` writes `~/.reticulum/config` with its defaults, which turn
on the **AutoInterface**: link-local IPv6 and UDP, with multicast to find
peers, so two machines on one Wi-Fi network or one Ethernet segment see each
other with no routers, no DHCP and no configuration. The ports are UDP 29716
(discovery) and 42671 (data), which upstream's manual names and the installed
source's defaults confirm, and 29717, the unicast discovery port, which the
source derives as the discovery port plus one; a firewall may need to allow
them. In a Debian 13 container (2026-10-03) `ss -lun` showed UDP 29716 on a
multicast group address and UDP 29717 and 42671 on the interface's link-local
address, and no TCP listener. The engine
writes none of this file and never edits it: it is created by `rnsd`, under
your account, and it is yours. `rnsd --exampleconfig` prints the whole
annotated reference.

**What goes on the air, and how to narrow it.** No radio transmits until you
attach and configure one. On a network it is different: the AutoInterface
announces on every link-local interface from the first start, so on a network
you do not trust, limit it or turn it off before you start the service. In
`~/.reticulum/config`, inside the `[[Default Interface]]` block, `devices =
eth0` (a comma-separated list) restricts it to the interfaces you name,
`ignored_devices = wlan0` leaves some out, and `enabled = No` turns the
interface off. These names were read from the installed
`AutoInterface.py` source (`devices`, `ignored_devices`) and the
default block `rnsd --exampleconfig` prints (`enabled`), not run against a
network; restart the service after editing. The engine never edits the file.

Three facts about the shared instance, each read from the installed program:

- **It is a local socket, not a TCP port.** On Linux `rnsd` binds the abstract
  Unix sockets `@rns/default` and `@rns/default/rpc`, and no TCP port
  (`ss -xl`, measured 2026-10-03; upstream's example configuration names port
  37428 only for platforms without domain sockets). `rnstatus` names it
  `Shared Instance[rns/default]` on a default configuration.
- **It is not private to your account, and it is one per machine.** An abstract
  socket has no file permissions, so another account on the same machine can
  attach to it as a client: a second account's `hammunition-rnsd` attached to
  the first account's instance (Debian 13 container, 2026-10-03). Treat the
  machine as a single-operator one.
- **`rnsd --service` logs to `~/.reticulum/logfile`**, not to the journal, so
  `journalctl --user -u hammunition-rnsd` shows very little. Read the file.

**Two laptops.** Install the profile on both, start the service on both, join
both to one network, and run `rnstatus` on either. The AutoInterface block
shows its peers: `Peers : 1 reachable` on each (two Debian 13 containers on one
bridge network, 2026-10-03). If it stays at none, see [Two laptops do not see each other](../troubleshooting/running.md#reticulum-autointerface):
a firewall, or a network that does not pass multicast between its devices
(upstream names very cheap ISP-supplied routers as the usual cause). Sections 4
and 8 then give you something to do with the link.

**If you already run a Reticulum program of your own** (Sideband, MeshChat or a
`rnsd` you started by hand), it already owns the instance. The service will
start, find the instance taken and attach to it as a client rather than fail:
its log says "Started rnsd version 1.5.6 connected to another shared local
instance, this is probably NOT what you want!" and `rnstatus` run against its
configuration says "Could not get RNS status" (both measured 2026-10-03).
Stop one of the two: [Another Reticulum program owns the shared
instance](../troubleshooting/running.md#reticulum-another-instance).

---

## 4. Messaging with NomadNet

```
nomadnet
```

(or *NomadNet* under *LoRa Mesh* in the desktop menu, which opens a terminal).
On first run it creates `~/.nomadnetwork/`: its configuration, a `storage`
directory holding your **identity**, your conversations, the pages and files
you publish, and an `examples` directory. Your NomadNet address, the LXMF
destination other people write to, is derived from that identity, and the
program displays it (the text interface was not run for this page: section
13). Give it to the person at the other laptop; they give you theirs; each of
you can then send the other a message, and the conversation stays on your
disk.

`nomadnet --daemon` runs it with no screen, serving your pages and accepting
messages with nobody at the keyboard. Do that on purpose, not by habit.

**Keep the identity.** If `~/.nomadnetwork` is lost, you have a new address
and everyone who knew the old one has to be told. Take a copy now, and again
whenever you change anything you care about:

```
( cd ~ && tar -czf mesh-identities-$(date +%F).tar.gz $(ls -d .reticulum .nomadnetwork .lxmd .rnsh 2>/dev/null) )
chmod 600 ~/mesh-identities-$(date +%F).tar.gz
```

That archive holds private keys: keep it on media you control, not in a shared
folder. It takes whichever of the four directories exist: `~/.lxmd` appears if
you run a propagation node (section 9) and `~/.rnsh` once you use `rnsh`
(section 8).

A single laptop is enough to see NomadNet start and write its files; messaging
needs a second. Which keys do what inside the program is its own help's job,
and this page does not describe the text interface, which was not run for it
(section 13).

---

## 5. Over the internet

Reticulum's own manual is plain about how to start. It says there is no "right"
way to build a network, and calls the reliance on "a few centralized,
hard-coded entrypoints" a common mistake: a long list of public addresses
pasted from a website makes the network brittle. What it recommends
instead is to use a temporary bootstrap connection to *discover* nearby
infrastructure, with the `discover_interfaces` and
`autoconnect_discovered_interfaces` options and an interface marked
`bootstrap_only`, and it points to two directory sites for interface
definitions, `directory.rns.recipes` and `rmap.world` (the manual for 1.5.5,
read 2026-10-03). `rnstatus -d` lists the interfaces your instance has
discovered and `rnstatus -D` prints a configuration entry for each.

This guide does not carry a list of public entry points, for the manual's
reason and for ours: an address that was up when this was written is not
evidence that it is now, and the connection to a public hub is not run from
this project's CI by policy. The manual's own example of connecting to a
remote listener is:

```
[[Backbone Remote]]
  type = BackboneInterface
  enabled = yes
  remote = amsterdam.connect.reticulum.network
  target_port = 4251
```

Read that as an example of the shape, not a promise about the host. For a
listener that is not a Reticulum Backbone, upstream's TCP client stanza is:

```
[[TCP Client Interface]]
  type = TCPClientInterface
  enabled = yes
  target_host = 127.0.0.1
  target_port = 4242
```

Add a stanza under `[interfaces]` in `~/.reticulum/config`, then restart the
service so the shared instance reads it:

```
systemctl --user restart hammunition-rnsd
rnstatus
```

**Your own hub.** The way to make your laptops reachable from afar is a
machine of your own that stays up: a small server with a public address, or an
always-on machine on your LAN, such as the one that holds your [LAN
mirror](lan-mirror.md). Upstream's server stanza is:

```
[[TCP Server Interface]]
  type = TCPServerInterface
  enabled = yes
  listen_ip = 0.0.0.0
  listen_port = 4242
```

That listens on **every address the machine has**. Say so to yourself before
you enable it, put it behind the firewall rules you intend, and read what the
manual says about TCP: TCP connections "reveal the IP address of both your
instance and the server to anyone who can inspect the connection", and someone
could use that to determine your location or identity. For something private,
upstream's gateway examples take a `network_name` and a `passphrase`. The I2P
interface hides the addresses at a cost in speed and needs an I2P daemon of your
own; the manual has the details.

---

## 6. Over LoRa with an RNode

An **RNode** is not one device. It is an open design and firmware that turns a
LoRa development board into a modem Reticulum can use. Upstream's manual lists
fifteen boards the auto-installer supports: LilyGO's T-Beam, T-Beam Supreme,
T3S3, LoRa32 v1.0, v2.0 and v2.1, T-Deck and T-Echo; RAK4631-based boards;
Heltec's T114 and LoRa32 v2.0, v3.0 and v4.0; the OpenCom XL; and unsigned.io's
RNode v2.x. It says RNodes use raw LoRa modulation and have nothing to do with
LoRaWAN. That list is upstream's as read on 2026-10-03, not a claim about which
boards work here. `docs/hardware/rnode.md` has the hardware entry.

**The legal note, before you transmit.** Radio spectrum is a regulated
resource and the rules differ by country; upstream's manual says so beside
every radio interface. Two things deserve stating plainly here, as
disclosure and not as a ruling:

- Reticulum encrypts every packet. In the United States, Part 97 forbids
  messages encoded to obscure their meaning on amateur frequencies. So an
  RNode on an amateur band is not something this guide can tell you is
  lawful: that is for you to establish, for your licence class, your band and
  your country.
- Upstream's manual says RNodes most commonly use LoRa in the common ISM bands
  (868 MHz in ITU Region 1 and 915 MHz in Region 2 are the usual
  allocations). Those have their own power and duty-cycle limits, which are
  yours to look up. The `RNodeInterface` has `airtime_limit_long` and
  `airtime_limit_short` for the duty cycle, and `id_callsign` and
  `id_interval` to send identification on the channel; whether any of that
  satisfies a rule is not for this guide to say either.

**Flash the board.** `rnodeconf` is installed with `rns`. Find the board's
port, look at it, and run the installer, which asks a series of questions
about your hardware and installs and provisions the firmware (the manual's
route):

```
ls /dev/serial/by-id/
rnodeconf --info /dev/ttyACM0
rnodeconf --autoinstall
```

If `rnodeconf` cannot open the port, you are not in `dialout` yet or the
session predates it: [rnodeconf cannot open the
port](../troubleshooting/running.md#rnodeconf-port). `rnodeconf` downloads
firmware itself from upstream unless you give it `--fw-url`, or a file you
extracted yourself with `--extract` and `--use-extracted`. What it checks
about the download, and from where it fetches, were not measured; read what it
prints before you let it flash, and note that the firmware is upstream's and
not something Hammunition installs (**D-026**).

**Add the interface.** Upstream's stanza, with the manual's example values
(867.2 MHz, a 125 kHz channel) that you must replace with ones you are
allowed to use:

```
[[RNode LoRa Interface]]
  type = RNodeInterface
  enabled = yes
  port = /dev/ttyACM0
  frequency = 867200000
  bandwidth = 125000
  txpower = 7
  spreadingfactor = 8
  codingrate = 5
  # id_callsign = N0CALL
  # id_interval = 600
```

Frequency is in hertz, bandwidth in hertz, `txpower` in dBm, the spreading
factor runs 7 to 12 (7 fastest, 12 longest range) and the coding rate 5 to 8
(upstream's comments). Two RNodes talk to each other only when frequency,
bandwidth, spreading factor and coding rate all match. Restart the service
(`systemctl --user restart hammunition-rnsd`) and read `rnstatus`. An RNode
that is plugged in but not configured does nothing, and one that is configured
transmits when Reticulum has something to send.

---

## 7. Over a packet TNC

Any packet modem that speaks KISS over USB, serial or TCP can carry
Reticulum; upstream's manual names Dire Wolf among them. The `packet` profile
installs Direwolf and, from your station values, writes the
`/etc/direwolf.conf` whose `KISSPORT 8001` is the port
[Packet and Winlink](packet-winlink.md) uses. A software modem on that port is
reached with the TCP client interface in KISS mode (upstream's stanza):

```
[[Direwolf KISS]]
  type = TCPClientInterface
  enabled = yes
  kiss_framing = True
  target_host = 127.0.0.1
  target_port = 8001
  fixed_mtu = 500
```

Upstream's warning goes with it: use `kiss_framing` only to reach a modem or
similar, never between two Reticulum instances. For a hardware TNC on a serial
port the plain `KISSInterface` takes `port`, `speed`, `preamble`, `txtail`,
`persistence` and `slottime`, and can send an identifying beacon with
`id_callsign` and `id_interval`. If you want Reticulum's frames wrapped in
AX.25 and your callsign on every transmission, there is an `AX25KISSInterface`
with `callsign` and `ssid` set (`callsign = N0CALL` in the examples), though
upstream says to use it only if you need to: it adds overhead to every packet
and the plain interface with beaconing is more efficient.

The legal note in section 6 applies word for word: this is the same encrypted
traffic on the same kind of channel. One packet station is also one radio:
Direwolf and your digital-mode programs share it, so plan which one is up.

---

## 8. A shell on the other laptop: rnsh

`rnsh` gives you a shell on another machine over Reticulum, with no SSH and no
IP address between you. It is part of `rns` (the separate PyPI project of the
same name is deliberately not installed: two packages would own one command).
It works by identity: the machine you want to reach runs a listener that
answers only to identities you name.

On **your own machine**, print its identity hash. The first `rnsh` run also
creates your `rnsh` identity in `~/.rnsh/identity`, and, if there is none yet,
starts Reticulum and writes `~/.reticulum/config`:

```
rnsh -p
```

It prints a line of the form `Identity     : <c89b4da064bf66d280f0e4d8abfd9806>`
(the hash shown is the manual's example, nobody's identity; yours differs).

On **the remote machine**, name that identity and listen, announcing at
startup. Substitute the hash `rnsh -p` printed for the made-up one below:

```
rnsh -l -a c89b4da064bf66d280f0e4d8abfd9806 -b 0
```

Allowed identities can also be listed one hash per line in
`~/.config/rnsh/allowed_identities` (or `~/.rnsh/allowed_identities`).
`rnsh -l -p` on the remote machine prints its identity and a line `Listening
on : <hash>`, the **destination** hash: the address you connect to. From your
machine:

```
rnsh a1b2c3d4e5f60718293a4b5c6d7e8f90
```

(again a made-up hash: use the one the listener printed). Be exact about what
you have done: **a listener runs commands as the account that started it, for
every identity you allowed.** `-n` allows anyone and is the wrong switch to try.
`-C` stops the listener running command lines sent by the other side, leaving
only the shell or the program you named after `--`. Treat an `rnsh` listener
like an SSH daemon.

`rncp` copies files the same way (`rncp --help`) and `rnx` runs a command on a
listener (`rnx --help`), under the same identity rules.

---

## 9. A propagation node: lxmd

Reticulum delivers messages while both ends are reachable. **LXMF** adds
store-and-forward: a *propagation node* holds a message for someone who is
off, and hands it over when they are next on the mesh. A mesh that has to move
messages between laptops that are not on at the same time wants at least one,
somewhere always on.

**What running one means.** `lxmd -p` makes your machine store encrypted
messages addressed to other people, on your disk, for as long as its limits
allow, and relay them to its peers. You do not read them, and you are carrying
them. Decide that on purpose, on a machine that is meant to stay on, and not
on the laptop that goes in the bag.

`lxmd` is installed by `lxmf` and **never started by Hammunition**. It keeps
its configuration, identity and message store in `~/.lxmd`, created on first
run (measured 2026-10-03). To read the annotated configuration and to see it
run in a terminal first:

```
lxmd --exampleconfig
lxmd -p -v
```

`lxmd --status` shows the node and `lxmd --peers` its peers. To keep one
running unattended, a user service of your own does it; this one is a sketch
and was not run:

```
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/lxmd.service <<'UNIT'
[Unit]
Description=LXMF propagation node

[Service]
ExecStart=%h/.local/share/hammunition/venvs/lxmf/bin/lxmd -p -s
Restart=on-failure

[Install]
WantedBy=default.target
UNIT
systemctl --user daemon-reload
systemctl --user enable --now lxmd.service
```

The path assumes the default `XDG_DATA_HOME`. `hammunition uninstall lxmf`
does not know about a unit you wrote: disable it first with
`systemctl --user disable --now lxmd.service`.

---

## 10. EMCOMM notes

What works with nothing but two laptops and their batteries: the
AutoInterface between them, NomadNet messages and pages, `rncp` file transfer
and `rnsh`. There is no tower, no internet and no server to depend on, and
the first test of all of it is that two laptops do exactly that in a room.
Anything past the length of one Wi-Fi network needs a radio, a TNC or the
internet:

- **Distance without infrastructure** needs an RNode or a packet modem at each
  end (sections 6 and 7), and a decision about the legal note in section 6.
- **Reaching somebody elsewhere** needs the internet, or a chain of
  radio-linked machines to something that has it (section 5).
- **Messages between machines that are not on together** need a propagation
  node somewhere (section 9).

Habits worth having before the bad day:

- **Back up the identities** (section 4) and keep a copy off the laptop.
- **Keep it running with nobody logged in**, if the laptop is meant to answer
  unattended: `hammunition station set --unattended` has the power-control
  helper enable linger for your account, so your user services, this one among
  them, keep running; the plan lists what it keeps alive before the prompt, and
  `--no-unattended` turns it off (**D-073**). Not measured for this unit.
- **Write the address down.** Your NomadNet address, and those of the people
  you will need, belong on paper as well as in a program.
- **Practise with the same setup.** Once a month, two laptops, one
  `rnstatus` and one message each way; it takes five minutes and finds the
  problem while there is time.
- **Have these notes with you.** An offline, served copy of the Hammunition
  documentation is a separate unit that does not exist yet; until it does,
  print this page or save it from the site.

---

## 11. Meshtastic beside it

The profile also installs the two Meshtastic clients the catalog already
carried: `python3-meshtastic`, the command-line client and Python library, and
`gtk-meshtastic-client`, a desktop GUI. They talk to a Meshtastic node over
USB, which is a different network from Reticulum's and does not interoperate
with it. A Meshtastic node on your USB bus is `/dev/ttyACM0` or `/dev/ttyUSB0`
and needs the same `dialout` membership as an RNode.

The command-line client's `--help` (read from version 2.7.11; the version your
distribution packages may differ, and Debian 13 and Parrot carry 2.6.0) shows
`--info` to read the node's configuration, `--nodes` for the nodes it has
heard, `--sendtext` to send a message, and `--set FIELD VALUE` to change a
setting:

```
meshtastic --info
meshtastic --nodes
```

A node transmits only once its region is set: `meshtastic --set lora.region
US` is the form, with your region's code in place of `US` (the field name and
region codes are Meshtastic's, and its documentation is the authority; this
guide did not run it). The same `--help` lists `--set-ham`, "Set licensed Ham
ID and turn off encryption", which is Meshtastic's own switch for the
amateur-radio question section 6 raises.

### The node daemon: `meshtasticd`

`meshtasticd` is a different thing from the two clients above. They talk to a
node you own over USB. `meshtasticd` **is** a node, running on the computer: it
drives a LoRa radio wired to the computer's SPI and GPIO pins (a Raspberry Pi
HAT such as the RAK6421, Nebra or MeshAdv) or plugged in as a USB CH341 SPI
module (Meshstick, MeshToad, uMesh), joins the mesh, and serves the Meshtastic
API on TCP port 4403 so that `meshtastic --host ...`, the web client and the
phone apps can use it. **A T-Deck, T-Echo or RAK on your USB bus is not a case
for it**: use `meshtastic --port /dev/ttyACM0` for those. A machine with neither
a HAT nor a CH341 module can run it only in simulation, which is useful to
try the client against.

It is not in any distribution's archive, which is why section 2 asked you to
trust a repository. What the install did to the machine, each item printed in
the plan before you confirmed and read from the package itself (`dpkg-deb -I`,
`-c` and `-e` on the 2.7.26 `.deb`, never run on the maintainer's machine):

- **A repository and its key.** `/etc/apt/sources.list.d/<name>.sources` and
  `/etc/apt/keyrings/<name>.gpg`, trusted for that repository alone. On Debian
  13, Parrot and Kali the repository is the Build Service's `network:Meshtastic:beta`,
  signed by the key of the whole `network:` project ("network OBS Project",
  RSA 4096, **expires 2027-08-26**): after that date the update fails until the
  catalog pins a new key. On the Ubuntu family it is `ppa:meshtastic/beta`,
  signed by "Launchpad PPA for Meshtastic", no expiry. `beta` is Meshtastic's
  own name for the channel they recommend; there is no "stable".
- **A boot service.** `meshtasticd.service` is enabled and started by the
  package, runs as a new `meshtasticd` system user, and is restarted whenever
  it exits. With no radio configured it exits within seconds (measured), so on
  a real machine systemd restarts it and then gives up after its start limit.
- **A udev rule with a world-writable line.**
  `/usr/lib/udev/rules.d/60-meshtasticd.rules` makes every USB device with the
  pair `1a86:5512` (the WCH CH341 in SPI mode) readable and writable by **every
  account on the machine** (mode 0666), and gives SPI and GPIO devices to the
  groups `spi` and `gpio` (mode 0660). The pair is generic, not specific to a
  Meshtastic radio. To narrow it, copy the file to `/etc/udev/rules.d/`, change
  that line to `GROUP="plugdev", MODE="0660"`, and `sudo udevadm control
  --reload`.
- **A user and groups.** `meshtasticd` (user and group) and `spi` and `gpio` if
  the machine has none; the user is added to `spi`, `gpio`, `plugdev`,
  `dialout`, `i2c`, `video`, `audio` and `input` where they exist. **You are not
  added to any group.** The package owns `/etc/meshtasticd` and
  `/var/lib/meshtasticd`.
- **A network listener.** The API on TCP 4403 listens on **every interface**
  and has no login (measured with `--sim`): anyone who can reach the machine on
  that port can read and change the node. Firewall it, or do not leave the
  daemon running on a network you do not trust. The web client the daemon serves
  itself (`Webserver:` in the config, off by default) listens on 0.0.0.0:9443
  over TLS with a certificate it makes on first start, and is open the same way.

**Using it.** On a machine with a HAT, enable SPI (and the I²C bus) in the kernel (on
Raspberry Pi OS, `raspi-config`), then pick the one hardware file that matches
your board from the 62 in `/etc/meshtasticd/available.d`, link it into
`config.d`, restart the service and set the region (section 11 above, with
`--host localhost`):

```
sudo ln -s /etc/meshtasticd/available.d/<file>.yaml /etc/meshtasticd/config.d/
sudo systemctl restart meshtasticd.service
meshtastic --host localhost --set lora.region US
```

(`<file>` is yours to choose, and `US` your region's code; this guide has not
driven a radio, see section 13.) To try it with no radio, run it by hand as the
service user:

```
sudo systemctl disable --now meshtasticd.service
sudo -u meshtasticd /usr/bin/meshtasticd --sim
meshtastic --host 127.0.0.1 --info
```

and to keep it off the machine entirely, `sudo systemctl disable --now
meshtasticd.service`. Version care: the node and the client want to be near each
other, and Debian 13 and Parrot carry client 2.6.0 against daemon 2.7.26.

**Removing it.** `hammunition uninstall meshtasticd` removes the package, the
repository file and the key, and refreshes apt. It leaves `/etc/meshtasticd`
and `/var/lib/meshtasticd` (the node's identity and settings), the `meshtasticd`
user and the groups, and the boot-enablement link until the package is purged:
`sudo apt-get purge meshtasticd` clears the link and `/var/lib/meshtasticd`, and
`sudo deluser meshtasticd` the account.

MeshCore's clients are not here.

---

## 12. Removing it

```
hammunition uninstall mesh --dry-run
hammunition uninstall mesh
```

removes what the profile installed: the `hammunition-rnsd` service and its
unit file, the three virtualenvs and their wrappers, and the two Meshtastic
packages if Hammunition installed them. It **leaves your identities and
configuration where they are**, and this page says so (the uninstall output
does not list what it did not touch):

- `~/.reticulum`: your Reticulum configuration, identity and known
  destinations.
- `~/.nomadnetwork`: your NomadNet identity, conversations and pages.
- `~/.lxmd`: your propagation node's identity and message store, if you ran
  one.
- `~/.rnsh`: the identity `rnsh` made for you, if you used it.

Those were created by the programs, under your account, and are not
Hammunition's to remove: delete one and you have a new address. If you really
mean to start over, after taking the backup in section 4:

```
rm -rf ~/.reticulum ~/.nomadnetwork ~/.lxmd ~/.rnsh
```

A unit you wrote yourself in section 9 stays too; disable it first.

---

## 13. What is measured, and what is not

Measured on 2026-10-03, in two ways. First on a Parrot 7.4 machine, in a scratch
virtualenv and with a Reticulum configuration that had no interfaces, so nothing
reached a network:

- **The pins install.** `rns` 1.5.6, `lxmf` 1.2.0 and `nomadnet` 1.4.4, each
  with its hash-pinned closure, installed with `pip install --require-hashes`
  on Python 3.13.5, every hash matching, and every command this page names is
  in `bin/`.
- **The shared instance is a local socket.** `rnsd` bound the abstract Unix
  sockets `@rns/<name>` and `@rns/<name>/rpc` and no TCP port, and
  `rnstatus` read it as `Shared Instance[rns/<name>]`. `rnsd --service` wrote
  `~/.reticulum/logfile`.
- **A second `rnsd` does not fail.** It attached to the first, logged "connected
  to another shared local instance, this is probably NOT what you want!" and
  kept running; `rnstatus` against its own configuration said "Could not get
  RNS status".
- **`lxmd` and `nomadnet --daemon`** both attached to that instance and kept
  running, and created `~/.lxmd` and `~/.nomadnetwork` with the contents
  sections 4 and 9 name.
- **The options this page quotes** are those of the installed programs'
  `--help`, and the interface stanzas are upstream's manual for 1.5.5, both
  read that day.

Then in Debian 13 containers built from the repository's own target image
(`containers/Dockerfile.target`) with rootless Podman, as an ordinary account,
with `hammunition install rns lxmf nomadnet` through the engine, and `systemctl`
replaced by a stub that records what it is asked, because a container has no
systemd user manager. The single-container run used Podman's pass-through of the
host's network, so its AutoInterface really did multicast there for about a
minute; the two-container run used a private bridge:

- **The engine.** The dry run printed the licence on each venv's line and the
  *User services* block (`runs .../venvs/rns/bin/rnsd --service`, "is not started
  now: it starts at your next login"). The install exited 0 with 32 commands
  confirmed, unprivileged, and left seventeen wrappers (14 from `rns`, one each for `lxmd` and `nomadnet`, and the `nomadnet-terminal` launcher the `nomadnet` unit generates: `lxmd`, `nomadnet`,
  `nomadnet-terminal`, `rncp`, `rnid`, `rnodeconf`, `rnpath`, `rnprobe`,
  `rnsd`, `rnsh`, `rnstatus`, `rnx` and the five `rns` 1.5.6 added, `rnir`,
  `rnpkg`, `rngit`, `rngcs` and `git-remote-rns`), the three virtualenvs and
  the unit file. The stub was asked to `daemon-reload`, `enable
  hammunition-rnsd.service` and `try-restart` it, and `~/.reticulum` **did not
  exist** after the install: the engine writes none.
- **What the unit runs.** Started from the `ExecStart` line the engine wrote,
  `rnsd` created `~/.reticulum` (`config`, `interfaces`, `logfile`, `storage`)
  whose default configuration enables only the AutoInterface, and `rnstatus`
  read `Shared Instance[rns/default]` and `AutoInterface[Default Interface]`.
  `ss -xl` showed the abstract sockets `@rns/default` and `@rns/default/rpc`;
  `ss -ltn` showed no TCP listener; `ss -lun` showed UDP 29716 on a multicast
  group address and UDP 29717 and 42671 on the interface's link-local address.
- **Another account.** A second account on the same machine ran `rnstatus`
  against the first account's instance and got "Could not get RNS status"
  (exit 2), and its own `rnsd --service` attached to the first account's
  instance with the same warning in its log. What an attached client can then
  send through your interfaces was not measured. A second `rnsd` of the same
  account, with its own configuration directory, also attached and kept running
  until killed (`exit=124` under `timeout`), so the unit sets no exit status to
  refuse a restart on.
- **Two machines.** Two containers on one bridge network, each running the
  instance: `rnstatus` on both read `Peers : 1 reachable`. With
  `enable_transport = yes` and `respond_to_probes = yes` on one, its log read
  `Probe responder at <hash> active`, and `rnprobe rnstransport.probe
  <hash>` from the other printed `Valid reply` with a round trip of 1.657 ms over
  one hop. An LXMF message sent from one to the other, with a ten-line Python
  script against the installed `lxmf`, was delivered and received. An `rnsh`
  listener on one logged `Initiator identified` and `Remote ... executing:
  ['echo', 'hello-from-a']` for the other's connection; the connecting
  client's own screen was not observed (the harness has no terminal). `rnsh -p`
  and `rnsh -l -p` printed an `Identity` line and a `Listening on` line.
- **Uninstall.** `hammunition uninstall rns lxmf nomadnet` exited 0 with 28
  commands, asked the stub to `disable --now hammunition-rnsd.service`, and
  removed the unit file, the virtualenvs, the wrappers and the menu entry. It
  left `~/.reticulum`, `~/.lxmd`, `~/.nomadnetwork` and `~/.rnsh` in place.

Then `meshtasticd` on 2026-10-05 (details in the pull request for issue #308):
the engine added each target's repository in a container of its own image (Debian
13, Parrot, Kali, Ubuntu 24.04 and 26.04, Linux Mint 22.3), after the typed
fingerprint, with `Signed-By` naming the key and nothing wider; the daemon
installed; `meshtasticd --version` printed 2.7.26; a second `install` planned zero
commands; `hammunition uninstall` removed both repository files and the package
(and, on every target, left the user, the groups, `/etc/meshtasticd` and
`/var/lib/meshtasticd`). Under `--sim`, as the `meshtasticd` user, the daemon
listened on 0.0.0.0:4403 on Debian 13, Parrot, Kali, Ubuntu 26.04 and Linux Mint 22.3 and
`meshtastic --host 127.0.0.1 --info` connected and read firmware 2.7.26 on
Debian 13, Parrot, Kali and Ubuntu 26.04. Ubuntu 24.04 and Mint 22.3 have no
`python3-meshtastic`, so no client was run there, and the Ubuntu 24.04 run did
not look at the listener. With the shipped config and no radio the daemon exited 0 within a
second. With a `Webserver:` block it listened on 0.0.0.0:9443 and wrote its
certificate. The package's postinst, udev rule, conffiles and unit were read with
`dpkg-deb`; the OBS `Release.key` fingerprint was computed with the engine's own
parser and agrees with `gpg --show-keys`, and `gpg --verify` accepts the
repository's `InRelease` against it.

Not measured, and this page says so where it matters:

- **`meshtasticd` under systemd**, and with a radio: no HAT or CH341 module has
  been run, no region set on a real node, no packet sent. The containers have no
  systemd, so the boot service, its restart limit and the udev rule's effect on
  a plugged-in module were read, not observed. arm64 and armhf packages exist in
  both repositories and were not installed. The web client's pages were not
  fetched.
- **No LoRa link.** No RNode, and no Meshtastic node, has been run for this
  page. The maintainer's LoRa boards were lost in a flood; the `rnode` entry
  is recorded from upstream's board lists, as `meshtastic` was, and says
  `untested`.
- **NomadNet's text interface**, and a message sent through NomadNet's own
  screen rather than at the LXMF layer. It was started in daemon mode only;
  what it looks like and where its keys are is its own help's business.
- **Any internet link.** The TCP and Backbone examples are upstream's, and a
  connection to a public hub is not run from this project's CI by policy.
- **What another account could do through your instance**, beyond attaching to
  it, and the `rncp` file transfer, which was not run.
- **`rnodeconf`'s download.** Where it fetches firmware from and whether it
  verifies what it fetches.
- **The service under a real systemd user manager**, `lxmd` as a service, and
  propagation between two nodes.
- **arm64 and Python 3.14.** The hash sets cover every file PyPI lists, and the
  wheels exist on both architectures (the inventory's resolver run); neither
  was installed.
