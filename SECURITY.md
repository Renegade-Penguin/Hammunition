# Security Policy

## Supported versions

The latest tagged release is supported (currently v0.22.0). Fixes land on
`main` and ship in the next tag; older tags are not patched.

## Reporting a vulnerability

Please report security issues **privately**, not in a public issue or pull
request. Use GitHub's private vulnerability reporting on this repository:
[Report a vulnerability](https://github.com/Renegade-Penguin/Hammunition/security/advisories/new).

Please include the affected command, manifest or file, what you expected and
what happened, and the steps to reproduce it. Do not include a callsign, grid
square, hostname or any other station detail you want kept private.

## What is in scope

- The engine under `src/hammunition/`: the CLI, the installer backends, the
  consent gates, the privileged helper and the transaction log.
- The catalog's pins: a checksum, key fingerprint or git ref in `catalog/` that
  does not verify what it claims to, or a manifest that causes the engine to
  fetch or run something unverified.
- The configuration the engine generates: udev rules, polkit actions, sudoers
  or systemd units, and files written under `/etc` or a user's home.

Vulnerabilities in the upstream software the catalog installs belong with that
project; tell us if a pin should move because of one.

## What to expect

Hammunition has a sole maintainer. Expect an acknowledgement within a week and a
fix or a written assessment as soon as practical after that. There is no bug
bounty. Reporters are credited in the changelog entry for the fix unless they
ask not to be.
