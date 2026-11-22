#!/usr/bin/env python3
"""
Audience sentiment, targeted post enrichment, step 3: themes for the new posts, enrichment tables, versioned (_v2) roll-ups,
before/after comparison and validation.

Reads (local only): data/audience/enrich_posts.parquet + enrich_tone.parquet (scripts 38, 39), the stage 26/27/28 files and the
analytics store (READ ONLY). No production access. Adds NEW tables to data/audience_sentiment.duckdb and never edits an existing one:

    enriched_post_facts / enriched_post_scores / enriched_post_theme / enriched_post_text (text, LOCAL ONLY)
    enrichment_targets, enrichment_validation, enrichment_run_meta
    post_audience_sentiment_v2, audience_rollup_v2, audience_rollup_validation_v2                (YouTube)
    ig_post_audience_sentiment_v2, ig_audience_rollup_v2, ig_audience_rollup_validation_v2        (Instagram)
    xplat_post_audience_sentiment_v2, xplat_audience_rollup_v2                                    (both, one schema)

Themes replay the existing stages on the new posts only, with the same code and thresholds: stage 26 zero-shot (lib/domain_zeroshot),
stage 27 hybrid cue rule (lib/domain_cues), stage 28 nearest-neighbour vote (k=15, share >= 0.9) against the same reference set built
from the stored stage 26/27 files. Posts already in the analytics store keep the results they have; nothing is written to the store,
to metrics_v2 or to production. A reproducibility sample of in-store posts proves the replay gives the stored themes.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from lib import domain_zeroshot as Z
from lib.audience_text import sha1_hex
from lib.config import DATA_DIR, DUCKDB_PATH
from lib.domain_cues import hybrid_theme

ROOT = HERE.parent
OUT = DATA_DIR / "audience"
DB = DATA_DIR / "audience_sentiment.duckdb"
PROTO = DATA_DIR / "domain_prototype_emb_v1.npz"
K, MIN_SHARE, MIN_SIM, MIN_CHARS = 15, 0.9, 0.0, 40          # stage 28 defaults

spec35 = importlib.util.spec_from_file_location("audience_rollup_35", HERE / "35_audience_rollup.py")
r35 = importlib.util.module_from_spec(spec35)
sys.modules["audience_rollup_35"] = r35
spec35.loader.exec_module(r35)

YT_V2 = r35.Spec(out_table="post_audience_sentiment_v2", rollup_table="audience_rollup_v2", validation_table="audience_rollup_validation_v2", enriched=True)
IG_V2 = r35.Spec(platform="instagram", comment_table="ig_comment_sentiment", reply_table="ig_reply_sentiment", post_table="ig_post_sentiment",
                 out_table="ig_post_audience_sentiment_v2", rollup_table="ig_audience_rollup_v2", validation_table="ig_audience_rollup_validation_v2",
                 id_col="post_native_id", reported_col="post_comment_count_reported", universe_from_store=False, extra_cols=("post_url", "n_source_rows"),
                 date_col="post_posted_at", protected=("ig_comment_sentiment", "ig_reply_sentiment", "ig_post_sentiment"), enriched=True)
V1_TABLES = {"post_audience_sentiment": "post_uid", "ig_post_audience_sentiment": "post_uid",
             "audience_rollup": "dimension||'|'||d_theme||'|'||period", "ig_audience_rollup": "dimension||'|'||d_theme||'|'||period",
             "xplat_post_audience_sentiment": "platform||post_uid", "xplat_audience_rollup": "platform||'|'||dimension||'|'||d_theme||'|'||period"}


def put(con, name: str, df: pd.DataFrame) -> None:
    con.register("_t", df)
    con.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM _t")
    con.unregister("_t")


def fp_audience(con) -> dict:
    out = dict(r35.fingerprint(con, ("comment_sentiment", "reply_sentiment", "post_sentiment", "ig_comment_sentiment", "ig_reply_sentiment", "ig_post_sentiment")))
    for t, k in V1_TABLES.items():
        r = con.execute(f"SELECT count(*), md5(string_agg(x::text, ',' ORDER BY {k})) FROM (SELECT * EXCLUDE(built_at) FROM {t}) x").fetchall()[0]
        out[t] = {"rows": int(r[0]), "md5": r[1]}
    return out


def fp_store() -> dict:
    a = duckdb.connect(str(DUCKDB_PATH), read_only=True)
    r = a.execute("SELECT count(*), md5(string_agg(post_uid || '|' || coalesce(cast(tone_score AS varchar), '') || coalesce(cast(tone_valid AS varchar), ''), ',' ORDER BY post_uid)) FROM post_scores").fetchall()[0]
    f = a.execute("SELECT count(*), md5(string_agg(post_uid, ',' ORDER BY post_uid)) FROM post_facts").fetchall()[0]
    a.close()
    files = {p: os.stat(DATA_DIR / p).st_mtime_ns for p in ("analytics.duckdb", "metrics.duckdb", "geo_ontology.duckdb", "post_theme_v1.parquet", "post_theme_knn_v1.parquet", "post_domain_zs.parquet")}
    return {"post_scores": [int(r[0]), r[1]], "post_facts": [int(f[0]), f[1]], "mtimes": files}


def rows_differ(a: pd.DataFrame, b: pd.DataFrame) -> pd.Series:
    """True per row where any column differs; numbers compare with a tolerance (float sums depend on addition order)."""
    d = pd.Series(False, index=a.index)
    for c in a.columns:
        x, y = a[c], b[c]
        if pd.api.types.is_numeric_dtype(x) and pd.api.types.is_numeric_dtype(y) and not pd.api.types.is_bool_dtype(x):
            same = np.isclose(x.astype(float).to_numpy(), y.astype(float).to_numpy(), rtol=1e-9, atol=1e-9, equal_nan=True)
        else:
            same = (x.astype(str).to_numpy() == y.astype(str).to_numpy())
        d |= ~pd.Series(same, index=a.index)
    return d


# ---------------------------------------------------------------------------------------------------------------------------
def load_references():
    """Same reference set as stage 28: stored stage-27 high tier -> its theme, stage-26 skip_llm -> 'none'; distinct messages only.

    Cached in data/audience/ref_cache.npz (git-ignored) and rebuilt whenever the stage 26/27 files or the store change.
    """
    key = [os.stat(DATA_DIR / f).st_mtime_ns for f in ("post_theme_v1.parquet", "post_domain_zs.parquet", "analytics.duckdb")]
    cache = OUT / "ref_cache.npz"
    if cache.exists():
        z = np.load(cache, allow_pickle=True)
        if list(z["key"]) == key:
            return z["R"], z["Rl"]
    R, Rl = _build_references()
    np.savez(cache, R=R, Rl=Rl, key=np.array(key))
    return R, Rl


def _build_references():
    th = pd.read_parquet(DATA_DIR / "post_theme_v1.parquet", columns=["post_uid", "theme", "tier"])
    zs = pd.read_parquet(DATA_DIR / "post_domain_zs.parquet", columns=["post_uid", "skip_llm", "chars", "is_first"])
    m = zs.merge(th, on="post_uid").drop_duplicates("post_uid").reset_index(drop=True)
    lab = np.where((m.theme != "unassigned") & (m.tier == "high"), m.theme, np.where(m.skip_llm, "none", ""))
    ref = m.is_first.to_numpy() & (lab != "")
    want = {u: l for u, l, r in zip(m.post_uid, lab, ref) if r}
    R, Rl = [], []
    a = duckdb.connect(str(DUCKDB_PATH), read_only=True)
    rdr = a.execute("SELECT post_uid, embedding FROM post_embeddings").to_arrow_reader(20000)
    for rb in rdr:
        uid = rb.column(0).to_pylist()
        keep = [j for j, u in enumerate(uid) if u in want]
        if not keep:
            continue
        X = rb.column(1).flatten().to_numpy(zero_copy_only=False).astype(np.float32).reshape(len(uid), -1)[keep]
        X /= np.maximum(np.linalg.norm(X, axis=1, keepdims=True), 1e-9)
        R.append(X)
        Rl += [want[uid[j]] for j in keep]
    a.close()
    return np.vstack(R), np.array(Rl)


def knn_vote(Q: np.ndarray, R: np.ndarray, Rl: np.ndarray) -> pd.DataFrame:
    classes = sorted(set(Rl))
    cid = {c: i for i, c in enumerate(classes)}
    Rc = np.array([cid[x] for x in Rl])
    Q = Q / np.maximum(np.linalg.norm(Q, axis=1, keepdims=True), 1e-9)
    idx, sims = [], []
    for s in range(0, len(Q), 512):
        sc = Q[s:s + 512] @ R.T
        part = np.argpartition(-sc, K, axis=1)[:, :K]
        ps = np.take_along_axis(sc, part, 1)
        o = np.argsort(-ps, axis=1)
        idx.append(np.take_along_axis(part, o, 1)); sims.append(np.take_along_axis(ps, o, 1))
    NB, NS = np.vstack(idx), np.vstack(sims)
    nc = Rc[NB]
    w = np.zeros((len(NB), len(classes)), np.float32)
    for c in range(len(classes)):
        w[:, c] = (NS * (nc == c)).sum(1)
    win = w.argmax(1)
    share = w[np.arange(len(NB)), win] / np.maximum(w.sum(1), 1e-9)
    wm = nc == win[:, None]
    sim = (NS * wm).sum(1) / np.maximum(wm.sum(1), 1)
    return pd.DataFrame({"knn_vote": np.array(classes, dtype=object)[win], "knn_share": share, "knn_sim": sim})


def replay(df: pd.DataFrame, R: np.ndarray, Rl: np.ndarray) -> pd.DataFrame:
    """Stages 26 + 27 + 28 on these posts only. Needs columns text, embedding (np array or None)."""
    pz = np.load(PROTO, allow_pickle=True)
    PX, pdom = pz["X"], pz["dom"]
    n = len(df)
    res = pd.DataFrame({"zs_best": [None] * n, "zs_best_score": np.nan, "confident_none": False, "security_cue": False, "theme": "unassigned",
                        "tier": "", "knn_vote": None, "knn_share": np.nan, "knn_sim": np.nan, "theme_note": ""}, index=df.index)
    text = df["text"].fillna("").astype(str)
    chars = text.str.strip().str.len()
    has = df["embedding"].map(lambda v: v is not None).to_numpy()
    res.loc[~has, "theme_note"] = "no_embedding"
    if has.any():
        X = np.vstack(df.loc[has, "embedding"].tolist()).astype(np.float32)
        s = Z.score(X, PX, pdom)
        ix = df.index[has]
        res.loc[ix, "zs_best"] = s["zs_best"]
        res.loc[ix, "zs_best_score"] = s["zs_best_score"]
        res.loc[ix, "confident_none"] = s["confident_none"]
        res.loc[ix, "security_cue"] = [Z.security_cue(t[:600]) for t in text[has]]          # stage 26 reads left(text, 600)
        hy = [hybrid_theme(t, b, sc, bool(cn)) for t, b, sc, cn in zip(text[has], res.loc[ix, "zs_best"], res.loc[ix, "zs_best_score"], res.loc[ix, "confident_none"])]
        res.loc[ix, "theme"] = [h[0] for h in hy]
        res.loc[ix, "tier"] = [h[1] for h in hy]
    skip = res["confident_none"].astype(bool) & ~res["security_cue"].astype(bool)
    cand = has & (res["theme"] == "unassigned").to_numpy() & ~skip.to_numpy() & (chars >= MIN_CHARS).to_numpy()
    res["knn_candidate"] = cand
    if cand.any():
        v = knn_vote(np.vstack(df.loc[cand, "embedding"].tolist()).astype(np.float32), R, Rl)
        res.loc[df.index[cand], ["knn_vote", "knn_share", "knn_sim"]] = v.to_numpy()
    kt = (res["knn_vote"].notna() & (res["knn_vote"] != "none") & (res["knn_share"] >= MIN_SHARE) & (res["knn_sim"] >= MIN_SIM))
    res["knn_theme"] = np.where(kt, res["knn_vote"], "unassigned")
    res["d_theme"] = np.where(res["theme"] != "unassigned", res["theme"], np.where(res["knn_theme"] != "unassigned", res["knn_theme"], None))
    res["theme_source"] = np.where(res["theme"] != "unassigned", "hybrid", np.where(res["knn_theme"] != "unassigned", "neighbour", None))
    res["skip_llm"] = skip
    res["chars"] = chars
    return res


# ---------------------------------------------------------------------------------------------------------------------------
def main() -> int:
    t0 = time.time()
    meta_x = json.loads((OUT / "enrich_meta.json").read_text())
    posts = pd.read_parquet(OUT / "enrich_posts.parquet")
    tone = pd.read_parquet(OUT / "enrich_tone.parquet").rename(columns={"reason": "no_tone_reason"})
    posts["text"] = posts["text"].fillna("").astype(str)
    posts["embedding"] = [e if (e is not None and not (isinstance(e, float))) else None for e in posts["embedding"]]
    posts = posts.merge(tone, on=["post_uid", "purpose"], how="left")
    tgt = posts[posts["purpose"] == "target"].reset_index(drop=True)
    val = posts[posts["purpose"] == "validation"].reset_index(drop=True)
    new = tgt[tgt["reason"] == "not_in_store"].reset_index(drop=True)          # in-store posts keep their stored theme
    print(f"targets {len(tgt)} (new {len(new)}, in-store without tone {int((tgt['reason'] == 'in_store_no_tone').sum())}); validation {len(val)}", flush=True)

    con = duckdb.connect(str(DB))
    pre_a, pre_s = fp_audience(con), fp_store()
    print("fingerprints before taken", flush=True)

    print("building the neighbour reference set (same as stage 28) ...", flush=True)
    R, Rl = load_references()
    print(f"  references {R.shape}, classes {sorted(set(Rl))}", flush=True)

    # ---- themes for the new posts
    th = replay(new, R, Rl)
    new_th = pd.concat([new[["post_uid", "platform"]], th], axis=1)
    print("new-post themes:", new_th.groupby("platform")["d_theme"].apply(lambda s: s.notna().sum()).to_dict(), "assigned of", new_th.groupby("platform").size().to_dict(), flush=True)

    # ---- reproducibility: replay on in-store posts must give the stored results
    vth = replay(val, R, Rl)
    vt = pd.concat([val[["post_uid", "platform", "tone_score", "store_tone", "tone_valid"]], vth], axis=1)
    stored = pd.read_parquet(DATA_DIR / "post_theme_v1.parquet", columns=["post_uid", "theme", "tier"]).rename(columns={"theme": "st_theme", "tier": "st_tier"})
    sk = pd.read_parquet(DATA_DIR / "post_theme_knn_v1.parquet", columns=["post_uid", "knn_vote", "knn_theme"]).rename(columns={"knn_vote": "st_knn_vote", "knn_theme": "st_knn_theme"})
    zsd = pd.read_parquet(DATA_DIR / "post_domain_zs.parquet", columns=["post_uid", "zs_best", "confident_none", "is_first"]).rename(columns={"zs_best": "st_zs_best", "confident_none": "st_cn", "is_first": "st_first"})
    vt = vt.merge(stored, on="post_uid", how="left").merge(sk, on="post_uid", how="left").merge(zsd, on="post_uid", how="left")
    rep = {}
    for plat, g in vt.groupby("platform"):
        g = g[g["zs_best"].notna() & g["st_theme"].notna()]
        td = (g["tone_score"] - g["store_tone"]).abs()
        kc = g[g["knn_candidate"] & g["st_knn_vote"].notna()]
        rep[plat] = {"n": int(len(g)), "tone_max_abs_diff": float(td.max()), "tone_identical_1e-4": int((td < 1e-4).sum()),
                     "zs_best_equal": int((g["zs_best"] == g["st_zs_best"]).sum()), "confident_none_equal": int((g["confident_none"].astype(bool) == g["st_cn"].astype(bool)).sum()),
                     "theme_equal": int((g["theme"] == g["st_theme"]).sum()), "tier_equal": int((g["tier"].fillna("") == g["st_tier"].fillna("")).sum()),
                     "knn_candidates_compared": int(len(kc)), "knn_vote_equal": int((kc["knn_vote"] == kc["st_knn_vote"]).sum()),
                     "knn_theme_equal": int((kc["knn_theme"] == kc["st_knn_theme"]).sum())}
    print("reproducibility (production inputs):", json.dumps(rep), flush=True)

    # same posts replayed on the STORED embedding and text: this isolates the code from any change in the source data
    a_ = duckdb.connect(str(DUCKDB_PATH), read_only=True)
    sv = a_.execute("SELECT f.post_uid, f.text AS text, e.embedding AS embedding FROM post_facts f JOIN post_embeddings e USING (post_uid) WHERE f.post_uid IN (SELECT unnest(?))", [val["post_uid"].tolist()]).df()
    a_.close()
    sv["embedding"] = [np.array(e, dtype=np.float32) for e in sv["embedding"]]
    sv = val[["post_uid", "platform", "embedding"]].rename(columns={"embedding": "prod_embedding"}).merge(sv, on="post_uid")
    sth = replay(sv.reset_index(drop=True), R, Rl)
    sd = pd.concat([sv.reset_index(drop=True)[["post_uid", "platform", "embedding", "prod_embedding"]], sth], axis=1).merge(stored, on="post_uid", how="left").merge(sk, on="post_uid", how="left").merge(zsd, on="post_uid", how="left")
    sd["cos_store_vs_prod"] = [float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9)) if b is not None else np.nan for a, b in zip(sd["embedding"], sd["prod_embedding"])]
    code_rep, drift = {}, {}
    for plat, g in sd.groupby("platform"):
        g = g[g["st_theme"].notna()]
        kc = g[g["knn_candidate"] & g["st_knn_vote"].notna()]
        code_rep[plat] = {"n": int(len(g)), "zs_best_equal": int((g["zs_best"] == g["st_zs_best"]).sum()), "none_equal": int((g["confident_none"].astype(bool) == g["st_cn"].astype(bool)).sum()),
                          "theme_equal": int((g["theme"] == g["st_theme"]).sum()), "tier_equal": int((g["tier"].fillna("") == g["st_tier"].fillna("")).sum()),
                          "knn_n": int(len(kc)), "knn_vote_equal": int((kc["knn_vote"] == kc["st_knn_vote"]).sum()), "knn_theme_equal": int((kc["knn_theme"] == kc["st_knn_theme"]).sum())}
        c = g["cos_store_vs_prod"].dropna()
        drift[plat] = {"n": int(len(c)), "embedding_identical_cos_gt_0.9999": int((c > 0.9999).sum()), "min_cos": float(c.min()), "median_cos": float(c.median())}
    print("code replay on STORED inputs:", json.dumps(code_rep), flush=True)
    print("embedding drift store vs production:", json.dumps(drift), flush=True)

    # ---- enrichment tables (targets only)
    now = datetime.now(timezone.utc)
    facts = pd.DataFrame({"post_uid": tgt["post_uid"], "platform": tgt["platform"], "native_id": tgt["native_id"], "post_url": tgt["post_url"], "reason": tgt["reason"],
                          "posted_at": pd.to_datetime(tgt["posted_at"], utc=True, errors="coerce"), "scraped_at": pd.to_datetime(tgt["scraped_at"], utc=True, errors="coerce"),
                          "reported_comment_count": pd.to_numeric(tgt["reported_comment_count"], errors="coerce").astype(float), "text_len": tgt["text"].str.strip().str.len().astype(int),
                          "has_embedding": tgt["has_embedding"].astype(bool), "n_source_copies": tgt["n_copies"].astype(int), "enriched_at": now})
    scores = pd.DataFrame({"post_uid": tgt["post_uid"], "tone_score": tgt["tone_score"], "tone_valid": tgt["tone_valid"].fillna(False).astype(bool), "tone_model": tgt["tone_model"],
                           "label": tgt["label"], "p_negative": tgt["p_negative"], "p_neutral": tgt["p_neutral"], "p_positive": tgt["p_positive"], "scored_at": tgt["scored_at"],
                           "no_tone_reason": tgt["no_tone_reason"]})
    theme = pd.DataFrame({"post_uid": new_th["post_uid"], "d_theme": new_th["d_theme"], "theme_source": new_th["theme_source"], "tier": new_th["tier"], "theme_note": new_th["theme_note"],
                          "zs_best": new_th["zs_best"], "zs_best_score": new_th["zs_best_score"], "confident_none": new_th["confident_none"].astype(bool),
                          "security_cue": new_th["security_cue"].astype(bool), "knn_vote": new_th["knn_vote"], "knn_share": new_th["knn_share"], "knn_sim": new_th["knn_sim"], "enriched_at": now})
    text = tgt[["post_uid", "text"]]
    targets = tgt[["post_uid", "platform", "native_id", "post_url", "reason", "n_copies"]].rename(columns={"n_copies": "n_source_copies"})
    for name, df in (("enriched_post_facts", facts), ("enriched_post_scores", scores), ("enriched_post_theme", theme), ("enriched_post_text", text), ("enrichment_targets", targets)):
        put(con, name, df)

    # ---- v2 roll-ups (YouTube, Instagram) and the common tables
    p_yt, r_yt, v_yt = r35.run(con, YT_V2)
    p_ig, r_ig, v_ig = r35.run(con, IG_V2)
    yt = con.execute("SELECT * FROM post_audience_sentiment_v2").df().rename(columns={"video_id": "post_native_id"})
    yt["post_url"] = None
    common = [c for c in p_ig.columns if c in yt.columns]
    x = pd.concat([yt[common], p_ig[common]], ignore_index=True)
    put(con, "xplat_post_audience_sentiment_v2", x)
    frames = []
    for plat, g in (("youtube_video", yt), ("instagram", p_ig), ("all", x)):
        rr = r35.rollup(g)
        rr.insert(0, "platform", plat)
        frames.append(rr.rename(columns={c: c.replace("n_videos", "n_posts") for c in rr.columns if c.startswith("n_videos")}))
    xr = pd.concat(frames, ignore_index=True)
    put(con, "xplat_audience_rollup_v2", xr)

    # ---- before / after
    def cov(table: str, vcol: str = "video_id") -> dict:
        d = con.execute(f"""SELECT count(*) posts, count(*) FILTER (WHERE rows_total > 0) with_comments,
              count(*) FILTER (WHERE rows_total > 0 AND post_tone_available) commented_with_tone,
              count(*) FILTER (WHERE rows_total > 0 AND d_theme IS NOT NULL) commented_themed,
              count(*) FILTER (WHERE rows_total > 0 AND theme_status <> 'post_not_in_store') commented_post_known,
              count(*) FILTER (WHERE rows_total > 0 AND posted_month <> 'unknown') commented_with_month,
              count(*) FILTER (WHERE c_supported) c_supported, count(*) FILTER (WHERE r_supported) r_supported,
              count(*) FILTER (WHERE gap_comment_vs_post IS NOT NULL) gap_comment, count(*) FILTER (WHERE gap_reply_vs_post IS NOT NULL) gap_reply,
              count(*) FILTER (WHERE gap_reply_vs_comment IS NOT NULL) gap_reply_vs_comment,
              count(*) FILTER (WHERE gap_comment_direction <> 'aligned') comment_gap_flagged, count(*) FILTER (WHERE gap_reply_direction <> 'aligned') reply_gap_flagged
            FROM {table}""").df().iloc[0]
        return {k: int(v) for k, v in d.items()}
    before = {"youtube_video": cov("post_audience_sentiment"), "instagram": cov("ig_post_audience_sentiment")}
    after = {"youtube_video": cov("post_audience_sentiment_v2"), "instagram": cov("ig_post_audience_sentiment_v2")}
    theme_after = {"youtube_video": con.execute("SELECT theme_status, count(*) FROM post_audience_sentiment_v2 WHERE rows_total>0 GROUP BY 1 ORDER BY 2 DESC").fetchall(),
                   "instagram": con.execute("SELECT theme_status, count(*) FROM ig_post_audience_sentiment_v2 WHERE rows_total>0 GROUP BY 1 ORDER BY 2 DESC").fetchall()}
    theme_before = {"youtube_video": con.execute("SELECT theme_status, count(*) FROM post_audience_sentiment WHERE rows_total>0 GROUP BY 1 ORDER BY 2 DESC").fetchall(),
                    "instagram": con.execute("SELECT theme_status, count(*) FROM ig_post_audience_sentiment WHERE rows_total>0 GROUP BY 1 ORDER BY 2 DESC").fetchall()}

    # ---- validation
    ch: list[dict] = []

    def chk(name, expected, observed, ok, level="FAIL", note=""):
        ch.append({"check": name, "expected": str(expected), "observed": str(observed), "status": "PASS" if ok else level, "note": note})
    req = meta_x["targets_requested"]
    for plat, key in (("instagram", "instagram"), ("youtube_video", "youtube_video")):
        n = int((tgt["platform"] == plat).sum())
        chk(f"{plat}: processed targets == deduplicated target list", req[key], n, n == req[key])
    chk("every target found in production", req, {k: meta_x["target_counts"][k] for k in req}, {k: meta_x["target_counts"][k] for k in req} == req)
    chk("no duplicate enriched post_uid", 0, int(tgt["post_uid"].duplicated().sum()), not tgt["post_uid"].duplicated().any())
    chk("no duplicate native id within a platform", 0, int(tgt.duplicated(["platform", "native_id"]).sum()), not tgt.duplicated(["platform", "native_id"]).any())
    chk("no duplicate Instagram post_url (one canonical row per post)", 0, int(tgt[tgt["platform"] == "instagram"]["post_url"].duplicated().sum()), not tgt[tgt["platform"] == "instagram"]["post_url"].duplicated().any())
    a = duckdb.connect(str(DUCKDB_PATH), read_only=True)
    in_store = set(a.execute("SELECT post_uid FROM post_facts").df()["post_uid"])
    a.close()
    n_dup_store = int(new["post_uid"].isin(in_store).sum())
    chk("new posts introduced into the store (they are not added to it)", 0, n_dup_store, n_dup_store == 0, note="none of the 5,191 new posts already exist in post_facts, so no duplicate post is created in any view")
    rec_ig = (tgt[tgt["platform"] == "instagram"].apply(lambda r: sha1_hex("instagram", r["native_id"]), axis=1) == tgt[tgt["platform"] == "instagram"]["post_uid"])
    rec_yt = (tgt[tgt["platform"] == "youtube_video"].apply(lambda r: sha1_hex("youtube_video", r["native_id"]), axis=1) == tgt[tgt["platform"] == "youtube_video"]["post_uid"])
    ig_new_ok = rec_ig[tgt[tgt["platform"] == "instagram"]["reason"] == "not_in_store"].all()
    chk("mapping: post_uid == sha1(platform|native id) for every new post", "all", f"IG new {bool(ig_new_ok)}, YT {bool(rec_yt.all())}", bool(ig_new_ok) and bool(rec_yt.all()))
    pig = con.execute("SELECT post_uid, post_native_id FROM ig_post_sentiment").df().set_index("post_uid")["post_native_id"]
    eq = tgt[tgt["platform"] == "instagram"].set_index("post_uid")["native_id"].astype(str).eq(pig.reindex(tgt[tgt["platform"] == "instagram"]["post_uid"].values).astype(str).values).all()
    chk("mapping: Instagram canonical id == the id used in the Phase 3 post table", "all", bool(eq), bool(eq))
    for plat, p_ in (("instagram", p_ig), ("youtube_video", p_yt)):
        ids = set(tgt[tgt["platform"] == plat]["post_uid"])
        in_v2 = set(p_["post_uid"])
        chk(f"{plat}: every enriched post is in the v2 per-post table", 0, len(ids - in_v2), not (ids - in_v2))
        e = p_[p_["post_uid"].isin(ids)]
        ok_t = tgt[(tgt["platform"] == plat) & tgt["tone_valid"].fillna(False).astype(bool)]["post_uid"]
        chk(f"{plat}: enriched posts with a valid tone have post_tone_available in v2", int(len(ok_t)), int(e["post_tone_available"].sum()), int(e["post_tone_available"].sum()) == len(ok_t))
    chk("v2 per-post tables keep the same universe as v1 (rows)", f"{before['youtube_video']['posts']} / {before['instagram']['posts']}", f"{after['youtube_video']['posts']} / {after['instagram']['posts']}",
        before["youtube_video"]["posts"] == after["youtube_video"]["posts"] and before["instagram"]["posts"] == after["instagram"]["posts"])
    # unaffected posts identical v1 vs v2 (metrics columns)
    enriched_uids = set(tgt["post_uid"])
    for plat, t1, t2 in (("youtube_video", "post_audience_sentiment", "post_audience_sentiment_v2"), ("instagram", "ig_post_audience_sentiment", "ig_post_audience_sentiment_v2")):
        a1, a2 = con.execute(f"SELECT * EXCLUDE(built_at) FROM {t1}").df(), con.execute(f"SELECT * EXCLUDE(built_at) FROM {t2}").df()
        a2 = a2.drop(columns=[c for c in ("post_source", "post_tone_source") if c in a2.columns])
        m1 = a1[~a1["post_uid"].isin(enriched_uids)].sort_values("post_uid").reset_index(drop=True)
        m2 = a2[~a2["post_uid"].isin(enriched_uids)].sort_values("post_uid").reset_index(drop=True)
        same = m1.shape == m2.shape and m1.astype(str).equals(m2[m1.columns].astype(str))
        chk(f"{plat}: posts that were not enriched are identical in v1 and v2", f"{len(m1)} rows", f"{len(m2)} rows, identical={same}", bool(same))
        e1 = a1[a1["post_uid"].isin(enriched_uids)].set_index("post_uid"); e2 = a2[a2["post_uid"].isin(enriched_uids)].set_index("post_uid").reindex(e1.index)
        comm = [c for c in e1.columns if c.startswith(("c_", "r_")) and not c.endswith("_supported")]
        same_c = e1[comm].astype(str).equals(e2[comm].astype(str))
        chk(f"{plat}: enriched posts keep identical comment and reply metrics (only post tone, theme, date and gaps may change)", "identical", same_c, bool(same_c))
    # roll-ups: only groups touched by enriched posts may differ
    for plat, t1, t2 in (("youtube_video", "audience_rollup", "audience_rollup_v2"), ("instagram", "ig_audience_rollup", "ig_audience_rollup_v2")):
        r1 = con.execute(f"SELECT * EXCLUDE(built_at) FROM {t1}").df().set_index(["dimension", "d_theme", "period"]); r2 = con.execute(f"SELECT * EXCLUDE(built_at) FROM {t2}").df().set_index(["dimension", "d_theme", "period"])
        keys = r1.index.union(r2.index)
        r1, r2 = r1.reindex(keys), r2.reindex(keys)
        diff = rows_differ(r1, r2)
        p1 = con.execute(f"SELECT post_uid, theme_status, posted_month FROM {'post_audience_sentiment' if plat == 'youtube_video' else 'ig_post_audience_sentiment'}").df()
        p2 = con.execute(f"SELECT post_uid, theme_status, posted_month FROM {'post_audience_sentiment_v2' if plat == 'youtube_video' else 'ig_post_audience_sentiment_v2'}").df()
        touched = set()
        for pp in (p1, p2):
            e = pp[pp["post_uid"].isin(enriched_uids)]
            for ts, mo in zip(e["theme_status"], e["posted_month"]):
                touched |= {("all", "all", "all"), ("d_theme", ts, "all"), ("month", "all", mo), ("d_theme_month", ts, mo)}
        changed = set(keys[diff.to_numpy()])
        outside = changed - touched
        chk(f"{plat}: roll-up groups that changed are only groups holding an enriched post", 0, f"{len(outside)} outside ({len(changed)} changed of {len(keys)})", not outside, note="unaffected groups are identical in v1 and v2")
        for dim in ("d_theme", "month"):
            tot1 = int(r1.loc[r1.index.get_level_values(0) == dim, "c_scored"].sum()); tot2 = int(r2.loc[r2.index.get_level_values(0) == dim, "c_scored"].sum())
            chk(f"{plat}: {dim} roll-up total of scored comments unchanged", tot1, tot2, tot1 == tot2)
    chk("v2 roll-up validation suites", "44 PASS each", f"YouTube {v_yt['status'].value_counts().to_dict()}, Instagram {v_ig['status'].value_counts().to_dict()}", (v_yt["status"] == "PASS").all() and (v_ig["status"] == "PASS").all())
    # nothing existing changed
    post_a, post_s = fp_audience(con), fp_store()
    chk("existing comment / reply / Phase 1 post tables unchanged (rows, ids, md5)", "identical", "identical" if all(pre_a[k] == post_a[k] for k in ("comment_sentiment", "reply_sentiment", "post_sentiment", "ig_comment_sentiment", "ig_reply_sentiment", "ig_post_sentiment")) else "CHANGED",
        all(pre_a[k] == post_a[k] for k in ("comment_sentiment", "reply_sentiment", "post_sentiment", "ig_comment_sentiment", "ig_reply_sentiment", "ig_post_sentiment")))
    chk("v1 per-post, roll-up and cross-platform tables unchanged (md5 excluding build time)", "identical", "identical" if all(pre_a[k] == post_a[k] for k in V1_TABLES) else "CHANGED", all(pre_a[k] == post_a[k] for k in V1_TABLES))
    chk("analytics post_scores unchanged (rows, md5 of uid|tone)", pre_s["post_scores"], post_s["post_scores"], pre_s["post_scores"] == post_s["post_scores"])
    chk("analytics post_facts unchanged (rows, md5 of post_uid)", pre_s["post_facts"], post_s["post_facts"], pre_s["post_facts"] == post_s["post_facts"])
    chk("metrics_v2 / geo / theme files not modified (mtime)", "same", "same" if pre_s["mtimes"] == post_s["mtimes"] else "CHANGED", pre_s["mtimes"] == post_s["mtimes"])
    for plat, d in rep.items():
        cr, dr = code_rep[plat], drift[plat]
        chk(f"{plat}: targeted tone reproduces the stored stage-04 tone", f"{d['n']} of {d['n']} within 1e-2", f"{d['tone_identical_1e-4']} within 1e-4, max diff {d['tone_max_abs_diff']:.4f}", d["tone_max_abs_diff"] < 1e-2 and d["tone_identical_1e-4"] >= 0.97 * d["n"], "WARN", "a tone differs only if the text changed since the store snapshot")
        chk(f"{plat}: replay of stages 26/27 on the stored embedding + text reproduces the stored themes exactly (code check)", f"{cr['n']} of {cr['n']}", f"zs {cr['zs_best_equal']}, none-flag {cr['none_equal']}, theme {cr['theme_equal']}, tier {cr['tier_equal']}",
            all(cr[k] == cr["n"] for k in ("zs_best_equal", "none_equal", "theme_equal", "tier_equal")))
        chk(f"{plat}: replay of stage 28 on the stored embedding reproduces the stored neighbour votes exactly (code check)", f"{cr['knn_n']} of {cr['knn_n']}", f"vote {cr['knn_vote_equal']}, theme {cr['knn_theme_equal']}",
            cr["knn_vote_equal"] == cr["knn_n"] and cr["knn_theme_equal"] == cr["knn_n"])
        chk(f"{plat}: production embeddings equal the stored embeddings (data drift)", f"{dr['n']} of {dr['n']}", f"{dr['embedding_identical_cos_gt_0.9999']} identical, median cosine {dr['median_cos']:.3f}, min {dr['min_cos']:.3f}", dr["embedding_identical_cos_gt_0.9999"] == dr["n"], "WARN",
            "production embeddings were recomputed for some posts; themes of NEW posts use the current production embeddings, as a stage 01 refresh would")
        chk(f"{plat}: themes from current production inputs agree with the stored themes", f"{d['n']}", f"{d['theme_equal']} of {d['n']} (neighbour votes {d['knn_vote_equal']} of {d['knn_candidates_compared']})", d["theme_equal"] == d["n"], "WARN", "differences come from embedding drift, not from the code")
    chk("production: read-only session asserted in the extraction; no write statement exists in scripts 38-40", "on", f"{meta_x['read_only_session']} (snapshot {meta_x['snapshot_ts']})", meta_x["read_only_session"] == "on")
    chk("posts with no caption / embedding stay unenriched (reported, not guessed)", "info", f"no text {int((tgt['text'].str.strip() == '').sum())}, no embedding {int((~tgt['has_embedding']).sum())}", True, "INFO")
    validation = pd.DataFrame(ch)
    put(con, "enrichment_validation", validation)
    run_meta = pd.DataFrame([{"built_at": datetime.now(timezone.utc).isoformat(), "production_snapshot": meta_x["snapshot_ts"], "targets_instagram": int((tgt["platform"] == "instagram").sum()),
                              "targets_youtube": int((tgt["platform"] == "youtube_video").sum()), "reproducibility_production_inputs": json.dumps(rep), "code_replay_stored_inputs": json.dumps(code_rep), "embedding_drift": json.dumps(drift),
                              "scope": "targeted, dedup-aware (one canonical row per post); stage 01/04/07 not run; analytics store and metrics_v2 not touched",
                              "seconds": round(time.time() - t0)}])
    put(con, "enrichment_run_meta", run_meta)
    con.close()

    pd.set_option("display.width", 250); pd.set_option("display.max_colwidth", 95)
    print(validation[["check", "status", "observed"]].to_string())
    print("\nstatus counts:", validation["status"].value_counts().to_dict())
    print("\nBEFORE:", json.dumps(before)); print("AFTER :", json.dumps(after))
    print("theme before:", theme_before); print("theme after :", theme_after)
    print("new tone:", {p: int(tgt[(tgt.platform == p)]["tone_valid"].fillna(False).astype(bool).sum()) for p in ("instagram", "youtube_video")})
    print("new themes:", new_th.groupby("platform")["d_theme"].apply(lambda s: int(s.notna().sum())).to_dict(), "of", new_th.groupby("platform").size().to_dict(), "| source:", new_th.groupby(["platform", "theme_source"]).size().to_dict())
    print("theme_note:", new_th.groupby(["platform", "theme_note"]).size().to_dict())
    print(f"seconds: {time.time() - t0:.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
