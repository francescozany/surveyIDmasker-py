import os
import re

import pandas as pd

from .detect import suggest_id_columns
from .read_write import read_survey
from .scan_text import scan_text
from .survey import expand_files
from .utils import is_blank, to_str

_MASKED = re.compile(r"^MSK(blank|([A-Z]{2}(-[A-Z0-9]{1,3})?_)?[A-Za-z0-9]{10,})$")


def is_masked_column(values):
    """A column made by mask_survey(): its filled cells are MSK codes."""
    x = [v.strip(" \t\r\n") for v in map(to_str, values) if not is_blank(v)]
    return bool(x) and all(_MASKED.match(v) for v in x)


def check_survey(file, header_rows=None):
    """Lists what still looks identifying in a survey file (e.g. a masked file
    before you share it): identifier columns that are not masked, and
    identifiers typed into other columns. No key is needed and the file is not
    changed.

    ``file`` is a path to a survey file (.csv, .sav, .dta or .xlsx), a list of
    such paths, or a folder. Returns a DataFrame with one row per finding:
    ``file``, ``column``, ``issue`` and ``cells``.
    """
    found = []
    for f in expand_files(file):
        data = read_survey(f, header_rows).data
        masked = [c for c in data.columns if is_masked_column(data[c])]
        cols = suggest_id_columns(data)
        cols = cols[~cols["column"].isin(masked)]
        text = scan_text(data, [c for c in data.columns if c not in masked and c not in set(cols["column"])])
        rows = [(c, reason, sum(1 for v in map(to_str, data[c]) if not is_blank(v)))
                for c, reason in zip(cols["column"], cols["reason"])]
        rows += [(c, f"{t} in text", n) for c, t, n in zip(text["column"], text["type"], text["cells"])]
        print(f"{os.path.basename(f)}: {len(data)} rows, {data.shape[1]} columns")
        if masked:
            print("  Masked columns:", ", ".join(map(str, masked)))
        if rows:
            print("  Still looks identifying:")
            for c, issue, n in rows:
                print(f"    {str(c):<28} {issue:<36} {n} cell(s)")
        else:
            print("  Nothing else that looks identifying was found.")
        found += [(os.path.basename(f), c, issue, n) for c, issue, n in rows]
    return pd.DataFrame(found, columns=["file", "column", "issue", "cells"])
