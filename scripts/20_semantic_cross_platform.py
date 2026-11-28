#!/usr/bin/env python3
"""Semantic cross-platform events (PRELIMINARY tier).

Links posts on DIFFERENT platforms that say the same thing in different words or languages,
using the stored BGE-M3 post embeddings (read-only; no model is loaded, no GPU needed).

Method
  1. Eligible posts: text >= 35 chars, not numeric/punctuation-only, has an embedding and a timestamp.
  2. Collapse identical text on the same platform to one representative (repeats add no evidence).
  3. Edge between two representatives when: different platforms, posted within WINDOW_DAYS of each
     other, cosine similarity >= SIM_THRESHOLD, and the cleaned text is NOT identical (identical
     text across platforms is the exact-text tier in script 19).
  4. Connected components of that graph = events. Every post sharing a representative's
     (platform, cleaned text) is a member.
  5. Confidence: 'high' when every edge in the event is >= SIM_HIGH, else 'medium'.
     `large_event` flags events with more than LARGE_EVENT_TEXTS distinct texts (chaining risk).

Eligibility also needs real words: at least MIN_LETTERS letters once links are removed (two different YouTube links were "matched" on
their link text alone), and known platform placeholder texts are dropped (401 Reddit posts reading "This post contains content not
supported on old Reddit" formed the largest event). Engagement and views are summed once per collected post (latest scrape per
platform + url): a post scraped 170 times used to count its engagement 170 times.

Precision (hand-judged by the assistant on 40 random pairs, similarity 0.80 to 1.0, window 3 days):
40 of 40 were the same event. NOT independently reviewed. Below 0.80 is untested.

Outputs (data/metrics.duckdb, new tables, nothing else touched):
  cross_platform_semantic_events, cross_platform_semantic_posts
Run AFTER 08 (it does not run inside run_rebuild.sh), with the dashboard container stopped
(DuckDB single writer):   .venv/bin/python scripts/20_semantic_cross_platform.py
Dry run (no write):       .venv/bin/python scripts/20_semantic_cross_platform.py --dry-run
"""
from __future__ import annotations

import hashlib
import sys
import time
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DATA = ROOT / "data"
ANALYTICS_DB = DATA / "analytics.duckdb"
METRICS_DB = DATA / "metrics.duckdb"
EMB_PARQUET = DATA / "parquet" / "post_embeddings"

SIM_THRESHOLD = 0.85   # edge threshold (events at 0.80 chain into 200+ post blobs; 0.85 max was 56)
SIM_HIGH = 0.90        # every edge at or above this -> confidence 'high'
WINDOW_DAYS = 3        # posts must be within this many days of each other
MIN_CHARS = 35
MIN_LETTERS = 15       # letters left after removing links; a text that is only a link has no meaning to compare
LARGE_EVENT_TEXTS = 30
# platform placeholder texts that stand in for content (matched on the lower-cased, whitespace-collapsed text)
PLACEHOLDER_RE = (r"this post contains content not supported on old reddit|^\[(removed|deleted)\]$"
                  r"|^\s*submitted by /u/\S+\s*\[link\]\s*\[comments\]\s*$")           # Reddit link posts that carry no text (628 posts; 19 rows sat in 12 events)
_URL_RE = r"https?://\S+|www\.\S+"


def _log(msg: str) -> None:
    print(msg, flush=True)


def load_eligible_posts(con) -> pd.DataFrame:
    p = con.execute(
        "SELECT post_uid, platform, author_uid, author_name, posted_at, scrape_lag_days, text, url, scraped_at, "
        "coalesce(likes,0) AS l, coalesce(comments,0) AS c, coalesce(shares,0) AS s, "
        "coalesce(views,0) AS v FROM post_facts WHERE text IS NOT NULL").fetchdf()
    p = p[p.posted_at.notna() & (p.text.str.strip().str.len() >= MIN_CHARS)].copy()
    p["ct"] = p.text.str.lower().str.replace(r"\s+", " ", regex=True).str.strip()
    p = p[~p.ct.str.fullmatch(r"[0-9\s\.\,\:\-\_\#\@]+")]
    n0 = len(p)
    words = p.text.str.replace(_URL_RE, " ", regex=True).str.count(r"[^\W\d_]")      # letters outside links
    p = p[(words >= MIN_LETTERS) & ~p.ct.str.contains(PLACEHOLDER_RE, regex=True)].copy()
    _log(f"-> dropped {n0 - len(p):,} link-only / placeholder texts of {n0:,}")
    p["eng"] = p.l + p.c + p.s
    return p


def load_embeddings(post_uids: set[str]) -> tuple[np.ndarray, pd.Series]:
    """Return (matrix float32, Series mapping post_uid -> row) for the requested posts.

    DuckDB reads the parquet in parallel and casts DOUBLE -> FLOAT (round-to-nearest, the same values as numpy's astype(float32)); the rows come
    back in file order, exactly as before. The previous pyarrow read of this single-row-group `list<double>` column ran for over 40 minutes
    without finishing; this takes about 5 minutes. Batches are filtered as they arrive, so only the requested posts are kept in memory."""
    files = sorted(EMB_PARQUET.glob("*.parquet"))
    uids, mats = [], []
    con = duckdb.connect(":memory:")
    try:
        for f in files:
            src = f"read_parquet('{f.as_posix()}')"
            dim = con.execute(f"SELECT len(embedding) FROM {src} LIMIT 1").fetchone()[0]
            reader = con.execute(f"SELECT post_uid, embedding::FLOAT[{dim}] AS e FROM {src}").to_arrow_reader(20000)
            for batch in reader:
                u = np.asarray(batch.column(0).to_pylist())
                keep = pd.Series(u).isin(post_uids).to_numpy()
                if not keep.any():
                    continue
                m = batch.column(1).values.to_numpy(zero_copy_only=False).reshape(len(u), dim)
                uids.append(u[keep]); mats.append(m[keep])
    finally:
        con.close()
    uid = np.concatenate(uids); mat = np.concatenate(mats)
    return mat, pd.Series(np.arange(len(uid)), index=uid)


def build_edges(u: pd.DataFrame, V: np.ndarray) -> pd.DataFrame:
    """Cross-platform pairs within the time window with cosine >= SIM_THRESHOLD (each pair once)."""
    day = u.posted_at.dt.tz_convert("UTC").dt.floor("D")
    di = (day - day.min()).dt.days.to_numpy()
    plat = u.platform.to_numpy()
    ctv = u.ct.to_numpy()
    starts = np.searchsorted(di, np.arange(di.max() + 2))
    rows = []
    for d in range(di.max() + 1):
        a0, a1 = starts[d], starts[d + 1]
        if a1 == a0:
            continue
        b0, b1 = starts[d], starts[min(d + WINDOW_DAYS + 1, len(starts) - 1)]
        S = V[a0:a1] @ V[b0:b1].T
        ii, jj = np.nonzero(S >= SIM_THRESHOLD)
        ga, gb = ii + a0, jj + b0
        keep = (gb > ga) & (plat[ga] != plat[gb]) & (ctv[ga] != ctv[gb])
        for x, y, s in zip(ga[keep], gb[keep], S[ii[keep], jj[keep]]):
            rows.append((int(x), int(y), float(s)))
    return pd.DataFrame(rows, columns=["a", "b", "sim"])


def compute_semantic_events(verbose: bool = True):
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    t0 = time.time()
    con = duckdb.connect(str(ANALYTICS_DB), read_only=True)
    try:
        p = load_eligible_posts(con)
        cov = con.execute("SELECT platform, avg((coalesce(views,0)>0)::int) c FROM post_facts GROUP BY 1"
                          ).fetchdf().set_index("platform").c
    finally:
        con.close()
    vp = set(cov[cov > 0].index)
    if verbose:
        _log(f"-> eligible posts: {len(p):,}")
    mat, row = load_embeddings(set(p.post_uid))
    p = p[p.post_uid.isin(row.index)].copy()
    p["ei"] = row.loc[p.post_uid].to_numpy()
    if verbose:
        _log(f"-> embeddings loaded for {len(p):,} posts ({time.time() - t0:.0f}s)")

    # one representative per (platform, cleaned text), ordered by time
    u = p.sort_values("posted_at").drop_duplicates(["platform", "ct"]).reset_index(drop=True)
    V = mat[u.ei.to_numpy()].copy()
    V /= np.linalg.norm(V, axis=1, keepdims=True).clip(1e-9)
    day = u.posted_at.dt.tz_convert("UTC").dt.floor("D")
    order = np.argsort((day - day.min()).dt.days.to_numpy(), kind="stable")
    u = u.iloc[order].reset_index(drop=True)
    V = V[order]
    if verbose:
        _log(f"-> unique (platform, text) representatives: {len(u):,}")
    E = build_edges(u, V)
    if verbose:
        _log(f"-> cross-platform edges >= {SIM_THRESHOLD}: {len(E):,} ({time.time() - t0:.0f}s)")
    if E.empty:
        return pd.DataFrame(), pd.DataFrame()

    n = len(u)
    g = coo_matrix((np.ones(len(E)), (E.a, E.b)), shape=(n, n))
    _, lab = connected_components(g, directed=False)
    nodes = np.unique(np.r_[E.a, E.b])
    comp = pd.Series(lab[nodes], index=nodes)
    E["comp"] = comp.loc[E.a].to_numpy()
    edge_stats = E.groupby("comp").agg(min_sim=("sim", "min"), mean_sim=("sim", "mean"), n_edges=("sim", "size"))

    # every post that shares a representative's (platform, text) belongs to that representative's event
    rep_comp = u.loc[nodes, ["platform", "ct"]].assign(event=comp.to_numpy())
    members = p.merge(rep_comp, on=["platform", "ct"], how="inner")
    members["norm_name"] = members.author_name.fillna("").str.lower().str.replace(r"[^a-z0-9]", "", regex=True)
    # one collected copy per (platform, url): the latest scrape. Totals sum these, not every scrape of the same post.
    members["copy_key"] = members.url.fillna(members.post_uid)
    members = members.sort_values("scraped_at", ascending=False, na_position="last")
    members["latest"] = ~members.duplicated(["event", "platform", "copy_key"])
    members = members.sort_index()

    rows = []
    for ev, x in members.groupby("event"):
        x = x.sort_values("posted_at")
        first = x.iloc[0]
        rep = x.sort_values(["eng", "posted_at"], ascending=[False, True]).drop_duplicates(["platform", "author_uid"])
        st = edge_stats.loc[ev]
        eid = hashlib.md5(("sem|" + "|".join(sorted(x.post_uid.head(50)))).encode()).hexdigest()
        rows.append(dict(
            event_id=eid, snippet=first.text[:220].replace("\n", " "),
            n_platforms=int(x.platform.nunique()), n_texts=int(x.ct.nunique()), n_posts=int(len(x)),
            n_unique_posts=int(len(rep)), n_authors=int(x.author_uid.nunique()),
            same_account_name=bool(x.norm_name.nunique() == 1),
            platforms=sorted(x.platform.unique().tolist()),
            origin_platform=first.platform,
            origin_scrape_lag_days=None if pd.isna(first.scrape_lag_days) else round(float(first.scrape_lag_days), 1),
            origin_time=str(first.posted_at),
            spread_duration_hours=round((x.posted_at.max() - x.posted_at.min()).total_seconds() / 3600.0, 1),
            observed_views=float(x.loc[x.latest & x.platform.isin(vp) & (x.v > 0), "v"].sum()),
            total_engagement=float(x.loc[x.latest, "eng"].sum()),
            min_sim=round(float(st.min_sim), 3), mean_sim=round(float(st.mean_sim), 3),
            match_confidence="high" if st.min_sim >= SIM_HIGH else "medium",
            large_event=bool(x.ct.nunique() > LARGE_EVENT_TEXTS)))
        members.loc[x.index, "event_id"] = eid
    events = pd.DataFrame(rows).sort_values(["n_platforms", "n_unique_posts"], ascending=False).reset_index(drop=True)
    posts = members[["event_id", "post_uid", "platform", "posted_at"]].copy()
    posts["posted_at"] = posts["posted_at"].astype(str)
    if verbose:
        _log(f"-> semantic events: {len(events):,} ({(events.match_confidence == 'high').sum()} high, "
             f"{(events.match_confidence == 'medium').sum()} medium, {int(events.large_event.sum())} large), "
             f"{len(posts):,} posts ({time.time() - t0:.0f}s)")
    return events, posts


def save(events: pd.DataFrame, posts: pd.DataFrame) -> None:
    con = duckdb.connect(str(METRICS_DB), read_only=False)
    try:
        con.execute("DROP TABLE IF EXISTS cross_platform_semantic_events")
        con.execute("DROP TABLE IF EXISTS cross_platform_semantic_posts")
        if len(events):
            con.execute("CREATE TABLE cross_platform_semantic_events AS SELECT * FROM events")
            con.execute("CREATE TABLE cross_platform_semantic_posts AS SELECT * FROM posts")
            con.execute("CREATE INDEX IF NOT EXISTS idx_cps_posts_event ON cross_platform_semantic_posts(event_id)")
    finally:
        con.close()
    _log(f"-> saved {len(events):,} events / {len(posts):,} posts to {METRICS_DB}")


def main() -> int:
    events, posts = compute_semantic_events()
    if "--dry-run" in sys.argv:
        _log("dry run: nothing written")
        return 0
    save(events, posts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
