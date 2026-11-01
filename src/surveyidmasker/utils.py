import math
import re
import time
from decimal import Decimal


def time_stamp():
    """Date and time added to the names of the files a run writes."""
    return time.strftime("%Y-%m-%d_%H-%M")


def to_str(v):
    """Like R's as.character(): None for missing, whole floats without ".0"."""
    if v is None:
        return None
    if isinstance(v, float):
        if math.isnan(v):
            return None
        if v.is_integer() and abs(v) < 1e15:
            return str(int(v))
        s = format(v, ".15g")
        return format(Decimal(s), "f") if "e" in s else s
    if isinstance(v, str):
        return v
    try:
        import pandas as pd
        if v is pd.NA or v is pd.NaT:
            return None
    except ImportError:  # pragma: no cover
        pass
    return str(v)


def is_blank(v):
    return v is None or v.strip(" \t\r\n") == ""


def make_unique(names):
    """Like R's make.unique(): duplicates get .1, .2, ... appended."""
    seen, out = set(names), []
    counts = {}
    first = set()
    for n in names:
        if n not in first:
            first.add(n)
            out.append(n)
            continue
        k = counts.get(n, 0) + 1
        while f"{n}.{k}" in seen:
            k += 1
        counts[n] = k
        seen.add(f"{n}.{k}")
        out.append(f"{n}.{k}")
    return out
