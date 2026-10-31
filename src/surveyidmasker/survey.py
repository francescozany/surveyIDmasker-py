import hashlib
import os
import re
import sys
import time
from dataclasses import dataclass

import pandas as pd

from . import utils
from .detect import is_ip_column, is_latitude, is_longitude, suggest_id_columns
from .key import derive_key, read_key_file, write_key_file
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
    data: pd.DataFrame
    output: str
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


def _choose_columns(data, ask):
    found = suggest_id_columns(data, include_empty=True)
    sugg = found[~found["empty"]]
    empty = list(found.loc[found["empty"], "column"])
    if not ask:
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
        ans = _ask_line("\nNumbers of the columns to mask (e.g. 4 14 15 120-123), "
                        + ("Enter for the suggested ones, " if not sugg.empty else "") + "or q to exit: ")
        if ans == "":
            if not sugg.empty:
                return list(sugg["column"])
            _quit()
        idx = _parse_numbers(ans, len(cols))
        if idx:
            print("Selected:", ", ".join(cols[i - 1] for i in idx))
            if _ask_line("Is this right? [Y/n, or q to exit] ").lower() in ("", "y", "yes"):
                return [cols[i - 1] for i in idx]
        else:
            print(f"Please type column numbers between 1 and {len(cols)}, e.g. 4 14 15 or 120-123.")


def _choose_mode(mode, what, ask):
    if mode is not None:
        return mode
    if not ask:
        arg = "ip" if what == "IP addresses" else "latlon"
        print(f"Masking {what} fully (set it with {arg}=).", file=sys.stderr)
        return "full"
    choice = _ask_choice(f"How should {what} be masked?", [
        "Fully masked, like the other identifiers (MSK + code)",
        "Keep country, and state for the US (e.g. MSKUSCT..., MSKIT...)"])
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


def resolve_columns(data, columns):
    names = list(data.columns)
    if isinstance(columns, str) and columns == "suggested":
        cols = list(suggest_id_columns(data)["column"])
        if not cols:
            raise ValueError("No identifier columns were found. Give them with `columns=`.")
        return cols
    if isinstance(columns, (str, int)):
        columns = [columns]
    columns = list(columns)
    if columns and all(isinstance(c, (int, float)) and not isinstance(c, bool) for c in columns):
        bad = [c for c in columns if c < 1 or c > len(names) or c != round(c)]
        if bad:
            raise ValueError(f"Column number(s) out of range (1-{len(names)}): {', '.join(map(str, bad))}")
        return list(dict.fromkeys(names[int(c) - 1] for c in columns))
    bad = [c for c in columns if c not in names]
    if bad:
        raise ValueError(f"Column(s) not found: {', '.join(map(str, bad))}\n"
                         'See the column names and numbers with list_columns("<file>").')
    return list(dict.fromkeys(columns))


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


def _unknown_note(region, values, what):
    n = sum(1 for r, v in zip(region, values) if r is None and not is_blank(v))
    if not n:
        return []
    return [f"{what}: {n} value(s) could not be matched to a country and got a plain MSK code."]


def _repeat_command(file, columns, ip, latlon, ip_db, key_file, mask_text):
    q = lambda s: '"' + str(s).replace('"', '\\"') + '"'
    args = [q(file), "columns=[" + ", ".join(q(c) for c in columns) + "]"]
    if ip:
        args.append(f"ip={q(ip)}")
    if latlon:
        args.append(f"latlon={q(latlon)}")
    if ip_db:
        args.append(f"ip_db={q(ip_db)}")
    args.append(f"key_file={q(key_file)}")
    if mask_text:
        args.append("mask_text=True")
    return f"mask_survey({', '.join(args)})"


def _write_report(path, input_, output, n_rows, columns, ip, latlon, notes, text_found, mask_text):
    from . import __version__
    label = lambda m: "country/US state kept" if m == "country_state" else "full"
    lines = [
        "surveyIDmasker report", "======================",
        "Created:         " + time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        f"Package version: {__version__} (Python)",
        "Original file:   " + os.path.basename(input_),
        "Masked file:     " + os.path.basename(output),
        f"Rows:            {n_rows}", "", "Masked columns:",
    ] + ["  " + c for c in columns] + [""]
    if ip is not None:
        lines.append("IP addresses:     " + label(ip))
    if latlon is not None:
        lines.append("Latitude/longitude: " + label(latlon))
    lines += ["Codes:           encrypted with a private key kept by the authors; without",
              "                 the key they cannot be turned back into identifiers",
              "Blank values:    MSKblank"]
    if notes:
        lines += ["", "Notes:"] + ["  - " + n for n in notes]
    if text_found is not None and len(text_found):
        lines += ["", "Possible identifiers in other columns (%s):" % ("masked" if mask_text else "NOT masked")]
        lines += [f"  {r.column:<30} {r.type:<18} {r.cells} cell(s)" for r in text_found.itertuples()]
    with open(output, "rb") as f:
        lines += ["", "SHA-256 of masked file: " + hashlib.sha256(f.read()).hexdigest()]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def mask_survey(file=None, columns=None, ip=None, latlon=None, ip_db=None, key_file=None,
                scan_text=True, mask_text=False, output=None, overwrite=False,
                header_rows=None, interactive=None):
    """Mask participant identifiers in a survey export.

    Replaces identifiers (Prolific/Connect/MTurk IDs, IP addresses, locations,
    emails, ...) with ``"MSK"`` plus a code made by encrypting the value with a
    private key. The same value gets the same code everywhere in the file;
    blank cells become ``"MSKblank"``. ``reidentify`` restores the originals
    given the key.

    Anything you pass is not asked; with no ``file`` or ``columns`` (and a
    terminal) you are guided step by step. ``columns`` is a list of names,
    1-based numbers (see ``list_columns``) or ``"suggested"``. ``ip`` and
    ``latlon`` are ``"full"`` or ``"country_state"`` (the latter needs ``ip_db``
    for IP addresses). The original file is never changed. Returns a
    :class:`MaskResult`.
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
        print("Type q at any question to exit. Nothing is saved until the end.\n")
    if file is None:
        if not ask:
            raise ValueError("Please give the path of the file to mask.")
        file = _ask_line("Path of the survey file to mask (.csv, .sav or .xlsx): ").strip("'\"")
    file = os.fspath(file)
    x = read_survey(file, header_rows)
    data = x.data
    print(f"Read {os.path.basename(file)}: {len(data)} rows, {data.shape[1]} columns.")

    columns = _choose_columns(data, ask) if columns is None else resolve_columns(data, columns)
    if not columns:
        raise ValueError("No columns selected, nothing to mask.")

    lat_cols = [c for c in columns if is_latitude(c)]
    lon_cols = [c for c in columns if is_longitude(c)]
    has_pair = len(lat_cols) == 1 and len(lon_cols) == 1
    ip_cols = [c for c in columns if is_ip_column(data, c)]

    if has_pair:
        latlon = _choose_mode(latlon, "latitude/longitude", ask)
    if ip_cols:
        ip = _choose_mode(ip, "IP addresses", ask)
    for value, name in ((latlon if has_pair else None, "latlon"), (ip if ip_cols else None, "ip")):
        if value is not None and value not in MODES:
            raise ValueError(f'{name} must be "full" or "country_state".')
    latlon = latlon if has_pair else None
    ip = ip if ip_cols else None
    if ip == "country_state" and ip_db is None:
        ip_db = _choose_ip_db(ask)
        if ip_db is None:
            ip = "full"
    if ip_db is not None and not os.path.exists(ip_db):
        raise FileNotFoundError(f"IP database not found: {ip_db}")

    stamp = utils.time_stamp()
    out_path = output or re.sub(r"(\.[^.]+)$", f"_masked_{stamp}\\1", file)
    report_path = re.sub(r"\.[^.]+$", "_report.txt", out_path)
    if not overwrite and (os.path.exists(out_path) or os.path.exists(report_path)):
        raise FileExistsError(f"Output already exists: {out_path}\nUse overwrite=True to replace it.")
    if os.path.abspath(out_path) == os.path.abspath(file):
        raise ValueError("The output must be a different file from the original.")

    # A key is never overwritten: that would make earlier masked files
    # impossible to re-identify.
    new_key = key_file is None
    if new_key:
        key_file = re.sub(r"\.[^.]+$", f"_KEY_{stamp}.txt", file)
        if os.path.exists(key_file):
            raise FileExistsError(
                f"A key file already exists: {key_file}\nReuse it with key_file=\"{key_file}\", "
                "or move it elsewhere first.")
        secret = os.urandom(32)
        key = derive_key(secret)
    else:
        key = read_key_file(key_file)

    cb = Codebook(key)
    notes, done = [], []
    originals = [to_str(v) for c in columns for v in data[c]]
    originals = [v.strip(" \t\r\n") for v in originals if v is not None]

    def put(col, values):
        data[col] = pd.Series(values, index=data.index, dtype=object)

    # Latitude and longitude are coded from the (lat, lon) pair, so the same
    # place gives the same pair of codes.
    if has_pair:
        lat_col, lon_col = lat_cols[0], lon_cols[0]
        lat = [to_str(v) for v in data[lat_col]]
        lon = [to_str(v) for v in data[lon_col]]
        ws = " \t\r\n"
        pair = [(a or "").strip(ws) + "," + (b or "").strip(ws) for a, b in zip(lat, lon)]
        prefix = "MSK"
        if latlon == "country_state":
            region = latlon_region(lat, lon)
            prefix = ["MSK" if r is None else "MSK" + r for r in region]
            notes += _unknown_note(region, lat, "latitude/longitude")
        put(lat_col, mask_with(cb, lat, keys=[LAT_TAG + p for p in pair], prefix=prefix))
        put(lon_col, mask_with(cb, lon, keys=[LON_TAG + p for p in pair], prefix=prefix))
        done = [lat_col, lon_col]

    if ip == "country_state":
        print("Reading the IP database (this can take a minute)...")
        db = read_ip_db(ip_db)
        for col in ip_cols:
            addr = [to_str(v) for v in data[col]]
            reg = ip_region(addr, db)
            notes += _unknown_note(reg, addr, f"IP addresses ({col})")
            put(col, mask_with(cb, addr, prefix=["MSK" if r is None else "MSK" + r for r in reg]))
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
                notes.append("Identifiers found inside other columns were replaced with their codes (see below).")
            else:
                notes.append("Possible identifiers were found inside other columns (see below). "
                             "Check them, or rerun with mask_text=True.")

    if x.format == "sav":
        long = long_labels(x)
        if long:
            notes.append(f"{len(long)} SPSS variable label(s) longer than 256 characters were "
                         f"shortened (an SPSS file limit): {', '.join(map(str, long))}.")

    write_survey(x, out_path, changed=set(columns) | (set(text_found["column"]) if mask_text and text_found is not None else set()))
    if new_key:
        write_key_file(secret, key_file, used_for=os.path.basename(file))
    _write_report(report_path, file, out_path, len(data), columns, ip, latlon,
                  notes, text_found, mask_text)

    print(f"\nMasked {len(columns)} column(s): {', '.join(map(str, columns))}")
    for n in notes:
        print("Note:", n, file=sys.stderr)
    if text_found is not None and len(text_found):
        print(text_found.to_string(index=False))
    print(f"Masked file: {out_path}\nReport:      {report_path}")
    print(f"Key:         {key_file}{' (new)' if new_key else ' (reused)'}")
    if new_key:
        print("\nKEEP THE KEY FILE PRIVATE. Store it with the original data and never share it.\n"
              "It is the only way to turn the codes back into the original identifiers.",
              file=sys.stderr)
    print("\nTo repeat this masking in one line:\n"
          + _repeat_command(file, columns, ip, latlon, ip_db, key_file, mask_text))
    return MaskResult(data=data, output=out_path, report=report_path, key_file=key_file)
