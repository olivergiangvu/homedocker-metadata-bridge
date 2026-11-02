#!/usr/bin/env python3
"""Read-only SH/SH2 configured catalog inventory for HomeDocker.

Run via stdin INSIDE current Metadata Bridge container; Bridge config/secret
manifests never printed. Does not need feature branch deployed.
"""
import argparse
import json
import re
from collections import Counter

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=int, default=3, help="Browse catalogs to sample per upstream (max 6)")
    args = parser.parse_args()
    import bridge
    samples = max(0, min(6, args.sample))
    types = {"movie", "series"}
    print("READ-ONLY source inventory; no catalog IDs, UUIDs, image URLs, titles, or tokens printed.")
    for provider in ("sh", "tvdb"):
        manifest = bridge.request_json(provider, "manifest.json") or {}
        cats = [x for x in manifest.get("catalogs") or [] if isinstance(x, dict)]
        category = Counter(x.get("type", "unknown") for x in cats)
        discover = []
        for c in cats:
            if c.get("type") not in types or not isinstance(c.get("id"), str):
                continue
            # Search, genre-required, or private-required catalogs must not
            # be guessed/requested without their configured extra.
            if any(isinstance(e, dict) and e.get("isRequired") for e in c.get("extra") or []):
                continue
            if not re.fullmatch(r"[A-Za-z0-9:_.,@-]{1,240}", c["id"]):
                continue
            discover.append(c)
        total = sum(category.values())
        print(provider.upper(), json.dumps({
            "catalogs": total, "by_type": dict(category),
            "direct_browse_sample_eligible": len(discover),
            "offers_catalog_resource": any(
                x == "catalog" or isinstance(x, dict) and x.get("name") == "catalog"
                for x in manifest.get("resources") or []
            ),
        }, sort_keys=True))
        for no, cat in enumerate(discover[:samples], 1):
            media_type = cat["type"]
            path = "catalog/{}/{}.json".format(media_type, cat["id"])
            body = bridge.request_json(provider, path) or {}
            rows = body.get("metas")
            if not isinstance(rows, list):
                print(f"  sample#{no}: invalid/unavailable page; no conclusions")
                continue
            missing = [x for x in rows if isinstance(x, dict)
                       and not bridge.usable_artwork(x.get("logo"))]
            eligible = [x for x in missing if isinstance(x.get("id"), str)
                        and re.fullmatch(r"[A-Za-z0-9:_.,@-]{1,240}", x["id"])
                        and not x["id"].startswith(("kitsu:", "mal:", "anidb:", "anilist:"))]
            own = 0
            other = 0
            for row in eligible[:6]:
                source = bridge.get_metadata(provider, media_type, row["id"])
                if not source or bridge.is_sh_anime_series(source, row["id"]):
                    continue
                if bridge.usable_artwork(source.get("logo")):
                    own += 1
                elif bridge.ids_of(source):
                    fallback = bridge.probe_other(
                        "tvdb" if provider == "sh" else "sh",
                        media_type, row["id"], source)
                    if fallback and bridge.usable_artwork(fallback.get("logo")):
                        other += 1
            print("  sample#{}: ".format(no) + json.dumps({
                "type": media_type, "preview_count": len(rows),
                "preview_logos": len(rows) - len(missing),
                "preview_missing_logo": len(missing),
                "scanned_missing": min(6, len(eligible)),
                "own_meta_logo_found": own,
                "other_verified_logo_found": other,
            }, sort_keys=True))
    print("NOTE: This estimates benefits on sampled pages, not overall library coverage.")
    print("NOTE: AIOStreams source catalog selections/order must be checked during UI cutover.")

if __name__ == "__main__":
    main()
