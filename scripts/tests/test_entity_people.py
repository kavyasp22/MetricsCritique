import sys
from pathlib import Path
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import entity_people as EP


def _cand(rows):
    return pd.DataFrame(rows, columns=["entity_uid", "canonical_name", "entity_type", "entity_subtype", "distinct_posts"])


def _names(rows):
    return [r[1] for r in EP.clean_candidates(_cand(rows))]


def test_titles_links_nationalities_and_first_names_are_dropped():
    rows = [(f"u{i}", n, "PERSON", "political_leader", 100 - i) for i, n in enumerate(
        ["Trump", "President", "Foreign Minister", "Https", "Israeli", "Donald", "National Security Advisor", "Affairs", "Lawmakers", "Supreme Leader", "Secret Service"])]
    assert _names(rows) == ["Trump"]


def test_spellings_of_one_person_are_merged():
    rows = [("a", "Xi Jinping", "PERSON", "political_leader", 1000), ("b", "Xijinping", "PERSON", "political_leader", 300),
            ("c", "Barack Obama", "PERSON", "political_leader", 200), ("d", "Obama", "PERSON", "political_leader", 800),
            ("e", "Narendra Modi", "PERSON", "political_leader", 900), ("f", "Pmmodi", "PERSON", "political_leader", 100)]
    out = EP.clean_candidates(_cand(rows))
    assert sorted(r[1] for r in out) == ["Barack Obama", "Narendra Modi", "Xi Jinping"]


def test_trump_is_not_merged_into_trump_jr():
    rows = [("a", "Trump", "PERSON", "political_leader", 14000), ("b", "Trump Jr", "PERSON", "political_leader", 300)]
    assert sorted(_names(rows)) == ["Trump", "Trump Jr"]


def test_nickname_is_merged_and_found_in_text():
    rows = [("a", "Benjamin Netanyahu", "PERSON", "political_leader", 900), ("b", "Bibi", "PERSON", "political_leader", 100)]
    cl = EP.clean_candidates(_cand(rows))
    assert [r[1] for r in cl] == ["Benjamin Netanyahu"]
    comp = EP.compile_patterns(cl)
    assert EP.find_people("bibi said no", comp) == [("a", "Benjamin Netanyahu")]
    assert EP.find_people("a trumpet played", EP.compile_patterns(EP.clean_candidates(_cand([("t", "Trump", "PERSON", "political_leader", 5)])))) == []
