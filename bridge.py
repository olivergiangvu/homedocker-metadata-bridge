#!/usr/bin/env python3
"""HomeDocker Metadata Bridge: read-only Stremio addon proxy/enricher.

AIOStreams watch_state stays on AIOMetadata SH. This service NEVER offers
watch_state, writes to an upstream, or changes a video's playable ID.
"""
import base64
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
CATALOG_PASSTHROUGH_ENABLED = os.getenv("CATALOG_PASSTHROUGH_ENABLED", "0").lower() in ("1", "true", "yes")
CHINESE_CATALOG_ENGLISH_META_ENABLED = os.getenv("CHINESE_CATALOG_ENGLISH_META_ENABLED", "0").lower() in ("1", "true", "yes")
CATALOG_ENRICH_LIMIT = max(0, min(24, int(os.getenv("CATALOG_ENRICH_LIMIT", "12"))))
CATALOG_ENRICH_WORKERS = max(1, min(6, int(os.getenv("CATALOG_ENRICH_WORKERS", "4"))))
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


def usable_artwork(value):
    """Reject upstream placeholder images when a real provider image exists."""
    return bool(isinstance(value, str) and value.strip() and
                not any(token in value.lower() for token in
                        ("missing_thumbnail", "placeholder", "/no-image", "no_image")))


def merge_missing_fields(target, fallback, fields):
    changes = 0
    for key in fields:
        missing = not target.get(key)
        if key in ("poster", "background", "logo", "landscapePoster", "thumbnail"):
            missing = not usable_artwork(target.get(key))
            available = usable_artwork(fallback.get(key))
        else:
            available = bool(fallback.get(key))
        if missing and available:
            target[key] = copy.deepcopy(fallback[key])
            changes += 1
    return changes


def union_episode_videos(sh, tvdb):
    """Keep SH episodes immutable and add *unambiguous* TVDB-only slots.

    A shared video ID that points to a different season/episode is a hard stop.
    Conflicting air dates on shared slots never overwrite SH's release dates.
    The inherited video ID of a new episode remains untouched for stream and
    watch-state routing. Do not infer mapping by ordinal episode number.
    """
    baseline = sh.get("videos")
    incoming = tvdb.get("videos")
    if not isinstance(baseline, list) or not isinstance(incoming, list):
        return 0
    if len(baseline) > 2000 or len(incoming) > 2000:
        return 0
    current_ids, current_pairs, incoming_ids, incoming_pairs = {}, {}, set(), set()
    for video in baseline:
        if not isinstance(video, dict) or not isinstance(video.get("id"), str):
            return 0
        pair = episode_pair(video)
        if pair is None or pair[0] < 0 or pair[1] < 1:
            return 0
        if video["id"] in current_ids or pair in current_pairs:
            return 0
        current_ids[video["id"]] = pair
        current_pairs[pair] = video["id"]
    candidates = []
    for video in incoming:
        if not isinstance(video, dict) or not isinstance(video.get("id"), str):
            return 0
        vid = video["id"]
        pair = episode_pair(video)
        if pair is None or pair[0] < 0 or pair[1] < 1 or not re.fullmatch(r"[a-zA-Z0-9:_.,-]{1,240}", vid):
            return 0
        if vid in incoming_ids or pair in incoming_pairs:
            return 0
        incoming_ids.add(vid)
        incoming_pairs.add(pair)
        if vid in current_ids:
            if current_ids[vid] != pair:
                return 0  # cross-provider episode disagreement
            continue
        if pair in current_pairs:
            return 0  # different IDs for the same season/episode; mapping unclear
        candidates.append(video)
    if not candidates:
        return 0
    # Only supplement genuinely new episode slots; never replace existing IDs.
    sh["videos"] = sorted([*baseline, *(copy.deepcopy(v) for v in candidates)],
                          key=lambda v: (episode_pair(v) or (9999, 9999)))
    seasons = sh.get("seasons")
    tvdb_seasons = tvdb.get("seasons")
    if isinstance(seasons, list) and isinstance(tvdb_seasons, list):
        def number(s):
            return s.get("season") if isinstance(s, dict) else None
        known = {number(s) for s in seasons}
        added = {episode_pair(v)[0] for v in candidates}
        sh["seasons"] = [*seasons, *(copy.deepcopy(s) for s in tvdb_seasons
                                 if number(s) in added and number(s) not in known)]
    return len(candidates)


def pick_series_meta(media_id, sh, tvdb):
    """TMDB priority: TVDB only when SH missing or an explicit override."""
    rules = read_overrides()
    prefer_tvdb = set(rules.get("prefer_tvdb_meta_ids") or [])
    if sh and tvdb and strongly_same_series(sh, tvdb) and media_id in prefer_tvdb:
        return copy.deepcopy(tvdb), "tvdb"
    if sh:
        return copy.deepcopy(sh), "sh"
    if tvdb:
        return copy.deepcopy(tvdb), "tvdb"
    return None, "none"


def is_sh_anime_series(meta, media_id):
    """Keep SH-managed anime franchises outside TVDB series episode mixing."""
    if media_id.startswith(("kitsu:", "mal:", "anilist:", "anidb:")):
        return True
    if str(meta.get("_metaProvider") or "").casefold() in (
            "mal", "kitsu", "anilist", "anidb", "jikan"):
        return True
    art = meta.get("_providerArt")
    return isinstance(art, dict) and art.get("anime") is True


def series_meta(media_type, media_id):
    sh = get_metadata("sh", media_type, media_id)
    if sh and is_sh_anime_series(sh, media_id):
        return copy.deepcopy(sh), "sh", 0
    tvdb = get_metadata("tvdb", media_type, media_id)
    if sh and not tvdb:
        tvdb = probe_other("tvdb", media_type, media_id, sh)
    if tvdb and not sh:
        sh = probe_other("sh", media_type, media_id, tvdb)
    if sh and tvdb and not strongly_same_series(sh, tvdb):
        LOG.info("Identity mismatch: preserving SH without mixed metadata")
        tvdb = None
    result, origin = pick_series_meta(media_id, sh, tvdb)
    if result is None:
        return None, "none", 0
    changed = 0
    if sh and tvdb and strongly_same_series(sh, tvdb):
        other = tvdb if origin == "sh" else sh
        if origin == "tvdb":
            # Even when TVDB is explicitly selected for episode structure,
            # retain TMDB artwork/text preference for the series card.
            for field in ("poster", "background", "logo", "landscapePoster", "description"):
                if (usable_artwork(sh.get(field)) if field != "description" else bool(sh.get(field))):
                    result[field] = copy.deepcopy(sh[field])
        changed += merge_missing_fields(result, other,
            ("poster", "background", "logo", "landscapePoster", "description", "genres", "runtime"))
        own = {v.get("id"): v for v in result.get("videos") or [] if isinstance(v, dict)}
        peer = {v.get("id"): v for v in other.get("videos") or [] if isinstance(v, dict)}
        for vid, video in own.items():
            candidate = peer.get(vid)
            if not candidate or episode_pair(video) != episode_pair(candidate):
                continue
            same_date = not aired(video) or not aired(candidate) or aired(video) == aired(candidate)
            title = video_title(video)
            same_title = bool(title and len(title) >= 9 and
                              not re.fullmatch(r"episode\s*\d+", title) and
                              title == video_title(candidate))
            if origin == "tvdb" and (same_date or same_title):
                # Prefer SH's episode thumbnail and overview when evidence says
                # the shared ID names the same episode, even in TVDB order.
                for field in ("thumbnail", "overview"):
                    good = (usable_artwork(candidate.get(field)) if field == "thumbnail"
                            else bool(candidate.get(field)))
                    if good and candidate[field] != video.get(field):
                        video[field] = copy.deepcopy(candidate[field])
                        changed += 1
            elif same_date or same_title:
                changed += merge_missing_fields(video, candidate,
                                                ("thumbnail", "overview", "title"))
        changed += union_episode_videos(result, other)
        aliases = list(result.get("aliases") or [])
        for alias in (sh.get("aliases") or []) + (tvdb.get("aliases") or []):
            if isinstance(alias, str) and alias not in aliases:
                aliases.append(alias)
        if aliases:
            result["aliases"] = aliases[:100]
    LOG.info("Unified series served provider=%s enriched_or_added=%d", origin, changed)
    return result, origin, changed


def enrich_movie_logo(payload, media_id):
    """Add only a verified missing ClearLogo to SH movie metadata.

    Keep the complete SH response intact (including its IDs, videos, extras
    and fields unknown to this bridge). Unlike series merging, movie fallback
    must never replace the movie itself with SH2 or invent metadata if SH is
    unavailable. SH-managed anime remains unchanged, regardless of resource
    type. All upstream reads go through the existing bounded request cache.
    """
    if not isinstance(payload, dict):
        return payload
    sh = payload.get("meta")
    if not isinstance(sh, dict) or not sh.get("id"):
        return payload
    if is_sh_anime_series(sh, media_id) or usable_artwork(sh.get("logo")):
        return payload
    # Only documented strong shared IDs: no title/year guesses.
    if not ids_of(sh):
        return payload
    candidate = probe_other("tvdb", "movie", media_id, sh)
    if candidate is None or not usable_artwork(candidate.get("logo")):
        return payload
    result = copy.deepcopy(payload)
    result["meta"]["logo"] = candidate["logo"]
    LOG.info("Movie missing ClearLogo enriched from verified SH2 identity")
    return result


def canonical_search_entries(grouped, media_type):
    """De-duplicate search previews using verified IDs, never title alone.

    Only resolve a bounded number of unique preview IDs: an IMDb/TVDB/TMDB
    spelling may differ across search catalogs even for the same title.
    """
    entries = []
    seen = set()
    for provider, item in grouped:
        ident = item.get("id") if isinstance(item, dict) else None
        if not isinstance(ident, str) or not re.fullmatch(r"[a-zA-Z0-9:_.,-]{1,240}", ident):
            continue
        key = (provider, item.get("type", media_type), ident)
        if key in seen:
            continue
        seen.add(key)
        entries.append((provider, item))
        if len(entries) >= 100:
            break
    # Preview ID fields are not always present in AIOMetadata; resolve at most
    # 24 distinct upstream results. Gracefully degrade to ID-only dedup beyond.
    cap = min(24, len(entries))
    def resolve(item):
        provider, preview = item
        return get_metadata(provider, media_type, preview["id"])
    with ThreadPoolExecutor(max_workers=6) as pool:
        detailed = list(pool.map(resolve, entries[:cap])) if cap else []
    identities = []
    for idx, (provider, item) in enumerate(entries):
        meta = detailed[idx] if idx < cap else None
        ident = ids_of(meta) if meta else ids_of(item)
        if not ident and meta:
            ident = ids_of(item)
        merged = False
        for group in identities:
            old = group["ids"]
            common = old.keys() & ident.keys()
            if common and all(old[k] == ident[k] for k in common):
                # Retain accumulated identity hints for subsequent items.
                group["ids"].update(ident)
                group["rows"].append((provider, item, meta))
                merged = True
                break
            if item["id"] == group["raw_id"]:
                group["rows"].append((provider, item, meta))
                merged = True
                break
        if not merged:
            identities.append({"ids": dict(ident), "raw_id": item["id"],
                               "rows": [(provider, item, meta)]})
    output = []
    for group in identities:
        # A SH-derived preview is always primary for poster/name when present.
        rows = sorted(group["rows"], key=lambda r: 0 if r[0] == "sh" else 1)
        preferred_provider, preview, meta = rows[0]
        card = copy.deepcopy(preview)
        # If the same identity is known by IMDb, canonicalize the link, but
        # don't invent an ID for unmatched provider-only search results.
        ids = group["ids"]
        canonical = ids.get("imdb") or (meta.get("id") if meta else preview["id"])
        card["id"] = canonical
        sh_meta = next((m for p, _, m in rows if p == "sh" and m), None)
        if sh_meta is None and meta is not None and ids:
            # SH search may omit an entry that SH's metadata endpoint knows.
            # A matching SH response should still own the unified card.
            candidate = get_metadata("sh", media_type, canonical)
            if candidate and strongly_same_series(candidate, meta):
                sh_meta = candidate
        if sh_meta:
            for field in ("name", "poster", "background", "description"):
                if (usable_artwork(sh_meta.get(field)) if field in
                        ("poster", "background") else bool(sh_meta.get(field))):
                    card[field] = copy.deepcopy(sh_meta[field])
        elif meta:
            merge_missing_fields(card, meta, ("poster", "background", "description"))
        output.append(card)
    return output



# Opaque catalog IDs are reversibly namespaced. SH and SH2 can reuse an ID.
_VALID_ID = re.compile(r"[A-Za-z0-9:_.,@-]{1,240}\Z")
_VALID_TYPE = re.compile(r"[A-Za-z0-9_.-]{1,48}\Z")
_PROXY_ID = re.compile(r"hd-(sh|tvdb)-([A-Za-z0-9_-]{1,340})\Z")
_VALID_EXTRA = re.compile(r"[A-Za-z0-9%_.~!$&'()*+,;=:@-]{0,1600}\Z")


def proxied_catalog_id(provider, original):
    blob = base64.urlsafe_b64encode(original.encode("utf-8")).decode("ascii").rstrip("=")
    return "hd-{}-{}".format(provider, blob)


def source_catalogs():
    """Copy user-configured SH/SH2 descriptors, preserving order/extras/names."""
    result = []
    for provider in ("sh", "tvdb"):
        manifest_data = request_json(provider, "manifest.json") or {}
        for catalog in manifest_data.get("catalogs") or []:
            if not isinstance(catalog, dict):
                continue
            cid, media_type = catalog.get("id"), catalog.get("type")
            if not (isinstance(cid, str) and _VALID_ID.fullmatch(cid)
                    and isinstance(media_type, str) and _VALID_TYPE.fullmatch(media_type)):
                continue
            mirrored = copy.deepcopy(catalog)
            mirrored["id"] = proxied_catalog_id(provider, cid)
            result.append(mirrored)
    return result


def resolve_catalog(media_type, bridged_id):
    match = _PROXY_ID.fullmatch(bridged_id)
    if not match:
        return None
    provider, encoded = match.groups()
    try:
        cid = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode("utf-8")
    except (ValueError, UnicodeError):
        return None
    if not _VALID_ID.fullmatch(cid) or proxied_catalog_id(provider, cid) != bridged_id:
        return None
    manifest_data = request_json(provider, "manifest.json") or {}
    if not any(isinstance(c, dict) and c.get("type") == media_type
               and c.get("id") == cid for c in manifest_data.get("catalogs") or []):
        return None
    return provider, cid


def rebind_collection_sources(payload, provider):
    """Map source catalog references into the Bridge's manifest namespace.

    AIOMetadata collection.meta.collection.sources references *original*
    configured catalog IDs. The Bridge publishes provider-namespaced IDs.
    AIOStreams resolves collection members using those advertised catalog IDs,
    so unmodified source links silently produce an empty folder.

    Preserve all IDs/collection.items and metadata. Rebind only a source with
    an exact (type, catalogId) in that provider's current manifest. External
    addonId references remain unaltered, never hijacked by the Bridge.
    """
    if not isinstance(payload, dict):
        return payload
    meta = payload.get("meta")
    if not isinstance(meta, dict):
        return payload
    collection = meta.get("collection")
    if not isinstance(collection, dict) or not isinstance(collection.get("sources"), list):
        return payload
    manifest_data = request_json(provider, "manifest.json") or {}
    own_addon_id = manifest_data.get("id")
    known = {
        (c["type"], c["id"])
        for c in (manifest_data.get("catalogs") or [])
        if isinstance(c, dict) and isinstance(c.get("type"), str)
        and isinstance(c.get("id"), str)
    }
    updated = None
    changed = 0
    unresolved = 0
    for index, source in enumerate(collection["sources"]):
        if not isinstance(source, dict):
            continue
        original_id = source.get("catalogId")
        media_type = source.get("type")
        addon_id = source.get("addonId")
        if addon_id and addon_id != own_addon_id:
            continue  # Explicit external addon ownership is not ours to change.
        if not (isinstance(original_id, str) and isinstance(media_type, str)
                and (media_type, original_id) in known):
            unresolved += 1
            continue
        proxied_id = proxied_catalog_id(provider, original_id)
        if source.get("catalogId") == proxied_id and not addon_id:
            continue
        if updated is None:
            updated = copy.deepcopy(payload)
        target = updated["meta"]["collection"]["sources"][index]
        target["catalogId"] = proxied_id
        # AIOStreams must attach the local Bridge instance, not stale SH.
        if addon_id == own_addon_id:
            target.pop("addonId", None)
        changed += 1
    if changed or unresolved:
        LOG.info("Collection catalog references rebound=%d unresolved=%d",
                 changed, unresolved)
    return updated if updated is not None else payload


def safe_catalog_extra(extra):
    if not _VALID_EXTRA.fullmatch(extra):
        return False
    try:
        decoded = urllib.parse.unquote(extra, errors="strict")
    except (UnicodeError, ValueError):
        return False
    return not any(ord(c) < 32 or ord(c) == 127 for c in decoded)


def candidate_preview_logo(provider, preview, media_type):
    """Only return a source logo or identity-verified other-source logo."""
    if not isinstance(preview, dict) or usable_artwork(preview.get("logo")):
        return None
    media_id = preview.get("id")
    if (media_type not in ("movie", "series") or not isinstance(media_id, str)
            or not _VALID_ID.fullmatch(media_id)
            or media_id.startswith(("kitsu:", "mal:", "anilist:", "anidb:"))):
        return None
    own = get_metadata(provider, media_type, media_id)
    if own is None or is_sh_anime_series(own, media_id):
        return None
    if usable_artwork(own.get("logo")):
        return own["logo"]
    if not ids_of(own):
        return None
    other = "tvdb" if provider == "sh" else "sh"
    candidate = probe_other(other, media_type, media_id, own)
    if (candidate and not is_sh_anime_series(candidate, media_id)
            and usable_artwork(candidate.get("logo"))):
        return candidate["logo"]
    return None


def passthrough_catalog(provider, media_type, catalog_id, extra=""):
    path = "catalog/{}/{}".format(
        urllib.parse.quote(media_type, safe=""),
        urllib.parse.quote(catalog_id, safe="-_."))
    if extra:
        path += "/" + extra
    source = request_json(provider, path + ".json")
    if not isinstance(source, dict):
        return None
    items = source.get("metas")
    if not isinstance(items, list) or not CATALOG_ENRICH_LIMIT:
        return source
    eligible = [i for i, x in enumerate(items)
                if isinstance(x, dict) and (x.get("type") or media_type) == media_type
                and not usable_artwork(x.get("logo"))][:CATALOG_ENRICH_LIMIT]
    if not eligible:
        return source
    with ThreadPoolExecutor(max_workers=CATALOG_ENRICH_WORKERS) as pool:
        logos = list(pool.map(lambda i: candidate_preview_logo(provider, items[i], media_type),
                              eligible))
    updated = None
    for i, logo in zip(eligible, logos):
        if not usable_artwork(logo):
            continue
        if updated is None:
            updated = copy.deepcopy(source)
        updated["metas"][i]["logo"] = logo
    return updated if updated is not None else source



# Chinese & HK Catalog v1.6.x issues title-specific "tch:<type>:<tmdb>[:<imdb>]"
# metadata IDs so Stremio does not pick English Cinemeta. Retain those *page*
# IDs when serving SH's English metadata, while retaining canonical playback IDs.
_CHINESE_CATALOG_ID = re.compile(
    r"tch:(movie|series):([1-9][0-9]{0,11})(?::(tt[0-9]{1,20}))?\Z"
)


def chinese_catalog_english_meta(media_type, media_id):
    """Translate supported tch: detail IDs, never titles or video IDs.

    Require both IDs when Chinese Catalog supplies both. A mismatched pair
    must not silently display metadata for an unrelated movie or series.
    This adapter does not serve watch_state or change any playable episode ID.
    """
    if media_type not in ("movie", "series"):
        return None
    match = _CHINESE_CATALOG_ID.fullmatch(media_id)
    if not match or match.group(1) != media_type:
        return None
    tmdb_id, imdb_id = str(int(match.group(2))), match.group(3)
    candidates = ([imdb_id] if imdb_id else []) + ["tmdb:" + tmdb_id]
    for source_id in candidates:
        source = get_metadata("sh", media_type, source_id)
        if not source:
            continue
        ids = ids_of(source)
        if ids.get("tmdb") != tmdb_id or (imdb_id and ids.get("imdb") != imdb_id):
            continue

        if media_type == "series":
            # Preserve the existing Bridge's identity-checked SH+SH2 episode
            # enrichment, including every original video.id and watch-state ID.
            combined, _, _ = series_meta(media_type, source_id)
            if not isinstance(combined, dict):
                continue
            final_ids = ids_of(combined)
            if final_ids.get("tmdb") != tmdb_id or (
                imdb_id and final_ids.get("imdb") != imdb_id
            ):
                continue
            payload = {"meta": combined}
        else:
            # Keep the full upstream movie payload and any existing clearlogo
            # fallback; merely change the *page* ID and playback hint.
            path = "meta/movie/{}.json".format(urllib.parse.quote(source_id, safe=":-_."))
            payload = request_json("sh", path)
            if not isinstance(payload, dict):
                continue
            complete_ids = ids_of(payload.get("meta"))
            if complete_ids.get("tmdb") != tmdb_id or (
                imdb_id and complete_ids.get("imdb") != imdb_id
            ):
                continue
            payload = enrich_movie_logo(payload, source_id)

        output = copy.deepcopy(payload)
        out_meta = output.get("meta")
        if not isinstance(out_meta, dict):
            continue
        out_meta["id"] = media_id
        if media_type == "movie":
            hints = out_meta.get("behaviorHints")
            hints = dict(hints) if isinstance(hints, dict) else {}
            hints["defaultVideoId"] = imdb_id or "tmdb:" + tmdb_id
            out_meta["behaviorHints"] = hints
        LOG.info("Chinese Catalog English meta served type=%s via SH", media_type)
        return output
    LOG.info("Chinese Catalog English meta unavailable/identity mismatch type=%s", media_type)
    return None


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
    catalogs = (source_catalogs() if CATALOG_PASSTHROUGH_ENABLED else
                [{"type": "series", "id": "unified-series-search",
                  "name": "Hybrid Series Search",
                  "extra": [{"name": "search", "isRequired": True}]}])
    return {
        "id": "com.homedocker.metadata-bridge",
        "version": "0.3.0" if CATALOG_PASSTHROUGH_ENABLED else "0.2.0",
        "name": "HomeDocker Unified Metadata",
        "description": "Read-only configured SH/SH2 catalogs with logo enrichment; SH keeps watch state",
        "resources": [{"name": "meta", "types": types}, "catalog"],
        "types": types,
        "catalogs": catalogs,
        "behaviorHints": {"configurable": False},
    }


def search(media_type, value):
    rules = read_overrides()
    terms = [value]
    aliasmap = rules.get("search_aliases") or {}
    if isinstance(aliasmap, dict):
        terms += [x for x in aliasmap.get(normalize(value), []) if isinstance(x, str)][:3]
    manifests = {p: request_json(p, "manifest.json") or {} for p in BASES}
    by_provider = {}
    for provider, data in manifests.items():
        catalogs = [c for c in data.get("catalogs") or []
                    if isinstance(c, dict) and c.get("type") == media_type and
                    isinstance(c.get("id"), str) and
                    any(isinstance(e, dict) and e.get("name") == "search"
                        for e in c.get("extra") or [])]
        catalogs.sort(key=lambda c: ("search" not in (c.get("name", "") + " " + c["id"]).lower(), c["id"]))
        by_provider[provider] = [(provider, "catalog/{}/{}/search={}.json".format(
            urllib.parse.quote(media_type, safe=""),
            urllib.parse.quote(c["id"], safe="-_."),
            urllib.parse.quote(term, safe="")))
            for c in catalogs[:2] for term in terms]
    tasks = []
    for i in range(max((len(v) for v in by_provider.values()), default=0)):
        for provider in ("sh", "tvdb"):
            if i < len(by_provider.get(provider, [])):
                tasks.append(by_provider[provider][i])
            if len(tasks) >= 12:
                break
        if len(tasks) >= 12:
            break
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda task: request_json(task[0], task[1]), tasks))
    groups = []
    for (provider, _), payload in zip(tasks, responses):
        if isinstance(payload, dict) and isinstance(payload.get("metas"), list):
            groups.append((provider, payload["metas"]))
    # Interleave SH and TVDB results, preserve provider origin for identity
    # resolution, and collapse only records with verified matching IDs.
    interleaved = []
    for i in range(max((len(items) for _, items in groups), default=0)):
        for provider, items in groups:
            if i < len(items):
                interleaved.append((provider, items[i]))
    return canonical_search_entries(interleaved, media_type)


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
            return self.send_json(200, {"status": "ok", "service": "metadata-bridge",
                                        "version": "0.3.0" if CATALOG_PASSTHROUGH_ENABLED else "0.2.0"})
        if route == "/manifest.json":
            return self.send_json(200, manifest())
        match = re.fullmatch(r"/meta/([^/]+)/(.+)\.json", route)
        if match:
            media_type = urllib.parse.unquote(match[1])
            media_id = urllib.parse.unquote(match[2])
            if len(media_id) > 240 or not re.fullmatch(r"[a-zA-Z0-9:_.,-]+", media_id):
                return self.send_json(400, {"error": "invalid media ID"})
            if CHINESE_CATALOG_ENGLISH_META_ENABLED and media_id.startswith("tch:"):
                payload = chinese_catalog_english_meta(media_type, media_id)
                return self.send_json(200 if payload else 404,
                                      payload or {"error": "tch metadata not found"})
            if media_type != "series":
                # Exact upstream JSON passthrough, not just meta fields.
                path = "meta/{}/{}.json".format(
                    urllib.parse.quote(media_type, safe=""), urllib.parse.quote(media_id, safe=":-_."))
                payload = request_json("sh", path)
                if media_type == "movie" and payload:
                    payload = enrich_movie_logo(payload, media_id)
                if media_type == "collection" and payload and CATALOG_PASSTHROUGH_ENABLED:
                    payload = rebind_collection_sources(payload, "sh")
                return self.send_json(200 if payload else 404, payload or {"error": "not found"})
            value, _, _ = series_meta(media_type, media_id)
            return self.send_json(200 if value else 404, {"meta": value} if value else {"error": "not found"})
        if CATALOG_PASSTHROUGH_ENABLED:
            match = re.fullmatch(
                r"/catalog/([A-Za-z0-9_.-]{1,48})/(hd-(?:sh|tvdb)-[A-Za-z0-9_-]{1,340})(?:/([^/]*))?\.json",
                route)
            if match:
                media_type, bridged_id, extra = match.groups()
                catalog = resolve_catalog(media_type, bridged_id)
                if catalog is None or not safe_catalog_extra(extra or ""):
                    return self.send_json(404, {"error": "catalog not found"})
                provider, original_id = catalog
                data = passthrough_catalog(provider, media_type, original_id, extra or "")
                return self.send_json(200 if data is not None else 502,
                                      data if data is not None else {"error": "catalog unavailable"})
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
