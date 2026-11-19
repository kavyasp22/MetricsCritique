"""Zero-shot domain scoring: the decision rules, on synthetic unit vectors (no model needed)."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import domain_zeroshot as Z  # noqa: E402


def _protos():
    # one unit vector per domain D01..D09 plus two "none" sentences, orthonormal
    dim = len(Z.DOMAINS) + 2
    E = np.eye(dim, dtype=np.float32)
    dom = np.array(Z.DOMAINS + ["none", "none"], dtype=object)
    return E, dom


def _post(*pairs, dim=11):
    v = np.zeros(dim, dtype=np.float32)
    for i, w in pairs:
        v[i] = w
    return v


def test_clear_security_post_gets_its_domain_and_is_not_confident_none():
    E, dom = _protos()
    X = np.stack([_post((4, 1.0))])            # pure D05
    r = Z.score(X, E, dom)
    assert r["zs_domain"][0] == "D05" and not r["confident_none"][0]
    assert r["zs_best"][0] == "D05" and r["zs_margin"][0] > 0.9


def test_post_nearer_to_a_not_security_sentence_is_confident_none():
    E, dom = _protos()
    X = np.stack([_post((9, 1.0))])            # pure "none" sentence
    r = Z.score(X, E, dom)
    assert r["zs_domain"][0] == "none" and r["confident_none"][0] and r["none_gap"][0] > Z.NONE_GAP


def test_weak_match_to_every_domain_is_none_but_not_confident_none():
    E, dom = _protos()
    # almost orthogonal to everything: best domain score below TAU, none score lower than best -> none, gap < NONE_GAP
    X = np.stack([np.ones(11, dtype=np.float32)])    # equally close (0.30) to everything
    r = Z.score(X, E, dom)
    assert r["zs_best_score"][0] < Z.TAU and r["zs_domain"][0] == "none" and not r["confident_none"][0]


def test_vectors_are_normalised_so_scale_does_not_matter():
    E, dom = _protos()
    a = Z.score(np.stack([_post((2, 1.0), (4, 0.3))]), E, dom)
    b = Z.score(np.stack([_post((2, 50.0), (4, 15.0))]), E, dom)
    assert a["zs_domain"][0] == b["zs_domain"][0] == "D03"
    assert abs(a["zs_margin"][0] - b["zs_margin"][0]) < 1e-5


def test_prototype_file_covers_every_domain_and_none():
    texts, dom = Z.load_prototype_texts(Path(__file__).resolve().parents[2] / "data" / "domain_prototypes_v1.json")
    assert set(dom) == set(Z.DOMAINS) | {"none"} and len(texts) == len(dom) >= 40


def test_security_cue_catches_alerts_aircraft_and_launch_but_not_local_crime_or_weather():
    cues = ["Київ: 🅿️ 1х Центр 🅿️ 1х Боярка", "Київ: 🅿️1х Голосіїв", "Київщина: 2х Бровари", "One of the best images showing SAC's J-XDS (Image via Weibo)", "Entered the predetermined orbit!",
            "The Russian Navy will launch a Bulava SLBM", "Heavy shelling reported near Kharkiv", "Ukrainian drones hit a warehouse",
            "俄军无人机袭击了仓库", "Пуск ракет з Брянська, загроза балістики", "India-China Border Tension | Arunachal Pradesh", "Countering China's Environmental Warfare Against India", "भारत की फौज सीमा पर", "پاکستان فوج کا حملہ"]
    plain = ["Student who murdered university housemate jailed for life", "Heavy rain and flash flood warning in Dhaka",
             "Happy birthday @narendramodi", "Selling a MacBook Pro, battery health 99%", "CBSE class 10 result 2026", "I had to",
             "The Ballon d'Or ceremony is on Monday in London", "Tiny endangered frogs found in a Cape Town racecourse"]
    assert all(Z.security_cue(x) for x in cues), [x for x in cues if not Z.security_cue(x)]
    assert not any(Z.security_cue(x) for x in plain), [x for x in plain if Z.security_cue(x)]
    assert not Z.security_cue(None) and not Z.security_cue("")
