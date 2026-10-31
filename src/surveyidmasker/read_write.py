import csv
import os
import re
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from .utils import make_unique, to_str

HEADER_LABELS = {"Start Date", "End Date", "Response Type", "Response ID",
                 "Recorded Date", "IP Address"}


@dataclass
class Survey:
    """A survey file: ``data`` (DataFrame), extra ``header`` rows (list of
    rows, Qualtrics question text / ImportId), original column ``names`` and
    ``format``."""
    data: pd.DataFrame
    format: str
    header: Optional[list] = None
    names: Optional[list] = None
    bom: bool = False
    sheet: Optional[str] = None
    meta: dict = field(default_factory=dict)


def detect_header_rows(rows):
    """Qualtrics CSV/Excel exports have the variable names in row 1, the
    question text in row 2 and (in newer CSVs) ImportId JSON in row 3."""
    row2 = rows[1] if len(rows) >= 2 else []
    row3 = rows[2] if len(rows) >= 3 else []
    if any(str(c).startswith('{"ImportId"') for c in row3):
        return 3
    if any(c in HEADER_LABELS for c in row2):
        return 2
    return 1


def read_survey(path, header_rows=None) -> Survey:
    """Read a CSV, SPSS (.sav) or Excel (.xlsx) survey file, keeping any extra
    header rows so the file can be written back unchanged apart from the
    masked columns."""
    path = os.fspath(path)
    if not os.path.exists(path):
        raise FileNotFoundError(f"File not found: {path}")
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    if ext == "csv":
        return _read_csv(path, header_rows)
    if ext == "sav":
        return _read_sav(path)
    if ext == "xlsx":
        return _read_xlsx(path, header_rows)
    raise ValueError(f"Unsupported file type '.{ext}'. Use a .csv, .sav or .xlsx file.")


def _frame(names, body):
    df = pd.DataFrame(body, columns=make_unique(list(names)), dtype=object)
    return df


def _read_csv(path, header_rows):
    with open(path, "rb") as f:
        bom = f.read(3) == b"\xef\xbb\xbf"
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            raw = list(csv.reader(f))
    except UnicodeDecodeError as e:
        raise ValueError(f"{path} is not UTF-8 text. Re-save it as CSV UTF-8 (Excel: "
                         "'CSV UTF-8') and try again.") from e
    width = max((len(r) for r in raw), default=0)
    raw = [r + [""] * (width - len(r)) for r in raw]
    h = detect_header_rows(raw) if header_rows is None else header_rows
    names = raw[0] if raw else []
    return Survey(data=_frame(names, raw[h:]), format="csv", header=raw[:h],
                  names=names, bom=bom)


def _read_xlsx(path, header_rows):
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.worksheets[0]
    ws.reset_dimensions()  # some writers omit the sheet size
    rows = [list(r) for r in ws.iter_rows(values_only=True)]
    while rows and all(v is None for v in rows[-1]):
        rows.pop()
    width = max((len(r) for r in rows), default=0)
    rows = [r + [None] * (width - len(r)) for r in rows]
    top = [[to_str(v) or "" for v in r] for r in rows[:3]]
    h = detect_header_rows(top) if header_rows is None else header_rows
    names = top[0] if top else []
    return Survey(data=_frame(names, rows[h:]), format="xlsx", header=top[:h],
                  names=names, sheet=ws.title)


def _read_sav(path):
    import pyreadstat
    df, meta = pyreadstat.read_sav(path)
    return Survey(data=df, format="sav", meta={
        "column_labels": dict(zip(meta.column_names, meta.column_labels)),
        "value_labels": meta.variable_value_labels,
        "measure": meta.variable_measure,
        "display_width": meta.variable_display_width,
        "missing_ranges": meta.missing_ranges,
        "file_label": meta.file_label,
    })


def write_survey(x: Survey, path, changed=()):
    path = os.fspath(path)
    d = x.data
    if x.format == "csv":
        body = [["" if (v is None or v != v) else to_str(v) for v in row]
                for row in d.itertuples(index=False, name=None)]
        with open(path, "w", encoding="utf-8-sig" if x.bom else "utf-8", newline="") as f:
            csv.writer(f, lineterminator="\n").writerows(list(x.header) + body)
    elif x.format == "sav":
        _write_sav(x, path, changed)
    elif x.format == "xlsx":
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = x.sheet or "Sheet1"
        for r in x.header:
            ws.append(list(r))
        for row in d.itertuples(index=False, name=None):
            ws.append([None if (v is None or v != v) else v for v in row])
        wb.save(path)
    return path


def _truncate_bytes(s, n=256):
    b = s.encode("utf-8")
    return s if len(b) <= n else b[:n].decode("utf-8", errors="ignore")


def long_labels(x: Survey):
    labels = x.meta.get("column_labels", {})
    return [c for c in x.data.columns if len((labels.get(c) or "").encode("utf-8")) > 256]


def _write_sav(x: Survey, path, changed):
    import pyreadstat
    m, d = x.meta, x.data
    changed = set(changed)
    cols = list(d.columns)
    labels = {c: _truncate_bytes(m["column_labels"][c]) for c in cols
              if m["column_labels"].get(c)}
    # Value labels and missing ranges belong to the original numeric values.
    keep = lambda dct: {k: v for k, v in (dct or {}).items() if k in cols and k not in changed}
    kwargs = dict(
        column_labels=[labels.get(c) for c in cols],
        variable_value_labels=keep(m.get("value_labels")),
        missing_ranges=keep(m.get("missing_ranges")),
        variable_display_width={k: v for k, v in (m.get("display_width") or {}).items() if k in cols},
        variable_measure={k: v for k, v in (m.get("measure") or {}).items() if k in cols},
    )
    if m.get("file_label"):
        kwargs["file_label"] = m["file_label"]
    out = d.copy()
    for c in cols:
        if c in changed:
            out[c] = pd.Series([None if v is None else str(v) for v in out[c]],
                               index=out.index, dtype=object)
    pyreadstat.write_sav(out, path, **kwargs)
