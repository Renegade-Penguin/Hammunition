# Device power control

**Parking a catalogued device tells the kernel to drop its interfaces and
lets its USB port suspend.** It is the reversible equivalent of unplugging
something you are not using right now — a GPS receiver that draws power on
battery whether anything is reading it or not, a device you want off until
the next session. This page covers what it does to a machine, per CLAUDE.md's
rule that every system modification says what changes, why, how to inspect it
afterwards, and how to reverse it.

**What follows is a mix of two kinds of claim, and they are kept visibly
apart.** What the engine writes to sysfs, and reads back to confirm, is
measured — it is exercised by the test suite on every run. **One device has
also been run on hardware**: the field laptop's USB GPS receiver was parked
and woken from the tray on 2026-09-27 (bench session 10 in
`docs/reference/bench-verification-5430.md`), and what that run saw is stated
below as measured. Nothing else has: **no modem, Bluetooth controller or
camera has been parked, no park has been run from the CLI or a menu entry on
hardware, and no reboot or replug with a device kept off has been run.** For
every claim below about a device other than that GPS receiver, and for the
claims the run did not cover, treat it as an expectation from reading the
kernel's own sysfs documentation, not a measurement, the same way each
class's `power_control.note` hedges it.

## What parking is, and what it is not

Parking writes `0` to a device's own `authorized` file under
`/sys/bus/usb/devices/<address>/` and `auto` to its `power/control`. The
kernel drops every interface the device presented, the same as an ordinary
unplug — but **the sysfs node itself stays**: `authorized` is a file on the
*device*, not on any one interface, so it is still there to read and to
write back to `1`, which is the whole mechanism `wake` and
`hammunition hardware state` depend on. If the node disappeared along with
the interfaces, `state` could never report a device as parked and `wake`
would have nothing to write to — parking a device does not remove it from
the bus, it deauthorizes it on the bus.

What a parked device looks like elsewhere, measured on the GPS receiver
(session 10): **`lsusb` still lists it** — deauthorising drops its
interfaces, not its USB entry — while gpsd's `gpsdctl@ttyACM0` removed it
from the running gpsd within a second and `/dev/ttyACM0` was gone. Waking
writes `1` back to `authorized`; the kernel logged `authorized to connect`
and re-created `ttyACM0` and `/dev/gps0` within a second, and gpsd took the
receiver back with no action from the operator. How a modem, a Bluetooth
controller or a camera looks while parked is not measured.
`power/control` is left alone on wake, deliberately — restoring it to `on`
would undo a runtime power-management setting a udev rule or the operator
already owns.

It is **not** a low-power mode the device itself enters and not a driver
unload — a park is a statement about the port, not the hardware. `/sys`
itself is still the only record of whether a device *is* parked right now:
`hammunition hardware state` reads the live answer back from the bus on
every call rather than trusting a cache that could go stale (**D-056**).

**What changed since D-056 was first decided:** `park` now also records, by
default, that the operator asked for this device to *stay* off — one udev
rule per device, in a file the engine owns — so that when the device is next
added, at boot or replugged into the same port, udev parks it again instead
of it waking on its own. That is the design; neither the reboot nor the
replug has been measured on hardware yet. That record is intent, never a
second copy of the live answer: it does not override what `state` reports,
and a device authorised by hand while its rule still exists shows up as
*awake, kept* rather than the file winning the argument. `hammunition
hardware park --until-reboot NAME` skips writing the rule and gets you the
original behaviour above — parked now, and a reboot wakes it — and removes
any kept entry the device already had, so an earlier `park` does not re-park
it at boot. See "Kept off
across reboots" below, and the 2026-09-28 amendment to **D-056** for why the
design changed and what it does not yet answer.

Two things the catalog can schema-validate but that are **refused at runtime**
until hardware proves them:

- **`pci_runtime`** — the power-control method for an MHI/PCIe card such as a
  WWAN modem, as opposed to `usb_deauthorize`'s USB path. One manifest uses
  it, [`dell-dw5930e`](dell-dw5930e.md), so the gap, its reason and its route
  (the radio switch) are written down; the hardware report reads the USB bus
  only, so that entry is never offered for parking. When a card that needs it
  is bench-verified, it ships.
- **`networkmanager_autoconnect`** — a "quiet verb" meant to stop
  NetworkManager racing to reconnect a WWAN modem's interface the instant it
  reappears on wake. It ships refused because the honest implementation binds
  it to *the device's own interface*, and `Parkable` carries no interface — a
  USB GPS receiver has none to bind to in the first place. The only device
  that would actually need this verb is a WWAN modem, and that method is
  itself refused above. An earlier draft implemented it by listing *every*
  NetworkManager profile on the machine and forcing `autoconnect yes` on all
  of them on wake, including ones the operator had deliberately set to `no` —
  a park/wake cycle would silently undo an unrelated setting. That is worse
  than doing nothing, so nothing is what it does until the filter can be
  built honestly.

Neither refusal blocks anything else: a device that declares `pci_runtime` or
a non-empty `quiet` list is simply refused *with a reason* the moment you try
to park it, the same way any other unbuilt capability in this project is
carried as a documented gap rather than dropped.

## Which devices are parkable, and why the catalog decides that

A device is parkable when, and only when, its catalog entry — a device or a
device **class** — carries a `power_control` block. There is no flag that
makes an arbitrary attached device parkable and no "parkable by default":
parking a rig cable mid-QSO because it happened to match a generic USB-serial
identifier is not a thing this project wants anyone to discover by accident.
The manifest author has to state, per device, that parking is safe and what an
operator should expect while it is parked.

Four classes are parkable, all by `usb_deauthorize`:
[`gps-receiver`](gps-receiver-class.md) (the one measured on the bench),
and, added with the 2026-10-02 amendment to D-056 and **not yet run on any
device**, [`wwan-modem`](wwan-modem-class.md),
[`bluetooth-controller`](bluetooth-controller-class.md) and
[`camera`](camera-class.md). The field laptop's own entries for them are
[`dell-dw5821e`](dell-dw5821e.md),
[`intel-ax210-bluetooth`](intel-ax210-bluetooth.md) and
[`sunplus-integrated-webcam-fhd`](sunplus-integrated-webcam-fhd.md), their
identifiers read-only from `lsusb` and `udevadm` and none of them marked
maintainer-verified. The DW5930e, a PCIe/MHI card, is carried as the
documented gap: [`dell-dw5930e`](dell-dw5930e.md).

**What a park of any of the three new classes is, and is not.** A parked USB
device is *unconfigured, not unpowered at the port*: the kernel drops its
interfaces and the port may suspend, and the supply is not cut. For the
radios there is a lighter switch that detaches nothing and needs no root in the
active session: `nmcli radio wwan off` and `bluetoothctl power off`, which
the helper's planned `radio off wwan` and `radio off bluetooth` verbs and the
tray's Radios toggles wrap (hammunition-tray 0.5.0; exercised against fakes only, never against a real NetworkManager or BlueZ). A camera has no such switch, and a park of it is
the kernel's view and not a hardware privacy guarantee.

The `gps-receiver` `power_control.note` is the model for how an unmeasured
expectation is written into a manifest — it says plainly that gpsd's own
hot-unplug handling means nothing further needs quieting. Every claim about
what the new classes look like while parked is an expectation, not a
measurement, until `docs/reference/bench-verification-5430.md` records a run.

A device is only ever offered for parking while it is actually attached.
`hammunition hardware state`, the CLI verbs, and the generated menu entries
all read the USB bus fresh on every call — never a cached list — because
between one check and the next a device can be unplugged, or a USB address
reused by something else entirely.

## The two files `hardware apply` installs

Parking and waking write to sysfs as root, and everything that can ask for
that write is unprivileged: the CLI, a generated menu entry, and the Plasma
applet in `hammunition-tray`. `hammunition hardware apply` installs the one
thing that bridges the two, behind one polkit action, so there is a single
privileged path to review rather than three.

| File | Path | Mode | What it is |
|---|---|---|---|
| The helper wrapper | `/usr/local/libexec/hammunition-devctl` | `0755`, owned by root | A small POSIX shell script |
| The polkit action | `/usr/share/polkit-1/actions/com.chiefgyk3d.hammunition.devctl.policy` | `0644`, owned by root | XML, one `<action>` |

**The helper wrapper** is a fixed path under the shared prefix — polkit
annotates an *absolute executable path*, and a venv's own path changes across
an upgrade, so the wrapper is the stable thing the policy names. Its content
is short enough to read in full:

```sh
#!/bin/sh
# Installed by `hammunition hardware apply` (D-056). Do not edit: the
# polkit action at /usr/share/polkit-1/actions/com.chiefgyk3d.hammunition.devctl.policy
# authorises this exact path, and the next apply rewrites this file.
cd /
exec /path/to/your/interpreter -I -m hammunition.cli.devctl "$@"
```

The interpreter path is whichever Python ran `hardware apply` — normally the
venv's own `.venv/bin/python`, since that is the documented install
(`docs/getting-started/install.md`). It is disclosed in the plan before it is
ever written, and re-baked every time `apply` runs, so reinstalling the
engine into a new venv updates what root actually executes.

**The `-I` is load-bearing, not tidiness.** `python -m <pkg>` inserts
`os.getcwd()` at `sys.path[0]`. `pkexec` normally masks that by `chdir()`-ing
to the target user's home before it execs the authorised program — but
`pkexec --keep-cwd` does not, and the polkit action above pins an *executable
path*, not an argument list, so nothing stops a caller from adding that flag.
Without `-I`, a local user with an active session could `cd` to a directory
holding their own `src/hammunition/cli/devctl.py`, run `pkexec --keep-cwd
/usr/local/libexec/hammunition-devctl state`, authenticate with their own
password (`auth_self_keep`), and have their module imported and run as root
instead of the real one. `-I` drops `sys.path[0]`, `PYTHONPATH`, `PYTHONHOME`
and user site-packages while still resolving `hammunition` from the
interpreter's own venv, so the hijack import fails instead of succeeding. The
`cd /` above it is defence in depth on top of that.

**The polkit action** authorises exactly that one path, for exactly the
`com.chiefgyk3d.hammunition.devctl` action id, nothing wider:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE policyconfig PUBLIC
 "-//freedesktop//DTD PolicyKit Policy Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/PolicyKit/1.0/policyconfig.dtd">
<policyconfig>
  <vendor>Hammunition</vendor>
  <vendor_url>https://github.com/Renegade-Penguin/Hammunition</vendor_url>
  <action id="com.chiefgyk3d.hammunition.devctl">
    <description>Park or wake a radio device, set the clock's time source, control a system service Hammunition manages, or keep your services running after you log out</description>
    <message>Authentication is required to change a radio device's power state, the clock's time source, a system service Hammunition manages, or whether your services keep running after you log out</message>
    <icon_name>preferences-system-power</icon_name>
    <defaults>
      <allow_any>auth_admin</allow_any>
      <allow_inactive>auth_admin</allow_inactive>
      <allow_active>auth_self_keep</allow_active>
    </defaults>
    <annotate key="org.freedesktop.policykit.exec.path">/usr/local/libexec/hammunition-devctl</annotate>
    <annotate key="org.freedesktop.policykit.exec.allow_gui">true</annotate>
  </action>
</policyconfig>
```

The same action authorises `hammunition-devctl time mode` (D-058), the `linger`
verb and, since the helper moved to hammunition-tray, `services` changes to a
system service; the wording above is hammunition-tray's, byte for byte, and the
next `hammunition hardware apply` reinstalls it where no tray helper answers.

Parking a GPS receiver also turns GPS time off, whatever the time mode: ntpd
stops hearing the receiver and follows the network, or holds over, until it
is woken, with no configuration rewritten (inferred from ntpd's reachability
rules; not yet watched on the bench). See `docs/guides/gps-time.md`.

`allow_active=auth_self_keep` means an active local session authenticates
once and stays authorised for a few minutes afterwards — polkit's own manual
page documents the interval as "a brief period (e.g. five minutes)" without
committing to an exact number, so read it as "a few minutes", not as "the
session". A park followed by a wake a few minutes later is one password; one
at breakfast and the next at lunch is two. It is still the shape a tray
switch needs, because one that demands a password on *every* flip is a
switch nobody uses — it just does not remove the prompt for good. A remote
or inactive session (SSH, a login on another virtual terminal) always needs
`auth_admin`: parking someone else's device over SSH is not a thing a single
password prompt should make easy.

`hardware apply` is idempotent here exactly as it is for the udev rules: if
both files already match what it would write, it reports that and does
nothing. Before writing either one, it checks who could tamper with what
root is about to run — the interpreter path and the `hammunition` package
directory it imports from, both as given and resolved through any symlink.
Two things make it refuse outright, before anything is written: either
component being writable by more than its own owner (world-writable, or
group-writable by a group someone else can hold, so another account could
then replace what root runs), or either
component failing to `stat` at all, which is treated as unsafe rather than
assumed safe. A component that is merely owned by one non-root account — the
ordinary shape of a venv under `$HOME` — is not refused; `apply` discloses it
and asks `Proceed? [yes/no]:` once, a question `--yes` cannot answer. Group
write by your own user-private group — the group named after you that nobody
else is in, which a `0002` umask makes the default on Parrot 7 — counts as
owner-only and gets the same question. See **D-056** and its 2026-09-27
amendments for the reasoning behind the distinction.

## The verbs and their exit codes

### `hammunition hardware park NAME [--until-reboot] [--dry-run]`

Detaches the named device and lets its port suspend, and — by default —
keeps it parked across reboots (see "Kept off across reboots" below).
`NAME` is the catalog name (`gps-receiver`), or `NAME@ADDRESS`
(`gps-receiver@1-4`) when two of the same kind are attached and the plain
name would be a guess. `--until-reboot` parks the device without adding the
kept entry, and removes the device's kept entry if an earlier `park` wrote
one, so a reboot alone wakes it. `--dry-run` prints the writes,
whether a kept entry is added, and the `pkexec` call it would make, then
stops — nothing is executed.

| Exit code | Meaning |
|---|---|
| 0 | Parked and verified, or a `--dry-run` that printed the plan |
| 1 | The helper ran but a write did not verify, or the command otherwise failed to run |
| 2 | Unplannable: the helper is not installed, `pkexec` is not on `PATH`, or `NAME` does not resolve to a parkable attached device |
| 3 | The authentication prompt was declined or denied; nothing was changed |

### `hammunition hardware wake NAME [--dry-run]`

The reverse of `park`, same `NAME` syntax, same flags, same exit codes. Also
removes the device's kept-off entry, if it has one, so a reboot after `wake`
does not park it again. `wake NAME@ADDRESS` also works for a device that is
not currently attached, to clear a stale kept entry for something already
unplugged and put away — see "Kept off across reboots" below. A device
parked with `--until-reboot` has no kept entry to remove; a reboot alone
already wakes it.

### `hammunition hardware state`

Read-only, needs no privilege: lists every catalogued device that is both
attached now and parkable, and whether each one is parked, plus any device
kept parked whose entry names a port nothing is attached to right now.
Always exits `0` — it is a report, and an empty report ("no parkable device
is attached") is not a failure.

### `hammunition hardware unapply [--dry-run] [--yes] [--user NAME]`

Removes the helper wrapper and the polkit action, and — since **D-056**'s
2026-09-28 amendment — the kept-off rules file too, if it exists. It is not
part of `uninstall` and is invoked separately for a reason recorded in
**D-056**: `uninstall` resolves the names it is given against the package
and profile catalogs, and there is no unit named `hardware` to give it.

`unapply` removes exactly what the transaction log records `hardware apply`
put there for the given operator — never a path it merely expects to exist,
and never anything the log names that is not the helper or the policy path,
even if a hand-edited log claimed otherwise — plus the kept-off rules file,
which is not something `apply` installs but `park` writes; removing it
reloads udev, so every device it was holding parked wakes from the next boot
on. **It deliberately never touches the device-access udev rules file.**
Those rules (`65-hammunition.rules`) are declarative, harmless for a device
that is not attached, and removing them would take away device access you
are still using — power control is the reversible part of this feature;
device permissions are not.

It also takes back GeoClue's two files for the GPS tether's socket
(**D-069**, written by `hardware apply` where GeoClue is installed), by
content: each file only when it starts with Hammunition's header, then the
socket, `rmdir /run/hammunition-gps` and `systemctl try-restart geoclue`.
`docs/guides/offline-navigation.md` §17 describes them.

| Exit code | Meaning |
|---|---|
| 0 | Removed and verified, nothing recorded to remove, everything already absent, a `--dry-run`, or the operator declined the confirmation prompt |
| 1 | The operator could not be determined, a removal command failed, or a file the run tried to remove is still present afterwards |

## Kept off across reboots

By default, `park` does more than the sysfs write above: it also adds two
lines to `/etc/udev/rules.d/66-hammunition-kept.rules` — a `# kept: NAME`
comment, then a rule naming the device's exact port and its vendor/product
pair. udev applies that rule when the device is added — at boot, and on an
unplug and replug into the *same* port — so udev itself writes
`authorized=0`, with nothing of Hammunition's running to do it. A
suspend/resume is not claimed: a resume is normally not a udev `add` event,
so the rule has no reason to fire then, and nothing here has measured it.
Nor has the reboot been measured yet. `hammunition hardware park
--until-reboot NAME` skips that: the device parks now, exactly as described
above, any kept entry an earlier `park` wrote for it is removed, and a reboot
wakes it, because nothing is left to reapply.

Whether the rule actually beats every consumer to the device — whether a tty
node like `/dev/ttyACM0` never appears at all across a reboot, or appears
briefly before the rule reasserts `authorized=0` — has not been measured
against real hardware yet; that is `docs/reference/bench-verification-5430.md`'s
job (this design's Task 7), not a claim this page makes ahead of it. See the
2026-09-28 amendment to **D-056**.

**Inspecting it.**

```
$ cat /etc/udev/rules.d/66-hammunition-kept.rules
# Written by hammunition-devctl (D-056): devices kept parked across reboots.
# Change it with `hammunition hardware park` and `wake`, not by hand.
# kept: gps-receiver
ACTION=="add", SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", KERNEL=="3-5.1", ATTR{idVendor}=="1546", ATTR{idProduct}=="01a9", ATTR{authorized}="0"
```

`hammunition hardware state` reports the same thing alongside the live sysfs
answer — a `kept` field next to `parked` — including a kept device that is
currently unplugged, shown with `attached: false`.

**Removing one.** `hammunition hardware wake NAME` removes the kept entry for
an attached device and wakes it in the same step. `wake NAME@ADDRESS` also
works for a kept device that is **not** attached — resolved from the port and
address the rules file itself names — so a stale line for something already
unplugged and put away can be cleared without plugging it back in first.

**Removing all of them.** `hammunition hardware unapply` deletes
`66-hammunition-kept.rules` along with the power-control helper and its
polkit action, and reloads udev, so every device it was holding parked wakes
from the next boot on (see above). The device-access rules file
(`65-hammunition.rules`) is untouched, as always.

**Moving a kept device to another port brings it back on.** The rule names
the port (`KERNEL=="3-5.1"`) and the model together; the same device plugged
into a different port is, as far as the rule is concerned, a line that does
not exist yet, so it comes up awake there. This errs toward not losing access
to a device over a cable swap, at the cost of the kept state not following
the device — re-park it in its new port if you still want it off, and clear
the old line with `wake NAME@<old-address>`.

**A line the engine did not write makes it refuse the whole file.** The file
is rewritten whole on every change, never appended to. If it finds a line
that is not exactly a `# kept: NAME` comment followed by the fixed rule shape
`park`/`wake` generate, it refuses to touch the file at all, naming the line
and the file, rather than discard whatever put that line there. Move the
foreign line to a file of its own and try again.

## The helper is moving to hammunition-tray (D-056, amended 2026-10-02)

The helper's code is leaving this engine for
[`hammunition-tray`](https://github.com/Renegade-Penguin/hammunition-tray), which
owns everything device-shaped. Three things follow, all already in this
engine:

- **Two more root files.** The helper no longer imports the engine's catalog:
  `hardware apply` writes `/etc/hammunition/devctl-devices.yaml` (the devices
  with a `power_control` block) and `/etc/hammunition/devctl-services.yaml`
  (`gpsd`, `time`, `gps-resume`), root-owned `0644`, disclosed whole in the
  plan, read back afterwards and removed by `unapply` by header. Their shapes,
  and why they exist, are in [the helper's lists](../reference/devctl-lists.md).
  Inspect them with `cat`, and what the helper makes of them with
  `hammunition services` and `hammunition hardware state`.
- **A hand-over.** Where the helper installed at
  `/usr/local/libexec/hammunition-devctl` answers `--version`, it is the
  tray's, and `hardware apply` writes neither its wrapper nor an existing
  polkit action, and `hardware unapply` leaves both. Where nothing answers,
  the engine's own copy, described above, is still written. It is removed in a
  later release.
- **The tray units install it.** `hammunition install hammunition-tray` (or
  `hammunition-tray-qt`) pins hammunition-tray v0.5.0's source archive, which
  carries the helper and publishes no `.deb` yet, and installs the helper from
  it, as root, exactly as that repository's `install.sh --helper-only
  --interpreter` does: the code under `/usr/local/lib/hammunition-devctl`
  (copied; root never runs the unpacked tree), the wrapper at
  `/usr/local/libexec/hammunition-devctl` running the engine's own venv Python
  as root, and the polkit action. The plan prints every one of those files and
  the interpreter before anything is written; it asks one `yes` that `--yes`
  cannot answer when that venv belongs to a single account, and refuses when
  any account can write it. A helper already answering `--version` (the
  `hammunition-devctl` `.deb`, the tray's own `install.sh`) is left alone and the
  plan says whose it is; one this engine installed earlier is refreshed to this
  pin; a file a package owns is never written over (the plan refuses and names
  the package, so a machine with the 0.4.0 `.deb` runs `sudo apt-get remove
  hammunition-tray` once first). Check it afterwards with
  `/usr/local/libexec/hammunition-devctl --version`, which prints
  `hammunition-devctl contract 1`. `hammunition uninstall hammunition-tray`
  removes the helper only if this engine installed it and no other tray unit is
  still installed. Not yet measured: an install as root on a real machine.

## How to inspect it afterwards

- **`cat /sys/bus/usb/devices/<address>/authorized`** — the ground truth for
  one device: `0` means parked, `1` means not. This is the exact file
  `park`/`wake` write and read back to confirm their own effect (D-031), so
  in the moment either command ran it agrees with what it reported — asked
  again later, the address may now name a different device entirely if
  something was unplugged and replugged in between, which is exactly why
  the verbs re-resolve `NAME` fresh from the bus on every call rather than
  trusting an address from a previous run.
- **`hammunition hardware state`** — the read-only, no-privilege summary of
  what is parkable and what is parked right now, reading the same file above
  for every catalogued, attached device in one pass.
- **`pkaction --action-id com.chiefgyk3d.hammunition.devctl --verbose`** —
  prints the polkit action as the system currently sees it: the
  `allow_active`/`allow_inactive`/`allow_any` defaults above, and confirms
  the action is actually registered (an `apply` that was never run, or one
  whose policy file failed verification, leaves this command reporting
  nothing for that action id).
- **`lsusb`** — a parked device is **still listed** (measured on the GPS
  receiver, bench session 10: deauthorising drops its interfaces, not its USB
  entry), so `lsusb` cannot tell you whether something is parked. Read
  `authorized` for that, the file the engine itself trusts; the missing
  `/dev/ttyACM0` or `/dev/gps0` is the visible sign for a serial device.

## How to reverse it

- **`hammunition hardware wake NAME`** brings one parked device back, and
  removes its kept entry (if any) so a later reboot does not park it again.
- **A reboot** wakes a device parked with `--until-reboot`. A device kept
  parked (the default since D-056's 2026-09-28 amendment) is meant to come
  back parked instead — that is the point of keeping it, and it has not yet
  been measured on hardware — so `wake NAME` or
  `hammunition hardware unapply` first if you want everything awake before
  rebooting. See "Kept off across reboots" above.
- **`hammunition hardware unapply`** removes the helper and the polkit action
  themselves, and also deletes the kept-off rules file so every device it was
  holding parked wakes from the next boot on. No CLI verb, menu entry or tray
  switch can park or wake anything on this machine until `hammunition
  hardware apply` reinstalls the helper. It does not wake a device that is
  currently parked in sysfs right now — do that with `wake` or a reboot
  first if you want a clean state immediately. It also removes the GPS
  receiver's resume step ("After suspend" below), and the helper's two lists
  (above). A helper that has become hammunition-tray's is left alone, with its polkit action, and the run names the logged paths it left.

## Removing the authentication prompt for the active session (optional, never installed by us)

`allow_active=auth_self_keep` already means one password covers a park and a
wake done a few minutes apart, but the grant lapses well before a session
does — a flip at breakfast and another at lunch are two separate prompts. If
even the recurring prompt is unwelcome — for a single-user field laptop
where the active session *is* the operator — polkit supports a local
authorization rule that grants the action to the active session with no
prompt at all, for as long as that session stays active. **Hammunition never
installs this file.** It is root-owned
policy that widens what an unattended process can do without a password, and
this project's own rule is that a security posture like that is the
operator's decision alone, made in full, not something an install script
nudges toward with a default. If you want it, create it yourself:

```js
// /etc/polkit-1/rules.d/49-hammunition-devctl.rules
polkit.addRule(function(action, subject) {
    if (action.id == "com.chiefgyk3d.hammunition.devctl" &&
        subject.active && subject.local) {
        return polkit.Result.YES;
    }
});
```

Save it at `/etc/polkit-1/rules.d/49-hammunition-devctl.rules`, mode `0644`,
owned by root; `polkitd` picks up rules files automatically, no reload
command needed. `subject.active && subject.local` is the same pair of
conditions the shipped action's `auth_self_keep` already narrows to a local,
active session — this rule only removes the password on top of that, it does
not widen who qualifies. Deleting the file (or reverting to the packaged
default, since nothing under `/etc/polkit-1/rules.d/` is ours to manage) puts
the prompt back.

## After suspend: a modem whose SIM wedges (issue #427)

**The symptom.** After a suspend or a boot the Quectel EM160R-GL looks dead:
NetworkManager shows `cdc-wdm0` unavailable and ModemManager reports the modem
`failed: sim-missing` ("Illegal SIM/USIM application state"), though the SIM is fine
(`AT+CPIN?` READY, QMI card state `present`). The USIM application is in QMI state
`illegal`, and ModemManager never retries. Measured on the field laptop, 2026-10-26 and
2026-10-27; the recovery sequence below worked by hand.

**What `hardware apply` installs for it.** The `quectel-em160r-gl` entry asks for
`resume: {step: modem_sim_recover}`, and where `qmicli` is installed (`libqmi-utils`)
apply adds two root-owned files, printed whole in the plan:
`/usr/local/libexec/hammunition-modem-resume` (0755) and
`/etc/systemd/system/hammunition-modem-resume.service`, run after each sleep target and
at boot. It reads the card status; if the USIM application is `illegal` it makes **one**
recovery attempt (stop ModemManager, `quectel-usbnet sim-recover` if installed, else
`qmicli --uim-sim-power-off=1` then `--uim-sim-power-on=1`, wait up to 15 s for `ready`,
start ModemManager) and logs the steps it ran. It exits non-zero and prints the manual
steps if the application does not come back. It never parks or wakes anything and does
nothing when the modem is parked. No polkit action or helper verb is added: systemd runs
it as root.

**Seeing it.** `hammunition hardware state` prints "SIM wedged" while the wedge stands
(read from `/run/hammunition/modem-sim.state`, because the node needs root).
`journalctl -u hammunition-modem-resume` or `/run/hammunition/modem-resume.log` has the
run. To run it again by hand: `sudo systemctl start hammunition-modem-resume.service`.
`hardware apply --no-modem-resume` leaves it out and `hardware unapply` removes it.

**Not measured:** the unit's run across a real suspend or boot, and a `wake` from the
CLI recovering the SIM (it does not: the helper has no verb for it, see D-058's
2026-10-27 amendment).

## After suspend

**The symptom.** The laptop sleeps and wakes, and the GPS has no fix:
`cgps` or `xgps` shows satellites stop updating or never come back, and
the map tether shows no position. Parking and waking the receiver brings it
back.

**Why** (issue #177, measured read-only on the field laptop, 2026-10-01).
Across 19 suspends in one boot the USB receiver was never re-enumerated: the
same USB device number throughout, and its `connected_duration` equal to the
time the machine was awake. Nothing unplugs it, so gpsd's USB hot-plug
(`USBAUTO`, `gpsdctl@`) never fires, and gpsd can keep a tty that has gone
quiet. Every recovery that worked gave gpsd a fresh open of the receiver.
The fault needs something watching across the suspend: `cgps`, `xgps`, the
map tether, or gpsd running with `-n` for [GPS time](../guides/gps-time.md).
Without a watcher, gpsd closes the receiver before the sleep and opens it
fresh afterwards. Power control plays no part: the kept-off rule was not
present and nothing writes `power/control` for this device.

**What `hardware apply` installs for it.** The `gps-receiver` class asks for
a resume step (`resume: {step: gpsd_reopen}`), and where gpsd is installed
`hammunition hardware apply` adds three root-owned files, each printed whole
in the plan before anything runs:

- `/usr/local/libexec/hammunition-gps-resume`, mode `0755`, a short Python
  script (standard library only, run by `/usr/bin/python3 -I`, which gpsd's
  own package depends on). It logs one line per action:
  1. no `/dev/gpsN` (gpsd's own udev rule makes them): nothing. A parked or
     unplugged receiver is never woken;
  2. no gpsd control socket (`/run/gpsd.sock`): nothing, because
     `gpsdctl add` would otherwise start a gpsd of its own outside systemd;
  3. for each receiver, `gpsdctl remove` then `gpsdctl add`, by the path
     gpsd reports for it (the tty, as `gpsdctl@` registers it, or
     `/dev/gpsN` where gpsd was configured with that name);
  4. `?DEVICES;` to gpsd on `127.0.0.1:2947`, two seconds at most. No
     device or no answer: `systemctl try-restart gpsd.service`, which
     restarts gpsd only if it is running. Clients then have to reconnect;
  5. a data check: gpsd's socket is watched for up to 20 s for a `SKY` or
     `TPV` report from the receiver (or any report with `mode` 1 or more).
     Satellites without a fix are alive; silence is the fault;
  6. only on silence, **one USB power cycle**. The script walks up from
     `/sys/class/tty/<tty>/device` to the first directory with
     `idVendor`/`idProduct` whose child is the tty's own interface (a hub
     is never taken for the receiver), checks the path lexically as
     `hammunition hardware park` does, writes `0` to its `authorized`,
     waits three seconds, writes `1`, waits for the tty to return, runs
     `gpsdctl add` if gpsd does not list it within 5 s, and repeats the
     data check once. The receiver loses its warm start: a 3D fix returned
     74 s after a wake on the bench. With no `authorized` file it restarts
     gpsd instead. It never loops.

  The unit succeeds only when data was seen. Otherwise it fails and the last
  journal line names the manual steps (`systemctl status
  hammunition-gps-resume`). Read it all with `sudo journalctl -b -u
  hammunition-gps-resume.service -u gpsd.service --since "-1h"`.
- `/etc/systemd/system/hammunition-gps-resume.service`, a oneshot ordered
  `After=` and `WantedBy=` `suspend.target`, `hibernate.target`,
  `hybrid-sleep.target` and `suspend-then-hibernate.target`. It is enabled,
  never started: it runs after each resume and at no other time.
- `/etc/tmpfiles.d/hammunition-gps-resume.conf`, mode `0644`, one line making
  `/run/hammunition` (`0755`, root); `systemd-tmpfiles --create` makes it at
  apply time. The script writes the lines of its last run to
  `/run/hammunition/gps-resume.log` (`0644`, replaced each run, gone at boot),
  so you can read them without `systemd-journal`:
  `cat /run/hammunition/gps-resume.log`, or
  `hammunition hardware gps-resume-report`, which also checks the installed
  files, the unit's last result and whether gpsd delivers data now. Both are
  read-only.

A file at any of the three paths that does not start with Hammunition's header refuses
the plan, and is never overwritten. `hammunition hardware apply
--no-gps-resume` leaves the step out.

**When the step fails.** The step power-cycles a silent receiver once
itself, through the same `authorized` switch as `park` and `wake`. If it
still fails, restart gpsd (session 12's measured recovery, a fix within
1 s), and if that does not do it, park and wake the receiver by hand (a 3D
fix took 74 s from a wake in bench session 10):

```
sudo systemctl restart gpsd.socket gpsd
hammunition hardware park gps-receiver
hammunition hardware wake gps-receiver
```

**Inspect it.** `systemctl cat hammunition-gps-resume` shows the unit,
`systemctl status hammunition-gps-resume` the last run, and
`journalctl -u hammunition-gps-resume` every run's lines (reading the
system journal may need `sudo` or membership of `systemd-journal`).
`hammunition doctor` reports whether the step is installed when a GPS
receiver is attached and gpsd is installed.

**Reverse it.** `hammunition hardware unapply` runs `systemctl disable` on
the unit and removes the files (and the log and its directory when empty), each only when it starts with Hammunition's
header, then reloads systemd and checks that the files and the four
`.wants` links are gone.

**Not yet measured.** Whether `gpsdctl remove` and `add` bring the fix back
after a real suspend is what the bench steps on issue #177 measure. If they
do not, a gpsd that still lists the receiver is not restarted
automatically, and the manual steps above are what recovers it. Until the
bench steps are run this is the design the measurement points to, not a
measured recovery.

## The tray applet and its Controls panel

A Plasma system-tray applet, and for Xfce, LXQt, LXDE, MATE and Cinnamon a Qt
tray icon, live in a separate repository,
[`hammunition-tray`](https://github.com/Renegade-Penguin/hammunition-tray), not in
this one. Each is a client of the engine exactly as the CLI and the generated
menu entries are: it calls the same `pkexec
/usr/local/libexec/hammunition-devctl park|wake|state` through the same one
polkit action. Since its 0.5.0 it also has a **Controls panel** with three
groups: the devices on this page, the services the helper controls, and the
machine's radios. The `hammunition-tray` and `hammunition-tray-qt` units
install the applet or tray and the helper together. How to use the panel, and
which switch asks for a password, is
[the tray's Controls panel](../guides/tray-controls.md); the same switches as
commands are `hammunition hardware park` and `wake` here and
`hammunition services` in the [command reference](../reference/cli.md).

## Troubleshooting

**`pkexec` exiting 126 or 127 does not only mean the dialog was dismissed.**
`hammunition hardware park`/`wake` report "the authentication prompt was
dismissed; nothing was changed" for any `pkexec` exit of 126 or 127. Per
`pkexec`'s own manual page, that pair of codes is not only "you clicked
Cancel": 126 is specifically the dialog being dismissed, and 127 covers
every other way authorisation did not happen.

**The likeliest 127 on a real ham's machine is not a denial at all — it is
no authentication agent being available to show a dialog in the first
place.** That is the ordinary state of a bare SSH session or a plain TTY
with no desktop session behind it, and it is the first thing anyone running
this headless will hit. `pkexec` falls back to registering its own textual
agent only when nothing else offers one, and on some setups that fallback
still isn't enough. If `park`/`wake` reports the prompt was "dismissed" and
you were never on a graphical session (or a terminal a graphical session's
own polkit agent is attached to) to dismiss anything, install `policykit-1`
— the same package `hammunition hardware park`/`wake` already names in
their own error message when `pkexec` is missing outright — and make sure a
polkit authentication agent is actually running for that session.

127 also covers the calling process being outright denied (a policy
decision, not merely "not yet given") and authentication failing, and folds
in **any other error**, which is the bucket a target that exists but is not
marked executable falls into — the state `/usr/local/libexec/hammunition-devctl`
would be in if its permissions were altered by hand after `apply` wrote it
`0755`. Nothing is written to the device in any of these cases, so this is a
message-accuracy issue rather than a correctness one — but if the dialog
never appeared at all and an authentication agent genuinely is running,
check `ls -l /usr/local/libexec/hammunition-devctl` before assuming you
dismissed a prompt you never saw.
