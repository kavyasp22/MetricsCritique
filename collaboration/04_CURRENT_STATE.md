# Current state (as of 2026-07-30, later — see top sections for the newest work)

## 2026-11-27 — Comment-sentiment quality layer, 4 platforms (DONE; verdict PARTIAL)

Spec: `docs/SENTIMENT_QUALITY_SPEC.md`. Stage `scripts/51_sentiment_quality.py` -> `data/sentiment_quality.duckdb` (new file; nothing existing modified).
- **What:** NO_TEXT exclusion, emoji-only tag (kept), confidence tiers (0.60 / 0.45), Wilson intervals (`stats_v2.wilson_interval`, 73/73 self-checks).
- **Dashboard:** new "Sentiment quality" section in the Audience Sentiment tile (`UI/audience_view.quality_block`, `dash.js`); absent when stage 51 has not run.
- **Finding:** emoji-only comments are 30% of Snapchat and 21% of Instagram comments; they lift net sentiment (+24 vs +10 words-only on Snapchat; +1 vs -12 on Instagram).
- **Checks:** `UI/tests/check_audience_api.py` 94/94; `scripts/tests/test_sentiment_quality.py` + audience tests 27 pass; `check_audience.mjs` new checks pass, its old "five tiles" check and a later tile lookup still fail (pre-existing).
- **Robust trends:** `stats_v2` theil_sen / mann_kendall (Hamed-Rao) / robust drift candidate (81/81). Weekly sentiment trend on the card (Theil-Sen + MK, coverage-guarded; Snapchat +0.36 net pts/week, p<0.001; YouTube NULL over all dates: 14% week coverage). `scripts/review/robust_drift_eval.py`: shipped `tone_drift_z` replicated exactly; on shuffled series it false-alerts 4.5% vs median/MAD 6.6% (nominal 5%), so the z-score rule is kept.
- **Not done:** hand-judged sample, sarcasm flag, language routing. Post-level confidence needs a GPU re-score (deferred).

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

## 2026-11-20 (later) — fresh check of the A-code at score >= 0.50 (stage 29): the earlier 87% did not hold; verdict PARTIAL

- **Sample:** 105 fresh posts never used before (15 per theme D02-D08, all with score >= 0.50, read by hand, non-blind). Population with score >= 0.50: 17,456 distinct messages (41% of the 42,677 themed ones).
- **Result:** A-code (A01-A10) right **77%** (81/105, 95% CI 68-84); sub-code right **58%** (61/105, CI 49-67). Population-weighted by theme: parent 79%, sub-code 60%. The tuning-sample figures (87% / 74%) were optimistic, as flagged. By theme (parent right / 15): D07 13, D03 14, D02 13, D04 11, D05 11, D08 11, D06 8 (D06 weakest: 07.1/07.4 collect unrelated border chatter, floods, history).
- **By the tier of the theme step:** theme tier high: parent 86% / sub 71% (n=49); medium 71% / 47% (n=49); neighbour 57% / 43% (n=7, too few). The A-code is only as good as the theme under it. The 7,532 distinct messages that are high tier AND score >= 0.50 split A06 2,323, A09 1,860, A05 1,590, A01 558, A02 347, A10 271, A04 259, A07 192, A03 75, A08 57. The tier split was looked at after the fact, so the 86% is a post-hoc figure.
- **Verdict: PARTIAL.** The sub-code (41 codes) is not good enough to show (58%). The A-code under a D theme is about 77-79% on score >= 0.50, 86% on its high-tier subset, i.e. in the range of the theme label itself. Mistakes seen: 01.1 vs 01.2, 06.1/06.3/06.4, 05.1 vs 05.3, 10.3 for plain local medical camps; non-security posts that carry a theme (fiction, ads, geofencing, beach clean-ups) still get an A-code.
- **Not wired** into the API or UI. If shown: A-code only, never the sub-code; score >= 0.50 and theme tier high or the "include lower-confidence" box on; labelled provisional.

## 2026-11-20 (evening) — Gemma labels the A-code (stage 30 pilot, 153 fresh themed posts): usable, verdict PARTIAL-to-SOUND pending a larger check

- **Setup:** `scripts/lib/event_topic_llm.py` (prompt built from `taxonomy.py`: 41 sub-codes with descriptions, plus two extra answers, `none` = not a state-level or security matter, and `conflict_unmapped` = armed conflict no sub-code covers; JSON with sub-code, confidence and a verbatim evidence phrase) and `scripts/30_gemma_event_topic.py` (pilot, parquet only, nothing written to a database). Gemma is the project endpoint (model gemma-4-26b-a4b-it, temperature 0); the post's D theme is NOT given to the model. Tests: `scripts/tests/test_event_topic_llm.py` 3/3.
- **Pilot:** 153 fresh posts (17 per theme D01-D09, never used in earlier reviews), 153/153 valid answers, evidence phrase found verbatim in the post for 98%. Speed: 153 posts in 90 s at 16 parallel calls (median 3.8 s, about 1,800 tokens per post), so the 42,677 distinct themed messages would take about 7 hours.
- **Result (read by hand, non-blind, one reviewer):** Gemma answered a sub-code for 111, `none` for 27, `conflict_unmapped` for 15. Exactly right overall **88%** (134/153, 95% CI 81-92); **78%** (120/153, CI 71-84) if 14 borderline calls are counted as wrong; right or right parent code 92%. Among the 111 coded: exact 83% (75-89), parent 89%, strict 70%. All 27 `none` and all 15 `conflict_unmapped` read as correct (recall of `none` not measured: 17 of the 119 posts the zero-shot step had coded got `none`). By confidence (coded): 1.0 -> 92% exact / 95% parent (n=74); 0.9 -> 69% / 80% (n=35); under 0.9 only 2 posts. Theme tier did not matter (high 87%, medium 87%, neighbour 90%).
- **Against stage 29 (bge-m3 zero-shot):** Gemma agrees with its sub-code on 59% of posts and its parent on 80%; stage 29 was 58% (sub) / 77% (parent) on a fresh read at score >= 0.50, so Gemma is clearly better (the two were not judged on the same posts).
- **Where Gemma still errs:** hate speech or opinion posts forced into a code (08.3, 08.2, 10.4), conflict reaction posts put under 07.4 or 08.1 instead of `conflict_unmapped`/none, adjacent codes (01.1 vs 01.2, 03.1 vs 03.3, 05.1 vs 06.1).
- **Gap confirmed:** 10% of themed posts are armed conflict with no A code (`conflict_unmapped`); terrorism/insurgent attacks have no A code either. Decision on an A11 (conventional conflict) still open.
- **Not done:** no full run, nothing wired into the API or UI. Next: decide full run (about 7 h) and what the page shows (sub-code only at confidence 1.0, parent code otherwise).

## 2026-11-20 (night) — can clusters cut Gemma calls? (simulation on existing labels, no new Gemma call)

- **Question:** label 3 sampled posts per cluster with Gemma; if all 3 agree, give that label to the whole cluster, else label its posts one by one. Simulated with the hidden stage-27/28 theme and the stage-29 A-code parent as stand-in labels (Gemma has labelled only 153 A-code and 500 domain posts, too few per cluster to test with its own labels), over the existing HDBSCAN sub-topic clusters of `topics_v5.duckdb` (coherent ones, at least 6 themed members, 300 random draws each). Clusters are used only as hidden units, never shown.
- **Coverage first:** of 66,762 distinct themed messages only 32,979 (49%) sit in an HDBSCAN cluster; the other 51% are noise. 27,376 are in a cluster judged coherent.
- **Purity:** size-weighted share of a cluster's themed posts that carry its most common label: theme 83%, A-code parent 85% (about half the clusters at 90% or more).
- **Rule result:** theme: 453 clusters, the 3 samples agree in 56% of them and that covers 61% of their 26,862 posts; **10% of the covered posts would receive a label different from their own**. A-code parent: 332 clusters, agree in 60%, covers 65%, **8% differ**. Calls inside those clusters fall by about 56-59% (theme 26,862 -> 11,800; parent 17,132 -> 6,939).
- **Overall effect:** only about 40% of themed posts are in a usable cluster, so total calls for all themed posts fall by about **23%** (66,762 -> about 51,700) while about 2.5% of all themed posts would get a different label than their own. The stand-in labels are themselves only about 75% right, so part of the disagreement is their noise; the share caused by cluster mixing cannot be separated without Gemma labels on cluster members.
- **Verdict:** not worth it as a way to save calls: a modest saving, a real error rate, and half the posts not clustered. Clusters stay a naming and event-grouping helper. Cheaper levers already in place: only themed posts need the A-code call (about 43-67k of 381k messages), exact copies and the bge-m3 "clearly not security" skip are already removed. Untested alternative: several posts per Gemma call so the long code list is not repeated for each post (about 1,700 of the 1,800 tokens per call are the fixed prompt).

## 2026-11-20 (night, later) — several posts per Gemma call (batching) for the A-code: test on the 153 pilot posts

- **Setup:** `scripts/30b_gemma_event_batch_test.py` + batch prompt in `scripts/lib/event_topic_llm.py` (one list of codes, N numbered posts, JSON `answers` list, each post judged independently, ids and count validated). Same 153 posts as the single-call pilot, temperature 0; 8 parallel calls. All batches valid (31 calls of 5, 16 calls of 10), evidence phrase verbatim 99%.
- **Cost:** tokens per post 1,831 (single) -> 535 at 5 per call (29%) -> 353 at 10 per call (19%). Summed call time 615 s -> 232 s -> 199 s. Rough full run for the themed messages: about 2-3 hours at batch 5, against about 7 hours single.
- **Answers:** same sub-code as the single-call answer for 86% (batch 5) and 79% (batch 10) of posts. Accuracy against my hand judgement (disagreeing posts re-read, non-blind): single 88% lenient / 78% strict; **batch 5: 87% / 77%** (better on 6 posts, worse on 10); **batch 10: 84% / 71%** (better on 6, worse on 19). Right-or-parent-right 92% / 91% / 91%. Batch-5 differences are inside the noise of 153 posts (CI about +-6 points); batch 10 loses about 8 points strict.
- **What changes in batches:** more `none`/`conflict_unmapped` mix-ups, phishing and scam alerts coded 09.3 instead of `none`, neighbouring codes swapped (01.1/02.3, 10.3/10.4); a few batch answers were better (6 posts, e.g. none instead of a forced 08.3).
- **Verdict:** batch of 5 is a usable saving (about 70% fewer tokens, about 3x faster) with a small, not clearly significant accuracy cost; batch 10 is not. Not applied to any run. If a full run is approved, use batch 5 and spot-check 150 posts from it.

## 2026-11-20 (night) — Trending Topics search now answers "what is trending around X?" (related trending, no sub-topics needed)

- **Page:** a keyword, entity or theme searched on Trending Topics now shows one sentence of answer (for example "ladakh: rising, 89 posts, +178% on the days before, mostly on youtube and snapchat"), the daily bar chart and the platforms, then **Related trending** in four groups: Topics (t004 + t005 merged), Security themes, People / organisations / places, Hashtags. Each row has a plain tag (Rising = at least +25% on the same number of days before, Declining = at least -20%, New = under 5 posts before, Few posts = under 10 now, Steady) and its post count; clicking narrows the search (topic opens in Viral Posts, theme and entity narrow the search, hashtag starts a new search). Raw metric names (emergence, surprise, q-value) are not shown; the how-it-works text is collapsed. Below: **Posts behind it** = most viewed and latest.
- **API (`/api/explore/search_trend`):** adds per-period counts for topics and themes, `entities` (production entity extract; name, type, whole-corpus posts; at least 5 posts, the searched entity left out) and `hashtags` (read from the post text, once per post, at least 5 posts). `/api/explore/viral` takes `latest=N` and returns the newest matching posts. 2.4 s for "ladakh".
- **Sub-topics are not needed:** the finer groups come from themes, entities and hashtags; the 1,193 sub-topics stay unused.
- **Tests:** `check_explore_search.mjs` 34, `check_explore_api.py` 72/72 (7 new), `check_explore.mjs` 90, `check_explore_features.mjs` 41, `check_taxonomy_drop.mjs` 6/6.
- **Limits:** the tag compares counts inside the search's own posts (not each item's trend across the whole corpus, and no baseline anomaly yet); entity coverage ends earlier on some platforms; hashtag and entity rows are not alert-keyed; a niche search still lands in one or two of the 8 topics.

## 2026-11-20 (night, later) — search panel: "is it unusual?" against a 28-day baseline, and per-platform speed

- **Anomaly (`_search_anomaly` in `UI/api_server.py`, reuses `scripts/lib/stats_v2.py`):** for each selected day a Negative Binomial baseline is fitted on the 28 days before it (never including it, at least 14 collected days), on counts rescaled to the same collection volume (the day's total posts on the same platforms), surprise = -log10 P(X >= observed), Benjamini-Hochberg q over the selected days, alert day = q < 0.05. Same gates as the pipeline (F3): no verdict (NULL) with fewer than 3 matching posts or a usual level under 0.5 a day. The page says "Unusual: N of M days judged stood out from the last 28 days (dates), up to X times the usual number of posts", "Within the usual range", or "Cannot say whether it is unusual: reason"; alert days are marked on the chart ribbon. Examples (1-26 Oct): "ceasefire" alerts on 10 and 11 Oct (up to 5.2x), "hormuz" one day (4 Oct, 2.6x), "ladakh" none (14 Oct 5.1x but q = 0.055), "nepal flood" none.
- **Per-platform speed (`_platform_speed`):** per platform (top 8): posts in the range with change on the days before, first day with at least 3 matching posts looking back 28 days ("already active before" when that day is before the range), peak day, and Rising / Steady / Declining (last 3 days against the 3 before per collected post of that platform; needs 10 posts in the last 6 days, else "Few posts"). A one-line lead says which platforms were active before and where it was first seen, in order. Clicking a platform row searches only that platform.
- **Tests:** `check_explore_api.py` 78/78 (6 new), `check_explore_search.mjs` 38, `check_explore.mjs` 90, `check_explore_features.mjs` 41.
- **Limits:** the baseline is the search's own recent past (a search that has been busy for 28 days looks usual); counts are collected posts, rescaled by collection volume not by audience; a day or platform with fewer than 3 matching posts gets no call; the cross-platform velocity in the trending score and the topic-level emergence for related items are not built.

## 2026-11-20 (night, last) — "who is involved": a search plus an entity type / subtype

- **The case that was wrong:** searching "iran war" and choosing People -> Political leader only showed the posts that mention the phrase; the type and subtype did nothing until a single name was picked. What the user wanted: who is involved in the war, and the posts about each of them.
- **Now:** with a type (and optionally a subtype) chosen next to the search, the panel shows **Who is involved** (people, organisations, places or events, depending on the type): the entities of that kind named in the matching posts, most named first, each with a Rising / Steady / Declining / New tag and its post count; **clicking a name shows the posts of the search that name them** (the name is added to the search words) with a button to narrow the whole search to that name. Example (1-26 Oct, "iran war", People -> Political leader, 964 posts): Trump 365, Benjamin Netanyahu 65, Putin 57, Pete Hegseth 32, Marco Rubio 32, Barack Obama 26, Xi Jinping 16, Abbas Araghchi 15, Zelensky 13 ...
- **Counted by name in the post text on every platform** (`_people_involved` in `UI/api_server.py`), not from the production entity extract, because that extract stops early on Twitter (2026-07-23), Telegram (07-28), Instagram and Reddit (08-02/03), the platforms where most war posts are. Candidates: the 200 best-known entities of that type / subtype, cleaned (`_clean_candidates`): titles ("President", "Prime Minister", "Foreign Minister", "Representative"), links, nationalities, plurals and bare first names removed; spellings of one person merged (Xijinping / Xi Jinping, Obama / Barack Obama, Pmmodi / Narendra Modi); the 80 most frequent are counted, at least 3 posts to be listed, top 20 shown. The related People / organisations / places group of the normal panel also drops titles now.
- **Limits:** a surname shared by two people is counted together (two Khameneis are listed separately only because the entity layer holds them as two names); the clean-up lists are heuristics (a short list of first names, nationality suffixes, title words); names the entity layer never extracted cannot appear; the match is on the words in the text, not on role in the war.
- **Tests:** `check_explore_search.mjs` 45 (7 new: the exact scenario), `check_explore_api.py` 84/84 (6 new), `check_explore.mjs` 90, `check_explore_features.mjs` 41.

## 2026-11-20 (night, last) — scenario search: "who is actually involved" instead of a keyword search (Iran War + People / Political leader)

- **What was wrong:** the typed search was a keyword search. Posts were found by the scenario words in the text, the entity type only helped pick a name, and the "who" list counted names in the keyword-matched posts. Measured on 45 random posts of the old result (keyword "iran war" and a political leader named, 604 posts in 12-26 Oct): about **42% really about the war and the leader's part in it (51% counting borderline)**; the rest were Ukraine, Israeli or Indian domestic politics, stray names ("Union", Imran Khan) or Saudi/Houthi items. Of the posts verified as involving a leader in the new search, **43% do not contain both words** "iran" and "war", so the keyword search could never return them.
- **New method (`scripts/lib/scenario_search.py`, endpoint `GET /api/explore/scenario_people`, used only when an entity type is chosen next to a search; with no type the search is unchanged):** (1) Gemma writes a scenario brief and 8 anchor sentences from the typed words, not from keyword hits; (2) bge-m3 ranks all distinct posts of the selected days and platforms by cosine similarity to the anchors (stored vectors, `scripts/31_post_embedding_index.py`, fp16 memory map, about 1 s); (3) the 12 best posts found by meaning are shown to Gemma, which rewrites the brief and anchors with the concrete actors and events, and the ranking is repeated; (4) of the top 1,500 posts those naming a known person of the chosen type (`scripts/lib/entity_people.py`: titles removed, spellings merged) are collected and the best 5 per person (24 people with most candidate posts, at most 110 posts) are shortlisted; (5) Gemma, 5 posts per call, says whether each is about the scenario (words not needed) and whether each named person is `direct` (the post is about what they do, decide, say, threaten, negotiate or are responsible for, also when others react to it), `mentioned` or `unrelated`, with role and a verbatim evidence phrase; failed calls are retried once; (6) ranking: semantic score 0.55 and involvement 0.45; views and engagement play no part. People are ordered by estimated direct posts (shrunk direct share of the checked posts times the posts naming them). First search 25-50 s (35.6 s on the validation run, 24 s for another scenario), cached on disk by scenario text, days, platforms and type.
- **Needs two things running:** `scripts/embed_service.py` on the host (torch; the container has none; `CUDA_VISIBLE_DEVICES= /usr/bin/python3 scripts/embed_service.py --host <docker bridge address> --port 8893`) and `EMBED_URL` in `.env`. Without the service the page says so and falls back to the names counted in the keyword-matched posts.
- **Validation, Iran War, 12-26 Oct, People / Political leader (non-blind, one reviewer):** people found directly involved (posts checked / direct): Trump 51 / 26 (about 190 of 372 posts naming him), Benjamin Netanyahu 13 / 10, Abbas Araghchi 5 / 4, Pete Hegseth 6 / 6, Marco Rubio 5 / 5, Masoud Pezeshkian 5 / 4, Mohammad Bagher Ghalibaf 5 / 4, Israel Katz 4 / 3, Mojtaba Khamenei 5 / 2 ... Of the 56 distinct posts verified as direct involvement, read by hand: **39 right (70%), 12 borderline, 5 wrong (91% counting borderline)**; 24 of the 56 (43%) lack the words iran + war. Excluded by Gemma as not about the scenario: 14 of 86 shortlisted (Ukraine strikes, Trump's Ukraine "energy ceasefire", French fuel prices, North Korea): 12 correct, 2 arguable. Gemma against my reading on the 45 old-search posts: relevance flag found all 23 posts I called relevant but also 9 of 22 I called irrelevant (war mentioned as context); relevant AND direct: accepted 23, of which 17 mine (74% precision), rejected 22 of which 16 mine, missed 6 (recall 74%), so Gemma is somewhat conservative on "direct".
- **Wrong or borderline in the new results:** a post about Saudi/Houthi fighting or the Ukraine war still counted for a leader when a regional link exists (MBS, Zelensky on diesel prices); "Rubio receiving a memo" counted direct four times (borderline); Gemma's direct counts vary a little between runs.
- **Limits:** a person called only by a title ("the US president") is not found; recall is not measured beyond the shortlist (best 5 posts per person); the same-surname problem remains; the keyword and name lists in `entity_people.py` are heuristics; one scenario validated; the brief depends on Gemma's general knowledge plus the retrieved posts.
- **Tests:** `scripts/tests/test_entity_people.py` 4, `test_scenario_search.py` 4 (pure logic), `check_explore_search.mjs` 48 (the scenario list, verified posts, narrowing and the fallback when the service is down), `check_explore_api.py` 89/89 (input checks).

## 2026-11-20 (night, last) — scenario search v2: the event is grounded in the data's own posts (Iran War + People / Political leader)

- **Gap found in v1:** the event brief came from Gemma's general knowledge (which predates the 2026 events) corrected by only the 12 best posts. v2 (`scripts/lib/scenario_search.py`, topic-agnostic, same endpoint `GET /api/explore/scenario_people`, only used when an entity type is chosen; no type = unchanged search; standalone Entity Search untouched): (1) hint sentences from the typed words start a semantic ranking only; (2) a 60-post grounding sample is drawn from the top 1,500 by round-robin over platform x time bucket x meaning (farthest-point), one pick in three from the fringe of the ranking (rank 500+) so that posts that do not belong are in the sample; its post ids are kept; (3) Gemma reads only those posts and returns the event, developments (with supporting post ids and time), actors with aliases, titles that point to a person, off-topic posts, contradictions and an assessment (clear / multiple_developments / ambiguous / insufficient); (4) adaptive: insufficient or ambiguous contexts get up to 3 more rounds (follow-up questions and developments become retrieval anchors, 40 more diverse posts each, at most 160), a clear-but-multi-development context gets one extra round and is then reported as it is; (5) retrieval anchors = grounded anchors + developments + typed words + hints; people = known entities of the type, spellings merged, plus actors and title resolutions that the context itself supplies; best 5 posts per person shortlisted; (6) Gemma validates each shortlisted post against the grounded context: direct / indirect / mention / unrelated with an evidence phrase that must be found in the post (otherwise the call is downgraded to mention); (7) people ordered by strength = involvement shown by their 5 best posts (ties: number of verified involvement posts); mention counts, views and engagement are not used. The page keeps its layout: the Who-is-involved list now shows Direct / Indirect tags, "N direct - M indirect of K checked", the posts with reason and evidence, the grounded event and developments in the collapsed explanation, and a warning when the assessment is not "clear".
- **Cost:** first search about 105 s on Iran War (grounding about 50 s for 2 rounds / 100 posts, shared by every entity type of the same search and cached; retrieval and validation about 35 s), then instant from `data/scenario_cache/`.
- **Validation, Iran War, 12-26 Oct, People / Political leader (non-blind, one reviewer, real posts).** Grounding: assessment `multiple_developments` after 2 rounds (60 -> 100 posts, 12 platforms); developments found: direct strikes on Iran, US and Israeli military posture, Iranian readiness and Hormuz shifts; a contradiction about whether Trump called it a "military exercise" or said he "had to attack Iran" is reported, not resolved. People with verified involvement in the final run: Araghchi, Trump, Netanyahu, Hegseth, Pezeshkian, Rubio (indirect), Katz, Ghalibaf, Obama (statements), Sanders (new: urged Trump to end the war), Xi Jinping (indirect), Mojtaba Khamenei (1 wrong). **49 involvement pairs (26 direct, 23 indirect): 33 right (67%), 15 borderline, 1 wrong; direct pairs 19 right / 6 borderline / 1 wrong (73% strict, 96% lenient); indirect pairs 14 / 9 / 0.** For comparison v1 gave 56 direct posts: 70% strict, 91% lenient, 5 wrong.
- **Same 56 v1 pairs re-validated with the v2 rules:** the 5 v1 mistakes: 4 removed (2 mention, 2 unrelated: MBS Yemen posts, Trump in an FT World-War article, an opinion comment), 1 remains (Zelensky blaming diesel prices on Hormuz, still called direct = false positive); the 12 v1 borderline (mostly "Rubio received a memo", Macron's Gulf tour): 8 reclassified to indirect (correct), 1 mention, 2 unrelated, 1 still direct (Macron); the 39 v1 right pairs: 22 stay direct, 14 move to indirect (real involvement, lower class, partly too strict: "Trump prepares massive strikes" came out indirect once), 2 mention, 1 unrelated, so 36 of 39 (92%) are still found as involved.
- **Not confirmed / not improved:** (a) posts without the words "iran" and "war": 11 of 38 involvement posts (29%) in v2 against 43% of v1's direct posts, and only 2 of the 15 involvement posts that v1 never shortlisted lack them: v2 does not recover more keyword-free posts than v1 in this run; (b) recall: people v1 found in earlier runs (Vance on gasoline prices, Bessent on the energy shock) are missing from the final v2 list because the shortlist depends on the ranking, and the list changes between runs (Trump 9-10 direct, Hegseth 1-4, Netanyahu 2-3); (c) the strength ordering is only as stable as Gemma's direct / indirect split; (d) titles: one title resolved by the context ("Iranian Parliament Speaker" = Ghalibaf), 2 posts, both judged unrelated; "the US president" was not resolved to a person.
- **Remaining false positives and limits:** Mojtaba Khamenei called direct when his adviser warned; Zelensky (diesel prices); borderline: Netanyahu and Hegseth as "indirect" from posts that only list their meeting; the context's event summary is generic and leans on late-October posts; the validation is one reviewer, non-blind, one scenario; Gemma is conservative and not deterministic across runs; a person named only by a title is mostly not found.
- **Tests:** `scripts/tests/test_scenario_search.py` 6, `test_entity_people.py` 4, `check_explore_search.mjs` 49, `check_explore_api.py` 89/89.

## 2026-11-20 (night, last) — audience sentiment Phase 1: YouTube comments and replies (own layers, not mixed into post sentiment)
- **New stages** `32_audience_extract.py` (fresh read-only pull of `youtube_comments`, production counts taken in the same transaction), `33_audience_score.py` (torch env; same cardiffnlp XLM-R model, 256 tokens, 2000-char cap and pos-neg tone as the post pipeline), `34_audience_build.py` (layers, coverage, validation). Helpers `lib/audience_text.py` (text triage) and `lib/sentiment_model.py` (verbatim copy of 04's model setup, guarded by a drift test). `04_nlp_scores.py` is unchanged.
- **Storage:** `data/audience_sentiment.duckdb` (`post_sentiment`, `comment_sentiment`, `reply_sentiment`, `comment_text` local only, summary / coverage / validation tables) and a new delivery schema `audience_sentiment` via `delivery/ingest_audience_sentiment.py` (replaces only its own schema; no text delivered). Production untouched.
- **Result:** 391,768 production rows = 391,768 extracted (270,262 comments + 121,506 replies, 12,523 videos); 391,500 scored, 268 kept unscored (empty 34, URL-only 171, symbols-only 63), 0 failed. Comments 46% negative / 20% neutral / 34% positive; replies 52% / 31% / 17%.
- **Validation:** 27 of 29 checks PASS, 2 WARN (30,446 rows have no posted_at; 4,219 videos / 119,306 comments have no post row in the older analytics store). Blind hand-check of 60 rows: 73% agreement (CI 61-83), 5 positive<->negative flips. Verdict PARTIAL (model not calibrated on this corpus; reads wording, not stance).
- Spec, coverage and validation tables: `docs/AUDIENCE_SENTIMENT_SPEC.md`. Tests: `scripts/tests/test_audience_sentiment.py` (7).
- Not done (later phases): Instagram / LinkedIn / Snapchat comments, replies for Instagram, audience roll-ups per post / topic / event, sentiment gap, emotions, entity-target sentiment, dashboard.

## 2026-11-20 (night, last) — audience sentiment Phase 2: YouTube per-video roll-ups, sentiment gap, theme / month roll-ups
- **Stage `scripts/35_audience_rollup.py`** (+ `lib/audience_rollup.py`): `post_audience_sentiment` (14,938 videos; comment `c_*` and reply `r_*` metrics in separate columns, existing post tone, three gaps, data status), `audience_rollup` (by D-theme, month, theme x month, all; counts and sums so groups re-sum) and `audience_rollup_validation` (44 checks, all PASS), added to `data/audience_sentiment.duckdb` and the delivery schema `audience_sentiment` (no text, no author names). Phase 1 tables unchanged (fingerprinted before and after).
- **Gap** = audience mean tone - post tone, same [-1, 1] scale; NULL without a post tone (4,219 videos have none, never set to neutral) or below 5 scored rows. Comment gap on 5,263 videos (mean +0.05; 685 audience clearly more negative, 1,161 more positive), reply gap on 2,443.
- **Zero vs missing:** has_comments 12,523, not_collected_known 80, unknown_no_comment_data 2,335, confirmed_zero 0 (YouTube's reported count is NULL for most videos, so a true zero cannot be confirmed).
- **Topics:** existing hybrid D-themes, unchanged; 65% of store videos are `unassigned`, 4,219 are `post_not_in_store` (explicit buckets).
- Tests: `scripts/tests/test_audience_sentiment.py` (14). Details, definitions and limits: `docs/AUDIENCE_SENTIMENT_SPEC.md` (Phase 2 section).
- Not done: Instagram, emotions, entity-target sentiment, dashboard, event roll-up.

## 2026-11-20 (night, last) — audience sentiment Phase 3: Instagram comments and nested replies
- **Key finding:** `instagram_posts` holds each post up to 501 times (959,236 rows, 15,861 distinct `post_url`; 98,389 rows with comments are copies of 1,397 posts). The unit of an Instagram post is the distinct post_url; comments are kept once per (post_url, comment_id) with `n_source_rows`. Reconciliation with production is exact (632,215 top-level and 114,825 nested elements with copies; 64,318 distinct).
- **New stages** `36_instagram_extract.py` (read-only), `33_audience_score.py --platform instagram` (same model, same code, 64,057 scored, 0 failed, 9.5 min), `37_instagram_build.py` (layers, coverage, validation, roll-ups, cross-platform tables). `lib/audience_instagram.py` parses `comments_data`; `35_audience_rollup.py` now takes a `Spec` (YouTube outputs verified byte-identical after the change).
- **Tables** (DuckDB + delivery schema `audience_sentiment`, no text / author names): `ig_post_sentiment`, `ig_comment_sentiment` (40,497), `ig_reply_sentiment` (23,821), `ig_post_audience_sentiment` (15,862 posts), `ig_audience_rollup`, `ig_*` summary / coverage / validation tables, and `xplat_post_audience_sentiment` + `xplat_audience_rollup` (YouTube + Instagram, one schema). YouTube tables unchanged.
- **Validation:** 38 PASS / 3 WARN / 0 FAIL on the layers (964 replies without their parent comment, 183 malformed items without a timestamp, 972 posts with comments missing from the older store), 44 / 44 on the roll-up. The first run caught two real defects (183 malformed items shared one id; the post-universe check ignored a NULL-url row); both fixed. Blind hand-check of 60 rows: 72% agreement (CI 59-81), 4 positive<->negative flips. Verdict PARTIAL.
- **Coverage gap:** only 1,397 of 15,861 posts (9%) have comment data; 6,929 posts report comments we do not hold; 7,536 report zero. Instagram text is not translated. Only 983 of 15,862 posts are in the older post store, so post tone / theme / date are mostly NULL and gaps exist for 123 posts.
- Tests: `scripts/tests/test_audience_sentiment.py` (20). Details: `docs/AUDIENCE_SENTIMENT_SPEC.md` (Phase 3 section).
- Not done: LinkedIn, Snapchat, emotions, entity-target sentiment, dashboard, refreshing the post store.

## 2026-11-21 — audience sentiment: targeted post enrichment (no stage 01 refresh)
- **Why:** the post store stops at 2026-10-23; 4,219 YouTube videos and 972 Instagram posts with comments had no post tone / theme / date. A full stage 01 refresh was unsafe (1.03 million Instagram rows for 15.9k posts; 04 and 07 are not dedup-aware), so only the posts that carry audience data were read (one canonical row per post) and enriched into NEW tables.
- **Stages** `38_audience_enrich_extract.py` (read-only), `39_audience_enrich_tone.py`, `40_audience_enrich_build.py`; `35_audience_rollup.py` gained an `enriched` switch (v1 outputs verified identical). Targets: Instagram 985 (972 not in store + 13 with an empty stored caption), YouTube 4,219. Analytics store, `metrics_v2`, stage 01/04/07 untouched; nothing written to production.
- **Result:** 5,185 new post tones (19 Instagram posts have no caption), 1,318 new themes (YouTube 1,166, Instagram 152) from replaying stages 26/27/28 on the new posts only, and `_v2` per-post / roll-up / cross-platform tables. Comment gap YouTube 5,263 -> 7,709 videos, Instagram 123 -> 596 posts; reply gap YouTube 2,443 -> 3,517, Instagram 45 -> 316. Commented posts with a theme: YouTube 3,019 -> 4,185, Instagram 78 -> 230.
- **Validation:** 39 PASS / 3 WARN / 0 FAIL. Replay of stages 26/27/28 on stored inputs reproduces stored themes 150/150 per platform; tone reproduces the store (Instagram 150/150, YouTube 148/150, max diff 0.0018). The warnings are embedding drift: production embeddings differ from the stored ones for many posts (YouTube 24/150 identical), so new posts use current production embeddings (as a refresh would). Existing comment/reply scores, v1 tables, `post_scores` / `post_facts` (md5) unchanged.
- Limits and details: `docs/AUDIENCE_SENTIMENT_SPEC.md` (targeted enrichment section). Not done: stage 01 refresh (still needs a collapse of Instagram copies), LinkedIn, Snapchat, emotions, entity-target sentiment, dashboard.

## 2026-11-21 — dashboard: Explore > Audience Sentiment tile (v2 audience tables)
- The dashboard had no audience section (the "Sentiment Analysis" tile is post tone from the metrics snapshot and is unchanged), so a new tile was added: `UI/audience_view.py` + route `/api/explore/audience` in `api_server.py` + tile/view in `dash.js` + `aud-*` styles. It reads only `xplat_post_audience_sentiment_v2` and the comment / reply layer tables (read-only, attached per request; 503 if the file is being rebuilt). Existing code touched: 14 added lines in `api_server.py`, 6 modified lines in `dash.js` (tile list, dispatch, state, three shared-control conditions), `check_explore.mjs` now expects six tiles.
- Shows post / comment / reply sentiment kept apart (YouTube, Instagram, both), shares, net, tone, gap vs post (comments, replies, replies vs comments), trends (post date or comment date), by theme, coverage (reported zero vs not collected vs unknown), posts with the largest gaps. Filters: date (page range / all dates / custom, because the page range stops at 2026-10-26 and audience data runs to 2026-11-20), platform, entity, theme (list read from the data; taxonomy untouched).
- Tests: `check_audience_api.py` 46/46, `check_audience.mjs` 47/47 live (38 canned). Regression: `check_explore` 90/90, `check_explore_features` 41/41, `check_explore_search` 49/49, `check_explore_api` 89/89, `check_tone_panels` 9/9, `check_metrics_panel` 40/40, `check_api_dimensions` 9/9, `check_related_hierarchy` 21/21, others unchanged; `check_taxonomy` has 2 failures that are identical with and without these changes (pre-existing).
- Limits: entity filter cannot match the 5,191 enriched posts (entity extract predates them); themes provisional; only YouTube and Instagram have comment text. Container rebuilt (`docker compose up -d --build`) so http://localhost:8882 serves the tile.


## 2026-11-24 — Viral Posts: audit, evidence-tiered score proposal (stage 24 v2, switched off)
- **Audit** (read-only): the card ranks popularity (raw attention within platform) or a ratio rank; no velocity (one counter snapshot per post), no event clustering, corpus-wide percentiles. Defects: zero-activity posts flagged "top 10 %" where p90 = 0 (Pinterest), the `P >= 0.80` branch admits tiny posts, RT counters, stale UI text, entity type/subtype selects send nothing. Docs: `Project-documents-final/Viral_Posts_Card_Audit.md`, `Viral_Posts_Virality_Spec_and_Research.md`.
- **Found:** `post_facts` rows are copies of one post (93,973 of 497,530; Instagram 93,029 rows = 982 posts). The current card and stage 24 do not collapse them.
- **New stage** `scripts/24_post_virality_v2.py` (+ `scripts/tests/test_virality_v2.py`, 9 pass): data-derived tiers A-E, within-platform age-band score, author baseline (leave-one-out + EB shrink), per-platform rules; writes `data/post_virality_v2.parquet`, `data/platform_virality_baselines_v2.parquet`. Not read by the dashboard.
- **Judged sample (AI analysts, not human):** 291 posts; v2 candidates 62 % breakout [53, 71] vs 2 % for the current "viral breakout" only; control 0 %. Restatement: `score_s` rho 0.64-0.98 with raw activity (it is a popularity rank); `author_overperf` 0.14-0.76.
- **Verdict: PARTIAL.** Label as "high attention" not virality; needs a human spot-check (tiers B/D ~0.5), recurring-bulletin/retweet exclusion, and recomputation on a prod snapshot (collapsed) before any dashboard use. Production not touched.

## 2026-11-24 — Viral Posts card fixes (UI/api_server.py, UI/assets/dash.js; not committed, container not rebuilt)
- **Copies collapsed:** one row per (platform, url) via an in-memory `viral_keep` table (built once, 0.13 s, no file written); Instagram 93,029 rows -> 982 posts in the card, counts and lists.
- **Zero activity is not "top 10 %":** the floors and the "Biggest" gate now require activity > 0 (Pinterest/Telegram/Baidu-video p90 = 0 case).
- **Viral breakout rule:** top 10 % of the platform by raw activity (> 0), OR virality percentile >= 0.80 with >= 20 reference posts AND at least the platform median activity (a ratio on tiny counts no longer qualifies). UI text updated.
- **Entity type / subtype** now filter the posts (they were sending nothing); `retweet @user:` counts as a retweet (+207 posts); stale "per-post percentile is not stored yet" text removed.
- **Tests:** `UI/tests/check_explore_api.py` 96/96 (7 new checks; 3 count checks re-based on collapsed posts). `check_explore_search` 50/50, `check_dim_labels` 16/16. **Pre-existing failures, identical with and without these edits:** `check_explore` (4 tiles vs 5), `check_explore_features` (2), `check_taxonomy` (4/9), `check_taxonomy_drop` (1/6).
- **Post Viewer removed (2026-11-25, on request; not committed):** `UI/post_viewer.py`, `UI/platform_config.py`, `UI/pv_templates/`, `UI/audit_results.json`, its routes in `api_server.py` (`/post-viewer*`, `/platform/*`, `/api/counts`, `/api/update-translation`), the left icon nav rail / iframe in `index.html` and the `window.setDashboardMode` hook in `dash.js`. Backup outside the repo: `/home/hariramanan/kavya/removed_post_viewer_backup/`; also in commit `1f2d7f5`. This also removes the `jinja2` import that `requirements.txt` did not list. Kept (not post-viewer): Explore as the default mode, hidden attention/alerts panels, collapsible search-trend sections.

## 2026-11-24 — Viral Posts v2: repost / template / feed exclusion (scripts/24_post_virality_v2.py, 13 tests)
- **Rules** (`rule_original`, all data-derived): `is_repost` = text starts "RT @" / "retweet @" (counters are the original's); `is_template` = same author, same normalised opening (digits masked, URLs/punctuation dropped, first 50 chars), >= 5 posts on >= 3 days; `is_feed` = author with >= 300 posts, >= 20 posts a day and median text <= 60 characters (param `exclude_feeds`). None of the three can be a candidate.
- **Measured on the judged sample (AI analysts):** the opening-text rule alone caught 0 of 4 artefact candidates; adding the feed rule caught 3 of 4 (the 4th is a one-post LinkedIn hiring template), at a cost of 1 of 68 breakouts (a genuine strike report from an alert channel) and 3 popular posts. Precision of v2 candidates in that sample moves from 0.62 to about 0.66; small sample, AI judges.
- **Production snapshot (read-only, 2026-11-24 18:02 UTC):** Telegram candidates 714 -> 97 (the alert channels dominated them); other platforms change by 0-3 %. RT posts were already out of the candidates (views = 0 fails the views-and-shares rule for tier A); the flag matters for tiers B/D and for the card.
- Trade-off to decide: `exclude_feeds=True` hides genuine hits from alert channels; set False to keep them and show the flag instead.

## 2026-11-25 — Viral Posts: stage 24 now collapses copies (the card's percentiles and lines)
- `scripts/24_post_virality.py` drops rows that share (platform, url) with a later-scraped copy before `enrich_posts`, with the SAME keep rule as the API (`viral_keep`). `data/post_virality.parquet` 769,778 -> 675,805 rows (93,973 copies removed); Instagram 93,028 -> 982 posts, its activity p90 223 -> 1,095, "top 10 %" flags 9,312 -> 99; every other platform unchanged (Mastodon/Truth Social/Facebook/Reddit/WhatsApp lose 1-1,622 copies). Previous file kept as `data/post_virality_before_collapse.parquet`. The container reads the mounted `data/`, so no rebuild is needed.
- `check_explore_api.py` 96/96 (the metrics_daily consistency check now allows platforms with copies to be lower by at most the copies removed).
- **Not fixed — wider issue:** metrics v2 (`metrics.duckdb`, authoritative) and `analytics.duckdb` still count every copy row, so Instagram volume and its topic/sentiment cells are inflated (see numbers in the message). Needs a collapse in stage 01 plus a 07 -> 08 -> 11 rebuild; not done.

## 2026-11-25 — Viral Posts v3 (EB spread efficiency): built and benchmarked; gate and composite NOT adopted
- `scripts/24_post_virality_v3.py` (+ `beta_binomial_prior`, `eb_ratio` in `lib/stats_v2.py`, 69/69 self-checks; `tests/test_virality_v3.py`, 7 pass). Writes `data/post_virality_v3.parquet`, `data/platform_virality_priors_v3.parquet`; not read by the dashboard.
- Benchmark (AI-judged, local): spread efficiency is independent of volume (rho -0.18..0.17) -> kept as a descriptive column / separate sort; the composite attention^0.75 x spread^0.25 buries the biggest posts (25 most-viewed Twitter posts at median rank 55,146 of 121,415) -> not used; the spread-efficiency gate removed posts judged breakout 0.65 vs 0.59 kept -> OFF by default; tier-D log engagement no better than v2 weights -> OFF by default. Details: `Viral_Posts_Virality_Spec_and_Research.md` section 8D. Verdict PARTIAL.

## 2026-11-25 — Viral Posts card: spread efficiency wired in (UI/api_server.py, UI/assets/dash.js; not committed, container not rebuilt yet)
- **API:** `explore_virality` LEFT JOINs `data/post_virality_v3.parquet` (new `POST_VIRALITY_V3`, optional) and returns `evidence_tier`, `spread_eff_pct`, `spread_rate_eb`, `spread_rate_raw`, `spread_stratum`; new `order=efficient` ("Most efficient": spread_eff_pct desc, then attention; activity floor kept; only tiers A/B). Default orders and the stage-24 read path are unchanged; without the v3 file the columns are NULL and `order=efficient` says to run `24_post_virality_v3.py`.
- **UI:** "Most efficient" button beside Biggest / Most amplified; each card shows `share rate: top X% of <platform> posts (n% of views shared | n% of interactions are shares)` where available and `no share counter` for tier C/D; texts explain the order. Never blended with size (the benchmark showed a blend buries the biggest posts).
- **File served:** `post_virality_v3.parquet` = local-based run (100 % of the dashboard's posts); prod-based run kept as `post_virality_v3_prod.parquet` (99.9 %).
- **Tests:** `check_explore_api.py` 105/105 (9 new); `check_explore_search` 50/50, `check_dim_labels` 16/16; `check_explore_features` still 41/2 (pre-existing).
