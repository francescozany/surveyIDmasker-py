"""Mask participant identifiers in survey exports (Python port of the
surveyIDmasker R package; key files and codes are interchangeable)."""
__version__ = "0.3.0"

from .detect import suggest_id_columns
from .key import new_key_file
from .mask import mask_values
from .read_write import read_survey
from .reidentify import reidentify
from .scan_text import scan_text
from .survey import list_columns, mask_survey


def citation():
    return (f"Zanibellato, F. (2026). surveyIDmasker: Mask participant identifiers in survey "
            f"exports (Version {__version__}). Zenodo. https://doi.org/10.5281/zenodo.23599338")


__all__ = ["list_columns", "mask_survey", "mask_values", "new_key_file", "read_survey",
           "reidentify", "scan_text", "suggest_id_columns", "citation"]
