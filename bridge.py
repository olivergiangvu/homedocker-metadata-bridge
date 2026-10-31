#!/usr/bin/env python3
"""HomeDocker Metadata Bridge: read-only Stremio addon proxy/enricher.

AIOStreams watch_state stays on AIOMetadata SH. This service NEVER offers
watch_state, writes to an upstream, or changes a video's playable ID.
"""
import copy
import difflib
import json
import logging
import os
import re
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

LOG = logging.getLogger("metadata-bridge")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(),
                    format="%(asctime)s %(levelname)s %(message)s")

SH = os.environ.get("SH_MANIFEST_URL", "").strip()
SH2 = os.environ.get("SH2_MANIFEST_URL", "").strip()
HOST = os.getenv("LISTEN_HOST", "127.0.0.1")
PORT = int(os.getenv("LISTEN_PORT", "18123"))
TIMEOUT = float(os.getenv("UPSTREAM_TIMEOUT", "7"))
MAX_CACHE = max(32, min(512, int(os.getenv("CACHE_ITEMS", "128"))))
TTL_META = max(30, int(os.getenv("META_TTL_SECONDS", "1800")))
TTL_SEARCH = max(15, int(os.getenv("SEARCH_TTL_SECONDS", "300")))
MAX_BODY = 4 * 1024 * 1024
USER_AGENT = "HomeDockerMetadataBridge/0.1"
OVERRIDES_FILE = Path(os.getenv("OVERRIDES_FILE", "/app/overrides.json"))


def validated_base(manifest_url):
    if not manifest_url:
        raise ValueError("SH_MANIFEST_URL and SH2_MANIFEST_URL must both be set")
    u = urllib.parse.urlsplit(manifest_url)
    if u.scheme not in ("http", "https") or not u.netloc or not u.path.endswith("/manifest.json"):
        raise ValueError("Upstream must be HTTP(S) URL ending in /manifest.json")
    if u.username or u.password:
        raise ValueError("Credentials must not be embedded in URL authority")
    return manifest_url.rsplit("/manifest.json", 1)[0]

BASES = {"sh": validated_base(SH), "tvdb": validated_base(SH2)}


class Cache:
    def __init__(self):
        self.lock = threading.Lock()
        self.data = OrderedDict()

    def get(self, key):
        with self.lock:
            item = self.data.get(key)
            if item and item[0] > time.monotonic():
                self.data.move_to_end(key)
                return copy.deepcopy(item[1])
            self.data.pop(key, None)
        return None

    def put(self, key, value, ttl):
        with self.lock:
            self.data[key] = (time.monotonic() + ttl, copy.deepcopy(value))
            self.data.move_to_end(key)
            while len(self.data) > MAX_CACHE:
                self.data.popitem(last=False)

CACHE = Cache()


def request_json(provider, path, query=""):
    """GET upstream only. No credentials printed in errors or logs."""
    key = (provider, path, query)
    cached = CACHE.get(key)
    if cached is not None:
        return cached
    url = BASES[provider] + "/" + path.lstrip("/")
    if query:
        url += "?" + query
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
            if response.status != 200:
                return None
            body = response.read(MAX_BODY + 1)
            if len(body) > MAX_BODY:
                LOG.warning("Upstream body too large: %s", provider)
                return None
            result = json.loads(body)
            if not isinstance(result, dict):
                return None
            ttl = TTL_SEARCH if path.startswith("catalog/") else TTL_META
            CACHE.put(key, result, ttl)
            return result
    except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as ex:
        # Deliberately avoid exception text: some HTTP libraries include tokenized URLs.
        LOG.warning("Upstream unavailable/invalid: %s, route=%s, error=%s", provider,
                    path.split("/")[0], ex.__class__.__name__)
        return None


def normalize(value):
    if not isinstance(value, str):
        return ""
    value = unicodedata.normalize("NFKD", value).casefold()
    return " ".join(re.findall(r"[\w]+", value))


def ids_of(meta):
    """Canonical provider IDs from AIOMetadata v3.8.2 and Stremio extensions.

    AIOMetadata normally emits `_tmdbId`, `_tvdbId`, `_imdbId`, `imdb_id`,
    plus a provider-specific `meta.id`; it does not require `meta.ids`.
    Reject contradictory IDs *within* a single response instead of inferring
    identity from an ambiguous record.
    """
    if not isinstance(meta, dict):
        return {}
    embedded = meta.get("ids")
    embedded = embedded if isinstance(embedded, dict) else {}
    raw_id = meta.get("id")
    raw_id = raw_id.strip() if isinstance(raw_id, str) else ""
    sources = {
        "imdb": (embedded.get("imdb"), meta.get("imdb_id"), meta.get("_imdbId"),
                 raw_id if raw_id.startswith("tt") else None),
        "tmdb": (embedded.get("tmdb"), meta.get("_tmdbId"),
                 raw_id.removeprefix("tmdb:") if raw_id.startswith("tmdb:") else None),
        "tvdb": (embedded.get("tvdb"), meta.get("_tvdbId"),
                 raw_id.removeprefix("tvdb:") if raw_id.startswith("tvdb:") else None),
    }
    resolved = {}
    for provider, candidates in sources.items():
        valid = set()
        for value in candidates:
            if value is None or value == "":
                continue
            value = str(value).strip()
            if provider in ("tmdb", "tvdb"):
                value = value.removeprefix(provider + ":")
                if not re.fullmatch(r"[0-9]+", value):
                    continue
                value = str(int(value))
            elif not re.fullmatch(r"tt[0-9]+", value):
                continue
            valid.add(value)
        if len(valid) > 1:
            # A conflict from any one provider makes the whole meta unsafe.
            return {}
        if valid:
            resolved[provider] = next(iter(valid))
    return resolved


def strongly_same_series(a, b):
    ia, ib = ids_of(a), ids_of(b)
    matches = [k for k in ia.keys() & ib.keys() if ia[k] == ib[k]]
    conflicts = [k for k in ia.keys() & ib.keys() if ia[k] != ib[k]]
    return bool(matches) and not conflicts


def extra_ids(meta):
    ids = ids_of(meta)
    variants = []
    for key, prefix in (("imdb", ""), ("tmdb", "tmdb:"), ("tvdb", "tvdb:")):
        v = ids.get(key)
        if not v:
            continue
        if key == "imdb" and not v.startswith("tt"):
            continue
        variants.append(prefix + v)
    return variants


def get_metadata(provider, media_type, media_id):
    path = "meta/{}/{}.json".format(urllib.parse.quote(media_type, safe=""),
                                    urllib.parse.quote(media_id, safe=":-_."))
    payload = request_json(provider, path)
    meta = payload.get("meta") if payload else None
    return meta if isinstance(meta, dict) and meta.get("id") else None


def probe_other(provider, media_type, requested_id, reference):
    other = get_metadata(provider, media_type, requested_id)
    if other and strongly_same_series(reference, other):
        return other
    # Only consult explicitly supplied cross-provider IDs, never title guesswork.
    for media_id in extra_ids(reference):
        if media_id == requested_id:
            continue
        candidate = get_metadata(provider, media_type, media_id)
        if candidate and strongly_same_series(reference, candidate):
            return candidate
    return None


def video_title(v):
    return normalize(v.get("title") or v.get("name") or "")


def episode_pair(v):
    try:
        return int(v.get("season")), int(v.get("episode"))
    except (TypeError, ValueError):
        return None


def aired(v):
    s = v.get("released") or v.get("airDate") or v.get("air_date") or ""
    return str(s)[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", str(s)) else ""


def strong_episode_match(v, w, same_layout):
    va, vb = ids_of(v), ids_of(w)
    # Episode IMDb and TVDB IDs are strong matches; TMDB episode IDs as well.
    if va and vb:
        common = va.keys() & vb.keys()
        if any(va[k] != vb[k] for k in common):
            return False
        if any(va[k] == vb[k] for k in common):
            return True
    date_a, date_b = aired(v), aired(w)
    ta, tb = video_title(v), video_title(w)
    title_match = bool(ta and tb and difflib.SequenceMatcher(None, ta, tb).ratio() >= 0.86)
    if date_a and date_b:
        return date_a == date_b and (title_match or episode_pair(v) == episode_pair(w))
    if same_layout and episode_pair(v) == episode_pair(w) and title_match:
        return True
    return False


def same_layout(a, b):
    """Only enable season/episode matching when distributions are identical."""
    def dist(m):
        count = {}
        for v in m.get("videos") or []:
            p = episode_pair(v)
            if p:
                count[p[0]] = count.get(p[0], 0) + 1
        return count
    x, y = dist(a), dist(b)
    return bool(x) and x == y


def overlay_episodes(target, tmdb_source):
    videos = target.get("videos") or []
    additions = tmdb_source.get("videos") or []
    if not isinstance(videos, list) or not isinstance(additions, list):
        return 0
    aligned = same_layout(target, tmdb_source)
    taken = set()
    enriched = 0
    for v in videos:
        if not isinstance(v, dict):
            continue
        candidates = [(j, w) for j, w in enumerate(additions) if isinstance(w, dict) and
                      j not in taken and strong_episode_match(v, w, aligned)]
        if len(candidates) != 1:
            continue
        j, w = candidates[0]
        taken.add(j)
        # Never replace video.id, season or episode (tracking + stream identity).
        changed = False
        for dest, src in (("thumbnail", "thumbnail"), ("overview", "overview")):
            data = w.get(src)
            if data and data != v.get(dest):
                v[dest] = data
                changed = True
        if changed:
            enriched += 1
    return enriched


def read_overrides():
    try:
        obj = json.loads(OVERRIDES_FILE.read_text(encoding="utf-8"))
        if not isinstance(obj, dict):
            return {}
        return obj
    except (OSError, ValueError):
        return {}


def pick_series_meta(media_id, sh, tvdb):
    """Default SH. Use TVDB only when SH fails, or explicit verified override."""
    rules = read_overrides()
    prefer_tvdb = set(rules.get("prefer_tvdb_meta_ids") or [])
    if sh and tvdb and strongly_same_series(sh, tvdb) and media_id in prefer_tvdb:
        return copy.deepcopy(tvdb), "tvdb"
    if sh:
        return copy.deepcopy(sh), "sh"
    if tvdb:
        return copy.deepcopy(tvdb), "tvdb"
    return None, "none"


def series_meta(media_type, media_id):
    sh = get_metadata("sh", media_type, media_id)
    tvdb = get_metadata("tvdb", media_type, media_id)
    if sh and not tvdb:
        tvdb = probe_other("tvdb", media_type, media_id, sh)
    if tvdb and not sh:
        sh = probe_other("sh", media_type, media_id, tvdb)
    # Refuse any series-level mixing if identity is not proven.
    if sh and tvdb and not strongly_same_series(sh, tvdb):
        LOG.info("Identity mismatch, leaving sources unmerged (id redacted)")
        tvdb = None
    result, origin = pick_series_meta(media_id, sh, tvdb)
    if result is None:
        return None, "none", 0
    count = 0
    if sh and tvdb and strongly_same_series(sh, tvdb):
        # TMDB (SH) is preferred for artwork/descriptions for matched TVDB episodes.
        if origin == "tvdb":
            count = overlay_episodes(result, sh)
            for field in ("poster", "background", "logo", "description"):
                if sh.get(field):
                    result[field] = sh[field]
        else:
            # SH is already TMDB; TVDB can fill only missing episode fields.
            backup = copy.deepcopy(result)
            overlay_episodes(backup, tvdb)
            for v, w in zip(result.get("videos") or [], backup.get("videos") or []):
                for field in ("thumbnail", "overview"):
                    if not v.get(field) and w.get(field):
                        v[field] = w[field]
                        count += 1
        # Keep aliases as an optional, non-destructive extension.
        if isinstance(sh.get("aliases"), list) or isinstance(tvdb.get("aliases"), list):
            a = list(result.get("aliases") or [])
            for alias in list(sh.get("aliases") or []) + list(tvdb.get("aliases") or []):
                if alias not in a:
                    a.append(alias)
            result["aliases"] = a[:100]
    LOG.info("Series meta served provider=%s enriched_fields=%d", origin, count)
    return result, origin, count


def manifest():
    sh = request_json("sh", "manifest.json") or {}
    tvdb = request_json("tvdb", "manifest.json") or {}
    types = []
    for t in (sh.get("types") or []) + (tvdb.get("types") or []):
        if isinstance(t, str) and t not in types:
            types.append(t)
    for t in ("movie", "series", "anime"):
        if t not in types:
            types.append(t)
    return {
        "id": "com.homedocker.metadata-bridge",
        "version": "0.1.0",
        "name": "HomeDocker Hybrid Metadata",
        "description": "SH tracking stays separate; TMDB + TVDB series metadata and search",
        "resources": [{"name": "meta", "types": types}, "catalog"],
        "types": types,
        "catalogs": [{"type": "series", "id": "unified-series-search",
                      "name": "Hybrid Series Search",
                      "extra": [{"name": "search", "isRequired": True}]}],
        "behaviorHints": {"configurable": False},
    }


def search(media_type, value):
    rules = read_overrides()
    terms = [value]
    aliasmap = rules.get("search_aliases") or {}
    if isinstance(aliasmap, dict):
        terms += [x for x in aliasmap.get(normalize(value), []) if isinstance(x, str)][:3]
    collected = []
    manifests = {p: request_json(p, "manifest.json") or {} for p in BASES}
    by_provider = {}
    for provider, data in manifests.items():
        catalogs = [c for c in data.get("catalogs") or []
                    if isinstance(c, dict) and c.get("type") == media_type and
                    isinstance(c.get("id"), str) and
                    any(isinstance(e, dict) and e.get("name") == "search"
                        for e in c.get("extra") or [])]
        # Prefer dedicated Series Search rather than limiting on the first 12
        # catalogs, which could otherwise exclude SH2 entirely.
        catalogs.sort(key=lambda c: ("search" not in (c.get("name", "") + " " + c["id"]).lower(),
                                     c["id"]))
        choices = []
        for c in catalogs[:2]:
            for term in terms:
                encoded = "search=" + urllib.parse.quote(term, safe="")
                path = "catalog/{}/{}/{}.json".format(
                    urllib.parse.quote(media_type, safe=""),
                    urllib.parse.quote(c["id"], safe="-_.'"), encoded)
                choices.append((provider, path))
        by_provider[provider] = choices
    # Keep balanced upstream fanout with a hard cap of 12 requests.
    tasks = []
    for i in range(max((len(v) for v in by_provider.values()), default=0)):
        for provider in ("sh", "tvdb"):
            group = by_provider.get(provider, [])
            if i < len(group):
                tasks.append(group[i])
            if len(tasks) >= 12:
                break
        if len(tasks) >= 12:
            break
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda task: request_json(task[0], task[1]), tasks))
    for payload in responses:
        if isinstance(payload, dict) and isinstance(payload.get("metas"), list):
            collected.append(payload["metas"])
    # Interleave (rather than SH always exhausting the response first).
    unique = set()
    output = []
    for i in range(max((len(x) for x in collected), default=0)):
        for group in collected:
            if i >= len(group) or not isinstance(group[i], dict):
                continue
            item = group[i]
            key = (item.get("type", media_type), item.get("id"))
            if not key[1] or key in unique:
                continue
            unique.add(key)
            output.append(item)
            if len(output) >= 100:
                return output
    return output


class Handler(BaseHTTPRequestHandler):
    server_version = "HomeDockerMetadataBridge/0.1"
    def log_message(self, fmt, *args):
        # Never log UUID URLs, media IDs or search strings.
        return

    def send_json(self, status, obj):
        data = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "public, max-age=60")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        route = urllib.parse.urlsplit(self.path).path
        if route == "/health":
            return self.send_json(200, {"status": "ok", "service": "metadata-bridge", "version": "0.1.0"})
        if route == "/manifest.json":
            return self.send_json(200, manifest())
        match = re.fullmatch(r"/meta/([^/]+)/(.+)\.json", route)
        if match:
            media_type = urllib.parse.unquote(match[1])
            media_id = urllib.parse.unquote(match[2])
            if len(media_id) > 240 or not re.fullmatch(r"[a-zA-Z0-9:_.,-]+", media_id):
                return self.send_json(400, {"error": "invalid media ID"})
            if media_type != "series":
                # Exact upstream JSON passthrough, not just meta fields.
                path = "meta/{}/{}.json".format(
                    urllib.parse.quote(media_type, safe=""), urllib.parse.quote(media_id, safe=":-_."))
                payload = request_json("sh", path)
                return self.send_json(200 if payload else 404, payload or {"error": "not found"})
            value, _, _ = series_meta(media_type, media_id)
            return self.send_json(200 if value else 404, {"meta": value} if value else {"error": "not found"})
        match = re.fullmatch(r"/catalog/series/unified-series-search/(.+)\.json", route)
        if match:
            extra = urllib.parse.unquote(match[1])
            # Standard Stremio route: /catalog/series/id/search=TERM.json
            params = urllib.parse.parse_qs(extra)
            term = (params.get("search") or [""])[0]
            if not term or len(term) > 160:
                return self.send_json(200, {"metas": []})
            return self.send_json(200, {"metas": search("series", term)})
        if route == "/catalog/series/unified-series-search.json":
            return self.send_json(200, {"metas": []})
        return self.send_json(404, {"error": "not found"})


def main():
    # No outgoing writes, no volume data mounts. Both upstreams must be reachable.
    for provider in BASES:
        data = request_json(provider, "manifest.json")
        if not data or not isinstance(data.get("resources"), list):
            LOG.error("Cannot start: %s manifest not reachable/valid", provider)
            sys.exit(2)
    LOG.info("Ready host=%s port=%s, SH watch_state untouched", HOST, PORT)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever(poll_interval=0.5)


if __name__ == "__main__":
    main()
