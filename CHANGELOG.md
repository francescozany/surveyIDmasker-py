# surveyidmasker (Python) 0.3.0

Port of surveyIDmasker (R) 0.3.0.

* Several files can be masked in one run with the same key: give a list of files
  or a folder to `mask_survey()` (or several files to the command line). The same
  identifier gets the same code in every file, and one combined report lists every
  file, its masked columns and checksum.
* Columns and IP/location rules can be set per file with a dict keyed by file name,
  e.g. studies with different IRB rules on what can be shared.
* Reports say which key was used by the time it was created (never the key itself);
  the key file keeps the list of every file masked with it.
* Identifiers from all files being masked are looked for in the free text of each file.
* Codes that keep a region now separate it from the code with `_`, and US states are
  written the ISO 3166-2 way: `MSKIT_...`, `MSKUS-CT_...`. Codes made before (no `_`)
  can still be re-identified.
* `reidentify()` takes several files (or a folder) at once, and a wrong key says when
  it was created.
* `reverse_geocoder` is now installed with the package (no `[location]` extra).
* In the guided mode, files are given as paths (drag them into the terminal) instead
  of a file window.
* Checked against R 0.3.0: identical output on the example files (CSV, SPSS, Excel), single and
  batch, apart from the readable region prefix and trailing spaces in Excel cells
  (R trims them).

# surveyidmasker (Python) 0.2.0

First release of the Python port of the surveyIDmasker R package (R version 0.2.0).

* Same functions as the R package: `mask_survey()`, `mask_values()`, `reidentify()`,
  `list_columns()`, `suggest_id_columns()`, `scan_text()`, `new_key_file()`,
  `read_survey()`, plus a `surveyidmasker` command-line tool.
* Codes and key files are byte-compatible with the R package.
* Checked against the R package on 52 example survey files (Qualtrics, Credamo,
  lab data; CSV, SPSS, Excel): identical output wherever R produces output.
* Country/state of coordinates (`latlon="country_state"`) uses the offline
  `reverse_geocoder` database. Points within about 10 km of a border or coast
  get a plain `MSK` code (or the country only, for the US) instead of a
  possibly wrong region.
* Excel/CSV cells keep trailing spaces (R's Excel reader trims them).
