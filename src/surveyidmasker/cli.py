import argparse
import sys

from . import citation, list_columns, mask_survey, new_key_file, reidentify


def _columns(values):
    if values is None:
        return None
    if values == ["suggested"]:
        return "suggested"
    if all(v.isdigit() for v in values):
        return [int(v) for v in values]
    return values


def main(argv=None):
    p = argparse.ArgumentParser(prog="surveyidmasker", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("mask", help="mask identifiers in a survey file")
    m.add_argument("files", nargs="*", help="one or more files, or a folder (same key for all)")
    m.add_argument("--columns", nargs="+", help='names, numbers, or "suggested"')
    m.add_argument("--ip", choices=["full", "country_state"])
    m.add_argument("--latlon", choices=["full", "country_state"])
    m.add_argument("--ip-db")
    m.add_argument("--key-file")
    m.add_argument("--no-scan-text", action="store_true")
    m.add_argument("--mask-text", action="store_true")
    m.add_argument("--output")
    m.add_argument("--overwrite", action="store_true")
    m.add_argument("--header-rows", type=int)

    r = sub.add_parser("reidentify", help="restore original identifiers using the key")
    r.add_argument("files", nargs="+", help="masked file(s) or a folder")
    r.add_argument("--key-file", required=True)
    r.add_argument("--output")
    r.add_argument("--overwrite", action="store_true")
    r.add_argument("--header-rows", type=int)

    c = sub.add_parser("columns", help="list columns with their numbers")
    c.add_argument("file")

    k = sub.add_parser("new-key", help="create a key file in advance")
    k.add_argument("path")

    sub.add_parser("cite", help="how to cite the package")

    a = p.parse_args(argv)
    try:
        if a.cmd == "mask":
            mask_survey((a.files if len(a.files) > 1 else a.files[0]) if a.files else None, _columns(a.columns), a.ip, a.latlon, a.ip_db, a.key_file,
                        not a.no_scan_text, a.mask_text, a.output, a.overwrite, a.header_rows)
        elif a.cmd == "reidentify":
            reidentify(a.files if len(a.files) > 1 else a.files[0], a.key_file, a.output, a.overwrite, a.header_rows)
        elif a.cmd == "columns":
            list_columns(a.file)
        elif a.cmd == "new-key":
            print(new_key_file(a.path))
        else:
            print(citation())
    except (ValueError, FileNotFoundError, FileExistsError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
