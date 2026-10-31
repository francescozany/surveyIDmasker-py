"""Country / US state of coordinates and IP addresses."""
import math
import re

import numpy as np
import pandas as pd

from .utils import is_blank, to_str

_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA",
    "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA",
    "kansas": "KS", "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV", "new hampshire": "NH",
    "new jersey": "NJ", "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA",
    "rhode island": "RI", "south carolina": "SC", "south dakota": "SD", "tennessee": "TN",
    "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC", "washington, d.c.": "DC", "washington d.c.": "DC",
}

# A point farther than this from the nearest known place has no region
# (open sea).
MAX_KM = 60
# Distance of the extra points used to detect borders and coasts.
RING_KM = 10


def state_abb(name):
    if name is None or name != name:
        return None
    return _STATES.get(str(name).strip(" \t\r\n").lower())


def _haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 6371 * 2 * math.asin(math.sqrt(a))


def latlon_region(lat, lon):
    """Region code for each coordinate: "IT", "FR", ... and "USCT", "USNY",
    ... for the United States. None when the point is near a border or the
    coast (within about 10 km) or far from any known place.

    Uses the offline ``reverse_geocoder`` database (nearest known place).
    """
    try:
        import reverse_geocoder as rg
    except ImportError as e:
        raise ImportError(
            "latlon='country_state' needs the 'reverse_geocoder' package: "
            "pip install reverse_geocoder") from e

    def num(v):
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return None if math.isnan(f) else f

    la = [num(v) for v in lat]
    lo = [num(v) for v in lon]
    ok = [i for i in range(len(la))
          if la[i] is not None and lo[i] is not None and abs(la[i]) <= 90 and abs(lo[i]) <= 180]
    out = [None] * len(la)
    if not ok:
        return out
    # Each point is checked together with four points ~RING_KM away. If they
    # do not all agree on one region, the point is near a border (or the
    # coast) and gets no region, so it is never given a confident wrong one.
    offsets = [(0, 0), (1, 0), (-1, 0), (0, 1), (0, -1)]
    queries = []
    for i in ok:
        for dy, dx in offsets:
            la_ = max(-89.9, min(89.9, la[i] + dy * RING_KM / 111.0))
            lo_ = lo[i] + dx * RING_KM / (111.0 * max(math.cos(math.radians(la_)), 0.1))
            queries.append((la_, ((lo_ + 180) % 360) - 180))
    found = rg.search(queries, mode=1)
    for n, i in enumerate(ok):
        countries, states = set(), set()
        for k in range(len(offsets)):
            r = found[n * len(offsets) + k]
            q = queries[n * len(offsets) + k]
            if _haversine_km(q[0], q[1], float(r["lat"]), float(r["lon"])) > MAX_KM:
                countries.add(None)
                continue
            countries.add(r["cc"])
            states.add(state_abb(r["admin1"]) if r["cc"] == "US" else None)
        if len(countries) != 1 or None in countries:
            continue
        cc = countries.pop()
        # Neighbouring states disagree: keep only the country.
        out[i] = "US" + states.pop() if cc == "US" and len(states) == 1 and None not in states else cc
    return out


def ip_to_number(ip):
    ip = None if ip is None else ip.strip(" \t\r\n")
    if ip is None or not re.fullmatch(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}", ip):
        return np.nan
    p = [int(s) for s in ip.split(".")]
    if any(s > 255 for s in p):
        return np.nan
    return float(p[0] * 256**3 + p[1] * 256**2 + p[2] * 256 + p[3])


def read_ip_db(path):
    """Read a DB-IP Lite CSV (https://db-ip.com/db/lite.php). Both the "IP to
    City" file and the "IP to Country" file work. IPv6 rows are skipped."""
    raw = pd.read_csv(path, header=None, dtype=str, keep_default_na=False)
    city = raw.shape[1] >= 5
    raw = raw[~raw[0].str.contains(":", regex=False)]
    db = pd.DataFrame({
        "start": [ip_to_number(v) for v in raw[0]],
        "end": [ip_to_number(v) for v in raw[1]],
        "country": raw[3] if city else raw[2],
        "state": raw[4] if city else None,
    })
    return db.dropna(subset=["start", "end"]).sort_values("start").reset_index(drop=True)


def ip_region(ips, db):
    n = np.array([ip_to_number(v) for v in ips], dtype=float)
    out = [None] * len(ips)
    starts = db["start"].to_numpy()
    for k, v in enumerate(n):
        if np.isnan(v):
            continue
        i = int(np.searchsorted(starts, v, side="right")) - 1
        if i < 0 or v > db["end"].iat[i]:
            continue
        cc = db["country"].iat[i]
        if cc == "ZZ":
            continue
        if cc == "US":
            st = state_abb(db["state"].iat[i])
            out[k] = "US" + st if st else cc
        else:
            out[k] = cc
    return out
