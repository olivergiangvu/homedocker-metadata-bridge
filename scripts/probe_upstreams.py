#!/usr/bin/env python3
"""Read-only SH/SH2 contract probe; prints aggregate metrics, never manifest URLs.

Usage (on host with private SH_MANIFEST_URL and SH2_MANIFEST_URL exported):
  python3 scripts/probe_upstreams.py --series-id tt6150576 --search 'Midnight Diner'

This script imports bridge.py for its read-only GET/parser functions. It does NOT
start the HTTP server, post watch_state, write to .env, or save fetched JSON.
"""
import argparse
import json
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def declared_resources(manifest):
    if not isinstance(manifest, dict):
        return []
    return sorted({r if isinstance(r, str) else r.get("name", "")
                   for r in (manifest.get("resources") or [])
                   if isinstance(r, (str, dict))} - {""})


def searchable_catalog_count(manifest, media_type="series"):
    if not isinstance(manifest, dict):
        return 0
    return sum(1 for c in (manifest.get("catalogs") or [])
               if isinstance(c, dict) and c.get("type") == media_type
               and any(isinstance(e, dict) and e.get("name") == "search"
                       for e in c.get("extra") or []))


def provider_search_ids(bridge, provider, manifest, term):
    """Read at most two search catalogs per provider; return identifiers in RAM only."""
    if not isinstance(manifest, dict):
        return set()
    catalogs = [c for c in (manifest.get("catalogs") or [])
                if isinstance(c, dict) and c.get("type") == "series"
                and isinstance(c.get("id"), str)
                and any(isinstance(e, dict) and e.get("name") == "search"
                        for e in c.get("extra") or [])]
    catalogs.sort(key=lambda c: ("search" not in (c.get("name", "") + " " + c["id"]).lower(), c["id"]))
    result = set()
    for c in catalogs[:2]:
        path = "catalog/series/{}/search={}.json".format(
            urllib.parse.quote(c["id"], safe="-_.'"), urllib.parse.quote(term, safe=""))
        payload = bridge.request_json(provider, path)
        for item in (payload or {}).get("metas") or []:
            if isinstance(item, dict) and item.get("id"):
                result.add((item.get("type", "series"), item["id"]))
    return result


def metadata_summary(meta, bridge):
    if not isinstance(meta, dict):
        return {"found": False}
    videos = meta.get("videos") if isinstance(meta.get("videos"), list) else []
    seasons = {}
    for video in videos:
        if isinstance(video, dict):
            pair = bridge.episode_pair(video)
            if pair is not None:
                seasons[str(pair[0])] = seasons.get(str(pair[0]), 0) + 1
    return {
        "found": True,
        "source_provider": str(meta.get("_metaProvider") or "unknown"),
        "identity_keys": sorted(bridge.ids_of(meta)),
        "episode_count": len(videos),
        "season_episode_counts": seasons,
        "thumbnails": sum(bool(v.get("thumbnail")) for v in videos if isinstance(v, dict)),
        "overviews": sum(bool(v.get("overview")) for v in videos if isinstance(v, dict)),
    }


def main():
    parser = argparse.ArgumentParser(description="Read-only, redacted AIOMetadata bridge preflight")
    parser.add_argument("--series-id", required=True, help="Public series identifier (e.g. tt6150576)")
    parser.add_argument("--search", default="", help="Public search term; optional")
    args = parser.parse_args()
    if not 0 < len(args.series_id) <= 240:
        parser.error("series-id must not be empty or more than 240 characters")
    try:
        import bridge
    except (ValueError, OSError) as exc:
        # Avoid printing URL values or config exception details.
        print(json.dumps({"error": "Bridge environment invalid; set both manifest URL variables privately"}))
        return 2

    manifests = {p: bridge.request_json(p, "manifest.json") for p in ("sh", "tvdb")}
    report = {
        "check": "read_only_upstream_contract",
        "upstreams": {
            p: {"reachable": bool(isinstance(m, dict) and isinstance(m.get("resources"), list)),
                "resources": declared_resources(m),
                "series_search_catalogs": searchable_catalog_count(m)}
            for p, m in manifests.items()
        },
    }
    if not all(v["reachable"] for v in report["upstreams"].values()):
        report["outcome"] = "BLOCKED: an upstream manifest is unavailable"
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1

    sh = bridge.get_metadata("sh", "series", args.series_id)
    tvdb = bridge.get_metadata("tvdb", "series", args.series_id)
    if sh and not tvdb:
        tvdb = bridge.probe_other("tvdb", "series", args.series_id, sh)
    if tvdb and not sh:
        sh = bridge.probe_other("sh", "series", args.series_id, tvdb)
    report["series"] = {
        "sh": metadata_summary(sh, bridge),
        "tvdb": metadata_summary(tvdb, bridge),
        "identity_verified": bool(sh and tvdb and bridge.strongly_same_series(sh, tvdb)),
        "same_season_distribution": bool(sh and tvdb and bridge.same_layout(sh, tvdb)),
    }
    if sh and tvdb:
        sh_video_ids = {v["id"] for v in sh.get("videos", [])
                        if isinstance(v, dict) and isinstance(v.get("id"), str)}
        tvdb_video_ids = {v["id"] for v in tvdb.get("videos", [])
                          if isinstance(v, dict) and isinstance(v.get("id"), str)}
        report["series"]["shared_video_id_count"] = len(sh_video_ids & tvdb_video_ids)
    if sh and tvdb and bridge.strongly_same_series(sh, tvdb):
        from copy import deepcopy
        tvdb_copy = deepcopy(tvdb)
        report["series"]["potential_tmdb_episode_enrichments"] = bridge.overlay_episodes(tvdb_copy, sh)
    if args.search:
        # Only counts are printed; raw result IDs and titles never leave process memory.
        sh_results = provider_search_ids(bridge, "sh", manifests["sh"], args.search)
        tvdb_results = provider_search_ids(bridge, "tvdb", manifests["tvdb"], args.search)
        report["search"] = {
            "sh_result_count": len(sh_results),
            "sh2_result_count": len(tvdb_results),
            "sh2_unique_result_count": len(tvdb_results - sh_results),
            "overlapping_result_ids": len(tvdb_results & sh_results),
        }
    report["outcome"] = "INSPECT: do not cut over until episode/video IDs are verified"
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
