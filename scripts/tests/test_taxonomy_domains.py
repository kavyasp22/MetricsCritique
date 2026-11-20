import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import taxonomy as T


def _tax():
    return T.build()


def test_every_event_topic_has_one_security_theme():
    a = _tax()["axes"]
    doms = set(a["security_domain"]["order"])
    for axis in ("theme_code", "theme_subcode"):
        for k, e in a[axis]["entries"].items():
            assert e["domain"] in doms, (axis, k)


def test_ten_codes_and_forty_one_subcodes_all_placed():
    t = _tax()
    placed = sum(len(v) for v in t["axes"]["security_domain"]["groups"].values())
    assert len(t["axes"]["theme_code"]["entries"]) == 10 and len(t["axes"]["theme_subcode"]["entries"]) == 41
    assert placed == 51


def test_known_placements():
    a = _tax()["axes"]
    by_code = {e["code"]: e["domain"] for ax in ("theme_code", "theme_subcode") for e in a[ax]["entries"].values()}
    assert by_code["07"] == "D06" and by_code["05.1"] == "D04" and by_code["10"] == "D08" and by_code["09.3"] == "D07"
    assert by_code["08.2"] == "D01" and by_code["08.4"] == "D08" and by_code["08.1"] == "D02"


def test_validate_catches_an_unplaced_code(monkeypatch):
    monkeypatch.delitem(T.CODE_TO_DOMAIN, "07.4")
    assert any("07.4" in x or "no security theme" in x for x in T.validate(T.build()))
