import re

import pandas as pd

from .utils import is_blank, to_str

# Column names (lower case, letters and digits only) that usually hold
# identifiers in survey files (Qualtrics, Credamo, Prolific/MTurk/Connect
# data, lab data).
ID_COLUMN_NAMES = {
    # MTurk
    "workerid": "MTurk worker ID", "assignmentid": "MTurk/Connect assignment ID",
    "hitid": "MTurk HIT ID", "mturkcode": "completion code",
    # Prolific
    "prolificpid": "Prolific ID", "prolificid": "Prolific ID",
    "studyid": "Prolific study ID", "sessionid": "Prolific session ID",
    # CloudResearch Connect
    "participantid": "Prolific/Connect participant ID", "projectid": "Connect project ID",
    "cloudresearchid": "CloudResearch ID", "connectid": "Connect ID",
    # Generic
    "id": "participant ID", "pid": "participant ID", "aid": "participant ID",
    "rid": "participant ID", "randomid": "random ID", "completioncode": "completion code",
    # Qualtrics built-in
    "ipaddress": "IP address", "locationlatitude": "location (latitude)",
    "locationlongitude": "location (longitude)", "recipientemail": "email",
    "recipientfirstname": "name", "recipientlastname": "name",
    "externalreference": "external reference", "externaldatareference": "external reference",
    "email": "email", "emailaddress": "email",
    # Location and contact details, e.g. added by a geolocation step
    "zip": "location (zip code)", "zipcode": "location (zip code)",
    "postcode": "location (postal code)", "postalcode": "location (postal code)",
    "city": "location (city)", "region": "location (region)", "areacode": "phone area code",
    "phone": "phone number", "phonenumber": "phone number", "telephone": "phone number",
    # Credamo (Chinese headers). 作答ID is the response ID, like Qualtrics'
    # ResponseId, so it is not suggested.
    "用户id": "Credamo user ID", "userid": "user ID",
    "经度": "location (longitude)", "纬度": "location (latitude)",
    "省份": "location (province)", "城市": "location (city)",
    "手机号": "phone number", "电话": "phone number", "邮箱": "email",
}

# Patterns that identify a whole cell as an identifier. Browser versions
# such as "145.0.0.0" are not IP addresses.
ID_PATTERNS = {
    "Prolific ID": r"^[0-9a-f]{24}$",
    "MTurk worker ID": r"^A(?=[0-9A-Z]*[0-9])[0-9A-Z]{11,20}$",
    "email": r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$",
    "IP address": r"^(?!.*\.0\.0\.0$)(\d{1,3}\.){3}\d{1,3}$|^[0-9a-fA-F:]*:[0-9a-fA-F:]+$",
}
_ID_RE = {k: re.compile(v, re.ASCII) for k, v in ID_PATTERNS.items()}


def normalize_name(x):
    return "".join(c for c in x.lower() if c.isalnum())


def column_strings(series):
    return [to_str(v) for v in series]


def suggest_id_columns(data, include_empty=False):
    """Suggest columns that contain participant identifiers.

    Looks at column names and at the content of the columns. Empty columns
    are left out unless ``include_empty``. Returns a DataFrame with
    ``column``, ``reason`` and ``empty``.
    """
    rows = []
    for col in data.columns:
        x = [v for v in column_strings(data[col]) if not is_blank(v)]
        by_name = ID_COLUMN_NAMES.get(normalize_name(str(col)))
        if not x:
            if include_empty and by_name:
                rows.append((col, by_name, True))
            continue
        if by_name:
            rows.append((col, by_name, False))
            continue
        for kind, rx in _ID_RE.items():
            hits = sum(1 for v in x if rx.search(v.strip(" \t\r\n")))
            if hits / len(x) >= 0.5:
                rows.append((col, f"{kind} (detected from content)", False))
                break
    return pd.DataFrame(rows, columns=["column", "reason", "empty"])


def is_latitude(col):
    return bool(re.search("latitude|纬度", col, re.I))


def is_longitude(col):
    return bool(re.search("longitude|经度", col, re.I))


def is_ip_column(data, col):
    if normalize_name(col) == "ipaddress":
        return True
    x = [v.strip(" \t\r\n") for v in column_strings(data[col]) if not is_blank(v)]
    rx = _ID_RE["IP address"]
    return bool(x) and sum(1 for v in x if rx.search(v)) / len(x) >= 0.5
