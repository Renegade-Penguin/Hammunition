# SPDX-FileCopyrightText: Copyright (C) 2026 Renegade Penguin LLC
# SPDX-License-Identifier: GPL-3.0-or-later

import base64
import struct
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class KeyStrength:
    algorithm: str
    bits: int
    hardware_by_type: bool
    rank: int
    weak: bool
    warning: str | None
    fingerprint: str


def classify(public_key_line: str) -> KeyStrength:
    parts = public_key_line.strip().split()
    if len(parts) < 2 or any(c in public_key_line for c in "\r\n\x00"):
        raise ValueError("public_key must be one OpenSSH public key line")
    algorithm = parts[0]
    ed = {"ssh-ed25519", "sk-ssh-ed25519@openssh.com"}
    ec = {
        "ecdsa-sha2-nistp256",
        "ecdsa-sha2-nistp384",
        "ecdsa-sha2-nistp521",
        "sk-ecdsa-sha2-nistp256@openssh.com",
    }
    if algorithm not in ed | ec | {"ssh-rsa"}:
        raise ValueError(f"public_key algorithm {algorithm!r} is refused (DSA is unsupported)")
    try:
        blob = base64.b64decode(parts[1], validate=True)
        size = struct.unpack(">I", blob[:4])[0]
        embedded = blob[4 : 4 + size].decode("ascii")
    except (ValueError, struct.error, UnicodeError) as exc:
        raise ValueError("public_key is not an OpenSSH key blob") from exc
    if embedded != algorithm:
        raise ValueError("public_key algorithm does not match its encoded key type")
    try:
        result = subprocess.run(
            ["ssh-keygen", "-l", "-E", "sha256", "-f", "/dev/stdin"],
            input=(public_key_line.strip() + "\n").encode(),
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("ssh-keygen could not classify public_key; install OpenSSH 8.2+") from exc
    if result.returncode:
        if algorithm == "ssh-rsa":
            # RSA's wire fields are type, exponent, modulus. Only diagnose
            # OpenSSH's size floor when the encoded modulus proves it.
            offset = 4 + size
            try:
                exponent_size = struct.unpack(">I", blob[offset : offset + 4])[0]
                offset += 4 + exponent_size
                modulus_size = struct.unpack(">I", blob[offset : offset + 4])[0]
                modulus = blob[offset + 4 : offset + 4 + modulus_size]
                if (
                    len(modulus) == modulus_size
                    and 0 < int.from_bytes(modulus, "big").bit_length() < 1024
                ):
                    raise ValueError("public_key is invalid; OpenSSH refuses RSA under 1024 bits")
            except struct.error:
                pass
        diagnostics = result.stderr.decode("utf-8", errors="replace").splitlines()
        reason = diagnostics[0] if diagnostics else "no diagnostic from ssh-keygen"
        raise ValueError(f"ssh-keygen could not read the key: {reason}")
    fields = result.stdout.decode().split()
    if len(fields) < 2 or not fields[0].isdigit() or not fields[1].startswith("SHA256:"):
        raise ValueError("ssh-keygen returned no public_key size/fingerprint")
    bits = int(fields[0])
    rank = (
        1
        if algorithm in ed
        else 2
        if algorithm in ec
        else 3
        if bits >= 3072
        else 4
        if bits > 2048
        else 5
    )
    weak = algorithm == "ssh-rsa" and bits <= 2048
    warning = f"RSA {bits}-bit is weak: replace with Ed25519, ECDSA or RSA 3072+" if weak else None
    return KeyStrength(algorithm, bits, algorithm.startswith("sk-"), rank, weak, warning, fields[1])
