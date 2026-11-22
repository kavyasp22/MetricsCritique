#!/usr/bin/env python3
"""Stage 42 -- review sample for T-topic labelling: fixed-k clusters inside each D-theme, ~10 posts per cluster.

Not a classifier. Clusters are only used to choose which posts a person reviews (and to show the structure); no label is assigned by a cluster
unless the analyst gold seeds inside it clearly agree, and even then it is only a *suggestion* in the sheet.

Steps
  1. relevance gate  : posts the stage-27 hybrid theme already assigned (D01..D09), distinct messages only (post_dedup.is_first), >= 40 chars,
                       not one of the analyst gold posts (those are used as seeds)
  2. clustering      : per D-theme, on the stored bge-m3 embeddings: UMAP (cosine, 10 dims, fixed seed) -> KMeans with a FIXED k
                       (k = number of T-topics of that D-theme in the taxonomy + 2 spare clusters for "other"). No HDBSCAN: it was unstable on this corpus.
                       At most --cap posts per theme are clustered (random, seeded) to keep UMAP quick.
  3. review sample   : per cluster, the posts nearest the centroid + a few random ones (--per-cluster in total)
  4. seeds           : analyst-gold Yes posts of the same D-theme are placed in the nearest cluster; the majority T-topic is suggested only when
                       >= 2 seeds agree on >= 60%.

Reads   data/analytics.duckdb (READ-ONLY), data/post_theme_v1.parquet, data/post_dedup.parquet, data/gold/analyst_gold_v1.parquet, the workbook (--xlsx) Topics sheet
Writes  data/gold/cluster_review_key_v1.parquet   (row id -> post_uid, D-theme, cluster, suggestion)       [git-ignored]
        <--out> workbook with Review / Clusters / Topics / Lists / Guide sheets (post text inside: keep it out of git)
Usage   PYTHONPATH=<dir with openpyxl,umap> python scripts/42_cluster_review_sample.py --xlsx <analyst workbook> --out <review.xlsx>
"""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "data" / "gold"
MIN_CHARS = 40
SEED = 42


def embeddings(con, uids):
    out = []
    uids = list(uids)
    for i in range(0, len(uids), 5000):
        df = con.execute("SELECT post_uid, embedding FROM post_embeddings WHERE post_uid IN (SELECT unnest(?))", [uids[i:i + 5000]]).df()
        out.append(df)
    df = pd.concat(out, ignore_index=True)
    M = np.vstack(df.embedding.map(lambda v: np.asarray(v, dtype=np.float32)).values)
    M /= np.linalg.norm(M, axis=1, keepdims=True) + 1e-9
    return df.post_uid.tolist(), M


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--xlsx", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--cap", type=int, default=6000)
    ap.add_argument("--per-cluster", type=int, default=10)
    ap.add_argument("--spare", type=int, default=2)
    a = ap.parse_args()
    import umap
    from sklearn.cluster import KMeans

    topics = pd.read_excel(a.xlsx, sheet_name="Topics")
    topics.columns = ["D", "T", "Name", "Definition", "Typical", "NotThis"]
    topics["Dc"] = topics["T"].str[:3]
    topics.loc[topics["T"] == "D06.T02", "Name"] = "Shared Rivers, Dams & Transboundary Water"   # the source sheet has the typo "faircraft"
    gold = pd.read_parquet(GOLD / "analyst_gold_v1.parquet")
    con = duckdb.connect(str(ROOT / "data" / "analytics.duckdb"), read_only=True)
    th = pd.read_parquet(ROOT / "data" / "post_theme_v1.parquet", columns=["post_uid", "theme"])
    dd = pd.read_parquet(ROOT / "data" / "post_dedup.parquet", columns=["post_uid", "is_first"])
    pf = con.execute("SELECT post_uid, platform, lang, length(trim(text)) AS chars FROM post_facts WHERE text IS NOT NULL").df()
    emb_ok = set(con.execute("SELECT post_uid FROM post_embeddings").df().post_uid)
    pool = th[th.theme != "unassigned"].merge(dd[dd.is_first.astype(bool)][["post_uid"]], on="post_uid").merge(pf, on="post_uid")
    pool = pool[(pool.chars >= MIN_CHARS) & pool.post_uid.isin(emb_ok) & ~pool.post_uid.isin(set(gold.post_uid))]
    print("gate: stage-27 assigned, distinct, >=40 chars, embedded, not gold:", len(pool), pool.theme.value_counts().sort_index().to_dict())

    rng = np.random.default_rng(SEED)
    sample_rows, cluster_rows = [], []
    gold_yes = gold[gold.sec == "Yes"]
    for D in sorted(pool.theme.unique()):
        tp = topics[topics.Dc == D]
        k = len(tp) + a.spare
        sub = pool[pool.theme == D]
        if len(sub) > a.cap:
            sub = sub.sample(n=a.cap, random_state=SEED)
        uids, E = embeddings(con, sub.post_uid)
        red = umap.UMAP(n_neighbors=15, n_components=10, min_dist=0.0, metric="cosine", random_state=SEED).fit_transform(E)
        km = KMeans(n_clusters=k, n_init=10, random_state=SEED).fit(red)
        lab = km.labels_
        cent = np.vstack([E[lab == c].mean(axis=0) for c in range(k)]); cent /= np.linalg.norm(cent, axis=1, keepdims=True) + 1e-9
        # seeds: analyst-gold Yes posts of this D-theme, nearest centroid in the embedding space
        gd = gold_yes[gold_yes.D == D]
        seeds = {c: [] for c in range(k)}
        if len(gd):
            gu, GE = embeddings(con, gd.post_uid)
            tmap = dict(zip(gd.post_uid, gd["T"]))
            for u, v in zip(gu, GE):
                seeds[int(np.argmax(cent @ v))].append(tmap[u])
        for c in range(k):
            idx = np.where(lab == c)[0]
            sim = E[idx] @ cent[c]
            order = idx[np.argsort(-sim)]
            near = list(order[: max(1, int(round(a.per_cluster * 0.6)))])
            rest = [i for i in order if i not in set(near)]
            extra = list(rng.choice(rest, size=min(len(rest), a.per_cluster - len(near)), replace=False)) if rest else []
            chosen = near + extra
            sd = Counter(seeds[c]); top, n_top = (sd.most_common(1)[0] if sd else (None, 0))
            ok = bool(sd) and sum(sd.values()) >= 2 and n_top / sum(sd.values()) >= 0.6
            suggestion = top if ok else None
            cluster_rows.append({"cluster": f"{D}-C{c + 1:02d}", "D": D, "size": int(len(idx)), "sampled": len(chosen), "n_seeds": int(sum(sd.values())),
                                 "seed_votes": ", ".join(f"{t}:{n}" for t, n in sd.most_common()), "suggested_T": suggestion,
                                 "platforms": ", ".join(f"{p} {n}" for p, n in Counter(sub.set_index("post_uid").loc[[uids[i] for i in idx]].platform).most_common(3))})
            for j, i in enumerate(chosen):
                sample_rows.append({"post_uid": uids[i], "D": D, "cluster": f"{D}-C{c + 1:02d}", "pick": "nearest centre" if i in near else "random", "suggested_T": suggestion, "sim": float(E[i] @ cent[c])})
        print(f"{D}: {len(sub)} clustered, k={k}, seeds placed {len(gd)}")

    S = pd.DataFrame(sample_rows)
    txt = con.execute("SELECT post_uid, platform, lang, text FROM post_facts WHERE post_uid IN (SELECT unnest(?))", [S.post_uid.tolist()]).df()
    S = S.merge(txt, on="post_uid")
    S = S.sample(frac=1.0, random_state=SEED).reset_index(drop=True)          # shuffled, so the reviewer is not led by the cluster order
    S["row"] = [f"C{i + 1:04d}" for i in range(len(S))]
    S[["row", "post_uid", "D", "cluster", "pick", "suggested_T", "sim"]].to_parquet(GOLD / f"cluster_review_key_{Path(a.out).stem}.parquet", index=False)   # one key per sheet, nothing overwritten
    CL = pd.DataFrame(cluster_rows)

    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation
    wb = Workbook(); ws = wb.active; ws.title = "Review"
    hdr = ["Row", "Platform", "Lang", "Text", "D-theme (stage 27)", "Cluster", "Suggested T-topic (from gold seeds)", "Security relevant? (Yes/No/Unsure)", "Your D-theme", "Your T-topic", "Notes"]
    ws.append(hdr)
    for r in S.itertuples():
        ws.append([r.row, r.platform, r.lang if isinstance(r.lang, str) and r.lang else "-", str(r.text)[:1500], r.D, r.cluster, r.suggested_T or "", None, None, None, None])
    fill = PatternFill("solid", fgColor="DDE6F3"); inp = PatternFill("solid", fgColor="FFF8DC")
    for c in ws[1]:
        c.font = Font(bold=True); c.alignment = Alignment(wrap_text=True, vertical="center"); c.fill = fill
    for row in ws.iter_rows(min_row=2):
        row[3].alignment = Alignment(wrap_text=True, vertical="top")
        for c in row[7:11]: c.fill = inp
    for col, w in zip("ABCDEFGHIJK", (8, 13, 6, 90, 12, 10, 18, 16, 12, 14, 30)): ws.column_dimensions[col].width = w
    ws.freeze_panes = "E2"
    ls = wb.create_sheet("Lists")
    ls.append(["Security relevant", "D-theme", "T-topic"])
    ds = [f"D0{i}" for i in range(1, 10)]; ts = topics["T"].tolist(); yn = ["Yes", "No", "Unsure"]
    for i in range(max(len(ds), len(ts), 3)):
        ls.append([yn[i] if i < 3 else None, ds[i] if i < len(ds) else None, ts[i] if i < len(ts) else None])
    n = ws.max_row
    for col, ref in (("H", f"=Lists!$A$2:$A$4"), ("I", f"=Lists!$B$2:$B${1 + len(ds)}"), ("J", f"=Lists!$C$2:$C${1 + len(ts)}")):
        dv = DataValidation(type="list", formula1=ref, allow_blank=True); ws.add_data_validation(dv); dv.add(f"{col}2:{col}{n}")
    cs = wb.create_sheet("Clusters"); cs.append(["Cluster", "D-theme", "Cluster size", "Posts in sheet", "Gold seeds", "Seed T-topics", "Suggested T-topic", "Top platforms"])
    for r in CL.itertuples(): cs.append([r.cluster, r.D, r.size, r.sampled, r.n_seeds, r.seed_votes, r.suggested_T or "", r.platforms])
    for c in cs[1]: c.font = Font(bold=True)
    tsht = wb.create_sheet("Topics"); tsht.append(["D-theme", "T-topic", "Name", "Definition", "Typical", "Not this (use instead)"])
    for r in topics.itertuples(): tsht.append([r.D, r.T, r.Name, r.Definition, r.Typical, r.NotThis])
    for c in tsht[1]: c.font = Font(bold=True)
    gd_ = wb.create_sheet("Guide")
    for line in ("Review sheet for T-topic labelling. Posts are grouped by clusters inside each D-theme; the cluster column is only to show structure, the order is shuffled.",
                 "Please fill the yellow columns without looking at any AI label: 1) Security relevant? 2) Your D-theme 3) Your T-topic (see the Topics sheet and its 'Not this' column).",
                 "'D-theme (stage 27)' is the automatic hybrid theme (about 81% right when lenient, 56% strict in the earlier audit): disagree with it freely.",
                 "'Suggested T-topic' appears only where >= 2 analyst-gold posts in the cluster agree on >= 60%; it is a hint, not a label.",
                 "Posts are distinct messages with at least 40 characters, drawn from posts the stage-27 theme already assigned (so this sheet says nothing about posts with no theme).",
                 "Use Unsure when the text is truncated, link-only or you cannot read the language, and say why in Notes."):
        gd_.append([line])
    gd_.column_dimensions["A"].width = 160
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    wb.save(a.out)
    print("rows in sheet:", len(S), "| clusters:", len(CL), "| clusters with a suggestion:", int(CL.suggested_T.notna().sum()), "->", a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
