"""Keyword query language: parsing, word forms, and the SQL it builds (checked against a real DuckDB)."""
import sys
from pathlib import Path

import duckdb
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "UI"))
import keyword_query as K  # noqa: E402

POSTS = ["Flash flood hits the city", "Floods and landslide near the dam", "plastics factory fire", "A flooded road, relief teams out",
         "Protest rally in Delhi", "Flood relief fund announced", "no match here", "Наводнение в городе"]


def match(q):
    con = duckdb.connect()
    con.execute("CREATE TABLE p(id INT, text VARCHAR)")
    for i, t in enumerate(POSTS):
        con.execute("INSERT INTO p VALUES (?, ?)", [i, t])
    sql, params = K.to_sql(K.parse(q), "p.text")
    return sorted(r[0] for r in con.execute(f"SELECT id FROM p WHERE {sql}", params).fetchall())


def test_word_forms_and_related():
    assert match("flood") == [0, 1, 3, 5]          # flood, floods, flooded, plus the related phrase "flash flood"
    assert 2 not in match("pla")                    # whole words only: "pla" is not inside "plastics"
    assert match("protest") == [4]                  # "rally" is related, and appears in the same post


def test_and_phrase_or_exclude():
    assert match("flood relief") == [3, 5]
    assert match('"flash flood"') == [0]
    assert match("landslide OR rally") == [1, 4]
    assert match("flood -dam") == [0, 3, 5]
    assert match("flood relief OR rally") == [3, 4, 5]    # AND binds tighter than OR


def test_non_latin_matches_exactly():
    assert match("наводнение") == [7]


def test_errors():
    for bad in ["", "   ", "-dam", "x" * 121, "a b c d e f g h i"]:
        with pytest.raises(K.KeywordError):
            K.parse(bad)


def test_highlight_words_include_forms():
    w = K.parse("flood").words()
    assert "floods" in w and "flooding" in w and "flash flood" in w
