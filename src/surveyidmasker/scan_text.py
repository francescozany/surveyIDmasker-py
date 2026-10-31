import re

import pandas as pd

from .utils import is_blank

# Patterns for identifiers typed inside free text.
TEXT_PATTERNS = {
    "Prolific ID": r"\b[0-9a-f]{24}\b",
    "MTurk worker ID": r"\bA(?=[0-9A-Z]*[0-9])[0-9A-Z]{11,20}\b",
    "email": r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
    "IP address": r"\b(?:\d{1,3}\.){3}\d{1,3}\b(?<!\.0\.0\.0)",
}
_TEXT_RE = {k: re.compile(v, re.ASCII) for k, v in TEXT_PATTERNS.items()}


def usable_ids(ids):
    """Short values, numbers and dates are too common to search for in text."""
    seen = []
    for i in ids:
        if (i is not None and not is_blank(i) and len(i) >= 6
                and not re.fullmatch(r"[-0-9.:/ ]+", i) and i not in seen):
            seen.append(i)
    return seen


def _is_text(series):
    return series.dtype == object or pd.api.types.is_string_dtype(series)


def scan_text(data, columns=None, known_ids=()):
    """Look for identifiers left in text columns.

    Returns a DataFrame with ``column``, ``type`` and ``cells`` (number of
    cells with a match). No matched values are returned.
    """
    known = usable_ids(list(known_ids))
    columns = list(data.columns) if columns is None else list(columns)
    rows = []
    for col in columns:
        if not _is_text(data[col]):
            continue
        x = ["" if (v is None or v != v) else str(v) for v in data[col]]
        hit_any = [False] * len(x)
        for kind, rx in _TEXT_RE.items():
            hit = [bool(rx.search(v)) for v in x]
            hit_any = [a or b for a, b in zip(hit_any, hit)]
            if any(hit):
                rows.append((col, kind, sum(hit)))
        if known:
            hit = [any(k in v for k in known) and not a for v, a in zip(x, hit_any)]
            if any(hit):
                rows.append((col, "known ID", sum(hit)))
    return pd.DataFrame(rows, columns=["column", "type", "cells"])


def mask_in_text(x, cb, known_ids=()):
    """Replace identifiers found inside text with their masked codes."""
    na = [v is None or v != v for v in x]
    out = ["" if n else str(v) for v, n in zip(x, na)]
    for rx in _TEXT_RE.values():
        out = [rx.sub(lambda m: "MSK" + cb.codes_for([m.group(0)])[0], v) for v in out]
    for i in usable_ids(list(known_ids)):
        code = None
        for j, v in enumerate(out):
            if i in v:
                code = code or "MSK" + cb.codes_for([i])[0]
                out[j] = v.replace(i, code)
    return [None if n else v for v, n in zip(out, na)]
