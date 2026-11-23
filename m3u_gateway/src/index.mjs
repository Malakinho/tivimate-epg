/**
 * Private M3U gateway. Stream directly from provider and correct XMLTV IDs.
 * Never put provider M3U URLs, tokens or generated playlists on GitHub Pages.
 */
const NAME_PREFIX = /^(?:DE GO|DE|GR|PRIME|JOYN|WOW|SKYGO|FR|IT|PL|HR|NL|US|UK)\s*[:\-]\s*/i;
const QUALITY = /\b(?:RAW|HEVC|H265|H264|SD|FHD|HD|UHD|4K|8K|720P|1080P|2160P|50FPS|60FPS|30FPS|BACKUP|BK|LOW|MOBILE|MOBIL|STREAM|HDR)\b/gi;
const MAX_LINE_LENGTH = 1024 * 1024;
let cachedIndex;
let indexExpires = 0;

export function canonicalName(text) {
  let s = String(text || "").normalize("NFKC").toUpperCase().trim();
  s = s.replace(NAME_PREFIX, "");
  s = s.replace(/\([^)]*(?:WEB|SAT|KABEL|MOBIL|BK|BACKUP|720|1080|2160|HEVC)[^)]*\)/g, " ");
  s = s.replace(QUALITY, " ");
  s = s.replace(/[◉●◆★☆■▣⭐]/g, " ");
  s = s.replace(/[^A-Z0-9ÄÖÜΑ-ΩΆΈΉΊΌΎΏ&+]/g, " ");
  s = s.replace(/\bDAZN\s+([12])\b/g, "DAZN$1");
  s = s.replace(/\bZDF\s+INFO\b/g, "ZDFINFO");
  s = s.replace(/\bZDF\s+NEO\b/g, "ZDFNEO");
  return s.replace(/\s+/g, " ").trim();
}

function splitInfo(line) {
  let quoted = false;
  for (let i = 0; i < line.length; i++) {
    if (line[i] === '"') quoted = !quoted;
    else if (line[i] === "," && !quoted) {
      return [line.slice(0, i), line.slice(i + 1)];
    }
  }
  return [line, ""];
}

function getAttribute(info, name) {
  const match = info.match(new RegExp("(?:^|\\s)" + name + "\\s*=\\s*\"([^\"]*)\"", "i"));
  return match ? match[1] : "";
}

function replaceId(info, id) {
  const safe = id.replace(/"/g, "&quot;");
  const pattern = /(^|\s)tvg-id\s*=\s*"[^"]*"/i;
  return pattern.test(info)
    ? info.replace(pattern, (_match, whitespace) => whitespace + 'tvg-id="' + safe + '"')
    : info.replace(/^#EXTINF:([^\s,]*)/i,
      (_match, duration) => '#EXTINF:' + duration + ' tvg-id="' + safe + '"');
}

export function createIndex(rows) {
  const names = new Map();
  for (const row of rows || []) {
    if (!Array.isArray(row) || !["programm", "platzhalter"].includes(row[5]) ||
        !row[4] || !row[0]) continue;
    const key = canonicalName(row[0]);
    if (!key) continue;
    if (!names.has(key)) names.set(key, []);
    names.get(key).push({
      id: String(row[4]),
      provider: String(row[3] || ""),
      name: String(row[0])
    });
  }
  return names;
}

export function chooseId(candidates, providerId, name) {
  if (!candidates || candidates.length === 0) return "";
  let pool = candidates;
  if (providerId) {
    const id = providerId.toLowerCase();
    const exact = pool.filter(item =>
      item.provider.toLowerCase() === id || item.id.toLowerCase() === id);
    if (exact.length) pool = exact;
  }
  const exactName = String(name || "").trim().toLocaleLowerCase("de");
  const named = pool.filter(item =>
    item.name.trim().toLocaleLowerCase("de") === exactName);
  if (named.length) pool = named;
  const unique = new Set(pool.map(item => item.id.toLowerCase()));
  return unique.size === 1 ? pool[0].id : "";
}

function formatHeader(line, epgUrl) {
  const withoutOld = line.replace(/\s+(?:x-tvg-url|url-tvg)\s*=\s*"[^"]*"/ig, "");
  return withoutOld + ' x-tvg-url="' + epgUrl + '" url-tvg="' + epgUrl + '"';
}

export function rewriteLine(line, index, epgUrl) {
  if (line.startsWith("#EXTM3U")) return formatHeader(line, epgUrl);
  if (!line.startsWith("#EXTINF:")) return line;
  const [info, title] = splitInfo(line);
  const provider = getAttribute(info, "tvg-id");
  const tvgName = getAttribute(info, "tvg-name");
  let selected = chooseId(index.get(canonicalName(title)), provider, title);
  if (!selected && tvgName) {
    selected = chooseId(index.get(canonicalName(tvgName)), provider, tvgName);
  }
  if (!selected || selected === provider) return line;
  return replaceId(info, selected) + (title ? "," + title : "");
}

export function streamingRewriter(body, index, epgUrl) {
  const decoder = new TextDecoder();
  const encoder = new TextEncoder();
  let pending = "";
  let firstLine = true;
  return body.pipeThrough(new TransformStream({
    transform(chunk, controller) {
      pending += decoder.decode(chunk, { stream: true });
      while (true) {
        const end = pending.indexOf("\n");
        if (end < 0) break;
        let line = pending.slice(0, end);
        pending = pending.slice(end + 1);
        if (line.endsWith("\r")) line = line.slice(0, -1);
        if (firstLine) {
          firstLine = false;
          if (!line.startsWith("#EXTM3U")) {
            controller.enqueue(encoder.encode(
              '#EXTM3U x-tvg-url="' + epgUrl + '" url-tvg="' + epgUrl + '"\n'));
          }
        }
        controller.enqueue(encoder.encode(rewriteLine(line, index, epgUrl) + "\n"));
      }
      if (pending.length > MAX_LINE_LENGTH) {
        throw new Error("Upstream M3U line exceeds safe limit");
      }
    },
    flush(controller) {
      pending += decoder.decode();
      if (pending.length > MAX_LINE_LENGTH) throw new Error("Unsafe line length");
      if (firstLine && !pending.startsWith("#EXTM3U")) {
        controller.enqueue(encoder.encode(
          '#EXTM3U x-tvg-url="' + epgUrl + '" url-tvg="' + epgUrl + '"\n'));
      }
      if (pending) controller.enqueue(encoder.encode(rewriteLine(pending, index, epgUrl)));
    }
  }));
}

function secureCompare(a, b) {
  if (typeof a !== "string" || typeof b !== "string") return false;
  if (a.length > 200 || b.length < 32 || b.length > 200) return false;
  let diff = a.length ^ b.length;
  for (let i = 0; i < b.length; i++) {
    diff |= (a.charCodeAt(i) || 0) ^ b.charCodeAt(i);
  }
  return diff === 0;
}

async function getIndex(env) {
  const now = Date.now();
  if (cachedIndex && now < indexExpires) return cachedIndex;
  const url = env.DASHBOARD_URL || "https://malakinho.github.io/tivimate-epg/dashboard.json";
  const result = await fetch(url, { headers: { "Accept": "application/json" }, cache: "no-store" });
  if (!result.ok) throw new Error("Public EPG mapping not available");
  const doc = await result.json();
  if (!Array.isArray(doc.rows)) throw new Error("Invalid public EPG mapping");
  const prepared = createIndex(doc.rows);
  if (prepared.size < 1) throw new Error("Public EPG mapping is empty");
  cachedIndex = prepared;
  indexExpires = now + 5 * 60 * 1000;
  return prepared;
}

function errorResponse(status, message) {
  return new Response(message, {
    status,
    headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store",
               "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff" }
  });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const matched = url.pathname.match(/^\/m3u\/([A-Za-z0-9_-]{32,100})\/playlist\.m3u$/);
    if (!matched || !["GET", "HEAD"].includes(request.method) ||
        !secureCompare(matched[1], env.ACCESS_TOKEN)) {
      return errorResponse(404, "Not found");
    }
    if (!env.UPSTREAM_M3U_URL || !env.EPG_URL) {
      return errorResponse(503, "M3U gateway is not configured");
    }
    let upstreamUrl;
    let epgUrl;
    try {
      upstreamUrl = new URL(env.UPSTREAM_M3U_URL);
      epgUrl = new URL(env.EPG_URL);
      if (!["http:", "https:"].includes(upstreamUrl.protocol) ||
          epgUrl.protocol !== "https:" || /["\r\n]/.test(env.EPG_URL)) {
        throw new Error("Bad configuration");
      }
    } catch (_) {
      return errorResponse(503, "M3U gateway configuration is invalid");
    }
    if (request.method === "HEAD") {
      return new Response(null, { status: 200, headers: {
        "Content-Type": "application/x-mpegURL; charset=utf-8",
        "Cache-Control": "private, no-store"
      } });
    }
    try {
      const index = await getIndex(env);
      const supplier = await fetch(upstreamUrl.href, {
        headers: { "Accept": "application/x-mpegURL, text/plain, */*",
                   "Accept-Encoding": "identity" },
        cache: "no-store",
        redirect: "follow"
      });
      if (!supplier.ok || !supplier.body) {
        return errorResponse(502, "Provider playlist is temporarily unavailable");
      }
      const stream = streamingRewriter(supplier.body, index, epgUrl.href);
      return new Response(stream, {
        status: 200,
        headers: {
          "Content-Type": "application/x-mpegURL; charset=utf-8",
          "Content-Disposition": 'inline; filename="playlist.m3u"',
          "Cache-Control": "private, no-store, max-age=0",
          "Referrer-Policy": "no-referrer",
          "X-Content-Type-Options": "nosniff"
        }
      });
    } catch (_) {
      return errorResponse(502, "M3U playlist could not be generated");
    }
  }
};
