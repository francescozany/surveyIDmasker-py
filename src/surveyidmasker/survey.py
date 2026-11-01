import hashlib
import os
import re
import shlex
import sys
import time
from dataclasses import dataclass
from typing import Any

import pandas as pd

from . import utils
from .detect import is_ip_column, is_latitude, is_longitude, suggest_id_columns
from .key import add_key_use, derive_key, read_key_file, write_key_file
from .location import ip_region, latlon_region, read_ip_db
from .mask import Codebook, mask_with
from .read_write import long_labels, read_survey, write_survey
from .reidentify import LAT_TAG, LON_TAG
from .scan_text import mask_in_text
from .scan_text import scan_text as _scan_text
from .utils import is_blank, to_str

QUIT_WORDS = ("q", "quit", "exit")
MODES = ("full", "country_state")


@dataclass
class MaskResult:
    """With one file, ``data`` and ``output`` are the masked DataFrame and its
    path. With several files, both are dicts keyed by the file's path."""
    data: Any
    output: Any
    report: str
    key_file: str


class Stopped(Exception):
    pass


def _quit():
    raise Stopped("Stopped. Nothing was masked or saved.")


def _ask_line(prompt):
    ans = input(prompt).strip()
    if ans.lower() in QUIT_WORDS:
        _quit()
    return ans


def _ask_choice(title, options):
    print(f"\n{title}\n")
    for i, o in enumerate(options, 1):
        print(f"{i}: {o}")
    while True:
        ans = _ask_line("\nSelection (or q to exit): ")
        if ans in [str(i) for i in range(1, len(options) + 1)]:
            return int(ans)
        print("Please type", " or ".join(map(str, range(1, len(options) + 1))), "(or q to exit).")


def _column_labels(cols, suggested, empty):
    return [f"{'*' if c in suggested or c in empty else ' '}{i:3d} {c}{' (empty)' if c in empty else ''}"
            for i, c in enumerate(cols, 1)]


def _format_columns(labels):
    width = max(len(s) for s in labels) + 2 if labels else 2
    per_row = max(1, 80 // width)
    return "\n".join("".join(s.ljust(width) for s in labels[i:i + per_row])
                     for i in range(0, len(labels), per_row))


def _parse_numbers(text, maximum):
    idx = []
    for p in re.split(r"[ ,;]+", text.strip()):
        try:
            r = [int(v) for v in p.split("-")]
        except ValueError:
            continue
        idx += list(range(r[0], r[1] + 1)) if len(r) == 2 else r
    if not idx or any(i < 1 or i > maximum for i in idx):
        return []
    return list(dict.fromkeys(idx))


def _rule(title):
    return "── " + title + " " + "─" * max(3, 60 - len(title))


# --- choosing files -------------------------------------------------------

def survey_files(directory):
    """Survey files in a folder, leaving out the files this package writes."""
    out = []
    for n in sorted(os.listdir(directory)):
        if not re.search(r"\.(csv|sav|xlsx)$", n, re.I):
            continue
        if re.search(r"_(masked|reidentified)_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}|^~\$", n):
            continue
        out.append(os.path.abspath(os.path.join(directory, n)))
    return out


def expand_files(file):
    """Files given as paths and/or folders; a folder stands for its survey files."""
    items = [file] if isinstance(file, (str, os.PathLike)) else list(file)
    out = []
    for f in items:
        f = os.fspath(f)
        if os.path.isdir(f):
            found = survey_files(f)
            if not found:
                raise ValueError(f"No survey files (.csv, .sav, .xlsx) in the folder: {f}")
            out += found
        else:
            out.append(os.path.abspath(f))
    return list(dict.fromkeys(out))


def _read_each(paths, header_rows, skip_unreadable=False):
    files = {}
    for f in paths:
        try:
            x = read_survey(f, header_rows)
        except Exception as e:  # noqa: BLE001
            if not skip_unreadable:
                raise
            print(f"Could not read {os.path.basename(f)}: {e}")
            continue
        print(f"Read {os.path.basename(f)}: {len(x.data)} rows, {x.data.shape[1]} columns.")
        files[f] = x
    return files


def choose_files(header_rows):
    """Guided choice of the files: one or several paths (drag them into the
    terminal), or a folder."""
    print("Give the survey file(s) to mask (.csv, .sav or .xlsx): one path, several\n"
          "paths to mask them with the same key, or a folder. You can drag them into\n"
          "this window.")
    while True:
        ans = _ask_line("\nPath(s): ")
        try:
            paths = expand_files([p for p in shlex.split(ans) if p])
        except ValueError as e:
            print(e)
            continue
        if not paths:
            continue
        files = _read_each(paths, header_rows, skip_unreadable=True)
        if files:
            return files
        print("None of the chosen files could be read.")


# --- choosing columns -----------------------------------------------------

def _choose_columns(data, ask, batch=False):
    found = suggest_id_columns(data, include_empty=True)
    sugg = found[~found["empty"]]
    empty = list(found.loc[found["empty"], "column"])
    if not ask:
        if sugg.empty and batch:
            print("No identifier columns found: this file will not be masked.", file=sys.stderr)
            return []
        if sugg.empty:
            raise ValueError("No identifier columns with data were found. Pass them with `columns=`.")
        print("Masking the suggested columns:", ", ".join(sugg["column"]), file=sys.stderr)
        return list(sugg["column"])
    cols = list(data.columns)
    if not len(data):
        print("\nThis file has no responses, so there is nothing to mask.")
    if empty:
        print("\nRecognized as identifiers but empty (nothing to mask):", ", ".join(empty))
    if not sugg.empty:
        print("\nThese columns look like identifiers:")
        for c, r in zip(sugg["column"], sugg["reason"]):
            print(f"  {c:<28} {r}")
        ans = _ask_line("\nPress Enter to mask these, type e to change the selection, or q to exit: ")
        if ans.lower() != "e":
            return list(sugg["column"])
    else:
        print("\nNo columns with identifiers to mask were found.")
    while True:
        print("\nAll columns (* = suggested, (empty) = identifier with no data):")
        print(_format_columns(_column_labels(cols, list(sugg["column"]), empty)))
        if not sugg.empty:
            tail = "Enter for the suggested ones, or q to exit: "
        elif batch:
            tail = "Enter to skip this file, or q to exit: "
        else:
            tail = "or q to exit: "
        ans = _ask_line("\nNumbers of the columns to mask (e.g. 4 14 15 120-123), " + tail)
        if ans == "":
            if not sugg.empty:
                return list(sugg["column"])
            if batch:
                print("This file will not be masked.")
                return []
            _quit()
        idx = _parse_numbers(ans, len(cols))
        if idx:
            print("Selected:", ", ".join(cols[i - 1] for i in idx))
            if _ask_line("Is this right? [Y/n, or q to exit] ").lower() in ("", "y", "yes"):
                return [cols[i - 1] for i in idx]
        else:
            print(f"Please type column numbers between 1 and {len(cols)}, e.g. 4 14 15 or 120-123.")


def _is_numbers(columns):
    return (isinstance(columns, (list, tuple)) and columns
            and all(isinstance(c, (int, float)) and not isinstance(c, bool) for c in columns)) \
        or (isinstance(columns, (int, float)) and not isinstance(columns, bool))


def resolve_columns(data, columns):
    names = list(data.columns)
    if isinstance(columns, str) and columns == "suggested":
        cols = list(suggest_id_columns(data)["column"])
        if not cols:
            raise ValueError("No identifier columns were found. Give them with `columns=`.")
        return cols
    if isinstance(columns, (str, int, float)):
        columns = [columns]
    columns = list(columns)
    if _is_numbers(columns):
        bad = [c for c in columns if c < 1 or c > len(names) or c != round(c)]
        if bad:
            raise ValueError(f"Column number(s) out of range (1-{len(names)}): {', '.join(map(str, bad))}")
        return list(dict.fromkeys(names[int(c) - 1] for c in columns))
    bad = [c for c in columns if c not in names]
    if bad:
        raise ValueError(f"Column(s) not found: {', '.join(map(str, bad))}\n"
                         'See the column names and numbers with list_columns("<file>").')
    return list(dict.fromkeys(columns))


def _columns_for(columns, path, paths):
    """The `columns` argument for one file: a dict keyed by file gives each
    file its own columns."""
    if not isinstance(columns, dict):
        return columns
    base = os.path.basename(path)
    hit = [k for k in (path, base) if k in columns]
    if not hit:
        raise ValueError(f"`columns` has no entry for {base}")
    if sum(os.path.basename(p) == base for p in paths) > 1 and path not in columns:
        raise ValueError(f"Several files are called {base}: name them by full path in `columns`.")
    return columns[hit[0]]


def _batch_columns(data, columns, batch, name):
    """With several files a file without the given columns, or without
    suggested ones, is left out (and listed in the report) instead of
    stopping everything."""
    if not batch:
        return resolve_columns(data, columns)
    if isinstance(columns, str) and columns == "suggested":
        found = list(suggest_id_columns(data)["column"])
        if not found:
            print(f"No identifier columns found in {name}: it will not be masked.", file=sys.stderr)
        return found
    if _is_numbers(columns):
        return resolve_columns(data, columns)
    cols = [columns] if isinstance(columns, str) else list(columns)
    missing = [c for c in dict.fromkeys(cols) if c not in data.columns]
    if missing:
        print(f"Not in {name}: {', '.join(missing)}", file=sys.stderr)
    if len(missing) == len(set(cols)):
        print(f"{name} will not be masked.", file=sys.stderr)
    return [c for c in dict.fromkeys(cols) if c in data.columns]


def list_columns(file):
    """Print the columns of a survey file with their numbers (``*`` =
    suggested identifier column) and return them as a DataFrame."""
    data = read_survey(file).data
    found = suggest_id_columns(data, include_empty=True)
    empty = list(found.loc[found["empty"], "column"])
    sugg = list(found.loc[~found["empty"], "column"])
    cols = list(data.columns)
    print(_format_columns(_column_labels(cols, sugg, empty)))
    return pd.DataFrame({"number": range(1, len(cols) + 1), "column": cols,
                         "suggested": [c in sugg for c in cols],
                         "empty": [c in empty for c in cols]})


# --- IP / location modes ---------------------------------------------------

def _modes_by_file(value, applies, paths, name):
    """How IPs or locations are masked in each file they apply to: a dict
    path -> mode, None where it is still to be asked. ``value`` is one mode
    for all files, or a dict keyed by file."""
    out = {p: None for p in applies}
    if value is None or not applies:
        return out

    def check(v):
        if not isinstance(v, str) or v not in MODES:
            raise ValueError(f'{name} must be "full" or "country_state" (or a dict of these keyed by file).')
        return v

    if not isinstance(value, dict):
        return {p: check(value) for p in applies}
    bad = [k for k in value if k not in paths and k not in [os.path.basename(p) for p in paths]]
    if bad:
        raise ValueError(f"`{name}` names a file that is not being masked: {', '.join(bad)}")
    for p in applies:
        hit = [k for k in (p, os.path.basename(p)) if k in value]
        if hit:
            out[p] = check(value[hit[0]])
    return out


def _choose_mode(mode, what, ask):
    if mode is not None:
        return mode
    if not ask:
        arg = "ip" if what == "IP addresses" else "latlon"
        print(f"Masking {what} fully (set it with {arg}=).", file=sys.stderr)
        return "full"
    choice = _ask_choice(f"How should {what} be masked?", [
        "Fully masked, like the other identifiers (MSK + code)",
        "Keep country, and state for the US (e.g. MSKUS-CT_..., MSKIT_...)"])
    return "country_state" if choice == 2 else "full"


def _choose_ip_db(ask):
    msg = ('Keeping the country of IP addresses needs the free DB-IP "IP to City Lite" CSV.\n'
           "Download it from https://db-ip.com/db/lite.php, unzip it, and give its path with ip_db=.")
    if not ask:
        raise ValueError(msg)
    print("\n" + msg)
    if _ask_choice("What would you like to do?",
                   ["Choose the DB-IP file now", "Mask IP addresses fully instead"]) == 2:
        return None
    return _ask_line("Path to the DB-IP csv file: ")


def region_prefix(region):
    """"MSK" + region + "_" (e.g. "MSKIT_", "MSKUS-CT_"), or just "MSK" when
    the region is unknown. The code itself never contains "_" or "-", so the
    region is always the part between "MSK" and "_"."""
    return "MSK" if region is None else f"MSK{region}_"


def _unknown_note(region, values, what):
    n = sum(1 for r, v in zip(region, values) if r is None and not is_blank(v))
    if not n:
        return []
    return [f"{what}: {n} value(s) could not be matched to a country and got a plain MSK code."]


# --- masking one file -------------------------------------------------------

def _mask_one(x, columns, cb, latlon, ip, ip_cols, db, scan_text, mask_text, originals):
    """Masks the chosen columns of one file. Returns the masked data, notes for
    the report, and identifiers found in the other columns."""
    data = x.data.copy()
    notes, done, changed = [], [], list(columns)

    def put(col, values):
        data[col] = pd.Series(values, index=data.index, dtype=object)

    # Latitude and longitude are coded from the (lat, lon) pair, so the same
    # place gives the same pair of codes.
    if latlon is not None:
        lat_col = [c for c in columns if is_latitude(c)][0]
        lon_col = [c for c in columns if is_longitude(c)][0]
        lat = [to_str(v) for v in data[lat_col]]
        lon = [to_str(v) for v in data[lon_col]]
        ws = " \t\r\n"
        pair = [(a or "").strip(ws) + "," + (b or "").strip(ws) for a, b in zip(lat, lon)]
        prefix = "MSK"
        if latlon == "country_state":
            region = latlon_region(lat, lon)
            prefix = [region_prefix(r) for r in region]
            notes += _unknown_note(region, lat, "latitude/longitude")
        put(lat_col, mask_with(cb, lat, keys=[LAT_TAG + p for p in pair], prefix=prefix))
        put(lon_col, mask_with(cb, lon, keys=[LON_TAG + p for p in pair], prefix=prefix))
        done = [lat_col, lon_col]

    if ip == "country_state":
        for col in ip_cols:
            addr = [to_str(v) for v in data[col]]
            reg = ip_region(addr, db)
            notes += _unknown_note(reg, addr, f"IP addresses ({col})")
            put(col, mask_with(cb, addr, prefix=[region_prefix(r) for r in reg]))
            done.append(col)

    for col in columns:
        if col not in done:
            put(col, mask_with(cb, list(data[col])))

    text_found = None
    if scan_text:
        others = [c for c in data.columns if c not in columns]
        text_found = _scan_text(data, others, known_ids=originals)
        if len(text_found):
            if mask_text:
                for col in dict.fromkeys(text_found["column"]):
                    put(col, mask_in_text(list(data[col]), cb, originals))
                    changed.append(col)
                notes.append("Identifiers found inside other columns were replaced with their codes (see the report).")
            else:
                notes.append("Possible identifiers were found inside other columns (see the report). "
                             "Check them, or rerun with mask_text=True.")

    if x.format == "sav":
        long = long_labels(x)
        if long:
            notes.append(f"{len(long)} SPSS variable label(s) longer than 256 characters were "
                         f"shortened (an SPSS file limit): {', '.join(map(str, long))}.")
    return {"data": data, "notes": notes, "text_found": text_found, "rows": len(data),
            "changed": changed}


# --- report and repeat command ---------------------------------------------

def _write_report(path, inputs, outputs, results, cols, key, new_key, ip_of, latlon_of,
                  mask_text, skipped):
    from . import __version__
    label = lambda m: "country/US state kept" if m == "country_state" else "full"  # noqa: E731
    # One line in the header when every file uses the same rule; otherwise the
    # rule is given for each file.
    same_ip = len(set(ip_of.values())) <= 1
    same_loc = len(set(latlon_of.values())) <= 1
    ip = next(iter(ip_of.values())) if ip_of and same_ip else None
    latlon = next(iter(latlon_of.values())) if latlon_of and same_loc else None
    batch = len(inputs) > 1 or bool(skipped)
    kind = "new" if new_key else "existing key"
    if len(inputs) > 1:
        key_line = f"Key:             one key for all {len(inputs)} files, created {key.created} ({kind})"
    else:
        key_line = f"Key:             created {key.created} ({kind})"
    lines = [
        "surveyIDmasker report", "======================",
        "Created:         " + time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        f"Package version: {__version__} (Python)",
        key_line,
    ]
    if ip is not None:
        lines.append("IP addresses:    " + label(ip))
    if not same_ip:
        lines.append("IP addresses:    differ by file (see each file)")
    if latlon is not None:
        lines.append("Locations:       " + label(latlon) + " (latitude/longitude)")
    if not same_loc:
        lines.append("Locations:       differ by file (see each file)")
    lines += ["Codes:           encrypted with a private key kept in a separate file",
              "Blank values:    MSKblank"]
    for i, p in enumerate(inputs, 1):
        r = results[p]
        pad = "   " if batch else ""
        lines.append("")
        if batch:
            lines.append(f"{i}. {os.path.basename(p)} -> {os.path.basename(outputs[p])}")
        else:
            lines += ["Original file:   " + os.path.basename(p),
                      "Masked file:     " + os.path.basename(outputs[p])]
        lines.append(f"{pad}Rows: {r['rows']}")
        lines.append(f"{pad}Masked columns: {', '.join(map(str, cols[p]))}")
        if not same_ip and p in ip_of:
            lines.append(f"{pad}IP addresses: {label(ip_of[p])}")
        if not same_loc and p in latlon_of:
            lines.append(f"{pad}Locations: {label(latlon_of[p])} (latitude/longitude)")
        lines += [f"{pad}Note: {n}" for n in r["notes"]]
        tf = r["text_found"]
        if tf is not None and len(tf):
            lines.append(f"{pad}Possible identifiers in other columns ({'masked' if mask_text else 'NOT masked'}):")
            lines += [f"{pad}  {t.column:<30} {t.type:<18} {t.cells} cell(s)" for t in tf.itertuples()]
        with open(outputs[p], "rb") as f:
            lines.append(f"{pad}SHA-256 of masked file: {hashlib.sha256(f.read()).hexdigest()}")
    if skipped:
        lines += ["", "Not masked (no identifier columns):"] + ["  " + os.path.basename(p) for p in skipped]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def _q(s):
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _mode_arg(name, modes):
    """`ip="full"` when every file uses the same rule, otherwise a dict by file."""
    if not modes:
        return None
    if len(set(modes.values())) == 1:
        return f"{name}={_q(next(iter(modes.values())))}"
    names = list(modes)
    base = [os.path.basename(p) for p in names]
    keys = names if len(set(base)) < len(base) else base
    return f"{name}={{" + ", ".join(f"{_q(k)}: {_q(modes[p])}" for k, p in zip(keys, names)) + "}"


def _repeat_command(paths, cols, suggested, ip_of, latlon_of, ip_db, key_file, mask_text):
    vec = lambda x: "[" + ", ".join(_q(c) for c in x) + "]"  # noqa: E731
    if len(paths) == 1:
        file_arg = _q(paths[0])
        col_arg = "columns=" + (_q("suggested") if suggested[paths[0]] else vec(cols[paths[0]]))
    else:
        file_arg = "[" + ",\n    ".join(_q(p) for p in paths) + "]"
        if all(suggested.values()):
            col_arg = 'columns="suggested"'
        else:
            base = [os.path.basename(p) for p in paths]
            keys = paths if len(set(base)) < len(base) else base
            col_arg = "columns={\n      " + ",\n      ".join(
                f"{_q(k)}: " + (_q("suggested") if suggested[p] else vec(cols[p]))
                for k, p in zip(keys, paths)) + "}"
    args = [file_arg, col_arg, _mode_arg("ip", ip_of), _mode_arg("latlon", latlon_of),
            f"ip_db={_q(ip_db)}" if ip_db else None, f"key_file={_q(key_file)}",
            "mask_text=True" if mask_text else None]
    return "mask_survey(" + ",\n  ".join(a for a in args if a) + ")"


# --- the main function --------------------------------------------------------

def mask_survey(file=None, columns=None, ip=None, latlon=None, ip_db=None, key_file=None,
                scan_text=True, mask_text=False, output=None, overwrite=False,
                header_rows=None, interactive=None):
    """Mask participant identifiers in survey files.

    Replaces identifiers (Prolific/Connect/MTurk IDs, IP addresses, locations,
    emails, ...) with ``"MSK"`` plus a code made by encrypting the value with a
    private key. The same value gets the same code everywhere, so repeat
    participants can still be spotted; blank cells become ``"MSKblank"``.
    ``reidentify`` restores the originals given the key.

    ``file`` can be one path, a list of paths or a folder: several files are
    masked with the same key and described in one report. Anything you pass is
    not asked; with no ``file`` or ``columns`` (and a terminal) you are guided
    step by step.

    ``columns`` is a list of names, 1-based numbers (one file only; see
    ``list_columns``) or ``"suggested"``; with several files it can also be a
    dict keyed by file name. ``ip`` and ``latlon`` are ``"full"`` or
    ``"country_state"`` (the latter needs ``ip_db`` for IP addresses), or a dict
    keyed by file name to give each file its own rule. The original files are
    never changed. Returns a :class:`MaskResult`.
    """
    ask = sys.stdin.isatty() if interactive is None else interactive
    try:
        return _mask_survey(file, columns, ip, latlon, ip_db, key_file, scan_text,
                            mask_text, output, overwrite, header_rows, ask)
    except Stopped as e:
        raise SystemExit(str(e))


def _mask_survey(file, columns, ip, latlon, ip_db, key_file, scan_text, mask_text,
                 output, overwrite, header_rows, ask):
    if ask and (file is None or columns is None):
        if file is None:
            print("You can mask one file, or several files using the same key.")
        print("Type q at any question to exit. Nothing is saved until the end.\n")

    # 1. The files
    if file is None:
        if not ask:
            raise ValueError("Please give the path of the file to mask.")
        files = choose_files(header_rows)
    else:
        files = _read_each(expand_files(file), header_rows)
    paths = list(files)
    n = len(paths)
    batch = n > 1
    if batch and output is not None:
        raise ValueError("`output` can only be used with one file.")
    if batch and _is_numbers(columns):
        raise ValueError("Column numbers differ from file to file: with several files, give column "
                         'names, "suggested", or a dict keyed by file.')
    if batch:
        print(f"\n{n} files will be masked using the same key.")

    # 2. The columns of each file
    skipped, cols = [], {}
    for i, p in enumerate(paths, 1):
        data = files[p].data
        if batch and columns is None and ask:
            print("\n" + _rule(f"File {i} of {n}: {os.path.basename(p)}"))
        if columns is None:
            sel = _choose_columns(data, ask, batch)
        else:
            sel = _batch_columns(data, _columns_for(columns, p, paths), batch, os.path.basename(p))
        if sel:
            cols[p] = sel
        else:
            skipped.append(p)
    if not cols:
        raise ValueError("No columns selected, nothing to mask.")
    paths = list(cols)

    # 3. How IPs and locations are masked. With several files the rules can
    # differ by file (studies can have different IRB rules on what is shared).
    has_pair, ip_cols = {}, {}
    for p in paths:
        sel = cols[p]
        has_pair[p] = sum(map(is_latitude, sel)) == 1 and sum(map(is_longitude, sel)) == 1
        ip_cols[p] = [c for c in sel if is_ip_column(files[p].data, c)]
    latlon_of = _modes_by_file(latlon, [p for p in paths if has_pair[p]], paths, "latlon")
    ip_of = _modes_by_file(ip, [p for p in paths if ip_cols[p]], paths, "ip")
    undecided = list(dict.fromkeys([p for p, m in latlon_of.items() if m is None]
                                   + [p for p, m in ip_of.items() if m is None]))
    if undecided:
        if ask and batch:
            title = (f"For all {n} files" if len(undecided) == len(paths) else
                     "IP addresses and locations: " + ", ".join(os.path.basename(p) for p in undecided))
            print("\n" + _rule(title))
        if any(m is None for m in latlon_of.values()):
            mode = _choose_mode(None, "latitude/longitude", ask)
            latlon_of = {p: m or mode for p, m in latlon_of.items()}
        if any(m is None for m in ip_of.values()):
            mode = _choose_mode(None, "IP addresses", ask)
            ip_of = {p: m or mode for p, m in ip_of.items()}
    if "country_state" in ip_of.values() and ip_db is None:
        ip_db = _choose_ip_db(ask)
        if ip_db is None:
            ip_of = {p: "full" for p in ip_of}
    if ip_db is not None and not os.path.exists(ip_db):
        raise FileNotFoundError(f"IP database not found: {ip_db}")

    # 4. Where everything goes. All checks happen before anything is written.
    stamp = utils.time_stamp()
    out_paths = {p: output or re.sub(r"(\.[^.]+)$", f"_masked_{stamp}\\1", p) for p in paths}
    first = paths[0]
    if batch:
        report_path = os.path.join(os.path.dirname(first), f"batch_masked_{stamp}_report.txt")
    else:
        report_path = re.sub(r"\.[^.]+$", "_report.txt", out_paths[first])
    existing = [f for f in list(out_paths.values()) + [report_path] if os.path.exists(f)]
    if not overwrite and existing:
        raise FileExistsError(f"Output already exists: {', '.join(existing)}\n"
                              "Use overwrite=True to replace it.")
    if any(os.path.abspath(o) == os.path.abspath(p) for p, o in out_paths.items()):
        raise ValueError("The output must be a different file from the original.")

    # A key is never overwritten: that would make earlier masked files
    # impossible to re-identify.
    new_key = key_file is None
    if new_key:
        key_file = (os.path.join(os.path.dirname(first), f"batch_KEY_{stamp}.txt") if batch
                    else re.sub(r"\.[^.]+$", f"_KEY_{stamp}.txt", first))
        if os.path.exists(key_file):
            raise FileExistsError(
                f"A key file already exists: {key_file}\nReuse it with key_file=\"{key_file}\", "
                "or move it elsewhere first.")
        key = derive_key(os.urandom(32))
        key.created = time.strftime("%Y-%m-%d %H:%M:%S %Z")
    else:
        key = read_key_file(key_file)

    # 5. Masking, in memory
    cb = Codebook(key)
    db = None
    if "country_state" in ip_of.values():
        print("Reading the IP database (this can take a minute)...")
        db = read_ip_db(ip_db)
    # IDs from all files are looked for in the free text of every file.
    originals = []
    for p in paths:
        for c in cols[p]:
            originals += [v.strip(" \t\r\n") for v in map(to_str, files[p].data[c]) if v is not None]
    results = {}
    for i, p in enumerate(paths, 1):
        if batch:
            print(f"Masking {os.path.basename(p)} ({i} of {len(paths)})...", end="")
        results[p] = _mask_one(files[p], cols[p], cb, latlon_of.get(p), ip_of.get(p),
                               ip_cols[p], db, scan_text, mask_text, originals)
        if batch:
            print(f" done, {len(cols[p])} column(s)")

    suggested = {p: cols[p] == list(suggest_id_columns(files[p].data)["column"]) for p in paths}

    # 6. Writing
    for p in paths:
        x = files[p]
        x.data = results[p]["data"]
        write_survey(x, out_paths[p], changed=results[p]["changed"])
    if new_key:
        write_key_file(key.secret, key_file, created=key.created)
    add_key_use(key_file, [os.path.basename(p) for p in paths])
    _write_report(report_path, paths, out_paths, results, cols, key, new_key,
                  ip_of, latlon_of, mask_text, skipped)

    # 7. Summary
    tag = " (new" if new_key else " (reused"
    if batch:
        print()
        for p in paths:
            for note in results[p]["notes"]:
                print(f"Note: {os.path.basename(p)}: {note}", file=sys.stderr)
        if skipped:
            print("Not masked (no identifier columns): "
                  + ", ".join(os.path.basename(p) for p in skipped), file=sys.stderr)
        print("\nMasked files:")
        for p in paths:
            print("  " + os.path.basename(out_paths[p]))
        print(f"Report: {os.path.basename(report_path)}")
        print(f"Key:    {os.path.basename(key_file)}{tag}, created {key.created})")
        print(f"Folder: {os.path.dirname(report_path)}")
    else:
        r = results[first]
        print(f"\nMasked {len(cols[first])} column(s): {', '.join(map(str, cols[first]))}")
        for note in r["notes"]:
            print("Note:", note, file=sys.stderr)
        if r["text_found"] is not None and len(r["text_found"]):
            print(r["text_found"].to_string(index=False))
        print(f"Masked file: {out_paths[first]}\nReport:      {report_path}")
        print(f"Key:         {key_file}{' (new)' if new_key else ' (reused)'}")
    if new_key:
        print("\nKEEP THE KEY FILE PRIVATE. Store it with the original data and never share it.\n"
              "It is the only way to turn the codes back into the original identifiers.",
              file=sys.stderr)
    print("\nTo repeat this masking in one line:\n"
          + _repeat_command(paths, cols, suggested, ip_of, latlon_of, ip_db, key_file, mask_text))

    if batch:
        return MaskResult(data={p: results[p]["data"] for p in paths},
                          output={p: out_paths[p] for p in paths},
                          report=report_path, key_file=key_file)
    return MaskResult(data=results[first]["data"], output=out_paths[first],
                      report=report_path, key_file=key_file)
