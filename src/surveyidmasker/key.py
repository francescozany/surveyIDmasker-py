"""Key file: one random 256-bit secret, from which separate keys for
encryption and for the integrity check are derived."""
import hashlib
import hmac
import os
import re
import time
from dataclasses import dataclass


@dataclass
class Key:
    secret: bytes
    enc: bytes
    mac: bytes


def derive_key(secret: bytes) -> Key:
    return Key(
        secret=secret,
        enc=hmac.new(secret, b"surveyIDmasker-encrypt", hashlib.sha256).digest(),
        mac=hmac.new(secret, b"surveyIDmasker-check", hashlib.sha256).digest(),
    )


def new_key_file(path, overwrite=False):
    """Make a new random key and save it as a small text file.

    Anyone who has this file can turn masked codes back into the original
    identifiers, so keep it private and never share it with the data.
    """
    if os.path.exists(path) and not overwrite:
        raise FileExistsError(f"Key file already exists: {path}")
    write_key_file(os.urandom(32), path)
    return path


def write_key_file(secret: bytes, path, used_for=None):
    lines = [
        "# surveyIDmasker KEY - KEEP THIS FILE PRIVATE.",
        "# Anyone with this file can turn the masked codes back into the original",
        "# identifiers. Never share it with the masked data; store it with the",
        "# original data. Without it, the masked codes cannot be turned back.",
        "# Created: " + time.strftime("%Y-%m-%d %H:%M:%S %Z"),
    ]
    if used_for is not None:
        lines.append(f"# First used for: {used_for}")
    lines.append("key: " + secret.hex())
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def read_key_file(path) -> Key:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Key file not found: {path}")
    with open(path, encoding="utf-8", errors="replace") as f:
        found = [m.group(1) for line in f.read().splitlines()
                 if (m := re.match(r"^key: *([0-9a-f]{64}) *$", line))]
    if len(found) != 1:
        raise ValueError(f"This does not look like a surveyIDmasker key file: {path}")
    return derive_key(bytes.fromhex(found[0]))
