#!/usr/bin/env python3
"""Read-only ClearLogo coverage cohort audit against configured SH and SH2.

Runs inside the existing HomeDocker Metadata Bridge container. It deliberately
prints *only aggregate counts*, never names, IDs, URLs, tokens or private config.
No server writes, no image downloads, no cache deletion, no provider changes.
"""
import argparse
import json
import re
import time
import urllib.parse
from collections import Counter, defaultdict, deque
from concurrent.futures import ThreadPoolExecutor

import bridge

VALID_ID = re.compile(r"[A-Za-z0-9:_.,@-]{1,240}\Z")
ART_TYPES = ("movie", "series")


def alternated(items):
    """Sample both film and series catalogs instead of only manifest head."""
    buckets = {t: deque(x for x in items if x["type"] == t) for t in ART_TYPES}
    result = []
    while any(buckets.values()):
        for t in ART_TYPES:
            if buckets[t]:
                result.append(buckets[t].popleft())
    return result


def catalog_sample(catalog, genre_ok):
    media_type, cid = catalog.get("type"), catalog.get("id")
    if media_type not in ART_TYPES or not isinstance(cid, str) or not VALID_ID.fullmatch(cid):
        return None
    extras = [x for x in (catalog.get("extra") or []) if isinstance(x, dict)]
    required = [x for x in extras if x.get("isRequired")]
    if any(x.get("name") != "genre" for x in required):
        return None
    params = []
    if required:
        if not genre_ok:
            return None
        genre = required[0]
        options = genre.get("options") or []
        value = next((v for v in options if isinstance(v, str) and v.strip()), None)
        if not value:
            return None
        params.append("genre=" + urllib.parse.quote(value, safe=""))
    skip = any(x.get("name") == "skip" for x in extras)
    return {"type": media_type, "id": cid, "extras": params, "skip": skip,
            "needs_genre": bool(required)}


def preview_eligible(preview, media_type):
    if not isinstance(preview, dict):
        return False
    mid = preview.get("id")
    return (
        isinstance(mid, str)
        and bool(VALID_ID.fullmatch(mid))
        and (preview.get("type") or media_type) == media_type
    )


def find_logo(row):
    source, media_type, mid = row["source"], row["type"], row["id"]
    if mid.startswith(("kitsu:", "mal:", "anilist:", "anidb:")):
        return "anime_excluded"
    own = bridge.get_metadata(source, media_type, mid)
    if not own:
        return "own_metadata_unavailable"
    if bridge.is_sh_anime_series(own, mid):
        return "anime_excluded"
    if bridge.usable_artwork(own.get("logo")):
        return "same_source"
    if not bridge.ids_of(own):
        return "unverified_identity"
    fallback = bridge.probe_other(
        "tvdb" if source == "sh" else "sh", media_type, mid, own
    )
    if fallback and not bridge.is_sh_anime_series(fallback, mid) and bridge.usable_artwork(fallback.get("logo")):
        return "verified_other_source"
    return "no_verified_logo_in_either_source"


def audit(args):
    print("READ-ONLY ClearLogo coverage audit. No identifiers, URLs, images, or credentials are printed.")
    print("Limits:", json.dumps({
        "catalogs_per_source": args.per_source, "extra_genre_catalogs": args.genre_samples,
        "pages_per_catalog": args.pages, "unique_detail_probes": args.meta_probes,
        "concurrent_detail_probes": args.workers, "bridge_missing_logo_cap": args.limit
    }, sort_keys=True))
    selections = []
    inventory = {}
    for source in ("sh", "tvdb"):
        m = bridge.request_json(source, "manifest.json") or {}
        catalogs = [x for x in m.get("catalogs") or [] if isinstance(x, dict)]
        inventory[source] = dict(Counter(x.get("type", "unknown") for x in catalogs))
        candidates = [catalog_sample(x, True) for x in catalogs]
        candidates = [x for x in candidates if x]
        plain = alternated([x for x in candidates if not x["needs_genre"]])[:args.per_source]
        genre = alternated([x for x in candidates if x["needs_genre"]])[:args.genre_samples]
        selections.extend((source, x) for x in plain + genre)
        print(source.upper(), json.dumps({
            "all_catalogs": len(catalogs), "by_type": inventory[source],
            "browsable_catalogs_selected": len(plain),
            "genre_required_selected": len(genre),
            "genre_or_search_required_not_sampled": len(catalogs) - len(candidates),
        }, sort_keys=True))
    totals = Counter()
    samples = []
    probe_rows = {}
    per_source = defaultdict(Counter)
    t_begin = time.monotonic()
    for source, cat in selections:
        index = len(samples) + 1
        page_summary = Counter()
        offset = 0
        for page_index in range(args.pages):
            params = list(cat["extras"])
            if offset and cat["skip"]:
                params.append("skip=" + str(offset))
            if page_index and not cat["skip"]:
                break
            path = "catalog/{}/{}".format(
                urllib.parse.quote(cat["type"], safe=""),
                urllib.parse.quote(cat["id"], safe="-_."))
            if params:
                path += "/" + "&".join(params)
            path += ".json"
            payload = bridge.request_json(source, path)
            if not isinstance(payload, dict) or not isinstance(payload.get("metas"), list):
                page_summary["unavailable_pages"] += 1
                break
            rows = payload["metas"]
            page_summary["pages"] += 1
            eligible_missing = 0
            for item in rows:
                if not isinstance(item, dict):
                    continue
                page_summary["preview_occurrences"] += 1
                logo = item.get("logo")
                if bridge.usable_artwork(logo):
                    page_summary["preview_logo_present"] += 1
                    if not isinstance(logo, str) or not logo.strip().startswith("https://"):
                        page_summary["preview_logo_not_https"] += 1
                    continue
                page_summary["preview_logo_missing"] += 1
                if not preview_eligible(item, cat["type"]):
                    page_summary["not_eligible"] += 1
                    continue
                eligible_missing += 1
                inside_cap = eligible_missing <= args.limit
                if not inside_cap:
                    page_summary["outside_bridge_cap"] += 1
                key = (source, cat["type"], item["id"])
                # Keep at most one lookup per unique work in this audit;
                # catalog occurrence statistics remain undeduplicated.
                if key not in probe_rows:
                    probe_rows[key] = {"source":source,"type":cat["type"],"id":item["id"],
                                       "inside_cap":inside_cap,"occurrences":1}
                else:
                    probe_rows[key]["inside_cap"] |= inside_cap
                    probe_rows[key]["occurrences"] += 1
            totals.update(page_summary)
            per_source[source].update(page_summary)
            offset += len(rows)
            if not rows or not cat["skip"]:
                break
        samples.append((source, cat["type"], page_summary))
        print("CATALOG", index, json.dumps({"source":source,"type":cat["type"],
               "genre_required":cat["needs_genre"],**page_summary}, sort_keys=True))
    # Round-robin across provider and type avoids spending all detail budget
    # on early entries from a single catalogue's first page.
    queues = defaultdict(deque)
    for entry in probe_rows.values():
        queues[(entry["source"], entry["type"])].append(entry)
    sampled = []
    keys = [("sh","movie"),("sh","series"),("tvdb","movie"),("tvdb","series")]
    while len(sampled) < args.meta_probes and any(queues.values()):
        for key in keys:
            if len(sampled) >= args.meta_probes:
                break
            if queues[key]:
                sampled.append(queues[key].popleft())
    results = Counter()
    in_cap = Counter()
    out_cap = Counter()
    status_by_source = defaultdict(Counter)
    if sampled:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            result_iter = pool.map(find_logo, sampled)
            for row, label in zip(sampled, result_iter):
                results[label] += 1
                status_by_source[row["source"]][label] += 1
                (in_cap if row["inside_cap"] else out_cap)[label] += 1
    recoverable = ("same_source", "verified_other_source")
    known_recovered = sum(in_cap[k] for k in recoverable)
    known_outside_cap = sum(out_cap[k] for k in recoverable)
    report = {
        "catalog_pages_sampled":totals["pages"],
        "preview_occurrences":totals["preview_occurrences"],
        "pre_enrichment_logos":totals["preview_logo_present"],
        "pre_enrichment_missing":totals["preview_logo_missing"],
        "preview_logo_not_https":totals["preview_logo_not_https"],
        "missing_outside_bridge_cap_occurrences":totals["outside_bridge_cap"],
        "unique_missing_candidates":len(probe_rows),
        "unique_missing_details_sampled":len(sampled),
        "unsampled_missing_candidates":max(0,len(probe_rows)-len(sampled)),
        "own_source_recovered_unique":results["same_source"],
        "cross_source_recovered_unique":results["verified_other_source"],
        "known_recovered_within_current_limit_unique":known_recovered,
        "known_recoverable_but_outside_limit_unique":known_outside_cap,
        "reasons_no_logo_on_probed_unique":dict(results),
        "by_source_probed":{k:dict(v) for k,v in status_by_source.items()},
        "sampling_wall_seconds":round(time.monotonic()-t_begin,1),
    }
    print("SUMMARY",json.dumps(report,sort_keys=True))
    print("INTERPRETATION: preview counts are occurrences, detail probes are unique IDs.")
    print("INTERPRETATION: do not infer whole-library coverage or claim all images load successfully.")
    print("INTERPRETATION: if details are unsampled, there is no coverage inference for them.")
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--per-source", type=int, default=8)
    parser.add_argument("--genre-samples", type=int, default=3)
    parser.add_argument("--pages", type=int, default=2)
    parser.add_argument("--meta-probes", type=int, default=36)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--limit", type=int, default=12)
    args = parser.parse_args()
    for name, low, high in (("per_source",1,12),("genre_samples",0,6),
                            ("pages",1,3),("meta_probes",0,60),
                            ("workers",1,4),("limit",0,24)):
        value = getattr(args,name)
        if not low <= value <= high:
            parser.error("--"+name.replace("_","-")+" must be between "+str(low)+" and "+str(high))
    audit(args)


if __name__ == "__main__":
    main()
