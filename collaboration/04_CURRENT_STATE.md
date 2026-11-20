# Current state (as of 2026-07-30, later — see top sections for the newest work)

## 2026-10-29 — Domain classifier for NER routing: BGE-M3 vs Laya (DONE; verdict PARTIAL)

`Experiments/DomainCLSTraining/` (write-up: `reports/COMPARISON.md`).
- **What it is:** a trained router for D00/D01..D09 on `data/parquet/DomainBucketing/labels_v0.parquet`.
  - The data is news title + description, with Gemma-relabelled, Claude-checked silver labels.
  - Split by date: train 12,541 / val 3,348 / test 4,058.
- **Winner: `runs/bgem3_v5`** (BGE-M3 + LoRA + auxiliary topic head + temperature).
  - Test macro-F1 0.699 [0.673, 0.722] vs Laya 0.638. Paired CI of the difference +0.034..+0.088.
  - In-scope recall 0.915, top-2 hit 0.912, ECE 0.019.
  - About 870 posts/s on GPU and 30/s on CPU (bf16). The Gemma router does about 2.3/s.
- **Laya:** only better on in-scope precision (+2 pts) and D04. It costs 5× the compute.
- **Verdict:** SOUND on the news distribution. **PARTIAL for social posts.**
  - It agrees with Gemma's in/out decision on 83% of the 11,995 routed posts (κ 0.64), but on the domain for only 56% of posts both call in-scope.
  - On hand-judged disagreements, each router was right about equally often. Gemma over-routes off-topic posts with topical words.
  - The classifier misses short off-topic Reddit chatter (it routes it in-scope) and ransomware leak posts (it routes them `none`).
- **Root cause found: the two routers use different taxonomies.** Gemma's routing prompt (`entity_routing.py:46`) gives domain names only. So wars go to D05 and riots or attacks to D09 under Gemma, while the labelled taxonomy puts both in D01 (about 480 posts).
- **Ready to use:** `predict.py` `load_router(run).route(texts, none_threshold=0.8)` returns `post_route_v1`-shaped columns. On val, the threshold of 0.8 gives 0.977 in-scope recall and the right L2 in top-2 0.932.
- `17`/`18` are untouched.
- **Open:**
  1. Hand-judge about 150 social posts as gold (over-sample Telegram, bn, ru).
  2. Add social negatives and ransomware positives, then retrain.
  3. Give Gemma's routing prompt the per-domain topic list.
  4. Decide on a `17b` / `post_route_v2` switch.
- **NER loop:** `run_gemma_ner.sh` was **paused** for this run (`logs/gemma_ner.stop`); it is not resumed.
- **Bug found (not fixed):** `entity_routing.py:108` renders types as `TYPE [L1]:`. Gemma copies the suffix into the type, so about 64% of `18` extractions fail validation (`invalid:type='LOC_COUNTRY [L1]'`). Failed posts are not marked done, so they will be retried after a fix.

## 2026-07-30, latest — Fixed-k=33 GPU (cuML) re-clustering + delivery DB refresh (DONE)

Risk/theme remediation below completed (script ran clean overnight, no crash this time).
Two more things landed after that:

1. **Topic clustering v3.** Tried GPU-accelerating the v2 clustering fix via cuML
   HDBSCAN first — a full parameter sweep found no viable region (only severe
   fragmentation or single-mega-cluster collapse; cuML's UMAP embedding geometry differs
   from `umap-learn`'s, so CPU-tuned parameters don't transfer). Pivoted to fixed-k
   clustering instead: `cuml.cluster.KMeans(n_clusters=33)` on the same 21,228 bge-m3
   embeddings, GPU memory capped at 3.5GB via RMM pool. Result is a real improvement:
   **largest cluster only 5.66% of corpus** (vs v2's 23-29%, vs the original bug's
   85-94%). 10 real posts per cluster were sampled and read (not just top-terms) to name
   and describe all 33 clusters; 27/33 are genuinely coherent single topics, 6 are marked
   `coherent=false` honestly (2 are data artifacts — dead LinkedIn pages, mistagged
   LinkedIn career content — the rest are loose miscellany). K=33 also resolved real
   structure v2's coarser 11 clusters hid: the old "Middle-East" mega-topic split into 6
   genuine sub-narratives, Russia-Ukraine into 4. Written to `topic_dim_v3`/
   `post_topic_v3` in `data/geo_ontology.duckdb` (v2 tables kept for comparison, not
   deleted). Full table and methodology: `docs/GEOPOLITICAL_ONTOLOGY_SPEC.md` §7.
2. **Delivery Postgres refreshed.** `delivery/ingest.py` re-run against the fixed
   risk/theme labels and the new v3 clustering — 27 tables across 2 schemas +
   `_delivery_manifest` now live in the `delivery/docker-compose.yml` Postgres container.
   Manifest verdicts updated: risk/theme labels now "PARTIAL (fix applied, not yet
   re-verified)" instead of UNSOUND; topic_dim_v3/post_topic_v3 added as "PARTIAL
   (substantial improvement)".

**Still open**: an independent, fresh hand-judged precision check on the new
risk_labels/theme_labels (the fix's own calibration sample scored 72%/46%
precision/recall, but that's not the same as an outside check); entity-subtype coverage
fix (98.5% unresolved) was not reattempted this pass; dashboard UI does not yet surface
`topic_dim_v3` (it didn't surface v2 either — consistent, but worth closing if the
receiving team wants cluster names visible in the UI, not just in the delivery DB).

## 2026-07-30, later — Remediation pass on entities + risk/theme (DONE, see above)

*(Historical account of the remediation run below is kept as-is; it completed
successfully after the session-limit interruption it describes.)*

Two real problems from the build below (`entity subtype coverage`, `risk/theme UNSOUND`)
are being fixed. **First remediation attempt failed entirely** — all 5 stages hit the
session's usage limit (reset 10:20 UTC) before any completed; `agents_done=0/5`. **Nothing
was corrupted**: `data/geo_ontology.duckdb` is untouched (verified: same row counts/mtime
as the original build, no partial writes — DuckDB's transactional model meant the crash
left no half-written table). One useful artifact survived: `scripts/12_risk_theme_classification.py`
(22KB, appears complete) was written before the crash, and its own docstring documents
real calibration work already done — a fresh 105-post hand-judged sample found bi-encoder
cosine similarity alone tops out at ~48% precision even with per-category thresholds
(confirming thresholds alone can't fix this), so it pivots to a bi-encoder-recall-net +
mDeBERTa-v3 multilingual-NLI-final-decision ensemble, calibrated to NLI_THRESHOLD=0.22
(72%/46% lenient precision/recall, 61%/65% strict, on that sample) — **but this script has
not yet been run against the full corpus**, and `risk_labels`/`theme_labels` in
`data/geo_ontology.duckdb` are still the original UNSOUND flat-threshold values.
`scripts/13_entity_subtype_resolution.py` (the entity-subtype fix) was not started.
Resuming now that the session limit has reset. **Do not treat this section's "IN
PROGRESS" state as stale without checking `data/geo_ontology.duckdb` row counts /
`scripts/12_risk_theme_classification.py` mtime directly — this file may lag the live
state if a session ends mid-remediation again.**

Also landed this pass (independent, direct verification — not from an automated agent):
entity extraction's base NER (before subtyping) checks out well — two 25-post stratified
samples (gazetteer-resolved and unresolved) both hit **25/25 and 25/25** surface-form-
present-in-source-text, across English/Russian/Chinese/Bengali — closing the "precision
never measured" gap from the build below for the *base extraction* question. Two problems
found in the same pass, now targeted by the remediation above:
1. **Entity subtype coverage is real but tiny**: only **1,649/110,983 (1.5%)** of mentions
   have any subtype at all (98.5% `unresolved`) — the zero-shot subtyping fallback named in
   the taxonomy was designed but never implemented in the full run, only gazetteer matches
   on well-known names got typed.
2. **Entity-type/subtype consistency bug**: 42/1,649 (2.5%) gazetteer matches have an
   entity_type inconsistent with the assigned subtype — e.g. "Narendra Modi" correctly
   tagged `political_leader` but under `entity_type=ORG` or `LOCATION` instead of `PERSON`,
   because the upstream generic NER model inconsistently classifies famous names depending
   on sentence context. Concentrated almost entirely in `political_leader` (42/983, 4.3% of
   that subtype) and negligibly in `diplomat` (2/98); every other subtype (armed_group,
   border_town, business_org, economic_zone, international_org, military_leader, ministry,
   port, state_owned_enterprise) had **zero** mismatches.

## 2026-07-30 — Geo-ontology layer added (entity/risk/theme/timeline)

A second, additive analysis layer landed on top of everything below: topic
clustering v2, entity extraction (NER + gazetteer), risk/theme zero-shot
labels, and timeline metrics + alerts for all three new grains, in a new DB
(`data/geo_ontology.duckdb`, separate from `data/metrics.duckdb`) plus four
new dashboard sections (§16–19) and a reference notification poller. **Not
uniformly sound — full verdict-by-subsystem record, dashboard rendering
proof, and known gaps are in `docs/GEOPOLITICAL_ONTOLOGY_SPEC.md`; read it
before relying on any of `entity_dim`, `risk_labels`, or `theme_labels`.**
One-line summary: topic clustering PARTIAL (real fix, one residual
mega-cluster), entity extraction PARTIAL (precision never measured), risk/
theme labels **UNSOUND** (measured 29–45% precision, confirmed recall gaps —
do not treat as ground truth), timeline metrics for all three new grains
SOUND (46/46 self-checks, 0 mismatches on independent recomputation). This
file (04_CURRENT_STATE.md) is otherwise unchanged below — it still describes
the Metrics v2 / Layer-4 state as of 2026-07-29.

## 2026-07-29 refresh — all platforms now included, fresh extract

Two fixes landed this session, both now reflected below:

1. **Platform exclusion removed, not widened.** `lib.config.TS_EXCLUDE_PLATFORMS` is now
   an empty frozenset (deprecated alias); `KNOWN_LOW_COVERAGE_PLATFORMS` is a
   documentation-only flag, never used to filter. Audited `03_ontology.py`,
   `07_metrics_v2.py`, `08_metrics_db.py`, `UI/build_dashboard.py` — no other hardcoded
   platform lists found. But a second, non-obvious exclusion mechanism was found and
   fixed: `07_metrics_v2.py`'s `load_source()` dropped every row with a null
   `posted_at`, which silently zeroed out **100% of Pinterest** (0/78 rows have
   `posted_at` in production — a real, pre-existing data gap, not a bug in our code).
   Fixed by falling back to `scraped_at` for the day axis when `posted_at` is missing,
   flagged via `day_is_scrape_proxy` and surfaced in `platform_dim.notes` (never
   confused with a true publish date; support gates still apply). Same principle
   applied to `scripts/review/stat_review.py`, which had its own independent
   `TS_EXCLUDE = ("snapchat","pinterest","whatsapp")` tuple — removed.
2. **Fresh extract required two real bug fixes**, not just a re-run: (a)
   `01_extract_base.py`'s embedding fetch referenced a column (`embedding_model`) that
   production has since dropped from every platform table (schema drift) — the resulting
   error wasn't rolled back, poisoning the next platform's query and silently producing
   **zero rows** for instagram/pinterest/snapchat. Fixed by dropping the dead column
   reference and adding rollback-on-failure. (b) A single live row had
   `scrape_lag_days` fractionally negative (clock skew, ~19s) and tripped a hard
   `assert` in `07_metrics_v2.py` that crashed the whole run — softened to a WARN + null.

## Production source

| Field | Value |
|-------|-------|
| Host | `<PG_HOST>:13366` |
| DB | `shaktidb_social` |
| Credentials | `.env` at repo root → `lib.config.PG` (never hardcoded) |
| Character | Live-ingesting OSINT social corpus |
| Access mode | **READ-ONLY** |

## Corpus (Layer 0 extract) — `extract_id 20260729T134230Z_bd348e9f`

| Platform | post_facts (deduped) | Notes |
|----------|---------------------:|-------|
| telegram | 15,257 | Raw 23,987 (36.4% URL dups) |
| linkedin | 3,495 | Was 924 — real ~4x jump upstream |
| youtube_video | 2,344 | Embeddings **now complete** (100%, CPU bge-m3 backfill) |
| reddit | 1,518 | No engagement fields; raw==facts, 0% dedup this pull (was ~36%) |
| facebook | 238 | |
| instagram | 237 | |
| snapchat | 189 | **Included** (was excluded); 179/189 rows have neither `posted_at` nor `scraped_at` — dropped for lacking any timestamp at all, not by platform |
| pinterest | 78 | **Included** (was excluded); 0/78 have `posted_at` — day axis falls back to `scraped_at` (flagged, support-gated) |
| whatsapp | 0 | Genuinely 0 rows in production; pipeline confirmed not to crash on this |
| **Total posts** | **23,356** | (23,176 after the "no timestamp at all" drop, in `07_metrics_v2`'s TS scope) |
| youtube comments | 77,496 | `comment_facts` |
| embeddings | 21,228 (90.9%) | 18,884 pulled directly + 2,344 YouTube backfilled this run |
| snapshot extract_ids | **4** | Append path verified again (was ≥2) |

## Ontology (Layer 2)

UMAP → HDBSCAN, **3 clusters + `unassigned`** (2.4% noise on the embedded matrix; ~9.6%
of all posts unassigned including no-embedding rows). Down from 8 clusters / ~21% noise
in the prior build.

**This is a coarsening artifact, not a quality improvement.** `min_cluster_size =
max(25, len(posts_e)//80)` scales directly with corpus size; the corpus grew from
~16,600 to 21,228 embedded posts (LinkedIn alone nearly 4x'd), so HDBSCAN was forced
into fewer, larger clusters. One cluster (`t002`, "Middle-East") now absorbs **92%** of
all embedded posts — too coarse to serve topic-keyed alerting, which needs the
underlying narratives (Russia-Ukraine, Cyber, Civil/Politics/Disaster, etc.) separated,
not merged into one mega-topic. Flagging for a future session to decouple
`min_cluster_size` from raw corpus size (fixed cap, or a different scaling rule) —
not fixed here, since retuning + re-validating clustering is out of scope for this
platform-inclusion/refresh task. (A cuML/GPU-accelerated clustering path was added to
`03_ontology.py` after this session's run completed — purely additive, falls back to
the CPU path used here, does not change the numbers above.)

## NLP (Layer 3)

| Score | Coverage | Notes |
|-------|----------|-------|
| Tone | 89.4% (20,887/23,356) | Mean ≈ −0.23 |
| Hate / toxicity | 89.4% | **Uncalibrated** (`hate_calibrated=false`) |
| Readability | 58.4% | Length-gated; up from ~46% (corpus mix shifted toward longer-text platforms) |

Ran on **CPU** (GPU fully held by a vLLM process, 44/49 GB, throughout this session) via
`PYTHONPATH` pointing at the lab env's torch/transformers install (`.venv` deliberately
excludes torch — see `requirements.txt` comment).

## Metrics v2 (authoritative)

| Item | Value |
|------|------:|
| DB | `data/metrics.duckdb` |
| Daily cells | 941 (486 in-window, 211 support_ok) |
| Tag cells | 26,257 (`tag_metrics_daily`) |
| Alerts | 28 (12 high / 16 medium) |
| max surprise | 3.01 |
| Non-null surprise on V≤2 | **0** |
| Hashtag coverage | 25.5% of posts (up from ~18%) |
| platform_dim | **8 rows — every platform in `post_facts`, including snapchat & pinterest** |

**v1 `analytics.duckdb.metrics_daily` is superseded** for decisions (keep for forensics).

## Dashboard

| Item | Value |
|------|--------|
| Shell | `UI/index.html` (~41 KB) |
| Snapshot | `UI/data/metrics.json` (~10.9 MB, up from ~4 MB — larger tag panel) |
| Engine | `UI/assets/dash.js` (filters → re-render; `PLATFORMS` derived from `platform_dim`, never hardcoded) |
| Model | **Dynamic UI, snapshot data** (not live DB) |
| Serve | `cd UI && python3 -m http.server 8877` |
| Snapchat/Pinterest | Render as their own small-multiple panels (sparse: snapchat has 1 in-window row, pinterest 21 — both non-zero, so both appear) |

## Alerting

Primary: **topic_id × platform × day**  
Secondary: hashtag / keyword  
Hate: off until calibration  
See `docs/ALERTING_KEYING.md`.

## Known footguns

1. Do not use v1 robust-z leaderboards.  
2. Prefer `V_rate` / effort-normalised growth over raw V.  
3. SavGol window changes `accel_smooth` rankings (parameter-sensitive).  
4. Cluster labels need human review for briefings — **more urgent now**: 3 clusters is
   likely too coarse (see Ontology note above), revisit `min_cluster_size` before using
   these labels in a briefing.  
5. Pinterest's day axis is a `scraped_at` proxy, not true `posted_at` — trend metrics
   for pinterest are observation-day, not publish-day, series.  
6. UI numbers stale until `build_dashboard.py` re-runs.  
7. DuckDB single-writer; prod stays read-only.  
8. `.venv` has no torch/transformers by design — GPU-touching stages (`03_ontology.py`,
   `04_nlp_scores.py`) need `PYTHONPATH` pointed at a torch-capable env (see
   `05_HOW_TO_RUN.md` / `requirements.txt` comment) and fall back to CPU cleanly when
   the GPU is contended.

## 2026-10-27 — topic keying experiments (see docs/TOPIC_KEYING_EVALUATION.md)
Soft top-2 counts, HMM bursts and event chains are wired into `07` as opt-in switches
(default = legacy, byte-identical). None passed adoption gates: v4_ext fallback precision
~39% (floor 50%), cluster stability ARI <= 0.26, soft-membership alert churn unexplained,
HMM unproven on precision. `07` now orders its source reads (previously nondeterministic).
Verdict for the topic-keyed layer: **PARTIAL/UNSOUND as keys** -- read topic metrics as provisional.

## 2026-11-14 — reach, tone and core-signal finalisation (data to 2026-10-26)
Verdicts and what changed (full table: Metrics_Finalisation_Two_Day_Action_Plan.docx; numbers: `reports/CORE_SIGNAL_CHECKS.md`, `reports/TONE_HATE_VALIDATION.md`, both generated, not committed):
- **Cross-platform reach — SOUND after removal.** Imputed views (clamp 5-80) were 42% of reported reach and 36 of 76 stories sat at the clamp; the breadth multiplier was a constant 1.5 (every story has 2 platforms). Both removed from `19`; `cross_platform_stories` now carries `observed_views` and `viewless_engagement` only.
- **Author-normalised reach — PARTIAL.** Authors with 1-4 other posts get an empirical-Bayes baseline (`stats_v2.thin_author_baseline_log`); held-out check beat the raw mean on Snapchat, TikTok, YouTube. Still a proxy.
- **Surprise / alerts / burst / momentum — checked, no formula change.** Weekday pattern is mostly absorbed by the effort offset (weekend/weekday median 0.73 -> 0.88); under Benjamini-Yekutieli 49 of 85 alerts survive (alerts are leads); burst days depend on `s` (overlap 0.70-0.77 at s=3 / 1.5); momentum describes direction and is negatively related to the next 7 days (-0.35), so it is not a forecast.
- **Tone — PARTIAL (provisional).** Reads wording, not attitude: factual reports of bad events score negative. Weighted tone ~ unweighted (Spearman 0.989). Single-labeller validation, not human-confirmed. Hate stays uncalibrated and hidden.
- **Readability — SOUND.** One LIX scale for every language; the language label 'nan' bug fixed (`04b_readability_only.py`).
- **Geo panels** now use `posted_at`, falling back to `scraped_at` like the topic panels (`11`).
- **Risk / theme labels — PARTIAL.** Hand check: ~40% / 31% clearly right (70% / 55% loose), below the script's 72% claim. CAMEO event codes still unpopulated.
- Known open: `check_taxonomy` 7/9 (theme/subtype cards not found in the rendered page); same-topic chain linking needs a decision (`test_chains_build_and_types` is xfail); `test_id_column_types.py` needs a DB with `topic_code_crosswalk`; fresh prod extract + GPU stages + rebuild pending.

## 2026-11-18 — production entities become the entity layer (data to 2026-10-26)
- Stage 21 reads the production `entities` jsonb (read-only snapshot) into `data/prod_entities.duckdb` (958,536 mentions, 153,116 of 497,530 posts = 30.8%; Instagram 4.5%, Reddit 2.8%, Telegram 16.9%, Twitter 26.3%; ~63K production rows not in our extract are not loaded). Stage 22 merges spelling variants (normalise / gazetteer / surname / acronym; titles stripped for PERSON only; audit table `entity_alias`). Stage 23 loads the result into `geo_ontology.duckdb` `entity_dim` / `entity_mentions`; the Babelscape tables are kept as `*_babelscape` (plus file backup `data/geo_ontology.backup_before_prod_entities_20261118.duckdb`). Script 11 re-run: entity alerts 2,467 -> 2,546 (3,525 entities with >= 20 in-window posts, was 5,397).
- Script 10 (Babelscape NER) is switched off in the working tree; production entities are the source.
- **Verdict: PARTIAL.** Alias merge precision not yet judged (sheet `reports/Entity_Alias_Review.xlsx`, generated, not committed). Generic words typed as PERSON ("President", "soldiers") are not filtered.

### Pending (agreed to do later)
1. **Fix `compute_related` memory failure (`UI/api_server.py`).** For large entities (Putin, NATO) the "appears with / similar posts" block returns empty: the similar-posts query scans all 497K embeddings and hashes their text inside the container's 4.7 GiB DuckDB limit ("Out of Memory Error"). Plan: return the entity chips even if similar posts fails; take the nearest ~200 posts first, then fetch text and dedupe; add a test on a large entity. Blocks the Explore page and entity use-case step 3.
2. Judge the alias review sheet; fix rules behind any N rows.
3. `UI/tests/check_metrics_panel.mjs` hard-codes 44 alerts for 1 week; now 25 after the entity swap — compute the expected value from the data.
4. Filter generic PERSON entities ("President", "soldiers", "police") from lists shown to customers.
5. Explore page (user-driven search over our entity list + keyword) — test version; per `Trending_Topics_and_Viral_Posts_Customer_Dashboard_Review.docx` section 9.
6. Fresh production extract (01, 21, 22, 23, 11) when ready; script 01 not run (hard-coded output paths overwrite `analytics.duckdb`; estimated 20-25 GB peak, unmeasured).
7. Entity use-case steps 2-3 (top posts by views, grouped co-mentions) and later step 10 ("about" filter); see `Final-docs/Entity_Led_Use_Cases_and_Implementation_Plan.docx`.

### Added 2026-11-18 (later): viral posts ranking
- Done: Explore drill-down ranks a group's posts by views / engagement / shares / likes within the selected dates (`/api/drill` `sort`, `from`/`to`, `platforms`); counts now match the card; NaN sent as null; ranked drills skip the slow related scan (commit 60525dc).
- Later (not started): save the per-post virality percentile from script 07 (`virality_pct_post`, `virality_ref_n` are computed per post and discarded; add a `post_virality` table), re-run 07 and 08 (~45-60 min, 6-8 GB), then rank viral posts across platforms by it. Validate with a hand-judged sample before any "Breakout" label.
- Pending: default drill-down (no sort) took ~34 s because `compute_related` scans all embeddings; cap it (see item 1 of the 2026-11-18 pending list).

### Added 2026-11-18 (evening): Explore page features taken from the news-trending UI (branch new-push)
- Keyword search with a small query language (words, "phrase", OR, -exclude, word forms + short related list) on post text: `UI/keyword_query.py`, `/api/explore/viral?q=`; snippet cut around the first match; matches highlighted. Searches take ~3-7 s over ~500K posts.
- "Show more" paging in the drill-down (`offset`), custom date boxes, entity cascade (type -> subtype -> entity, incl. countries = posts naming the country), "why it is trending" cards + "How this ranking is decided" panel (thresholds in one `EX_RULES` object), cross-platform story cards with Cards/Table switch.
- Reader feedback: thumbs up/down with reasons on alerts, topic "why" cards and story cards. Own SQLite file `data/feedback.sqlite` (git-ignored; never production, never the metrics DB). Read results: `GET /api/feedback/summary`. Votes are opinions from whoever clicks; collect from several readers before reading a rate.
- Tests: `UI/tests/check_explore_features.mjs` (32), `check_explore.mjs` (39), `scripts/tests/test_keyword_query.py`, `test_feedback_store.py`; live API smoke 14 checks.
- Not done (excluded on purpose): data-freshness panel (needs the production connection for "last ingest / lag").

## 2026-11-19 — per-post virality, entity filter on Trending Topics, platform filter (branch new-push)
- **Stage 24 `scripts/24_post_virality.py`** (new, read-only, ~10 min, container can stay up): runs script 07's own `load_source` + `enrich_posts` and keeps `virality_pct_post` / `virality_ref_n` per post in `data/post_virality.parquet` (769,778 rows incl. YouTube comments; git-ignored). It replaces the planned 45-60 min rerun of 07/08. Check: rated posts per platform equal `metrics_daily.virality_n` on all 17 platforms. Reddit, WhatsApp, Pinterest, Baidu Video have no percentile (no usable ratio). 205 posts of `post_facts` are outside 07's scope.
- **Viral Posts > Top posts**: "Rank by Views | Virality percentile", and "Overall | Top 3 per platform" (`/api/explore/viral?rank=virality&per_platform=N`). A percentile is shown only with >= 20 similar posts. Views are not comparable across platforms: in the 2026-10-20..26 sample the most-viewed post (YouTube, 2.08M views) is at percentile 0.92 and a 1.58M-view tweet at 0.47. **Retweets:** retweets (`RT @...`) carry the original's share count but almost no views, so they can reach percentile 1.00 (2 views, 1,060 shares); ~31% of the top 300 by percentile (2026-06-14..10-26) were retweets. They are kept (they show spread), tagged "Retweet", and a RETWEETS switch (Include | Original posts only, `retweets=hide`) hides them. Only Twitter (4,219) and Truth Social (10) mark reposts with `RT @`; Mastodon boosts / Telegram forwards are not marked in the data.
- **Trending Topics > entity filter**: same picker as Viral Posts (type -> subtype -> name). New `/api/explore/entity_topics` counts, per topic, posts naming the entity in the selected days and in the same number of days before; growth shown with >= 5 earlier and >= 10 current posts. Emergence / alert days / stage stay the whole topic's and are labelled so. The topic pop-up accepts `entity_uid` (posts only; YouTube comments carry no entity mentions) and is opened ranked by engagement (fast). Card counts equal pop-up totals on 18 sampled entity x topic pairs.
- **Entity coverage gap (important):** the production entity extract is a snapshot, so some platforms' entities stop early: Twitter 2026-07-23, Telegram 2026-07-28, Instagram 2026-08-02, Reddit 2026-08-03, Pinterest 2026-09-03, WhatsApp 2026-10-01, TikTok 2026-10-14. For the latest weeks the entity views see mainly Snapchat, Mastodon, YouTube, Truth Social, LinkedIn, Facebook. The page lists the affected platforms in a warning. Fix = pending item 6 (fresh production extract: 01, 21, 22, 23, 11).
- **Trending Topics > "Seen on N+ platforms"** (cross-platform presence), Lifecycle column renamed "Burst stage" with a note that it uses a different rule from Trending's Stage.
- **Server**: static files and the metrics snapshot are revalidated every load (`Cache-Control: no-cache`, ETag/304); the old `max-age=3600` hid rebuilds.
- Explore: tables closed by default behind "+", explanations behind "click to see explanation"; Last 1d default (Explore only).
- Tests: `UI/tests/check_explore_api.py` (20, new), `check_explore.mjs` (61), `check_explore_features.mjs` (41), `check_api_dimensions.py` (9).
- Not changed on purpose: the emergence formula (cross-platform presence is only a filter), growth velocity (needs repeated counter snapshots; not confirmed to exist), author stance / comment tone for sentiment (not built).

### Added 2026-11-19 (later): minimum-activity floor on the virality ranking
- Problem (measured): the percentile ranks a RATIO, so tiny counts win. Among posts at percentile >= 0.99, 88% of Telegram, 89% of TikTok, 87% of YouTube and (on the first, mixed-basis cut) 98% of Snapchat posts were below their own platform's median activity; the top of the list showed posts with 4 views and 1 share.
- Fix (relative, no fixed number): stage 24 now also saves `activity` (views if > 0, else likes + comments + shares), `activity_basis`, `activity_floor` (median of rated posts of the same platform AND basis) and `activity_ok`. `/api/explore/viral?rank=virality` hides posts with `activity_ok = false` unless `floor=off`; the page has MIN ACTIVITY: "Platform median or more" (default) | Off, and states how many ranked posts it hides (2026-06-14..10-26: 118,305 kept of 237,783 rated).
- Effect at percentile >= 0.99 (share kept): Telegram 12%, TikTok 11%, Facebook 32%, Instagram 32%, LinkedIn 43%, Twitter 60%, Truth Social 60%; Snapchat, Mastodon, YouTube comments 100% (their top posts already have real activity within their own basis).
- Still true: retweets (tagged) keep high scores because their share counts are large; use the RETWEETS switch.
- Tests: `UI/tests/check_explore_api.py` 31/31, `check_explore.mjs` 69/69.

### Added 2026-11-19 (later still): default floor raised to the top 10% of each platform
- Judged sample #1 (40 posts, median floor, my own reading): clearly viral (>= platform p90) 8 (20%), popular only 29 (73%), not viral 3 (8%). Retweets were 41% of the top 300.
- Change: stage 24 also saves `activity_p90` and `activity_top10`; `/api/explore/viral?rank=virality&floor=top10|median|off` (default `top10`; old `floor=on` = median). Page: MIN ACTIVITY = Top 10% of platform (default) | Platform median or more | Off. Ranked posts 2026-06-14..10-26: 22,800 (top 10%) <= 118,305 (median) <= 237,783 (off). Last 30 days: 5,229 ranked of 54,346 rated.
- Judged sample #2 (fresh 40 posts from the new default, stricter bar: >= 1.5x the platform p90 or >= 50K views / >= 1,000 engagement): clearly viral 26 (65%), borderline 13 (32%, small-platform posts just over the line), not viral 1. The same stricter bar on sample #1 gives 7 of 40 (18%). Retweets 2% of the top 300 (was 41%).
- Caveats: judged by me, not a human; Twitter now has few posts in the top 300 (15; its views p90 is 56K and retweets, which have no views, sit on the engagement basis), TikTok and Snapchat dominate (104 and 84), partly because the list is ordered by percentile first; Snapchat counters come from parsed captions. Option not built: order by percentile x activity rank.
- Tests: `check_explore_api.py` 34/34, `check_explore.mjs` 70/70.

### Added 2026-11-19 (evening): partial last day, data-freshness label, entity aliases (review against `plans/viral posts and trending .md`)
- **Partial last day.** 2026-10-26 has 1,587 posts (2,704 posts + comments in `metrics_daily`) against ~5,600 / ~11,700 on a normal day, so every topic showed a collapse on a 1-day view (-50% .. -90%). `dqInfo()` in `dash.js` calls a day PARTIAL when its total is under half the median of the 7 days before. Explore now opens on, and its presets (Last 1d / 7d / 14d / 30d / Full window) end at, the last COMPLETE day (2026-10-25); the date boxes can still include the partial day. 1-day growth on 2026-10-25: Civil/Politics +139%, Civil/Disaster +163%, China +42%, Russia-Ukraine -45% (mixed, no longer all negative).
- **Freshness label.** Header: "newest data 2026-10-26 (24 days ago), 2026-10-26 is a partial day" (also on the Overview line); an Explore notice repeats it and lists platforms whose data ends 2+ days earlier (tiktok 10-14, instagram 10-23, facebook_profile 10-22, baidu 10-24, whatsapp 10-01, pinterest 09-30). Computed from `metrics_daily` in the snapshot, so it works without the API. The freshness panel that needed the production connection ("last ingest / lag") is still not built.
- **Entity aliases (stage 22 manual rules, R0).** `data/entity_aliases_manual.json` is now tracked (`.gitignore` exception; entity ids only). Added: Chinese People's Liberation Army, Chinese Liberation Army, "People's Liberation Army (PLA" -> `org:pla`; Chinese PLA Navy (two spellings) -> `org:pla_navy`; PLA Air Force -> `org:plaaf`; Communist Party of China -> `org:ccp`; Donaldtrump, Donald J. Trump, Realdonaldtrump -> `person:trump`. Result: PLA 878 posts (was 837 + 68 + 18 split), CCP 1,328, Trump variants merged; entities 198,781 (was 198,791). Reran 22 -> 23 -> 11 -> dashboard build (container stopped for 23/11, back up); entity alerts 2,657 (was 2,546). Backup of geo_ontology.duckdb / prod_entities.duckdb taken before the rewrite (scratchpad, not in the repo). The pre-existing pair `person:trump -> person:donald_trump` is a no-op (Trump is canonical already).
- Not merged on purpose: "Chinese Military" / "Chinese Army" (generic), PLA Naval Air Force, "Chinese Communist Party-linked" (adjective phrase).
- Still open from the review: counter history for post growth velocity (one snapshot per post in the DB), the stale entity extract (pending fresh production extract, deliberately not done), generic PERSON entities, duplicate posts in the top lists, fixed set of 8 topics (no discovery of new topics).
- Tests: `check_explore.mjs` 77/77, `check_explore_features.mjs` 41/41, `check_explore_api.py` 34/34, entity pytest 13/13.

### Added 2026-11-19 (night): viral-posts validation and the two Critical fixes (`Final-docs/Viral_Posts_Validation_Report.docx`)
- **Audit (read-only, 60 posts: 20 viral / 20 high-attention not viral / 20 ordinary; verdict PARTIALLY CORRECT):** the ratio percentile is technically right but ranks efficiency, not size. Of 302 posts in the top 0.5% of their platform by raw attention only 60 were in the top 300; the 15 most-viewed posts ranked 52-4,758 of 5,229 (an 8.0M-view tweet, `cef9f32bfb`, 2,426th); Spearman(ratio, size) Twitter 0.15, YouTube -0.19. 0 of 20 ordinary posts were listed. YouTube `views = 0` (counter unavailable) became ratio = likes (41 of the 107 posts at >= 0.99). Snapchat has no scrape age (scraped_at is 2026-07-06..08-11 for posts up to 10-26), YouTube 99% missing. One counter snapshot per post: growth speed cannot be validated.
- **Fix 1 (stage 24 + API + page):** `rank=virality` has `order=biggest` (default) | `order=amplified`. Biggest sorts by `activity_pct` (raw attention among posts of the same platform, basis and age band; unknown-age posts vs the whole platform), then by the multiple of the platform's top-10% line; posts with no ratio can appear. Both numbers are shown on every post. Tried and rejected: sort by multiple only (Truth Social took 153 of 300), geometric mean of the two percentiles (most-viewed posts at ranks 26-30,003).
- **Fix 2:** YouTube `views = 0` -> no percentile, no activity (127 videos all-time). Done in stage 24 only; `07_metrics_v2.py` `virality_ratio` is unchanged (it still feeds the group-level breakout table), so rated counts equal `metrics_daily.virality_n` on every platform except YouTube (difference = exactly those videos). Change `virality_ratio` in 07 at the next full metrics run.
- **Re-validation (last 30 days):** very-high-attention posts in the top 300: 60 -> 280 of 281; top 300 at >= 99th percentile of attention: 33% -> 100%; the 15 most-viewed posts: all inside the top 300; fresh random 20: all >= 99.5th percentile.
- Stage 24 also saves `activity_pct`, `activity_ref_n`, `activity_mult`. Tests: `check_explore_api.py` 44/44, `check_explore.mjs` 80/80.

## 2026-11-19 (late) — clustering plan steps 0 and 1 (`Final-docs/Topic_and_Event_Clustering_Review_and_Plan.docx`)
- **Review that triggered it (my reading of 100 posts, not a human review):** the 8 topics fit about 67% for the six news topics, but "Geopolitics / China" (44% of all posts) fit 1 of 10; of 30 sampled v4 sub-topics only 6 were real multi-source news clusters, 14 were one author's copies. Diagnosis: copies dominate, no time dimension, one catch-all topic.
- **Step 0 (dashboard):** the merged t004 + t005 topic now carries both subjects in its label ("Geopolitics / Neighbourhood (pakistan + bangladesh)"; the dashboard merges the two everywhere, so the duplicate label was never visible, but the label hid that they cover different places). Trending Topics shows a note when one topic holds 30%+ of all collected posts (Geopolitics / China, 44%): its numbers describe a mixture.
- **Step 1 (`scripts/25_post_dedup.py`, `scripts/lib/text_dedup.py`):** exact-copy groups by author + normalised text (case, whitespace, URLs and Snapchat's generated counter caption removed; digits and words kept; texts under 20 characters or without an author are never grouped). Output `data/post_dedup.parquet` (git-ignored). **497,530 -> 391,162 (21.4% copies)**; Instagram 93,029 -> 1,087 (98.8%), Truth Social 13.2%, Reddit 8.2%, others under 4%. Largest groups: Instagram 312, Truth Social 231, Twitter 67. Sampled groups checked by eye: genuine repeats. Raw tables untouched. 6 unit tests (`scripts/tests/test_text_dedup.py`).
- `15_topic_clustering_v4.py` gained `--exclude-copies` (reads `is_first` posts only). Stability sweep on 60,000 posts with and without copies run to test whether copies caused the earlier instability (cross-seed ARI 0.09-0.26); result recorded below once finished.
- **Result of the copy test (script 15 `--dry-run`, 60,000 random embedded posts, min_cluster_size 30, 3 seeds, CPU, nothing written; logs `logs/topic_dry_*.log`):**

  | | with copies | copies excluded |
  |---|---|---|
  | clusters | 342 | 286 |
  | cross-seed ARI (mean / min) | 0.456 / 0.423 | **0.553 / 0.520** |
  | clusters passing the script's coherence flags | 204 of 342 (60%) | **260 of 286 (91%)** |
  | noise | 39.5% | 40.5% |
  | silhouette in the real 1024-d space | 0.033 | 0.014 |

  Removing exact copies raises stability (+0.10 ARI) and the share of coherent clusters (60% -> 91%, partly because the single-author and duplicate-text flags no longer fire). **Verdict: PARTIAL.** Stability is moderate (about 0.55), silhouette is near zero in both cases (clusters are soft, overlapping regions, not separated groups), and the 91% is the script's own flag, which passed 3 of 11 bad clusters in my earlier hand review. Not adopted; no v5 tables written. Needs a hand-judged sample before any use. The full `--sweep` (6 cluster sizes x 5 seeds, UMAP refit each time) takes over 90 min on CPU at 60K posts with the machine busy, so it was not run.

### Added 2026-11-19 (night): Viral Posts search box takes a keyword or a topic
- The Viral Posts search box ("Search a keyword, or pick a topic...") suggests the 7 topics (t004 + t005 shown as one). A topic name picked or typed in full filters to the posts in that topic (`/api/explore/viral?topic_id=`; applies to both the Viral ranking and the Views lists and to the breakout-groups table); anything else is a keyword search as before. One entry is either a topic or a keyword in the UI; the API accepts both together. Reset clears it. Last 30 days, topic Civil / Disaster: 7,482 of 97,673 posts; keyword flood: 1,105.
- Note: the top posts of "Civil / Disaster" include crime stories (a road-rage sentence, a jailing), which shows the topic is fuzzy, not that the filter is wrong. Sub-topics would sharpen this.
- Tests: `check_explore.mjs` 90/90, `check_explore_api.py` 51/51.

### Added 2026-11-19 (night): Gemma domain routing reviewed and re-tested (`Final-docs/Gemma_Routing_Posts_For_Review.xlsx`, `Gemma_Routing_Prompt_v2_Retest_150.xlsx`)
- **Pilot review (160 of the 500 routed posts, my reading, not a human review):** strict 70% / lenient 85%; wrong security domain only 4 of 160; the main error was putting a security domain on non-security posts (19 of 160: local crime, consumer tech, markets, weather, obituaries). Weakest: D08 (33% strict), D09 (50%), D03 (61%); `none` answers 19 of 19 correct. Confidence >= 0.95: 85% strict vs 56% below.
- **Prompt v2** (`ROUTING_RULES_V2` in `scripts/lib/entity_routing.py`, `17_gemma_route.py --prompt v2`, writes `post_route_v2`; v1 stays the default and the 500-row `post_route_v1` is untouched): explicit "none" rule and tighter D03 / D07 / D08 / D09 rules.
- **Fresh 150-post retest (same model, same posts, v1 vs v2):** v1 66% strict / 83% lenient (24 security domains on non-security posts); **v2 90% strict / 99% lenient** (1 such post; 0 security posts called none; 0 wrong security domains); v2 at confidence >= 0.95: 94% strict, 100% lenient (n=72). Choices: none 52 -> 84, D08 11 -> 1, D03 12 -> 5. **Caveat:** the gold labels are mine, set after seeing both outputs, and the v2 rules came from my own earlier review, so the test flatters v2; a blind human review is still needed. Only 56 of 148 posts are security related. Domain level only (no T-level themes yet).
- Cost note: v2 uses about 900 tokens per post; the earlier estimate for a full run was about 20 h on the shared server. Not run on the corpus.
- Tests: `scripts/tests/test_entity_routing.py` 9/9.

### Added 2026-11-19 (night): zero-shot domains with the pretrained bge-m3 (stages 26a / 26; no training)
- **Idea tested:** describe D01..D09 and a "not security" group in a few sentences (`data/domain_prototypes_v1.json`, 47 sentences, tracked), embed them with the same bge-m3 that produced the stored post embeddings (checked: cosine 1.000), and give each post the closest description. No model is trained; two thresholds (TAU 0.44, DELTA -0.04) and NONE_GAP 0.02 were tuned on 273 hand-reviewed posts.
- **Measured (273 reviewed posts, domain level):** theme names alone 46-61% strict; one-sentence descriptions 71% strict (optimistic, tuned on the same posts; 48-77% when tuned on one set and tested on the other). Gemma prompt v2 on the fresh 150: 90% strict. Confidence is a poor uncertainty signal: the share of posts sent to an LLM would have to be 60-75% to reach ~85-90% hybrid accuracy.
- **What it IS good for:** spotting posts that are clearly NOT security. Where a "not security" sentence beats every domain by >= 0.02: 22% of the reviewed posts, 97% truly none, 2 of 149 security posts dropped. **What it is NOT good for:** choosing between D01..D09 (about 60% right even among confident security posts).
- **Full pass (`26_domain_zeroshot.py`, 17 seconds, read-only, 487,713 embedded posts):** `confident_none` = 24.0% of all posts, 27.1% of the 381,350 distinct messages, so the Gemma queue shrinks from 381,350 to 278,105 distinct messages (-103,245). Per platform: Snapchat 81%, Baidu Tieba 71%, Reddit 46%, WhatsApp 41%, TikTok 31%, Twitter 19%, Telegram/Instagram/Truth Social/Mastodon about 11-12%. Provisional domain counts: none 265,142, D01 57,052, D05 41,676, D02 37,621, D09 24,120, D04 23,079, D03 18,974, D07 10,663, D06 6,381, D08 3,005 (do not use as labels).
- **Check on the skipped posts (random 100 of the confident-none, my reading):** 97 correct, 3 security posts wrongly dropped (a Ukrainian air-raid drone alert, a Chinese fighter-aircraft image post, a launch/orbit tweet): a blind spot for short alert-style posts. Extrapolated, about 2-3K security messages would be dropped. A guard (do not skip short Telegram alert posts) is advisable before use.
- **Plan:** Gemma prompt v2 on the 278,105 remaining distinct messages (about 12-18 hours at the pilot's pace on the shared server, not run), copies inherit the label, then a blind 150-post check of the combined result.
- Tests: `scripts/tests/test_domain_zeroshot.py` 5/5. Local scratch only: the experiments' labelled-post arrays.

### Added 2026-11-19 (night, later): step 1 of the labelling plan, the security-cue guard and the Gemma queue
- **Unlabelled counts (distinct messages after exact copies):** 497,530 posts; 106,368 are repeats; 391,162 distinct messages; 28,300 are under 40 characters (the LLM routing stage's own minimum); only the 500-post pilot (prompt v1) is labelled.
- **Guard (`lib/domain_zeroshot.security_cue`, used by `26_domain_zeroshot.py`):** a post with a clear security term (drone, missile, army, navy, J-20-style designators, orbit, border tension, warfare, Ukrainian "1х" alert pattern, plus Russian/Ukrainian, Chinese, Hindi/Urdu/Arabic terms) is never skipped, and short posts that carry one are sent to the LLM even under 40 characters. Not cues: murder, killed, arrested, flood, rain, fire, accident, crash. Not covered: Burmese, Bengali, Tamil, Thai and other scripts (stated limit).
- **Effect (full pass, 17 s):** `skip_llm` (clearly not security, no cue) = **99,795** distinct messages; 3,450 confident-none posts moved back to the queue because of a cue; 2,006 short posts rescued by a cue; 7,384 short posts without a cue are not routed. **`needs_llm` = 274,171 distinct messages, plus 711 that have no embedding** (about 274,900; they stand for roughly 365,000 posts including repeats).
- **Hand checks of the skipped posts (random 100 each time, my reading):** before the guard 3 of 100 security posts wrongly skipped (air-raid alert, J-XDS aircraft, orbit launch); after the first guard 2 clear + 2 borderline (border-tension clickbait, "environmental warfare" series); after widening the lexicon 2 (an alert whose emoji touched the digit: regex fixed; a post about an internet shutdown during a crackdown on a political party). So about 2-3% of skipped posts are security related (roughly 2-3K messages), mostly short, clickbait or uncovered-language items. Accepted for now, recorded as a limit.
- Tests: `scripts/tests/test_domain_zeroshot.py` 6/6.


## 2026-11-19 (night) — bge-m3 zero-shot domain label as a theme: precision check (verdict UNSOUND as a standalone label)

- **Sample:** 216 English distinct posts (40+ characters) labelled D01..D09 by `26_domain_zeroshot.py` (not `none`), 24 per domain, 8 per score tercile. Read by hand (non-blind, lenient on D07/D08 where the taxonomy is broad). Judged sheet kept in the scratchpad only (post text, not committed).
- **Result:** exactly right **42%** (91/216); security but another domain 18% (39); not security at all **40%** (86). So only 60% of posts labelled with a domain are security posts.
- **By predicted domain (right / n=24):** D07 16, D05 15, D04 14, D08 11, D01 9, D02 8, D03 8, D06 5, D09 5. D06 (borders) and D09 (terror) are the worst: most are domestic politics, local news or other domains.
- **Score cut-offs help only a little:** best-domain score >= 0.50 keeps 50% of posts, 56% right; >= 0.52 keeps 26%, 65% right; >= 0.54 keeps 13%, 71% right. The margin over the second domain does not help (46-50%).
- **Verdict:** UNSOUND as a theme label for the dashboard; stays a provisional hint. The earlier finding stands: the "clearly not security" filter is the only reliable part. Caveat: one non-blind reviewer, 24 posts per cell (Wilson interval about +-17 points).
- **Options not yet done (need user's choice):** (a) hybrid keyword/entity cue per domain plus the bge-m3 score; (b) use the bge-m3 label only for D04/D05/D07; (c) a light classifier head on the stored bge-m3 embeddings using reviewed posts (this is training, user said not now); (d) Gemma on the remaining queue (user said not now).

## 2026-11-19 (night, later) — hybrid theme (stage 27): fresh-sample precision (verdict PARTIAL; high tier usable)

- **What it is:** `scripts/27_post_theme.py` + `scripts/lib/domain_cues.py` -> `data/post_theme_v1.parquet` (local, not committed). A post gets theme D01..D09 only when the pretrained bge-m3 best domain (stage 26) has score >= 0.48, the post is not a confident none, AND an English keyword cue of that same theme is in the text. Tier **high** = >= 2 distinct cue terms, **medium** = 1; else `unassigned`. No training, no Gemma. Tests: `scripts/tests/test_domain_cues.py` 6/6.
- **Coverage (381,350 distinct messages):** assigned 14.1% (53,749): high 22,769, medium 30,980; the rest unassigned (86%). Cues are English only, so untranslated Burmese/Bengali/Tamil/Thai posts mostly stay unassigned (stated limit).
- **Cues were tuned on the earlier 216-post set** (same-set precision rose 42% -> 61% at one cue, 78% at two cues + score >= 0.48). **Fresh test:** 180 posts never used for tuning, 10 per theme x tier, read by hand (non-blind): **exactly right 67% (120/180)**, security but another theme 13%, not security 21%. **High tier 74% (95% CI 65-82)**, **medium 59% (49-68)**. Prevalence-weighted overall about 65%.
- **By theme (n=20 each, right):** D02 18, D04 17, D03 15, D07 15, D01 14, D08 11, D09 11, D05 10, D06 9. D05/D06/D08/D09 are the weak themes (generic words: army, border, relief, explosion; domestic posts, recruitment ads, games).
- **Verdict: PARTIAL.** Fit for a filter marked "automatic, provisional" if the default is the high tier (74%); medium tier only behind an explicit switch. Not a finding source: theme prevalence is not reported. Caveats: one non-blind reviewer, lenient on D07 (cyber fraud/phishing counted as D07), +-8-10 points per tier.
- **Next:** Theme filter on Trending Topics / Viral Posts (high tier default), after the user's go.

## 2026-11-19 (night, late) — Theme filter and search trend panel in Explore

- **Theme dropdown (D01-D09)** on Trending Topics and Viral Posts. Names come from `data/entity_schema.json` via a new taxonomy axis `security_domain` in `scripts/lib/taxonomy.py` (not alertable: provisional label; alerts stay on `topic_id`). Default = high tier only; "Include lower-confidence" adds the medium tier. A note on the page says the theme is automatic and provisional and that counts per theme are not the share of the conversation.
- **Search on Trending Topics now answers "what is trending about X?"** A keyword, entity or theme shows (`explore_search_trend`, `GET /api/explore/search_trend`, read-only): posts in the selected days against the previous period, distinct messages, posts per 1,000 collected posts (so a collection ramp-up is not read as interest), growth in posts and in share of all posts (shown only with >= 10 earlier and >= 20 current posts; under 20 matches: "too few posts to call a trend"), stage (same rule as the topics list, per collected post), a per-day bar chart, platforms, topic chips (t004 + t005 merged), theme chips and the top posts. Chips filter or open the topic in Viral Posts. Before this, the Trending Topics search matched topic names only (8 topics), so "ladakh" found nothing.
- **Day rule:** search trend counts by posted day, else scraped day (as the metrics do); Viral Posts keeps its posted-day rule (16 D04 posts have no posted day, 95 are dated before 2020).
- **Tests:** `UI/tests/check_explore_search.mjs` (25, new), `check_explore.mjs` 90, `check_explore_features.mjs` 41, `check_explore_api.py` 64/64 (13 new: theme filter equals the stage-27 file, tiers, bad themes refused, search-trend counts consistent with the Viral Posts total), `check_taxonomy.mjs` expects the 7th axis (7/9; the two failing checks, theme cards and entity-subtype cards, failed before this change).
- **Limits:** the keyword match is on the words in the post text (not what the post is about); theme precision as recorded above (high tier 74%); only most-viewed posts are listed under the trend (no "newest" list yet); the static snapshot has no post-level data, so the panel needs the API server.
- **'Show all' option (2026-11-19):** the Show 10 / 25 / 50 list also has Show all: every row of the table lists; post lists are cut at 300 per list (server limit), and the page says so. `check_explore_search.mjs` 29 checks.

## 2026-11-20 — stage 28: nearest-neighbour theme voting (no clustering, no Gemma, no training); review verdict PARTIAL

- **What:** `scripts/28_theme_knn.py` (+ `scripts/lib/knn_gpu.py`, torch top-k run through `/usr/bin/python3`; the CPU numpy search was far too slow, > 1 h, the GPU run takes minutes next to vLLM) -> `data/post_theme_knn_v1.parquet` (local). References: stage-27 high tier (22,769 distinct messages, theme labels) and clearly-not-security posts (99,795, label none). Candidates: 218,668 distinct messages with no stage-27 theme, not clearly none, >= 40 characters. Each takes its 15 nearest references (cosine on the stored bge-m3 vectors, similarity-weighted vote); a theme is given only if the winner is a theme (never none) and its vote share >= 0.9 (adopted default; raw `knn_vote`, `knn_share`, `knn_sim`, `knn_top1` are kept). Exact copies inherit the first post's result.
- **Review (147 fresh posts never used before, stratified by theme and vote-share band, read by hand, non-blind, all languages I can read):** at share >= 0.6: exactly right **64%** (94/147, 95% CI 56-71), security but another theme 10%, not security 27%. By vote share: 0.6-0.8 -> 54% (n=52), 0.8-<1 -> 63% (n=52), unanimous 1.0 -> 77% (n=43). **share >= 0.9: 75% right (n=65, wide interval, about 63-84)**; population-weighted precision at share >= 0.6 is about 61%. Neighbour similarity as a second gate adds little (sim >= 0.63 with share >= 0.8: 72%).
- **Coverage:** share >= 0.9 gives a theme to **13,013** more distinct messages (D01 6,075, D05 2,944, D04 1,170, D02 842, D09 840, D03 491, D07 402, D06 167, D08 82); high tier + neighbours = 35,782 of 381,350 (9.4%, was 6.0%); with the medium tier 17.5%. Share >= 0.6 would add 42,078 but is only about 61% right, so it is not adopted.
- **Failure modes seen:** posts near a security cluster that are not security themselves (phishing/crypto scams, exam-prep, AI-industry chatter, local crime, hate-speech quotes); a post about war/politics gets the war theme instead of diplomacy (D01 for D02 subjects); D08 and D09 weakest (7/14 and 7/17 right).
- **Verdict: PARTIAL.** Fit as a third, separate tier ("via neighbours", share >= 0.9, about 75%) next to the hybrid high tier (74%) and medium (59%). Not yet wired into the Explore Theme filter (it still reads stage 27 only); no theme prevalence is reported. Caveats: one non-blind reviewer, n=65 for the adopted band.
- **2026-11-20 — neighbour tier wired into Explore.** The Theme filter's default now lists the clear matches: stage-27 high tier plus stage-28 neighbour votes (share >= 0.9), about 75% right; "Include lower-confidence" still adds only the stage-27 medium tier (59%). `search_trend` returns the tier `neighbour` and the theme chips count it. Tests: `check_explore_api.py` 65/65, `check_explore_search.mjs` 29, `check_explore.mjs` 90, `check_explore_features.mjs` 41.

## 2026-11-20 — A01-A10 event topics (10 codes, 41 sub-codes) placed under the D01-D09 security themes

- **Mapping only; no posts are assigned to A codes yet** (theme_code / theme_subcode stay "declared, not yet populated"). `CODE_TO_DOMAIN` in `scripts/lib/taxonomy.py` (single source, emitted into `metrics.json`: entries carry `domain` + `domain_also`, the `security_domain` axis carries `groups`). `validate()` now fails the build if a code has no theme, a secondary is not a real theme, or a theme has no event topic. Tests: `scripts/tests/test_taxonomy_domains.py` 4/4; `check_taxonomy_drop.mjs` 6/6; `check_taxonomy.mjs` 7/9 (same two old failures).
- **Placement (primary theme):** D02 <- A01 (4 sub-codes); D03 <- A02, A03, A04 (12); D04 <- A05 (5); D05 <- A06 (4); D06 <- A07 (4); D07 <- A09 (4); D08 <- A10 (4) + 08.4 refugee spillover; D09 <- A08 + 08.3 domestic stability narratives; D01 <- 08.2 external support to state / non-state actors only; 08.1 mediation -> D02.
- **Gap found:** the A list has no event topic for conventional war between states (strikes, shelling, ceasefires), so D01 Conflict has one sub-code (08.2) and no parent code. Posts such as missile strikes on Kyiv have no A code. To be decided: add a code (for example a new A11) or leave D01 without sub-topics.
- **Not done:** assigning posts to codes, any filter or page for A codes; precision of such an assignment is unmeasured.

## 2026-11-20 — stage 29: A-code event topic per post (constrained zero-shot); review verdict UNSOUND at default, PARTIAL at a high score cut

- **What:** `scripts/29a_embed_event_topics.py` (system python with torch) embeds the 41 sub-codes + 10 parents (text = display + taxonomy description) with the pretrained bge-m3; `scripts/29_post_event_topic.py` -> `data/post_event_topic_v1.parquet` (local): for each post that already has a D theme (stage 27 high/medium or stage 28 neighbour) the best sub-code **among the sub-codes placed under its theme only** (1-12 candidates), with `score` (cosine) and `margin`. No training, no LLM. D01 and D09 have a single sub-code (08.2, 08.3), which the first run forced onto every post of the theme; they are now skipped.
- **Review (118 fresh posts, 14 per theme D02-D08 and 10 each D01/D09, read by hand, non-blind; the sample was drawn before the D01/D09 skip):** sub-code exactly right **45%** (95% CI 36-54), parent code (A01-A10) right 59%. D01 and D09: **0 of 20** (forced single code; skipped now). D02-D08 only (n=98): sub-code 54% (44-64), parent 70%. By theme (sub right / 14): D02 10, D08 9, D03 9, D07 8, D04 7, D05 5, D06 5. Part of the error is inherited from the theme step (a post that is not security cannot get a right event topic).
- **Cut-offs (chosen on the same 98 posts, so optimistic):** score >= 0.50 keeps 40% of posts with sub-code 74% / parent 87% (n=39); score >= 0.48: 70% / 81% (n=54). Margin adds little. Neighbour-tier posts were only 5 in the sample (not judged separately).
- **Verdict: UNSOUND as a default label for all themed posts; the parent code (A01-A10) at score >= 0.50 is PARTIAL (about 87%, n=39, tuned on the review sample) and needs a fresh sample before it is shown.** Not wired into the API or UI.
- **Findings:** the A list has no code for conventional war between states (D01 posts have no home); the sub-codes of 01 (01.1/01.2/01.3) and of 06 (06.1/06.3/06.4) are hard to separate by meaning alone (many 01.1 vs 01.2 and 06.1 vs 06.3 mix-ups); fiction, games, history posts and crypto-scam alerts leak in from the theme step.
