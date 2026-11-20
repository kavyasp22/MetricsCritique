#!/usr/bin/env python3
"""Tiny local embedding service: the pretrained bge-m3 (CLS vector, L2-normalised, the same as the stored post embeddings).

The dashboard container has no torch, so query-time semantic search (scenario retrieval) asks this service for the vectors of a few
short texts. Run it on the host with a torch-capable python (CPU is enough: a handful of sentences):

    CUDA_VISIBLE_DEVICES= /usr/bin/python3 scripts/embed_service.py --host 172.17.0.1 --port 8893

POST /embed {"texts": ["...", ...]}  ->  {"dim": 1024, "vectors": [[...], ...]}      GET /health -> {"ok": true}
At most 64 texts of 400 characters per call. No data is stored. Bind it to the docker bridge address (not 0.0.0.0).
"""
from __future__ import annotations
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import torch
from transformers import AutoModel, AutoTokenizer

TOK = MOD = None


def embed(texts):
    with torch.no_grad():
        b = TOK(texts, padding=True, truncation=True, max_length=192, return_tensors="pt")
        return torch.nn.functional.normalize(MOD(**b).last_hidden_state[:, 0], dim=-1).numpy().round(5).tolist()


class H(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._send(200 if self.path == "/health" else 404, {"ok": self.path == "/health"})

    def do_POST(self):
        if self.path != "/embed":
            return self._send(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length", "0"))
            texts = [str(t)[:400] for t in json.loads(self.rfile.read(n)).get("texts", [])][:64]
            v = embed(texts) if texts else []
            self._send(200, {"dim": 1024, "vectors": v})
        except Exception as e:                      # noqa: BLE001
            self._send(400, {"error": str(e)[:200]})

    def log_message(self, *a):
        pass


def main():
    global TOK, MOD
    ap = argparse.ArgumentParser(); ap.add_argument("--host", default="127.0.0.1"); ap.add_argument("--port", type=int, default=8893)
    a = ap.parse_args()
    TOK = AutoTokenizer.from_pretrained("BAAI/bge-m3"); MOD = AutoModel.from_pretrained("BAAI/bge-m3").eval()
    print(f"embedding service on {a.host}:{a.port}", flush=True)
    ThreadingHTTPServer((a.host, a.port), H).serve_forever()


if __name__ == "__main__":
    main()
