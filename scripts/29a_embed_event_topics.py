#!/usr/bin/env python3
"""
Stage 29a -- embed the A01-A10 event-topic sub-codes (theme_subcode, 41) and parent codes (10) with the pretrained bge-m3
(CLS vector, L2-normalised, the same as the stored post embeddings). Text = "<display>. <description>" from scripts/lib/taxonomy.py.

Needs torch + transformers (use /usr/bin/python3, not the .venv). Writes data/event_topic_emb_v1.npz (derived, ignored by git).
    /usr/bin/python3 scripts/29a_embed_event_topics.py
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np
import torch
from transformers import AutoModel, AutoTokenizer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
import taxonomy as T  # noqa: E402

OUT = ROOT / "data" / "event_topic_emb_v1.npz"


def main() -> int:
    A = T.build()["axes"]
    rows = []
    for axis in ("theme_subcode", "theme_code"):
        for k, e in A[axis]["entries"].items():
            rows.append((e["code"], e["domain"], e["parent_key"] or "", e["display"], f"{e['display']}. {e['description']}"))
    tok = AutoTokenizer.from_pretrained("BAAI/bge-m3")
    mod = AutoModel.from_pretrained("BAAI/bge-m3").eval()
    with torch.no_grad():
        b = tok([r[4] for r in rows], padding=True, truncation=True, max_length=160, return_tensors="pt")
        X = torch.nn.functional.normalize(mod(**b).last_hidden_state[:, 0], dim=-1).numpy()
    np.savez(OUT, X=X, code=np.array([r[0] for r in rows]), domain=np.array([r[1] for r in rows]),
             parent=np.array([r[2] for r in rows]), display=np.array([r[3] for r in rows]))
    print(f"wrote {OUT}: {len(rows)} codes, dim {X.shape[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
