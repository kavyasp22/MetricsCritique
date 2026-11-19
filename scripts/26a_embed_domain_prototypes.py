#!/usr/bin/env python3
"""
Stage 26a -- embed the domain description sentences with the pretrained bge-m3 (the same model and pooling as the stored
post embeddings: CLS vector, L2-normalised; checked: cosine 1.000 against stored embeddings).

Needs torch + transformers (the .venv has neither; use a torch environment, e.g. /usr/bin/python3) and the BAAI/bge-m3
weights in the Hugging Face cache. Tiny (47 sentences), CPU is fine. Writes data/domain_prototype_emb_v1.npz (derived, ignored by git).

    /usr/bin/python3 scripts/26a_embed_domain_prototypes.py
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import torch
from transformers import AutoModel, AutoTokenizer

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "domain_prototypes_v1.json"
OUT = ROOT / "data" / "domain_prototype_emb_v1.npz"


def main() -> int:
    P = json.loads(SRC.read_text(encoding="utf-8"))
    texts, dom = [], []
    for d, lst in P.items():
        for t in lst:
            texts.append(t)
            dom.append(d)
    tok = AutoTokenizer.from_pretrained("BAAI/bge-m3")
    mod = AutoModel.from_pretrained("BAAI/bge-m3").eval()
    with torch.no_grad():
        b = tok(texts, padding=True, truncation=True, max_length=96, return_tensors="pt")
        X = torch.nn.functional.normalize(mod(**b).last_hidden_state[:, 0], dim=-1).numpy()
    np.savez(OUT, X=X, dom=np.array(dom), texts=np.array(texts))
    print(f"wrote {OUT}: {len(texts)} sentences, dim {X.shape[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
