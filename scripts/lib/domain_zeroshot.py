"""Zero-shot domain scoring with the pretrained bge-m3 (no training).

Each security domain D01..D09 and a "not security" group are described by a few sentences (data/domain_prototypes_v1.json).
A post's stored bge-m3 embedding is compared with every sentence (cosine); a domain's score is its best sentence.

What it is good for (measured on 273 hand-reviewed posts, 2026-11-19):
  * spotting posts that are clearly NOT security related: when the nearest "not security" sentence beats the best domain by at
    least NONE_GAP, 97% were truly none (2 of 149 security posts dropped) and 22% of the posts were caught;
  * NOT choosing between D01..D09: even for confident security posts the domain was right only about 60% of the time.
So `confident_none` is meant to skip the LLM; `zs_domain` is only a provisional hint, never a final label.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np

DOMAINS = ["D01", "D02", "D03", "D04", "D05", "D06", "D07", "D08", "D09"]
TAU = 0.44         # best domain score below this -> none
DELTA = -0.04      # a "not security" sentence closer than best + DELTA -> none
NONE_GAP = 0.02    # "not security" beats the best domain by at least this -> confident none (skip the LLM)


def load_prototype_texts(path: Path) -> tuple[list[str], list[str]]:
    P = json.loads(Path(path).read_text(encoding="utf-8"))
    texts, dom = [], []
    for d, lst in P.items():
        for t in lst:
            texts.append(t)
            dom.append(d)
    return texts, dom


def score(X: np.ndarray, proto_emb: np.ndarray, proto_dom: np.ndarray) -> dict:
    """X: (n, 1024) post embeddings (any norm); proto_emb rows are unit length. Returns per-post arrays."""
    X = X / np.maximum(np.linalg.norm(X, axis=1, keepdims=True), 1e-9)
    sim = X @ proto_emb.T
    ds = np.stack([sim[:, proto_dom == d].max(1) for d in DOMAINS], 1)
    ns = sim[:, proto_dom == "none"].max(1)
    order = np.argsort(-ds, axis=1)
    rows = np.arange(len(X))
    best, second = order[:, 0], order[:, 1]
    bs, ss = ds[rows, best], ds[rows, second]
    dom = np.array(DOMAINS, dtype=object)[best]
    is_none = (bs < TAU) | (ns > bs + DELTA)
    dom = np.where(is_none, "none", dom)
    gap = ns - bs                       # > 0: nearer to a "not security" sentence than to any domain
    return {"zs_domain": dom, "zs_best": DOMAINS_ARR()[best], "zs_best_score": bs, "zs_second": DOMAINS_ARR()[second],
            "zs_margin": bs - ss, "none_score": ns, "none_gap": gap, "confident_none": gap >= NONE_GAP}


def DOMAINS_ARR() -> np.ndarray:
    return np.array(DOMAINS, dtype=object)


# ---------------------------------------------------------------------------------------------------------------------------
# Security-cue guard. The zero-shot "none" filter missed short alert-style posts ("Київ: 1х Центр 1х Боярка" = a drone alert),
# a fighter-aircraft image caption and a launch/orbit tweet (3 of 100 skipped posts read by hand). A post that carries a clear
# security term is never skipped, whatever the embedding says, and it is sent to the LLM even when it is shorter than the LLM's
# usual minimum. English, Russian/Ukrainian and Chinese terms; Hindi/Urdu/Arabic/Burmese are NOT covered (stated limit).
# Deliberately NOT cues: murder, killed, arrested, flood, rain, fire, accident, crash (local crime / weather / accidents).
# ---------------------------------------------------------------------------------------------------------------------------
_CUE_EN = (r"warfare|border tensions?|border disputes?|border conflicts?|world war|war ?\\d|drones?|uavs?|missiles?|ballistic|air ?strikes?|shelling|artillery|warships?|destroyers?|frigates?|submarines?|"
           r"aircraft carriers?|fighter jets?|bombers?|military|army|armed forces|troops?|soldiers?|navy|naval|air force|nato|"
           r"idf|hamas|hezbollah|houthis?|irgc|taliban|terror\w*|insurg\w*|militants?|ceasefire|nuclear|warheads?|"
           r"cyber ?attacks?|ransomware|espionage|spy|spies|satellites?|orbit|rocket launch|strait of hormuz|south china sea|"
           r"border clash\w*|line of control|coup|sanctions?|invasion|invaded|annex\w*|frontline|front line|defen[cs]e|"
           r"pla|plaaf|pla navy|coast guard|intercepted|explosions?|air defen[cs]e")
_CUE_AIRCRAFT = r"\b(?:J|JH|JL|KJ|H|Y|Z|WZ|CH|Su|MiG|Tu|F|B|KC|E|P)-\d{1,3}[A-Za-z]{0,2}\b|\bJ-XDS\b|\bType ?0?\d{2}[A-Z]?\b"
_CUE_RU = (r"бпла|дрон\w*|ракет\w*|ппо|шахед\w*|геран\w*|обстр[іе]л\w*|вибух\w*|взрыв\w*|тривог\w*|тревог\w*|"
           r"авіац\w*|авиац\w*|армі\w*|армия|арми[ия]|військ\w*|войск\w*|фронт\w*|наступ\w*|оборон\w*|ядерн\w*|санкц\w*")
_CUE_ZH = r"导弹|无人机|战机|军队|军事|解放军|卫星|发射|轨道|核武|制裁|战争|空袭|袭击|海军|空军"
_CUE_HI = r"सेना|फौज|युद्ध|सीमा|हमला|आतंक\w*|मिसाइल|ड्रोन|परमाणु|जंग|فوج|جنگ|حملہ|دہشت\w*|میزائل|سرحد|جيش|حرب|هجوم|صاروخ|إرهاب\w*|طائرة مسيرة"
_RE_CUE = re.compile(rf"(?i)(?<![\w])(?:{_CUE_EN})(?![\w])|{_CUE_AIRCRAFT}|(?<![\w])(?:{_CUE_RU})|{_CUE_ZH}|{_CUE_HI}")
_RE_ALERT = re.compile(r"(?<![\w])\d\s?[хx](?![\w])")        # "1х Центр", "2x Boyarka": drone-alert style (Cyrillic context only)
_RE_CYR = re.compile(r"[А-Яа-яІіЇїЄє]")


def security_cue(text) -> bool:
    if not isinstance(text, str) or not text:
        return False
    if _RE_CUE.search(text):
        return True
    return bool(_RE_ALERT.search(text) and _RE_CYR.search(text))
