#!/usr/bin/env python3
"""Private-source-safe, read-only Collection source reference diagnosis.

Run inside EXISTING HomeDocker Metadata Bridge container via stdin.
Only aggregates are printed; no UUIDs, catalog IDs, media IDs, names, titles,
URLs, source code, tokens or collection member data are displayed.
"""
import collections
import json
import re
import urllib.parse
import bridge

def main():
    sh = bridge.request_json("sh", "manifest.json") or {}
    prox = bridge.manifest()
    upstream = {(c.get("type"), c.get("id")) for c in (sh.get("catalogs") or [])
                if isinstance(c, dict)}
    bridged = {(c.get("type"), c.get("id")) for c in (prox.get("catalogs") or [])
               if isinstance(c, dict)}
    categories = [c for c in (sh.get("catalogs") or [])
                  if isinstance(c, dict) and c.get("type") == "collection"
                  and isinstance(c.get("id"),str) and bridge._VALID_ID.fullmatch(c["id"])]
    stats = collections.Counter()
    visited = set()
    queue = []
    seen_catalogs = 0
    for c in categories:
        cid = urllib.parse.quote(c["id"],safe="-_.")
        # AIOMetadata collection descriptors declare genre=None as a required
        # value in some configurations; try default then explicit None.
        pages = [
            "catalog/collection/" + cid + ".json",
            "catalog/collection/" + cid + "/genre=None.json",
        ]
        metas = None
        for path in pages:
            body = bridge.request_json("sh",path)
            if isinstance(body,dict) and isinstance(body.get("metas"),list):
                metas = body["metas"]
                break
        if metas is None:
            stats["unavailable_collection_catalogs"] += 1
            continue
        seen_catalogs += 1
        stats["collection_previews"] += len(metas)
        queue.extend(x for x in metas[:8] if isinstance(x,dict))
    processed = 0
    # Include nested folder previews, but do not traverse unbounded families.
    while queue and processed < 60:
        entry = queue.pop(0)
        media_id = entry.get("id")
        media_type = entry.get("type") or "collection"
        if (not isinstance(media_id,str) or not bridge._VALID_ID.fullmatch(media_id)
                or media_type != "collection" or media_id in visited):
            continue
        visited.add(media_id)
        processed += 1
        payload = bridge.request_json("sh", "meta/collection/"+urllib.parse.quote(media_id,safe=":-_.")+".json")
        meta = payload.get("meta") if isinstance(payload,dict) else None
        if not isinstance(meta,dict):
            stats["unavailable_full_collection_meta"] += 1
            continue
        coll = meta.get("collection")
        if not isinstance(coll,dict):
            stats["missing_collection_object"] += 1
            continue
        stats["full_collection_metas"] += 1
        children = coll.get("items") or []
        if isinstance(children,list):
            stats["nested_folder_items"] += len(children)
            queue.extend(x for x in children[:6] if isinstance(x,dict))
        sources = coll.get("sources") or []
        if not isinstance(sources,list):
            stats["sources_not_list"] += 1
            continue
        if sources: stats["collections_with_sources"] += 1
        else: stats["collections_without_sources"] += 1
        for src in sources:
            if not isinstance(src,dict):
                stats["malformed_source"] += 1
                continue
            cat_type, cat_id = src.get("type"),src.get("catalogId")
            if not isinstance(cat_type,str) or not isinstance(cat_id,str):
                stats["malformed_source"] += 1
                continue
            if src.get("addonId") and src["addonId"] != sh.get("id"):
                stats["external_source"] += 1
                continue
            pair=(cat_type,cat_id)
            if pair in upstream:
                stats["raw_source_matches_SH_manifest"] += 1
                bridged_pair=(cat_type,bridge.proxied_catalog_id("sh",cat_id))
                if bridged_pair in bridged:
                    stats["rebound_target_in_Bridge_manifest"] += 1
                else:
                    stats["rebound_target_missing_from_Bridge"] += 1
            elif pair in bridged:
                stats["already_matches_Bridge_manifest"] += 1
            else:
                stats["source_missing_from_SH_manifest"] += 1
    print("READ-ONLY COLLECTION CHECK",json.dumps({
        "sh_collection_catalogs_declared":len(categories),
        "collection_catalogs_reachable":seen_catalogs,
        "sampled_full_collection_meta_max":60,
        "stats":dict(stats)},sort_keys=True))
    if stats["raw_source_matches_SH_manifest"] and stats["rebound_target_in_Bridge_manifest"]:
        print("DIAGNOSIS: source IDs use SH namespace; Bridge manifest uses namespaced IDs. Candidate rebinding is applicable.")
    elif stats["collections_with_sources"] == 0:
        print("DIAGNOSIS: no source-bearing collections sampled; cannot confirm root cause from this cohort.")
    else:
        print("DIAGNOSIS: review aggregate counts before shipping; source reference mismatch not proven.")
    print("No state modified; no IDs, titles, URLs or credentials printed.")

if __name__ == "__main__":
    main()
