"""Cross-Platform Spread 'same publisher' label (UI/spread_publisher.py): who counts as one publisher, and who must NOT."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "UI"))
import spread_publisher as S  # noqa: E402

N = S.normalise


def same(a, b):
    return S.same_account(N(a), N(b))


def test_same_organisation_under_different_handles():
    assert same("CHINA MFA Spokesperson 中国外交部发言人", "mfa_china")          # word order, "spokesperson" and the Chinese suffix ignored
    assert same("Aaj Tak", "aajtakofficial") and same("AajTak", "Aaj Tak")
    assert same("U.S. Naval Institute", "navalinstitute")                      # containment, shorter name is 14 characters
    assert same("powerus", "powerus_us") and same("Powerus", "powerus")
    assert same("BBC News", "BBC_Chinese_news") and same("BBC News (World)", "BBC News")   # curated brand group
    assert same("Maritime Institute of Malaysia", "mima_malaysia")
    assert same("Bangladesh Institute of Peace & Security Studies - BIPSS", "bipss.org.bd")
    assert same("Cyber Detective", "cybdetective")


def test_different_publishers_are_not_merged():
    assert not same("MFA Russia 🇷🇺", "CHINA MFA Spokesperson")                # share only the short word "mfa"
    assert not same("MFA SriLanka 🇱🇰", "MFA Russia 🇷🇺")
    assert not same("BBC News", "bbcfan123")                                    # a fan account is not the BBC
    assert not same("World News", "BBC World News")                            # generic words alone never identify a publisher
    assert not same("Breaking News", "Breaking News UK")
    assert not same("Global Times", "Global Defense") and not same("The Hindu", "hindustan")
    assert not same("Dawn", "dawn_news")                                       # shorter than 7 characters: needs an alias, not a guess


def test_classify_only_flags_items_where_every_account_is_one_publisher():
    assert S.classify([("telegram", "aajtakofficial"), ("whatsapp", "Aaj Tak")]) == {"same": True, "publishers": 1, "label": "Aaj Tak"}
    mixed = S.classify([("mastodon", "BBC News"), ("telegram", "BBC_Chinese_news"), ("reddit", "some_reader")])
    assert mixed["same"] is False and mixed["publishers"] == 2                  # an independent pickup keeps the row visible
    assert S.classify([("twitter", "A"), ("instagram", None)])["same"] is False  # an unnamed account cannot be shown to be the same
    assert S.classify([("twitter", "Powerus")])["same"] is False                # one account is not "syndication"
    one_user = S.classify([("reddit", "rhk111"), ("tiktok", "rhk111")])
    assert one_user["same"] and one_user["label"] == "rhk111"                   # the same person on two platforms


def test_alias_keywords_are_normalised_like_names():
    g = S._groups()
    assert all(k == "".join(ch for ch in k if ch.isalnum()) for kws in g.values() for k in kws)   # compact form, comparable with names
    assert "maritimeinstitutemalaysia" in g["Maritime Institute of Malaysia"]                     # "of" dropped by the same clean-up
