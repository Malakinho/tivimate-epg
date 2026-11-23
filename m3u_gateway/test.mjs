import test from "node:test";
import assert from "node:assert/strict";
import gateway, { canonicalName, createIndex, chooseId, rewriteLine, streamingRewriter } from "./src/index.mjs";

const EPG = "https://example.org/personal/epg.xml.gz";
const rows = [
  ["GR: MAGENTA SPORT 1 HD", "2415", "2415", "", "MagentaSport1.gr", "programm"],
  ["GR: MAGENTA SPORT 1 RAW", "2415", "2415", "", "MagentaSport1.gr", "programm"],
  ["GR: MAGENTA SPORT LEAGUE 1 HD", "2415", "2415", "", "MagentaSport1League.gr", "programm"],
  ["GR: MAGENTA SPORT 2 HD", "2415", "2415", "", "MagentaSport2.gr", "programm"],
  ["DE: DAZN BAR 1 HEVC", "509", "509", "DAZN.de", "DAZN1.de", "programm"],
  ["DE: DAZN BAR 2 HEVC", "509", "509", "DAZN2.de", "DAZN2.de", "programm"],
  ["GR: SAME NAME HD", "9", "9", "", "one.gr", "programm"],
  ["GR: SAME NAME SD", "9", "9", "", "two.gr", "programm"],
  ["GR: PLACEHOLDER 24/7", "11", "11", "", "always.123", "platzhalter"],
  ["GR: UNCERTAIN", "11", "11", "BadID", "", "pruefen"]
];
const index = createIndex(rows);

test("keeps Magenta Sport and Magenta Sport League separate", () => {
  const one = rewriteLine('#EXTINF:-1 tvg-id="",GR: MAGENTA SPORT 1 HD', index, EPG);
  const league = rewriteLine('#EXTINF:-1 tvg-id="",GR: MAGENTA SPORT LEAGUE 1 HD', index, EPG);
  assert.match(one, /tvg-id="MagentaSport1.gr"/);
  assert.match(league, /tvg-id="MagentaSport1League.gr"/);
  assert.equal(canonicalName("GR: MAGENTA SPORT 1 HD"), "MAGENTA SPORT 1");
  assert.notEqual(canonicalName("GR: MAGENTA SPORT LEAGUE 1 HD"),
                  canonicalName("GR: MAGENTA SPORT 1 HD"));
});

test("uses verified DAZN BAR mappings and not unrelated numbered variants", () => {
  assert.match(rewriteLine('#EXTINF:-1 tvg-id="DAZN.de",DE: DAZN BAR 1 HEVC', index, EPG),
               /tvg-id="DAZN1.de"/);
  assert.match(rewriteLine('#EXTINF:-1 tvg-id="DAZN2.de",DE: DAZN BAR 2 HEVC', index, EPG),
               /tvg-id="DAZN2.de"/);
  assert.equal(rewriteLine('#EXTINF:-1 tvg-id="DAZN.de",DE: DAZN BAR 3 HEVC', index, EPG),
               '#EXTINF:-1 tvg-id="DAZN.de",DE: DAZN BAR 3 HEVC');
});

test("does not invent IDs for ambiguous or uncertain matches", () => {
  const ambig = '#EXTINF:-1 tvg-id="",GR: SAME NAME UHD';
  assert.equal(rewriteLine(ambig, index, EPG), ambig);
  const uncertain = '#EXTINF:-1 tvg-id="",GR: UNCERTAIN';
  assert.equal(rewriteLine(uncertain, index, EPG), uncertain);
  assert.equal(chooseId(index.get("SAME NAME"), "", "GR: SAME NAME UHD"), "");
});

test("preserves stream URLs and refreshes XMLTV header", async () => {
  const src = '#EXTM3U x-tvg-url="https://old.invalid/xmltv.xml"\r\n' +
    '#EXTINF:-1 tvg-id="" group-title="Greek",GR: MAGENTA SPORT 2 HD\r\n' +
    'http://user:pass@provider.example/live/123\r\n' +
    '#EXTINF:-1 tvg-id="",GR: PLACEHOLDER 24/7\r\n' +
    'https://provider.example/stream/456';
  const encoder = new TextEncoder();
  const bytes = encoder.encode(src);
  const chunks = [bytes.slice(0, 3), bytes.slice(3, 21),
                  bytes.slice(21, 78), bytes.slice(78)];
  const body = new ReadableStream({
    start(controller) {
      for (const c of chunks) controller.enqueue(c);
      controller.close();
    }
  });
  const out = await new Response(streamingRewriter(body, index, EPG)).text();
  assert.match(out, /x-tvg-url="https:\/\/example.org\/personal\/epg.xml.gz"/);
  assert.match(out, /tvg-id="MagentaSport2.gr"/);
  assert.match(out, /tvg-id="always.123"/);
  assert.match(out, /http:\/\/user:pass@provider.example\/live\/123/);
  assert.match(out, /https:\/\/provider.example\/stream\/456/);
  assert.doesNotMatch(out, /old.invalid/);
});

test("requires private token before any provider fetch", async () => {
  const token = "q".repeat(48);
  const env = {
    ACCESS_TOKEN: token,
    DASHBOARD_URL: "https://example.org/dashboard.json",
    EPG_URL: EPG,
    UPSTREAM_M3U_URL: "https://provider.example/get.php?username=secret&password=secret"
  };
  const oldFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url) => {
    calls.push(url);
    return String(url).includes("dashboard.json")
      ? new Response(JSON.stringify({ rows }), { status: 200 })
      : new Response('#EXTM3U\n#EXTINF:-1 tvg-id="",GR: MAGENTA SPORT 1 HD\nhttps://provider.example/live/123\n');
  };
  try {
    const blocked = await gateway.fetch(
      new Request("https://worker.example/m3u/" + "bad".repeat(16) + "/playlist.m3u"), env);
    assert.equal(blocked.status, 404);
    assert.equal(calls.length, 0);
    const allowed = await gateway.fetch(
      new Request("https://worker.example/m3u/" + token + "/playlist.m3u"), env);
    assert.equal(allowed.status, 200);
    const text = await allowed.text();
    assert.match(text, /MagentaSport1.gr/);
    assert.equal(allowed.headers.get("Cache-Control"), "private, no-store, max-age=0");
    assert.equal(calls.length, 2);
    assert.doesNotMatch(text, /username=secret/);
  } finally {
    globalThis.fetch = oldFetch;
  }
});
