"""Top-k cosine neighbours on the GPU (torch). Run with a torch-capable python (e.g. /usr/bin/python3), not the project .venv.

    python scripts/lib/knn_gpu.py R.npy Q.npy out.npz K [CHUNK]

R (n_ref, d) and Q (n_query, d) are unit-length float32 matrices. Writes idx (n_query, K) int32 and sim (n_query, K) float32.
Memory: R in fp16 plus one (CHUNK x n_ref) score block; falls back to CPU if CUDA is not available or CUDA_VISIBLE_DEVICES is empty.
"""
import sys
import numpy as np
import torch


def main() -> int:
    r_path, q_path, out, k = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
    chunk = int(sys.argv[5]) if len(sys.argv) > 5 else 2048
    R = np.load(r_path, mmap_mode="r"); Q = np.load(q_path, mmap_mode="r")
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    dt = torch.float16 if dev == "cuda" else torch.float32
    Rt = torch.from_numpy(np.ascontiguousarray(R)).to(dev, dt)
    idx = np.empty((len(Q), k), np.int32); sim = np.empty((len(Q), k), np.float32)
    for s in range(0, len(Q), chunk):
        q = torch.from_numpy(np.ascontiguousarray(Q[s:s + chunk])).to(dev, dt)
        v, i = torch.topk(q @ Rt.T, k, dim=1)
        idx[s:s + chunk] = i.cpu().numpy(); sim[s:s + chunk] = v.float().cpu().numpy()
        if (s // chunk) % 10 == 0:
            print(f"  knn {min(s + chunk, len(Q)):,}/{len(Q):,} on {dev}", flush=True)
    np.savez(out, idx=idx, sim=sim)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
