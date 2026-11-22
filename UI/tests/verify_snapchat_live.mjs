#!/usr/bin/env node
/**
 * Live verification of Snapchat Audience Sentiment integration.
 * Tests against running API server at http://localhost:8877.
 */
import assert from "node:assert";

const BASE = process.env.AUD_API || "http://localhost:8882";

async function fetchJson(url) {
  const res = await fetch(url);
  if (!res.ok) {
    throw new Error(`HTTP ${res.status} for ${url}: ${await res.text()}`);
  }
  return res.json();
}

let passed = 0;
let total = 0;

function test(desc, fn) {
  total++;
  try {
    fn();
    passed++;
    console.log(`  ✓ ${desc}`);
  } catch (err) {
    console.error(`  ✗ ${desc}`);
    console.error(`    ${err.message}`);
  }
}

async function main() {
  console.log("=== Snapchat Audience Sentiment Live Verification ===");
  console.log(`API Base: ${BASE}\n`);

  // 1. Unfiltered call (all platforms)
  console.log("1. All Platforms (Unfiltered)");
  const all = await fetchJson(`${BASE}/api/explore/audience`);
  
  test("available_platforms contains snapchat", () => {
    assert(all.available_platforms.includes("snapchat"), "snapchat missing from available_platforms");
  });

  test("platform_caveats contains snapchat caveat", () => {
    assert(all.platform_caveats && all.platform_caveats.snapchat, "snapchat caveat missing");
    assert(all.platform_caveats.snapchat.includes("20 comments"), "caveat doesn't mention 20 comments limit");
  });

  test("limits includes snap_cap = 20", () => {
    assert.strictEqual(all.limits.snap_cap, 20);
  });

  test("snapchat layer exists in layers dictionary", () => {
    assert(all.layers.snapchat, "layers.snapchat missing");
  });

  test("snapchat comment layer has scored comments", () => {
    const snap = all.layers.snapchat;
    assert(snap.comment.scored > 90000, `Expected >90000 scored comments, got ${snap.comment.scored}`);
  });

  test("snapchat reply layer has 0 rows / 0 scored (comment-only)", () => {
    const snap = all.layers.snapchat;
    assert.strictEqual(snap.reply.rows, 0);
    assert.strictEqual(snap.reply.scored, 0);
  });

  test("all layers comment scored equals sum of all platforms", () => {
    const sum = all.layers.youtube_video.comment.scored +
                all.layers.instagram.comment.scored +
                all.layers.linkedin.comment.scored +
                all.layers.snapchat.comment.scored;
    assert.strictEqual(all.layers.all.comment.scored, sum);
  });

  // 2. Snapchat isolated
  console.log("\n2. Snapchat Isolated");
  const snapOnly = await fetchJson(`${BASE}/api/explore/audience?platforms=snapchat`);

  test("filters.platforms is ['snapchat']", () => {
    assert.deepStrictEqual(snapOnly.filters.platforms, ["snapchat"]);
  });

  test("snapchat n_posts matches expected (~11,900)", () => {
    assert(snapOnly.n_posts >= 11000 && snapOnly.n_posts <= 13000, `n_posts=${snapOnly.n_posts}`);
  });

  test("snapchat post vs audience sentiment gaps exist (posts with post tone)", () => {
    const gaps = snapOnly.layers.snapchat.gaps.comment_vs_post;
    assert(gaps.n > 0, `Expected >0 posts with gap, got ${gaps.n}`);
  });

  test("snapchat top_gaps populated (negative or positive)", () => {
    const tg = snapOnly.top_gaps;
    assert(tg.negative.length > 0 || tg.positive.length > 0, "No divergent posts found");
  });

  // 3. Existing platforms regression
  console.log("\n3. Existing Platforms Regression");
  const ytOnly = await fetchJson(`${BASE}/api/explore/audience?platforms=youtube_video`);
  test("YouTube comment scored unchanged (~270k)", () => {
    assert(ytOnly.layers.youtube_video.comment.scored >= 270000, `yt scored=${ytOnly.layers.youtube_video.comment.scored}`);
  });

  const igOnly = await fetchJson(`${BASE}/api/explore/audience?platforms=instagram`);
  test("Instagram comment scored unchanged (~40k)", () => {
    assert(igOnly.layers.instagram.comment.scored >= 40000, `ig scored=${igOnly.layers.instagram.comment.scored}`);
  });

  const liOnly = await fetchJson(`${BASE}/api/explore/audience?platforms=linkedin`);
  test("LinkedIn comment scored unchanged (~4.7k)", () => {
    assert(liOnly.layers.linkedin.comment.scored >= 4700, `li scored=${liOnly.layers.linkedin.comment.scored}`);
  });

  console.log(`\n========================================`);
  console.log(`Passed: ${passed} / ${total} tests`);
  console.log(`========================================`);
  if (passed !== total) {
    process.exit(1);
  }
}

main().catch(err => {
  console.error("Fatal error:", err);
  process.exit(1);
});
