import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import event_topic_llm as E


def test_prompt_lists_every_subcode_and_the_two_extra_answers():
    p = E.system_prompt()
    for code in E.codes()["sub"]:
        assert f"  {code} " in p
    assert E.NONE in p and E.UNMAPPED in p and len(E.codes()["sub"]) == 41


def test_validate():
    assert E.validate({"subcode": "05.3", "confidence": 0.9}) is None
    assert E.validate({"subcode": "none", "confidence": 1}) is None
    assert E.validate({"subcode": "conflict_unmapped", "confidence": 0.5}) is None
    assert E.validate({"subcode": "99.9", "confidence": 0.9})
    assert E.validate({"subcode": "05.3", "confidence": 1.5})
    assert E.validate({"subcode": "05.3", "confidence": "x"})


def test_parent_of_and_truncation():
    assert E.parent_of("05.3") == "05" and E.parent_of("none") is None
    assert len(E.user_prompt("x" * 5000)) < 1600
