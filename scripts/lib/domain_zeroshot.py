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
