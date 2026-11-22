#!/usr/bin/env python3
"""
Load the audience-sentiment layers (Phase 1, YouTube) into the delivery Postgres as a NEW schema
`audience_sentiment`. Never touches production, and never touches the `metrics_v2` / `geo_ontology` schemas
(unlike ingest.py this does not drop anything but its own schema).

Source: data/audience_sentiment.duckdb (read-only). Comment TEXT is not delivered (`comment_text` stays local);
tables carry IDs, labels, probabilities, parents, timestamps and flags, so the receiving team joins on comment_id.

It talks to the delivery container through `docker exec ... psql` over the container's local socket, so no
password is needed or stored here (delivery/.env is not required). Container name comes from docker-compose.yml.

    python3 delivery/ingest_audience_sentiment.py          # idempotent: replaces only audience_sentiment.*
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import duckdb

EDA_ROOT = Path(__file__).resolve().parent.parent
SRC = EDA_ROOT / "data" / "audience_sentiment.duckdb"
CONTAINER = "ntro-eda-delivery-postgres"
SCHEMA = "audience_sentiment"

# (table, verdict, caveat). Verdicts follow public._delivery_manifest conventions.
MANIFEST = [
    ("post_sentiment", "SOUND (copy of existing)", "Existing post tone (tone_score = P(pos) - P(neg), cardiffnlp XLM-R) copied unchanged from post_scores for the parent YouTube videos. No probabilities or label: the post pipeline stores only tone_score. Kept separate from comment and reply layers on purpose. Videos missing from the analytics post store have NULL tone (post_in_store=false)."),
    ("comment_sentiment", "PARTIAL", "Top-level YouTube comments scored with the SAME model as posts. Label + 3 probabilities + tone_score. score_status separates scored / not_scored (empty, deleted marker, URL-only, symbols-only) / failed; nothing dropped. Model is general-purpose, not calibrated on this corpus: it reads wording, not attitude (sarcasm, quoted hostility, reported violence are mis-read). Blind hand-check of 60 scored rows (comments + replies): label agreement 73% (95% CI 61-83), 5 of 60 flipped positive<->negative; one reviewer, small sample (docs/AUDIENCE_SENTIMENT_SPEC.md). YouTube only (Phase 1)."),
    ("reply_sentiment", "PARTIAL", "YouTube replies, scored like comments, kept as a separate layer. parent_comment_id / parent_comment_uid link each reply to its top-level comment, post_uid to the video. Same model caveats as comment_sentiment. Short replies and emoji-only replies are flagged in `flags`. Same hand-check as comment_sentiment (replies 23/30 agreed)."),
    ("post_audience_sentiment", "PARTIAL", "Phase 2: one row per YouTube video (14,938). Comment (c_*) and reply (r_*) metrics stay in separate columns: counts, % and net sentiment (100*(pos%-neg%), metrics-v2 units), mean tone (P(pos)-P(neg), same scale as post tone), average label confidence, minimum-support flag. post_tone_score is the existing post tone, NULL where the post pipeline has none (4,219 videos missing from the post store) - never replaced by neutral. gap_* = audience mean tone minus post tone (NULL below 5 scored rows or without post tone); gap_reply_vs_comment compares the two audience layers. comment_data_status separates has_comments / not_collected_known / unknown_no_comment_data / confirmed_zero (YouTube's reported count is NULL for most videos, so true zero is not confirmable). d_theme = existing hybrid D-theme (D-theme accuracy: 81% lenient / 56% strict in the 2026-11-20 audit; 7,000 videos unassigned). Model caveats as comment_sentiment."),
    ("audience_rollup", "PARTIAL", "Phase 2: pooled roll-ups of the per-video table by D-theme, video-posted month, theme x month and overall. Stores counts and sums so groups can be re-summed. Pooled statistics are NULL below 30 scored rows (counts kept); per-video gap means need 5 videos with a gap. Explicit buckets: unassigned (no D-theme) and post_not_in_store; period 'unknown' = no post date. Descriptive only: sentiment reads wording on war-related videos, not attitude."),
    ("audience_rollup_validation", "SOUND", "Phase 2 checks (44 PASS, 0 WARN, 0 FAIL): roll-up totals equal Phase 1 rows, independent recounts, Phase 1 tables unchanged, post tone identical to post_scores, gap recomputation, NULL-not-zero rules."),
    # ---- Phase 3: Instagram (kept separate from YouTube; xplat_* tables share one schema across platforms)
    ("ig_post_sentiment", "SOUND (copy of existing)", "Instagram post layer: one row per DISTINCT post_url (15,862 incl. one row without a url). instagram_posts repeats each post up to 501 times, so rows are copies, not posts (n_source_rows). Existing post tone copied from post_scores (NULL where none: 14,879 of the posts are not in the older analytics store). post_comment_count_reported = largest comment_count seen across copies."),
    ("ig_comment_sentiment", "PARTIAL", "Instagram top-level comments from comments_data, one row per distinct (post_url, comment_id), scored with the SAME model as posts and YouTube. Label + 3 probabilities + tone_score. Text is scored as scraped (comments_data has no translation field, so non-English comments are not translated, unlike YouTube). Blind hand-check of 60 rows (comments + replies): agreement 72% (95% CI 59-81), 4 of 60 flipped positive<->negative; one reviewer. Short, emoji and @mention-led comments are weakly informative (flags). Not calibrated on this corpus."),
    ("ig_reply_sentiment", "PARTIAL", "Instagram replies: nested replies (22,857) plus 964 top-level objects that name a parent_comment_id (shape = top_level_with_parent; parent_in_extract = false because Instagram did not return the parent). Separate layer from comments. parent_comment_id / parent_comment_uid link to the comment where present. Same model caveats as ig_comment_sentiment."),
    ("ig_post_audience_sentiment", "PARTIAL", "Phase 3 per-post roll-up, same logic and columns as post_audience_sentiment: comment (c_*) and reply (r_*) metrics separate, post tone, gaps, comment_data_status. Unlike YouTube, Instagram reports a comment count, so confirmed_zero (reported 0) is real: 7,536 posts; 6,929 posts report comments that were not collected; only 1,397 posts have comment data. Only 983 of the 15,862 posts exist in the older post store, so post tone, D-theme and date are NULL for the rest."),
    ("ig_audience_rollup", "PARTIAL", "Pooled Instagram roll-ups by D-theme, post-month and both (same code as YouTube; n_videos means posts here). Pooled statistics NULL below 30 scored rows. Most posts fall in the post_not_in_store bucket, so theme roll-ups rest on few posts."),
    ("ig_audience_rollup_validation", "SOUND", "44 checks, all PASS (same checks as the YouTube roll-up)."),
    ("ig_sentiment_summary", "PARTIAL", "Label shares and mean tone per layer for Instagram, with cuts by flag, repeated text and reply shape."),
    ("ig_coverage_report", "SOUND", "Production counts (one snapshot) against extracted and scored Instagram rows, including the copy-aware reconciliation."),
    ("ig_validation_checks", "SOUND", "Instagram extraction and scoring checks (38 PASS, 3 WARN, 0 FAIL): copy-aware reconciliation with production, IDs, duplicates, relationships, probabilities, YouTube tables unchanged."),
    ("ig_run_meta", "SOUND", "Snapshot time, model, text rule, post unit and tone formula for the Instagram build."),
    ("xplat_post_audience_sentiment", "PARTIAL", "YouTube videos and Instagram posts in ONE schema (platform = youtube_video | instagram; post_native_id; post_url NULL for YouTube): per-post comment and reply metrics, post tone, gaps, theme, month. Platforms differ in text (YouTube scored on translations, Instagram on original text) and in comment collection coverage, so compare platforms with care."),
    ("xplat_audience_rollup", "PARTIAL", "Pooled roll-ups by platform (youtube_video, instagram, all) x D-theme x month, built from xplat_post_audience_sentiment with the same functions as the per-platform roll-ups (n_posts replaces n_videos). 'all' pools both platforms comment-weighted."),
    # ---- Targeted post enrichment (scripts 38-40): post tone / theme / date for audience posts missing from the older post store; _v2 roll-ups
    ("enriched_post_facts", "PARTIAL", "5,204 posts enriched without a stage 01 refresh: 4,219 YouTube videos and 985 Instagram posts (972 not in the store + 13 in the store with an empty stored caption). One canonical row per post read read-only from production (Instagram: smallest-id copy of a post_url). 19 Instagram posts have no caption in production (13 + 6): no tone, no embedding. Not added to post_facts or metrics_v2."),
    ("enriched_post_scores", "PARTIAL", "Post tone for the enriched posts: same model, 2000-char cap, batching and tone formula as stage 04 (tone_score = P(pos) - P(neg)). Reproduction check on 300 in-store posts: Instagram 150/150 within 1e-4, YouTube 148/150 within 1e-4 (max difference 0.0018, text edited since the store snapshot). Empty text -> tone_valid false."),
    ("enriched_post_theme", "PARTIAL", "D-theme for the 5,191 NEW posts only (stages 26/27/28 replayed with the same code and thresholds; in-store posts keep their stored theme). 1,318 of 5,191 assigned (YouTube 1,166, Instagram 152); the rest are 'unassigned' as in the stored data (6 Instagram posts have no embedding). Code check: replay on stored inputs reproduces stored themes 150/150 per platform. NOTE: production embeddings differ from the stored ones for many posts (YouTube 24/150 identical), so themes use current production embeddings, as a refresh would; themes agree with stored themes for 139/150 YouTube and 150/150 Instagram re-read posts. Hybrid theme accuracy: 81% lenient / 56% strict (2026-11-20 audit)."),
    ("enrichment_targets", "SOUND", "The deduplicated target list (post_uid, platform, native id, post_url, reason, source copies)."),
    ("enrichment_validation", "SOUND", "Phase 4 checks of the enrichment (39 PASS, 3 WARN about embedding drift, 0 FAIL): target counts, no duplicates, mappings, store and v1 tables unchanged, roll-up groups changed only where enriched posts sit, code replay checks."),
    ("enrichment_run_meta", "SOUND", "Production snapshot, scope, reproducibility and embedding-drift measurements for the enrichment run."),
    ("post_audience_sentiment_v2", "PARTIAL", "YouTube per-video roll-up v2: same columns as v1 plus post_source (store | targeted_enrichment) and post_tone_source. Post tone, D-theme and date now exist for all 12,523 videos with comments (v1: 8,304); comment and reply metrics are identical to v1. Gaps: comment gap 7,709 videos (v1 5,263), reply gap 3,517 (v1 2,443). Unenriched videos are identical to v1."),
    ("audience_rollup_v2", "PARTIAL", "YouTube pooled roll-ups v2. Only groups holding an enriched video differ from v1; the 'post_not_in_store' and 'unknown' buckets are now empty. Totals of scored comments are unchanged."),
    ("audience_rollup_validation_v2", "SOUND", "44 roll-up checks, all PASS (YouTube v2)."),
    ("ig_post_audience_sentiment_v2", "PARTIAL", "Instagram per-post roll-up v2 (distinct posts). Post tone for 1,378 of the 1,397 posts with comments (v1: 412; 19 posts have no caption in production), D-theme 230 (v1: 78), post known 1,397 (v1: 425). Gaps: comment gap 596 posts (v1 123), reply gap 316 (v1 45). Comment and reply metrics identical to v1."),
    ("ig_audience_rollup_v2", "PARTIAL", "Instagram pooled roll-ups v2 (n_videos means posts). Thin by D-theme: most commented posts remain unassigned."),
    ("ig_audience_rollup_validation_v2", "SOUND", "44 roll-up checks, all PASS (Instagram v2)."),
    ("xplat_post_audience_sentiment_v2", "PARTIAL", "YouTube + Instagram per-post rows v2 in one schema (platform, post_native_id, post_url; post_source, post_tone_source). Platforms differ in text type (YouTube scored on translations, Instagram on original text) and in comment collection coverage."),
    ("xplat_audience_rollup_v2", "PARTIAL", "Pooled roll-ups v2 by platform (youtube_video, instagram, all) x D-theme x month, same functions as the per-platform roll-ups (n_posts replaces n_videos)."),
    ("sentiment_summary", "PARTIAL", "Label shares and mean tone per layer, with cuts by text source and flags. Descriptive only."),
    ("coverage_report", "SOUND", "Production counts (one snapshot) against extracted and scored rows."),
    ("validation_checks", "SOUND", "Record-count, ID, duplicate, failed-prediction, probability and distribution checks with PASS/WARN/FAIL status."),
    ("run_meta", "SOUND", "Snapshot time, model, text rule and tone formula for this build."),
]

PG_TYPES = {"VARCHAR": "text", "BIGINT": "bigint", "INTEGER": "integer", "DOUBLE": "double precision", "FLOAT": "real",
            "BOOLEAN": "boolean", "TIMESTAMP WITH TIME ZONE": "timestamptz", "TIMESTAMP": "timestamp", "HUGEINT": "numeric"}


def psql(sql: str, *, check: bool = True) -> str:
    cmd = ["docker", "exec", "-i", CONTAINER, "sh", "-c",
           'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tA']
    r = subprocess.run(cmd, input=sql, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"psql failed: {r.stderr.strip()[:400]}")
    return r.stdout.strip()


def main() -> int:
    if not SRC.exists():
        print(f"missing {SRC}; run scripts/32..34 first")
        return 1
    con = duckdb.connect(str(SRC), read_only=True)
    psql(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE; CREATE SCHEMA {SCHEMA};")
    now = datetime.now(timezone.utc).isoformat()
    counts = {}
    with tempfile.TemporaryDirectory() as td:
        for table, _, _ in MANIFEST:
            cols = con.execute(f"DESCRIBE {table}").fetchall()
            ddl = ", ".join(f'"{c[0]}" {PG_TYPES.get(c[1].upper().split("(")[0], "text")}' for c in cols)
            psql(f'CREATE TABLE {SCHEMA}."{table}" ({ddl});')
            csv = Path(td) / f"{table}.csv"
            con.execute(f"COPY (SELECT * FROM {table}) TO '{csv}' (HEADER, DELIMITER ',')")
            subprocess.run(["docker", "cp", str(csv), f"{CONTAINER}:/tmp/{table}.csv"], check=True, capture_output=True)
            psql(f"COPY {SCHEMA}.\"{table}\" FROM '/tmp/{table}.csv' WITH (FORMAT csv, HEADER true);")
            subprocess.run(["docker", "exec", CONTAINER, "rm", "-f", f"/tmp/{table}.csv"], check=True, capture_output=True)
            n_pg = int(psql(f'SELECT COUNT(*) FROM {SCHEMA}."{table}";'))
            n_src = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            if n_pg != n_src:
                raise RuntimeError(f"{table}: {n_src} rows in DuckDB but {n_pg} in Postgres")
            counts[table] = n_pg
            print(f"  {SCHEMA}.{table}: {n_pg:,} rows", flush=True)
    # Manifest: replace only this schema's rows; other schemas' rows are untouched.
    psql(f"DELETE FROM public._delivery_manifest WHERE schema_name = '{SCHEMA}';")
    for table, verdict, caveat in MANIFEST:
        cav = caveat.replace("'", "''")
        psql(f"INSERT INTO public._delivery_manifest VALUES ('audience_sentiment.duckdb', '{SCHEMA}', '{table}', "
             f"'{verdict}', '{cav}', {counts[table]}, '{now}');")
    print(f"Done: {len(MANIFEST)} tables in schema {SCHEMA}, manifest updated (other schemas untouched).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
