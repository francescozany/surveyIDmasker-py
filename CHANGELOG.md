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
