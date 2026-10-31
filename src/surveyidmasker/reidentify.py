import os
import re

from . import utils
from .key import read_key_file
from .mask import decrypt_code
from .read_write import read_survey, write_survey

LAT_TAG = "\x1fa"
LON_TAG = "\x1fo"
_TOKEN = re.compile(r"(?<![A-Za-z0-9])MSK[A-Za-z0-9]{10,}(?![A-Za-z0-9])")


def decode_token(token, key):
    """A token is "MSK" + optional region (e.g. "IT" or "USCT") + code. The
    region length is unknown, so each possibility is tried; only the right one
    passes the key check."""
    rest = token[3:]
    for n in (0, 2, 4):
        if n and not re.match(rf"[A-Z]{{{n}}}", rest):
            continue
        val = decrypt_code(rest[n:], key)
        if val is not None:
            if val.startswith(LAT_TAG):
                return val[2:].split(",", 1)[0]
            if val.startswith(LON_TAG):
                return val[2:].split(",", 1)[1] if "," in val[2:] else val[2:]
            return val
    return None


def reidentify(file, key_file, output=None, overwrite=False, header_rows=None):
    """Turn masked codes back into the original identifiers.

    Reads a file masked by :func:`mask_survey` and, using the private key
    file, replaces every ``MSK`` code (also inside free text) with the original
    value. ``MSKblank`` becomes an empty cell again. The masked file is not
    changed. Returns a dict with the re-identified ``data`` and the ``output``
    path.
    """
    file = os.fspath(file)
    key = read_key_file(key_file)
    x = read_survey(file, header_rows)
    data = x.data

    base = re.sub(r"_masked(_\d{4}-\d{2}-\d{2}_\d{2}-\d{2})?(\.[^.]+)$", r"\2", file)
    out_path = output or re.sub(r"(\.[^.]+)$", f"_reidentified_{utils.time_stamp()}\\1", base)
    if not overwrite and os.path.exists(out_path):
        raise FileExistsError(f"Output already exists: {out_path}\nUse overwrite=True to replace it.")
    if os.path.abspath(out_path) == os.path.abspath(file):
        raise ValueError("The output must be a different file from the masked one.")

    cache, restored, unknown, changed = {}, 0, 0, []

    def restore(m):
        nonlocal restored, unknown
        tok = m.group(0)
        if tok not in cache:
            cache[tok] = decode_token(tok, key)
        if cache[tok] is None:
            unknown += 1
            return tok
        restored += 1
        return cache[tok]

    for col in data.columns:
        v = list(data[col])
        if not any(isinstance(s, str) and "MSK" in s for s in v):
            continue
        new = []
        for s in v:
            if not (isinstance(s, str) and "MSK" in s):
                new.append(s)
            elif s == "MSKblank":
                new.append("")
            else:
                new.append(_TOKEN.sub(restore, s))
        if new != v:
            data[col] = new
            changed.append(col)
    if restored == 0 and unknown > 0:
        raise ValueError("None of the codes match this key. Is it the key file used to mask this file?")

    write_survey(x, out_path, changed=changed)
    print(f"Restored {restored} value(s) in {len(changed)} column(s): {', '.join(map(str, changed))}")
    if unknown:
        print(f"Note: {unknown} code(s) were not made with this key and were left unchanged.")
    print(f"Re-identified file: {out_path}")
    print("This file contains identifiers again: keep it private.")
    return {"data": data, "output": out_path}
