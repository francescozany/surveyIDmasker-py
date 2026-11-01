import re

import pandas as pd
import pyreadstat
import pytest

from surveyidmasker import (list_columns, mask_survey, mask_values, new_key_file, read_survey,
                            reidentify, scan_text, suggest_id_columns)
from surveyidmasker import utils
from surveyidmasker.detect import is_latitude, is_longitude
from surveyidmasker.key import derive_key
from surveyidmasker.location import latlon_region
from surveyidmasker.mask import Codebook, decrypt_code, encrypt_code, mask_with
from surveyidmasker.scan_text import mask_in_text

FAKE_EXPORT = [
    "StartDate,IPAddress,LocationLatitude,LocationLongitude,PROLIFIC_PID,participantId,comment",
    "Start Date,IP Address,Location Latitude,Location Longitude,PROLIFIC_PID,participantId,Any comments?",
    '"{""ImportId"":""startDate""}","{""ImportId"":""ipAddress""}","{""ImportId"":""locationLatitude""}","{""ImportId"":""locationLongitude""}","{""ImportId"":""PROLIFIC_PID""}","{""ImportId"":""participantId""}","{""ImportId"":""QID1_TEXT""}"',
    "2026-01-01 10:00:00,1.2.3.4,41.3083,-72.9279,5f1a2b3c4d5e6f7a8b9c0d1e,5f1a2b3c4d5e6f7a8b9c0d1e,my id is 5f1a2b3c4d5e6f7a8b9c0d1e",
    "2026-01-01 11:00:00,5.6.7.8,41.9028,12.4964,aaaaaaaaaaaaaaaaaaaaaaaa,,nice study",
    "2026-01-01 12:00:00,1.2.3.4,41.3083,-72.9279,5f1a2b3c4d5e6f7a8b9c0d1e,5f1a2b3c4d5e6f7a8b9c0d1e,",
]
ALL_ID_COLS = ["IPAddress", "LocationLatitude", "LocationLongitude", "PROLIFIC_PID", "participantId"]


@pytest.fixture
def fake_export(tmp_path):
    p = tmp_path / "study.csv"
    p.write_text("\n".join(FAKE_EXPORT) + "\n", encoding="utf-8")
    return str(p)


@pytest.fixture
def fixed_stamp(monkeypatch):
    monkeypatch.setattr(utils, "time_stamp", lambda: "2026-01-01_10-00")


def lines(path):
    with open(path, encoding="utf-8") as f:
        return f.read().splitlines()


def test_codes_match_the_r_package(tmp_path):
    # Codes produced by the R package for this key.
    key_file = tmp_path / "k.txt"
    key_file.write_text("key: fbbc8d40dc2d6787f841986e626618973ba640f33a78bd92f2d9feeb69422865\n")
    expected = {
        "5f1a2b3c4d5e6f7a8b9c0d1e": "E0MihuTyU9TqvtzDENrMj2KAS1Ad3Aj4PBRxo55Za7P8L",
        "A1B2C3": "BIlhYKhRHuddOobrho4U0Ka1YPpKiWrcVMS2",
        "河南省": "BeqohnVyG6QBjuHybcwdrKLzO5duXSHKqrkB",
    }
    got = mask_values(list(expected), key_file=str(key_file))
    assert got == ["MSK" + c for c in expected.values()]
    r = reidentify  # noqa: F841  (decryption is checked below)
    from surveyidmasker.key import read_key_file
    key = read_key_file(str(key_file))
    for v, c in expected.items():
        assert decrypt_code(c, key) == v


def test_codes_have_the_right_form_and_repeat_for_equal_values():
    m = mask_values(["A1B2C3", "", None, "A1B2C3", " A1B2C3 ", "XYZ"])
    for i in (0, 3, 5):
        assert re.fullmatch(r"MSK[A-Za-z0-9]{12,}", m[i])
    assert m[1:3] == ["MSKblank", "MSKblank"]
    assert m[0] == m[3] == m[4]
    assert m[0] != m[5]


def test_same_key_same_codes_other_key_not(tmp_path):
    k1 = new_key_file(str(tmp_path / "k1.txt"))
    k2 = new_key_file(str(tmp_path / "k2.txt"))
    assert mask_values(["x1", "y2"], key_file=k1) == mask_values(["x1", "y2"], key_file=k1)
    assert mask_values(["x1"], key_file=k1) != mask_values(["x1"], key_file=k2)
    assert mask_values(["x1"]) != mask_values(["x1"])
    with pytest.raises(FileExistsError, match="already exists"):
        new_key_file(k1)
    assert "x1" not in (tmp_path / "k1.txt").read_text()


def test_encrypt_roundtrip_and_wrong_key():
    k1, k2 = derive_key(b"\x01" * 32), derive_key(b"\x02" * 32)
    for v in ["a", "x" * 200, "Zürich", "河南省", "\x1fa41.3,-72.9"]:
        c = encrypt_code(v, k1)
        assert decrypt_code(c, k1) == v
        assert decrypt_code(c, k2) is None


def test_csv_keeps_header_rows_and_masks_consistently(fake_export, fixed_stamp):
    res = mask_survey(fake_export, columns=ALL_ID_COLS, ip="full", latlon="full",
                      scan_text=True, mask_text=True)
    assert lines(res.output)[:3] == lines(fake_export)[:3]
    d = read_survey(res.output).data
    assert d["PROLIFIC_PID"][0] == d["participantId"][0] == d["PROLIFIC_PID"][2]
    assert d["participantId"][1] == "MSKblank"
    assert d["LocationLatitude"][0] == d["LocationLatitude"][2]
    assert d["LocationLatitude"][0] != d["LocationLongitude"][0]
    assert d["comment"][0] == "my id is " + d["PROLIFIC_PID"][0]
    out = "\n".join(lines(res.output))
    assert not re.search(r"5f1a2b3c4d5e6f7a8b9c0d1e|1\.2\.3\.4|41\.3083", out)
    import hashlib
    sha = hashlib.sha256(open(res.output, "rb").read()).hexdigest()
    assert any(sha in line for line in lines(res.report))
    with pytest.raises(FileExistsError, match="already exists"):
        mask_survey(fake_export, columns="IPAddress", ip="full")


def test_country_state_keeps_country_and_us_state(fake_export):
    assert latlon_region([41.3083, 41.9028, None], [-72.9279, 12.4964, 1]) == ["US-CT", "IT", None]
    res = mask_survey(fake_export, columns=["LocationLatitude", "LocationLongitude"],
                      latlon="country_state", scan_text=False)
    d = read_survey(res.output).data
    assert all(re.fullmatch(r"MSK(US-CT|IT)_[A-Za-z0-9]{12,}", v) for v in d["LocationLatitude"])
    assert d["LocationLatitude"][1].startswith("MSKIT_")


def test_coastal_points_take_their_country_and_open_sea_none():
    got = latlon_region([30.3205, 36.8720, 0], [120.3834, 121.7546, -30])
    assert got == ["CN", "CN", None]


def test_border_points_get_no_wrong_region():
    # Zhuhai (next to Macau) and Jerusalem are within ~10 km of a border.
    assert latlon_region([22.2159, 31.77], [113.5501, 35.21]) == [None, None]
    # New York: country is certain, state is not (NJ is close).
    assert latlon_region([40.7128], [-74.006]) == ["US"]
    # Hawaii and Alaska work.
    assert latlon_region([21.3, 61.2], [-157.86, -149.9]) == ["US-HI", "US-AK"]


def test_suggestions_find_identifier_columns_by_name_and_content():
    d = pd.DataFrame({"workerId": ["A1B2C3D4E5F6G7"], "note": ["hi"], "other": ["x@y.com"],
                      "RecipientEmail": [None]})
    assert list(suggest_id_columns(d)["column"]) == ["workerId", "other"]
    s = suggest_id_columns(d, include_empty=True)
    assert list(s["column"]) == ["workerId", "other", "RecipientEmail"]
    assert list(s["empty"]) == [False, False, True]


@pytest.mark.parametrize("name", ["id", "Id", "ID"])
def test_column_named_id_is_suggested_but_not_response_id(name):
    d = pd.DataFrame({name: ["p1", "p2"], "ResponseId": ["R_1", "R_2"], "q": [1, 2]})
    assert list(suggest_id_columns(d)["column"]) == [name]


def test_list_columns_marks_empty_identifier_columns(tmp_path, capsys):
    p = tmp_path / "empty.csv"
    p.write_text("StartDate,IPAddress,Q1\nStart Date,IP Address,Question\n2026-01-01,,yes\n")
    cols = list_columns(str(p))
    assert re.search(r"\* +2 IPAddress \(empty\)", capsys.readouterr().out)
    assert list(cols["empty"]) == [False, True, False]
    with pytest.raises(ValueError, match="No identifier"):
        mask_survey(str(p), columns="suggested")


def test_spss_files_keep_variable_labels(tmp_path):
    p = str(tmp_path / "s.sav")
    d = pd.DataFrame({"workerId": ["A1B2C3D4E5F6G7", ""], "age": [30.0, 40.0]})
    pyreadstat.write_sav(d, p, column_labels=["MTurk worker", "Age"])
    res = mask_survey(p, columns="workerId", scan_text=False)
    back, meta = pyreadstat.read_sav(res.output)
    assert meta.column_labels == ["MTurk worker", "Age"]
    assert back["workerId"][1] == "MSKblank"
    assert list(back["age"]) == [30, 40]


def test_masking_inside_text_leaves_empty_cells_empty():
    cb = Codebook(derive_key(b"\x03" * 32))
    out = mask_in_text([None, "ip 1.2.3.4", "fine"], cb)
    assert out[0] is None
    assert out[1].startswith("ip MSK")
    assert out[2] == "fine"


def test_browser_versions_are_not_ip_addresses():
    d = pd.DataFrame({"MetaInfo_Version": ["145.0.0.0", "143.0.0.0"], "ip": ["1.2.3.4", "5.6.7.8"]})
    assert list(suggest_id_columns(d)["column"]) == ["ip"]
    assert len(scan_text(d, ["MetaInfo_Version"])) == 0


def test_columns_by_number_or_suggested(fake_export):
    res = mask_survey(fake_export, columns=[2, 5], ip="full", scan_text=False)
    d = read_survey(res.output).data
    assert d["PROLIFIC_PID"][0].startswith("MSK")
    assert d["LocationLatitude"][0] == "41.3083"
    with pytest.raises(ValueError, match="out of range"):
        mask_survey(fake_export, columns=[99], overwrite=True)
    res = mask_survey(fake_export, columns="suggested", ip="full", latlon="full",
                      scan_text=False, overwrite=True, key_file=res.key_file)
    assert read_survey(res.output).data["participantId"][0].startswith("MSK")


def test_country_of_ip_addresses_comes_from_a_dbip_file(tmp_path, fake_export):
    db = tmp_path / "dbip.csv"
    db.write_text('1.2.3.0,1.2.3.255,NA,US,Connecticut,"New Haven",41.3,-72.9\n'
                  '5.6.7.0,5.6.7.255,EU,IT,Lazio,Rome,41.9,12.5\n'
                  '2001:db8::,2001:db8::ffff,EU,FR,,,0,0\n')
    res = mask_survey(fake_export, columns="IPAddress", ip="country_state", ip_db=str(db),
                      scan_text=False)
    d = read_survey(res.output).data
    assert all(re.fullmatch(r"MSK(US-CT|IT)_[A-Za-z0-9]{12,}", v) for v in d["IPAddress"])
    assert d["IPAddress"][1].startswith("MSKIT_")
    with pytest.raises(ValueError, match="DB-IP"):
        mask_survey(fake_export, columns="IPAddress", ip="country_state", overwrite=True,
                    interactive=False)


def test_ip_and_latlon_are_checked_and_ignored_when_not_applicable(fake_export):
    with pytest.raises(ValueError, match="must be"):
        mask_survey(fake_export, columns="IPAddress", ip="country")
    with pytest.raises(ValueError, match="must be"):
        mask_survey(fake_export, columns=["LocationLatitude", "LocationLongitude"],
                    latlon="everything")
    res = mask_survey(fake_export, columns="PROLIFIC_PID", ip="country_state",
                      latlon="country_state", scan_text=False)
    assert read_survey(res.output).data["PROLIFIC_PID"][0].startswith("MSK")


def test_reidentify_restores_every_original_value(fake_export, fixed_stamp):
    res = mask_survey(fake_export, columns=ALL_ID_COLS, ip="full", latlon="country_state",
                      mask_text=True)
    assert re.search(r"study_masked_2026-01-01_10-00\.csv$", res.output)
    assert re.search(r"study_masked_2026-01-01_10-00_report\.txt$", res.report)
    assert res.key_file.endswith("study_KEY_2026-01-01_10-00.txt")
    back = reidentify(res.output, key_file=res.key_file)
    assert lines(back["output"]) == lines(fake_export)
    assert back["output"].endswith("study_reidentified_2026-01-01_10-00.csv")


def test_existing_key_is_never_overwritten_and_can_be_reused(fake_export, fixed_stamp):
    r1 = mask_survey(fake_export, columns="PROLIFIC_PID", scan_text=False)
    before = lines(r1.key_file)
    with pytest.raises(FileExistsError, match="key file already exists"):
        mask_survey(fake_export, columns="PROLIFIC_PID", overwrite=True)
    r2 = mask_survey(fake_export, columns="PROLIFIC_PID", overwrite=True,
                     key_file=r1.key_file, scan_text=False)
    # The key stays the same; only the history of its use grows.
    after = lines(r1.key_file)
    assert after[:len(before)] == before
    assert sum(bool(re.match(r"^#   .*study\.csv$", ln)) for ln in after) == 2
    assert list(r1.data["PROLIFIC_PID"]) == list(r2.data["PROLIFIC_PID"])


def test_wrong_key_cannot_reidentify(tmp_path, fake_export):
    res = mask_survey(fake_export, columns="PROLIFIC_PID", scan_text=False)
    other = new_key_file(str(tmp_path / "other_KEY.txt"))
    with pytest.raises(ValueError, match=r"match this key \(created"):
        reidentify(res.output, key_file=other)
    with pytest.raises(ValueError, match="does not look like"):
        reidentify(res.output, key_file=fake_export)


def test_each_runs_files_get_the_time_it_ran(fake_export, monkeypatch):
    monkeypatch.setattr(utils, "time_stamp", lambda: "2026-01-01_10-00")
    r1 = mask_survey(fake_export, columns="PROLIFIC_PID", scan_text=False)
    monkeypatch.setattr(utils, "time_stamp", lambda: "2026-01-01_10-05")
    r2 = mask_survey(fake_export, columns="PROLIFIC_PID", scan_text=False)
    assert r2.key_file.endswith("study_KEY_2026-01-01_10-05.txt")
    assert list(r1.data["PROLIFIC_PID"]) != list(r2.data["PROLIFIC_PID"])


def test_xlsx_roundtrip(tmp_path, fake_export, fixed_stamp):
    s = read_survey(fake_export)
    from surveyidmasker.read_write import write_survey
    s.format, s.sheet = "xlsx", "Sheet1"
    p = str(tmp_path / "x.xlsx")
    write_survey(s, p)
    res = mask_survey(p, columns="suggested", ip="full", latlon="full", scan_text=False)
    back = reidentify(res.output, key_file=res.key_file)
    d = read_survey(back["output"]).data
    assert d["PROLIFIC_PID"][0] == "5f1a2b3c4d5e6f7a8b9c0d1e"
    assert list(d.columns) == list(read_survey(p).data.columns)


def fake_credamo(path):
    d = pd.DataFrame({
        "作答ID": ["oB5yZqDnXwd", "mnakKv6dNAO", "Q4Z2rX4b8Bv"],
        "用户ID": ["5LwdoXve", "VMJbGkRM", "5LwdoXve"],
        "IP": ["120.219.185.45", "183.217.193.60", "120.219.185.45"],
        "经度": ["113.8067240", "12.4964", "113.8067240"],
        "纬度": ["36.0512680", "41.9028", "36.0512680"],
        "省份": ["河南省", "江西省", "河南省"],
        "城市": ["安阳市", "赣州市", "安阳市"],
        "您的年龄是？": [30.0, 41.0, 30.0],
    })
    pyreadstat.write_sav(d, path, column_labels=list(d.columns))
    return d


def test_credamo_columns_are_suggested_but_not_response_id(tmp_path):
    p = str(tmp_path / "credamo.sav")
    fake_credamo(p)
    d = read_survey(p).data
    s = suggest_id_columns(d)
    assert list(s["column"]) == list(d.columns[1:7])
    assert is_latitude(d.columns[4]) and is_longitude(d.columns[3])


def test_credamo_exports_masked_coded_by_country_and_reidentified(tmp_path, fixed_stamp):
    p = str(tmp_path / "credamo.sav")
    orig = fake_credamo(p)
    res = mask_survey(p, columns="suggested", ip="full", latlon="country_state")
    d = read_survey(res.output).data
    assert list(d.columns) == list(orig.columns)
    assert list(d.iloc[:, 0]) == list(orig.iloc[:, 0])
    assert all(re.fullmatch(r"MSK[A-Za-z0-9]{32,}", v) for v in d.iloc[:, 1])
    assert d.iloc[0, 1] == d.iloc[2, 1]
    assert all(re.match(r"MSK(CN|IT)_", v) for v in d.iloc[:, 4])
    assert d.iloc[1, 4].startswith("MSKIT_")
    assert d.iloc[0, 5] == d.iloc[2, 5] != d.iloc[1, 5]
    back = read_survey(reidentify(res.output, key_file=res.key_file)["output"]).data
    for col in orig.columns:
        assert [str(v) if not isinstance(v, float) else str(int(v)) if v == int(v) else str(v)
                for v in back[col]] == [str(v) if not isinstance(v, float) else str(int(v)) if v == int(v) else str(v)
                                        for v in orig[col]]


def test_spss_dates_missing_and_value_labels_survive(tmp_path):
    import datetime
    p = str(tmp_path / "e.sav")
    d = pd.DataFrame({
        "workerId": ["A1B2C3D4E5F6G7", "A9B8C7D6E5F4G3", None],
        "when": [datetime.datetime(2026, 1, 1, 10), datetime.datetime(2026, 1, 2, 11), None],
        "grp": [1.0, 2.0, 1.0],
        "score": [1.5, float("nan"), 3.0],
    })
    pyreadstat.write_sav(d, p, column_labels=["w", "t", "g", "s"],
                         variable_value_labels={"grp": {1: "ctl", 2: "trt"}},
                         variable_format={"when": "DATETIME20"})
    res = mask_survey(p, columns="workerId", scan_text=False)
    back, meta = pyreadstat.read_sav(res.output)
    assert back["workerId"][2] == "MSKblank"
    assert list(back["when"].dropna()) == list(d["when"].dropna())
    assert meta.variable_value_labels["grp"] == {1: "ctl", 2: "trt"}
    assert back["score"].isna().tolist() == [False, True, False]
    assert meta.column_labels == ["w", "t", "g", "s"]


def test_numeric_id_column_in_spss_becomes_codes_and_back(tmp_path, fixed_stamp):
    p = str(tmp_path / "n.sav")
    pyreadstat.write_sav(pd.DataFrame({"userId": [12345.0, 67890.0, 12345.0], "q": [1.0, 2.0, 3.0]}), p)
    res = mask_survey(p, columns="userId", scan_text=False)
    back, _ = pyreadstat.read_sav(res.output)
    assert back["userId"][0] == back["userId"][2] and back["userId"][0].startswith("MSK")
    restored, _ = pyreadstat.read_sav(reidentify(res.output, key_file=res.key_file)["output"])
    assert list(restored["userId"]) == ["12345", "67890", "12345"]


def test_csv_with_bom_and_unicode_roundtrips_exactly(tmp_path, fixed_stamp):
    p = tmp_path / "u.csv"
    p.write_bytes("\ufeffStartDate,workerId,note\nStart Date,Worker,Note\n2026-01-01,A1B2C3D4E5F6G7,Zürich 河南\n".encode("utf-8"))
    res = mask_survey(str(p), columns="workerId", scan_text=False)
    assert open(res.output, "rb").read(3) == b"\xef\xbb\xbf"
    back = reidentify(res.output, key_file=res.key_file)
    assert open(back["output"], "rb").read() == p.read_bytes()


def test_latin1_csv_gives_a_clear_error(tmp_path):
    p = tmp_path / "l.csv"
    p.write_bytes("StartDate,workerId,note\n2026-01-01,A1B2C3D4E5F6G7,caf\xe9\n".encode("latin-1"))
    with pytest.raises(ValueError, match="UTF-8"):
        read_survey(str(p))


def test_duplicate_column_names_are_kept_apart(tmp_path):
    p = tmp_path / "d.csv"
    p.write_text("workerId,Q,Q\nA1B2C3D4E5F6G7,a,b\n")
    s = read_survey(str(p))
    assert list(s.data.columns) == ["workerId", "Q", "Q.1"]
    res = mask_survey(str(p), columns="workerId", scan_text=False)
    assert open(res.output).read().splitlines()[0] == "workerId,Q,Q"


def test_large_file_is_fast(tmp_path):
    import time
    p = tmp_path / "big.csv"
    n = 100_000
    rows = ["workerId,PROLIFIC_PID,ans"] + [f"A{i:012d}A,{i % 5000:024x},x{i}" for i in range(n)]
    p.write_text("\n".join(rows) + "\n")
    t = time.time()
    res = mask_survey(str(p), columns=["workerId", "PROLIFIC_PID"], scan_text=False)
    assert time.time() - t < 60
    assert len(res.data) == n


# --- version 0.3.0: several files, per-file rules, new code format ---------

@pytest.fixture
def batch_files(tmp_path, fake_export):
    b = tmp_path / "lab.csv"
    b.write_text("id,PROLIFIC_PID,score\np1,5f1a2b3c4d5e6f7a8b9c0d1e,3\np2,bbbbbbbbbbbbbbbbbbbbbbbb,4\n")
    c = tmp_path / "clean.csv"
    c.write_text("condition,score\na,1\nb,2\n")
    return fake_export, str(b), str(c)


def test_several_files_one_key_one_report(batch_files, fixed_stamp):
    import hashlib
    import os
    a, b, c = batch_files
    res = mask_survey([a, b, c], columns="suggested", ip="full", latlon="full")
    outs = list(res.output.values())
    assert len(outs) == 2 and all(os.path.exists(o) for o in outs)
    assert os.path.basename(res.key_file) == "batch_KEY_2026-01-01_10-00.txt"
    assert os.path.basename(res.report) == "batch_masked_2026-01-01_10-00_report.txt"
    d = os.path.dirname(a)
    assert not os.path.exists(os.path.join(d, "study_KEY_2026-01-01_10-00.txt"))
    d1, d2 = read_survey(outs[0]).data, read_survey(outs[1]).data
    assert d1["PROLIFIC_PID"][0] == d2["PROLIFIC_PID"][0]
    report = lines(res.report)
    assert any("one key for all 2 files, created" in ln for ln in report)
    for o in outs:
        assert any(hashlib.sha256(open(o, "rb").read()).hexdigest() in ln for ln in report)
    assert any("clean.csv" in ln for ln in report)
    assert not any("5f1a2b3c4d5e6f7a8b9c0d1e" in ln for ln in report)
    key = lines(res.key_file)
    assert any(ln.endswith("study.csv") for ln in key) and any(ln.endswith("lab.csv") for ln in key)
    assert not any("5f1a2b3c4d5e6f7a8b9c0d1e" in ln for ln in key)
    back = reidentify(outs, key_file=res.key_file)
    assert back["data"][outs[1]]["PROLIFIC_PID"][0] == "5f1a2b3c4d5e6f7a8b9c0d1e"
    assert list(read_survey(back["output"][outs[0]]).data["PROLIFIC_PID"]) == \
        list(read_survey(a).data["PROLIFIC_PID"])


def test_a_folder_stands_for_its_survey_files(tmp_path, fake_export, fixed_stamp):
    from surveyidmasker.survey import survey_files
    (tmp_path / "lab.csv").write_text("id,score\np1,3\n")
    (tmp_path / "old_masked_2025-01-01_10-00.csv").write_text("id,score\nMSKx,3\n")
    assert sorted(p.rsplit("/", 1)[-1] for p in survey_files(str(tmp_path))) == ["lab.csv", "study.csv"]
    res = mask_survey(str(tmp_path), columns="suggested", ip="full", latlon="full")
    assert len(res.output) == 2


def test_several_files_take_names_or_a_dict_but_not_numbers(batch_files):
    a, b, _ = batch_files
    with pytest.raises(ValueError, match="Column numbers differ"):
        mask_survey([a, b], columns=[1])
    res = mask_survey([a, b], columns=["PROLIFIC_PID"], scan_text=False)
    assert read_survey(list(res.output.values())[1]).data["id"][0] == "p1"
    res = mask_survey([a, b], columns={"study.csv": ["PROLIFIC_PID"], "lab.csv": ["id"]},
                      scan_text=False, overwrite=True, key_file=res.key_file)
    out2 = list(res.output.values())[1]
    assert read_survey(out2).data["id"][0].startswith("MSK")
    assert read_survey(out2).data["PROLIFIC_PID"][0] == "5f1a2b3c4d5e6f7a8b9c0d1e"
    with pytest.raises(ValueError, match="one file"):
        mask_survey([a, b], columns="PROLIFIC_PID", output="x.csv")


def test_ip_and_location_rules_can_differ_by_file(tmp_path, fake_export, fixed_stamp, capsys):
    import shutil
    b = str(tmp_path / "study2.csv")
    shutil.copy(fake_export, b)
    cols = ["LocationLatitude", "LocationLongitude", "IPAddress", "PROLIFIC_PID"]
    res = mask_survey([fake_export, b], columns=cols, scan_text=False, ip="full",
                      latlon={"study.csv": "country_state", "study2.csv": "full"})
    o1, o2 = res.output.values()
    d1, d2 = read_survey(o1).data, read_survey(o2).data
    assert all(re.match(r"MSK(US-CT|IT)_", v) for v in d1["LocationLatitude"])
    assert not any("_" in v for v in d2["LocationLatitude"])
    assert list(d1["PROLIFIC_PID"]) == list(d2["PROLIFIC_PID"])
    report = lines(res.report)
    assert any(re.match(r"^Locations: +differ by file", ln) for ln in report)
    assert any(re.match(r"^IP addresses: +full", ln) for ln in report)
    assert sum(ln.startswith("   Locations: ") for ln in report) == 2
    back = reidentify(list(res.output.values()), key_file=res.key_file)
    assert list(back["data"][o2]["LocationLatitude"]) == list(read_survey(b).data["LocationLatitude"])
    with pytest.raises(ValueError, match="not being masked"):
        mask_survey([fake_export, b], columns=cols, latlon={"other.csv": "full"},
                    key_file=res.key_file, overwrite=True)
    with pytest.raises(ValueError, match="must be"):
        mask_survey([fake_export, b], columns=cols, latlon={"study.csv": "partial"},
                    key_file=res.key_file, overwrite=True)
    capsys.readouterr()
    res3 = mask_survey([fake_export, b], columns=cols, ip="full", latlon={"study.csv": "country_state"},
                       scan_text=False, key_file=res.key_file, overwrite=True)
    assert "latitude/longitude fully" in capsys.readouterr().err
    assert not any("_" in v for v in read_survey(list(res3.output.values())[1]).data["LocationLatitude"])


def test_the_region_is_always_the_part_between_msk_and_underscore():
    from surveyidmasker.survey import region_prefix
    cb = Codebook(derive_key(b"\x05" * 32))
    values = [str(i) for i in range(300)]
    regions = ["IT", "US-CT", None] * 100
    m = mask_with(cb, values, prefix=[region_prefix(r) for r in regions])
    assert not any(re.search(r"[_-]", v) for v, r in zip(m, regions) if r is None)
    assert [re.sub(r"^MSK([^_]*)_.*$", r"\1", v) for v, r in zip(m, regions) if r] == \
        [r for r in regions if r]


def test_codes_with_a_region_are_read_back_also_in_text_and_in_the_old_format(tmp_path):
    from surveyidmasker.key import write_key_file
    from surveyidmasker.reidentify import decode_token
    key = derive_key(b"\x06" * 32)
    cb = Codebook(key)
    new = mask_with(cb, ["1.2.3.4"], prefix="MSKUS-CT_")[0]
    old = mask_with(cb, ["5.6.7.8"], prefix="MSKUSCT")[0]
    assert decode_token(new, key) == "1.2.3.4"
    assert decode_token(old, key) == "5.6.7.8"
    path = tmp_path / "m_masked_2026-01-01_10-00.csv"
    path.write_text(f"ip,note\n{new},seen {new}.\n")
    kf = str(tmp_path / "k.txt")
    write_key_file(key.secret, kf)
    back = reidentify(str(path), key_file=kf)
    assert back["data"]["ip"][0] == "1.2.3.4"
    assert back["data"]["note"][0] == "seen 1.2.3.4."


def test_key_file_format_matches_r_030(tmp_path):
    from surveyidmasker.key import read_key_file
    p = str(tmp_path / "k.txt")
    new_key_file(p)
    text = lines(p)
    assert any(ln.startswith("# Created:") for ln in text)
    assert text[-1] == "# Files masked with this key:"
    assert read_key_file(p).created != "(unknown)"
