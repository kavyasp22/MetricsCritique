# Comment-sentiment quality layer (stage 51)

Adds quality flags next to the **stored** comment sentiment scores for YouTube, Instagram, LinkedIn and Snapchat. No score, model,
or existing table is changed; no GPU, no production access. Code: `scripts/lib/sentiment_quality.py`, `scripts/51_sentiment_quality.py`,
`stats_v2.wilson_interval`. Output: `data/sentiment_quality.duckdb` (`comment_quality_v1`, `sentiment_quality_rollup_v1`, `sentiment_quality_meta_v1`).
Dashboard: Explore > Sentiment & Audience > Audience Sentiment, section "Sentiment quality" (served by `UI/audience_view.quality_block`).

## Rules (all provisional)
| Rule | Definition |
|---|---|
| `NO_TEXT` (excluded) | empty text, fewer than 2 letters, punctuation only, or URL only. Never mapped to neutral. |
| `is_emoji_only` (kept, tagged) | fewer than 2 letters and at least one non-ASCII symbol. Scored normally: the model reads them sensibly (hearts / thumbs-up positive, crying negative). |
| Confidence tier | max class probability: `confident` >= 0.60, `uncertain` 0.45-0.60, `weak` < 0.45. Rows are tiered, not deleted. |

A hard 0.60 gate was rejected: it would drop 52-54% of Instagram and Snapchat comments and about 80% of their neutral ones, biasing the result toward the extremes.

## Bases (net sentiment = 100 x (positive share - negative share))
- **A** all scored rows (the number the card showed before). **B** headline = A minus `NO_TEXT`. **C** confident rows only (sensitivity check).
- **T** rows with words (B minus emoji-only): shows what emoji reactions add. Positive / negative share carry a Wilson 95% interval.
- Statistics are NULL (not 0) below 30 rows; counts are always kept. The dashboard sums monthly counts for a date range (month grain) and recomputes shares.

## Results (all dates, net sentiment)
| | A | B | T | C | emoji-only rows |
|---|---|---|---|---|---|
| Snapchat comments | +24.3 | +24.4 | +9.6 | +37.6 | 30,506 |
| Instagram comments | +0.7 | +0.7 | -12.2 | +3.0 | 8,251 |
| YouTube comments | -12.6 | -12.6 | -16.6 | -19.1 | 12,727 |
| YouTube replies | -35.0 | -35.1 | -37.1 | -49.8 | 3,869 |
| LinkedIn comments | +45.0 | +45.1 | +44.5 | +59.6 | 106 |

## Robust trend statistics (added 2026-11-27)
Library (`stats_v2`, 81/81 self-checks): `theil_sen` (median pairwise slope with a Sen interval; checked against `scipy.stats.theilslopes`),
`mann_kendall` (Hamed-Rao autocorrelation-corrected variance, ties handled, never narrower than the naive test), `prior_window_drift_robust` (candidate, see below).

**Weekly sentiment trend (adopted, dashboard "Weekly sentiment trend").** Stage 51 also writes `sentiment_quality_weekly_v1` (platform x layer x week, headline-base counts, weeks by the
comment's own date). `UI/audience_view.trend_block` computes, for the chosen date range, the Theil-Sen slope (net points per week, 95% interval) and the Mann-Kendall p.
Guards: weeks need >= 30 scored comments; a trend needs >= 8 such weeks covering >= 70% of the weeks between the first and the last, otherwise NULL (never 0) with the reason shown.
The coverage rule matters: YouTube comments run back to 2015 with only 45 usable weeks (14% coverage), so its all-dates trend is NULL until a shorter range is chosen.
Current readings (all dates): Snapchat comments rising about +0.36 net points/week (p < 0.001); Instagram and LinkedIn: no clear trend (18 weeks).

**Tone-drift z (NOT replaced).** `scripts/review/robust_drift_eval.py` (read-only) reproduces the stored `tone_drift_z` exactly (3,347 / 3,347 cells-days) and compares the shipped Student-t mean/sd rule with a median/MAD candidate on the real topic x platform series.
On shuffled copies of each series (no real drift, so every flag is false) the shipped rule flags 4.5% of days, median/MAD 6.6%, against a nominal 5%. The pasted claim that z-scores are too fragile is not supported on our data:
median/MAD is the worse calibrated rule here, as the original check in `prior_window_drift` found. `tone_drift_z` stays as is; changing it would be a spec change.

## Sarcasm cue flag (added 2026-11-27)
`scripts/lib/sarcasm_cues.py` -> `comment_quality_v1.sarcasm_cue` (likely / possible / none) and rollup counts (`n_pos`, `n_sarc_likely`, `n_pos_sarc_likely`, `n_pos_sarc_possible`).
A FLAG ONLY: no label or net is changed (test-guarded). Read it next to the positive share, since only positive-labelled comments can be hurt by sarcasm.
- **likely:** a mocking idiom ("nice try", "yeah right", "good luck with that", "sure buddy", "what a surprise", "best comeback", ...) or praise + eye-roll / clown / upside-down / smirk / facepalm emoji.
- **possible:** praise + laughing emoji (laughter is mostly sincere; weak evidence, not shown as a headline).
- **Judged out:** "what a great/amazing/wonderful ...", "wah kya", "as if". In a first read of 36 flagged positive-labelled comments these were overwhelmingly sincere praise (every LinkedIn hit), precision about 25%.
- **Precision (one analyst, AI-assisted, exploratory, not human validation):** revised "likely" about 16 of 21 positive-labelled hits sarcastic (about 76%; 3 sincere, 2 ambiguous); "possible" about 7-8 of 24 (about 30%).
- **Recall is not measured** and is low by design (narrow rules): sarcasm without these cues is missed, so the count is a lower bound. Instagram/Snapchat Roman-Urdu/Hindi sarcasm is largely uncovered; YouTube is read on the English translation.
- **Recall is very low (analyst read of 96 unflagged positive comments, 2026-11-27):** about 2% [0.6-7.3] of unflagged positives read as sarcastic, about 3,800 comments (range 1,000-13,000) against about 87 caught by `likely`. Effect on net sentiment if that holds: about -0.7 to -2.5 points per platform (upper bound -2 to -9). The flag is a precise lower bound, not an estimate of sarcasm.
- Open: a human-judged sample with both flagged and unflagged comments to get recall; the laughing-emoji-only comments that the model labels neutral are not touched by this flag.

## Analyst review (2026-11-27, exploratory; human validation still pending)
308 stratified comments read blind to model output (`/home/hariramanan/kavya/validation/sentiment_quality_eval/REPORT.md`; blind workbook `human_validation/Sentiment_Quality_Human_Validation.xlsx`, key and scorer alongside).
- Emoji-only: model agrees with the reader on clear-meaning emoji 88% [73-95] (n=33); laughing-only emoji are undecidable from text (16 of 19 ambiguous ones labelled neutral). Supports keeping them scored and tagged.
- Confidence tiers (words-only): label agrees with reader 86% confident, 66% uncertain, 48% weak (n=29/29/25, wide intervals). Supports tiering over a hard 0.60 gate.
- Sarcasm: `likely` precision 72% [49-88] (n=18), `possible` 32%, recall very low (see above).
- Open: YouTube (read on the translation) was lowest at 52% (n=21); a larger YouTube sample is needed.

## Verdict: PARTIAL
Checked: base A equals the stored labels on every platform and layer; C is a subset of B, B of A; no scored row lacks a probability;
tests pass. **Not checked:** no hand-judged sample of the emoji-only group or of the thresholds. Known weak spot: the model labels most
laughing-face-only comments neutral (88%), though the emoji is ambiguous (joy or mockery). YouTube comments are scored on the translated
text, which dropped the emoji in about 0.8% of comments (not corrected). Post-level scores are unchanged (`post_scores` has `tone_score` only;
a post-level confidence or neutral class would need a GPU re-score, deferred). Language routing is not built yet. The weekly trend is descriptive of collected comments (per-post collection caps apply) and has not been checked against a judged sample of weeks.
