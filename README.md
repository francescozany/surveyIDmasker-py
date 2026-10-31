# surveyIDmasker (Python)

Mask participant identifiers in survey exports before sharing them. This is the
Python port of the [surveyIDmasker R package](../surveyIDmasker).

**Works with survey data files** in CSV, SPSS `.sav` and Excel `.xlsx` format:
Qualtrics exports (extra header rows are kept), Credamo exports (Chinese column
names) and lab data files with one header row.

Prolific, CloudResearch Connect and MTurk IDs, IP addresses, locations and
emails are replaced with `MSK` plus an encrypted code, for example
`MSKBccdxZewIPBZRaFxcnEafypOrWP6dPsTkW2x`. The same value always gets the same
code within the file, so repeat participants can still be spotted. Blank cells
become `MSKblank`.

**Compatible with the R version.** The encryption is identical, so one
`_KEY_*.txt` file gives the same codes in R and in Python, and either language
can `reidentify` files masked by the other.

## Install

Python 3.11 or newer.

```bash
pip install "surveyidmasker[location]"   # [location] = country/state of coordinates
```

(From a checkout: `pip install -e ".[location]"`.)

## Use

```python
from surveyidmasker import mask_survey
mask_survey()          # guided: pick the file, confirm columns, choose options
```

Type `q` at any question to exit. Nothing is saved until the end. The original
file is never changed. Files written:

- `<name>_masked_<date>_<time>.<ext>`: the file to share;
- `<name>_masked_<date>_<time>_report.txt`: what was masked and when;
- `<name>_KEY_<date>_<time>.txt`: the private key. **Keep it, never share it.**

### In one line (scripts, AI agents)

Anything you pass is not asked:

```python
from surveyidmasker import list_columns, mask_survey, reidentify

list_columns("my_study.sav")      # column numbers; * = suggested
res = mask_survey("my_study.sav",
                  columns=[4, 14, 15, 36],      # numbers, names, or "suggested"
                  ip="full",                    # "full" or "country_state"
                  latlon="country_state")       # "full" or "country_state"
reidentify(res.output, key_file=res.key_file)
```

Command line:

```bash
surveyidmasker columns my_study.sav
surveyidmasker mask my_study.csv --columns suggested --ip full --latlon full
surveyidmasker reidentify my_study_masked_*.csv --key-file my_study_KEY_*.txt
```

## Good to know

- **The key file.** Codes are the identifiers encrypted with a private key.
  Keep it with the original data and **never share it**: without it nobody can
  turn the codes back; with it you can run `reidentify`.
- **Same codes across studies**: pass `key_file=` to reuse a key.
- **Spotting repeat participants**: the same platform ID code means the same
  person. Equal IP or location codes only mean the same network or estimated
  place, so treat them as supporting evidence, not proof.
- **Country / state** (`latlon="country_state"`) uses an offline database of
  places. Points within about 10 km of a border or coast, or far from any
  place, get a plain `MSK...` code (for the US, the country only: `MSKUS...`).
  This can differ slightly from the R version, which uses a world map. It only
  changes the readable prefix, never the encrypted part.
- **IP addresses with country/state** need the free DB-IP "IP to City Lite" CSV
  from <https://db-ip.com/db/lite.php> (`ip_db="path/to/dbip-city-lite.csv"`).
  IP geolocation by DB-IP (db-ip.com), CC BY 4.0: please credit it.
- **Free text**: other columns are checked for things that look like IDs,
  emails or IP addresses. Use `mask_text=True` to replace them too.
- **CSV files must be UTF-8** (Excel: "CSV UTF-8").
- Your survey responses never leave your computer.

## Tests

```bash
pip install -e ".[dev]" && pytest
```

## Cite

> Zanibellato, F. (2026). *surveyIDmasker: Mask participant identifiers in
> survey exports* (Version 0.2.0). Zenodo. https://doi.org/10.5281/zenodo.23599338
