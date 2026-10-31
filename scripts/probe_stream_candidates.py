#!/usr/bin/env python3
"""Read-only, redacted Stremio stream discovery audit for Bridge episode IDs.

Requires HomeDocker Bridge at http://127.0.0.1:18123 and an existing
AIOStreams manifest URL provided privately with getpass. Prints no URLs,
IDs, torrent hashes or titles. Does not initiate playback or tracker writes.
"""
import getpass
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter

BRIDGE_META = "http://127.0.0.1:18123/meta/series/tt6150576.json"
CHECK_EPISODES = ((3, 1), (3, 2))
SEASON_EPISODE = re.compile(r"(?<![a-zA-Z0-9])s(\d{1,2})[._\-\s]*e(\d{1,3})(?!\d)", re.I)
ALT_SEASON_EPISODE = re.compile(r"(?<!\d)(\d{1,2})x(\d{1,3})(?!\d)", re.I)


def stream_url_from_manifest(manifest_url, video_id):
    parsed = urllib.parse.urlsplit(manifest_url)
    if (parsed.scheme not in ("http", "https") or not parsed.netloc
            or not parsed.path.endswith("/manifest.json") or parsed.username
            or parsed.password or parsed.fragment):
        raise ValueError("Invalid manifest URL")
    if not isinstance(video_id, str) or not re.fullmatch(r"[A-Za-z0-9:_.,-]{1,240}", video_id):
        raise ValueError("Invalid video id")
    path = (parsed.path[:-len("/manifest.json")] + "/stream/series/"
            + urllib.parse.quote(video_id, safe=":-_.") + ".json")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, ""))


def episode_hints(stream):
    labels = " ".join(x for x in (stream.get("name"), stream.get("title")) if isinstance(x, str))
    hints = {(int(s), int(e)) for s, e in SEASON_EPISODE.findall(labels)}
    hints.update((int(s), int(e)) for s, e in ALT_SEASON_EPISODE.findall(labels))
    return hints


def locator(stream):
    """Opaque digest kept in memory solely for comparing two candidate sets."""
    for name in ("infoHash", "url", "externalUrl", "ytId"):
        val = stream.get(name)
        if isinstance(val, str) and val:
            data = {"kind": name, "value": val}
            if name == "infoHash":
                data["fileIdx"] = stream.get("fileIdx")
            blob = json.dumps(data, sort_keys=True).encode()
            return name, hashlib.sha256(blob).hexdigest()
    return "unidentified", None


def summarize(streams, season, episode):
    kinds = Counter()
    matched = wrong = ambiguous = no_hint = 0
    locators = set()
    for s in streams:
        if not isinstance(s, dict):
            continue
        kind, digest = locator(s)
        kinds[kind] += 1
        if digest:
            locators.add((kind, digest))
        hints = episode_hints(s)
        if not hints:
            no_hint += 1
        elif (season, episode) in hints:
            if len(hints) == 1:
                matched += 1
            else:
                ambiguous += 1
        else:
            wrong += 1
    return {
        "candidate_count": len(streams),
        "locator_kinds": dict(sorted(kinds.items())),
        "distinct_locator_count": len(locators),
        "explicit_matching_episode_labels": matched,
        "explicit_wrong_episode_labels": wrong,
        "ambiguous_multi_episode_labels": ambiguous,
        "no_parseable_episode_label": no_hint,
    }, locators


def get_json(url, timeout):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if resp.status != 200:
            return None
        raw = resp.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            return None
        return json.loads(raw)


def main():
    try:
        meta_payload = get_json(BRIDGE_META, 20)
        episodes = (meta_payload or {}).get("meta", {}).get("videos", [])
        if not isinstance(episodes, list):
            raise ValueError("Invalid Bridge response")
        selected = {}
        for season, episode in CHECK_EPISODES:
            matches = [v for v in episodes if isinstance(v, dict)
                       and v.get("season") == season and v.get("episode") == episode]
            if len(matches) != 1 or not isinstance(matches[0].get("id"), str):
                raise ValueError("Missing or duplicate Bridge episode")
            selected[(season, episode)] = matches[0]["id"]
        manifest = os.environ.get("AIO_MANIFEST_URL", "").strip()
        if not manifest:
            manifest = getpass.getpass("AIOStreams Manifest URL (hidden): ").strip()
        report = {"check": "read_only_stream_candidate_audit", "episodes": {}}
        all_locators = {}
        for season, episode in CHECK_EPISODES:
            name = f"S{season:02d}E{episode:02d}"
            payload = get_json(stream_url_from_manifest(manifest, selected[(season, episode)]), 55)
            streams = (payload or {}).get("streams", [])
            if not isinstance(streams, list):
                raise ValueError("Invalid AIOStreams result")
            report["episodes"][name], all_locators[name] = summarize(streams, season, episode)
        shared = set.intersection(*all_locators.values())
        report["shared_locators_between_episodes"] = len(shared)
        report["shared_locators_by_kind"] = dict(sorted(Counter(
            kind for kind, _ in shared).items()))
        report["note"] = ("Candidate labels/locators only; does NOT verify actual playback, "
                          "correct content, or SH Watch State continuity")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except urllib.error.HTTPError as exc:
        print(json.dumps({"error": "HTTP_error", "status": exc.code}))
        return 1
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError,
            TypeError, json.JSONDecodeError) as exc:
        # Exception text may contain private manifest URL: do NOT include it.
        print(json.dumps({"error": exc.__class__.__name__}))
        return 1


if __name__ == "__main__":
    sys.exit(main())