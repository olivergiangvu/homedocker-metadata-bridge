#!/usr/bin/env python3
"""Read-only ClearLogo audit in the deployed HomeDocker Metadata Bridge container.

Read SH/SH2 series artwork and the *running* bridge HTTP endpoint.
No credentials, provider URLs, media IDs, image URLs or history are printed.
No changes to the running bridge, cache/config, SH watch state, or AIOStreams.
Run in container: python3 - --search "This City Never Sleeps" ...
"""
import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter

MAX_BYTES = 1024 * 1024
# For safety, do not fetch arbitrary URLs found inside external metadata.
TRUSTED_ART_HOSTS = frozenset({
    "image.tmdb.org", "artworks.thetvdb.com", "assets.fanart.tv",
    "images.metahub.space", "metahub.space",
})

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None

HTTP = urllib.request.build_opener(NoRedirect())

def is_valid_art(value):
    return (
        isinstance(value, str)
        and bool(value.strip())
        and not any(x in value.lower() for x in
                    ("missing_thumbnail", "placeholder", "/no-image", "no_image"))
    )

def art_vendor(value):
    if not is_valid_art(value):
        return "-"
    try:
        host = (urllib.parse.urlsplit(value).hostname or "").lower()
    except ValueError:
        return "invalid"
    return {
        "image.tmdb.org": "TMDB",
        "artworks.thetvdb.com": "TVDB",
        "assets.fanart.tv": "Fanart",
        "images.metahub.space": "Metahub",
        "metahub.space": "Metahub",
    }.get(host, "Other/Proxy")

def head_art(value):
    if not is_valid_art(value):
        return "ABSENT"
    try:
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme != "https" or parsed.hostname not in TRUSTED_ART_HOSTS:
            return "NOT_TESTED_HOST"
        req = urllib.request.Request(value, method="HEAD",
            headers={"User-Agent": "HomeDockerLogoAudit/1.0"})
        with HTTP.open(req, timeout=6) as res:
            mime = (res.headers.get("Content-Type") or "").split(";")[0].strip()
            return "OK_IMAGE" if res.status == 200 and mime.startswith("image/") else "BAD_CONTENT"
    except urllib.error.HTTPError as ex:
        return "HTTP_" + str(ex.code)
    except (urllib.error.URLError, ValueError, TimeoutError):
        return "NETWORK_ERROR"

def local_meta(media, media_id):
    url = "http://127.0.0.1:18123/meta/" + urllib.parse.quote(media, safe="") + "/" + urllib.parse.quote(media_id, safe=":-_.") + ".json"
    try:
        with urllib.request.urlopen(url, timeout=15) as res:
            raw = res.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                return None, "TOO_LARGE"
            doc = json.loads(raw)
            return (doc.get("meta") if isinstance(doc, dict) else None), "OK"
    except urllib.error.HTTPError as ex:
        return None, "HTTP_" + str(ex.code)
    except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError):
        return None, "BRIDGE_UNAVAILABLE"

def choose_source(sh, sh2, merged):
    chosen = (merged or {}).get("logo")
    if not is_valid_art(chosen):
        return "NONE"
    if sh and chosen == sh.get("logo"):
        return "SH"
    if sh2 and chosen == sh2.get("logo"):
        return "SH2"
    return "OTHER"

def find_series(bridge, query):
    # Search only public user-given text; never print full catalog or IDs.
    items = bridge.search("series", query)
    if not isinstance(items, list):
        return None, "NO_RESULTS"
    match = [x for x in items if isinstance(x, dict)
             and bridge.normalize(x.get("name", "")) == bridge.normalize(query)
             and isinstance(x.get("id"), str)]
    if not match:
        return None, "NO_EXACT_MATCH"
    if len(set(x["id"] for x in match)) != 1:
        return None, "AMBIGUOUS"
    return match[0]["id"], "EXACT"

def check_one(bridge, label, media, media_id):
    sh = bridge.get_metadata("sh", media, media_id)
    # SH2 is currently consumed by Bridge only for series. Probe its movie
    # metadata as a *candidate*, but never assume it can safely replace SH.
    sh2 = bridge.get_metadata("tvdb", media, media_id)
    if sh and not sh2:
        sh2 = bridge.probe_other("tvdb", media, media_id, sh)
    if sh2 and not sh and media == "series":
        # Existing Bridge series path can resolve its SH identity via SH2.
        sh = bridge.probe_other("sh", media, media_id, sh2)
    sh2_raw_logo = art_vendor((sh2 or {}).get("logo"))
    sh2_raw_present = bool(sh2)
    verified = bool(sh and sh2 and bridge.strongly_same_series(sh, sh2))
    if not verified:
        sh2 = None  # Conflicting or weak identities must not supply logo.
    merged, runtime = local_meta(media, media_id)
    a, b, c = (sh or {}).get("logo"), (sh2 or {}).get("logo"), (merged or {}).get("logo")
    a_yes, b_yes, c_yes = is_valid_art(a), is_valid_art(b), is_valid_art(c)
    source = choose_source(sh, sh2, merged)
    return {
        "label": label,
        "media": media,
        "sh": art_vendor(a),
        "sh2": art_vendor(b) if verified else "-",
        "identity": "YES" if verified else "NO",
        "sh_meta": "YES" if sh else "NO",
        "sh2_meta_matched": "YES" if sh2 else "NO",
        "sh2_raw_meta": "YES" if sh2_raw_present else "NO",
        "sh2_raw_logo": sh2_raw_logo,
        "bridge_meta": "YES" if merged else "NO",
        "bridge": art_vendor(c),
        "origin": source,
        "reachable": head_art(c) if runtime == "OK" else runtime,
        "sh_present": a_yes, "sh2_present": b_yes, "bridge_present": c_yes,
        "gain": bool(c_yes and not a_yes),
    }

def self_test():
    assert is_valid_art("https://image.tmdb.org/t/p/original/foo.png")
    assert not is_valid_art("https://x/missing_thumbnail.png")
    assert art_vendor("https://assets.fanart.tv/fanart/foo.png") == "Fanart"
    assert art_vendor("https://a.private.invalid/x") == "Other/Proxy"
    assert choose_source({"logo": "a"}, {"logo": "b"}, {"logo": "b"}) == "SH2"
    assert head_art("http://127.0.0.1:3232/private") == "NOT_TESTED_HOST"
    print("PASS: logo audit source classification, read-only artwork host safeguards")

def main():
    p = argparse.ArgumentParser(description="Read-only actual deployed metadata/logo comparison (no tokens printed)")
    p.add_argument("--search", action="append", default=[], help="Exact public series title (may be repeated)")
    p.add_argument("--series", action="append", default=[], help="Known series meta ID (redacted from output)")
    p.add_argument("--movie", action="append", default=[], help="Known movie meta ID (redacted from output)")
    p.add_argument("--self-test", action="store_true")
    args = p.parse_args()
    if args.self_test:
        self_test()
        return
    if len(args.search) + len(args.series) + len(args.movie) == 0:
        p.error("Supply --search TITLE or --series ID or --movie ID")
    if len(args.search) + len(args.series) + len(args.movie) > 12:
        p.error("Maximum 12 cases per audit")
    # Import only after --self-test, so CI can run with no private .env.
    import bridge
    sample = []
    for name in args.search:
        try:
            media_id, status = find_series(bridge, name)
        except Exception as ex:
            media_id, status = None, "SEARCH_" + type(ex).__name__
        if media_id:
            sample.append((name, "series", media_id))
        else:
            print("NOT_RESOLVED:", name[:70].replace("|", "/"), status)
    for i, media_id in enumerate(args.series, 1):
        if not re.fullmatch(r"(?:tt\d+|(?:tmdb|tvdb):\d+)", media_id):
            p.error("Invalid series ID")
        sample.append(("series#" + str(i), "series", media_id))
    for i, media_id in enumerate(args.movie, 1):
        if not re.fullmatch(r"(?:tt\d+|(?:tmdb|tvdb):\d+)", media_id):
            p.error("Invalid movie ID")
        sample.append(("movie#" + str(i), "movie", media_id))
    print("READ-ONLY. Source: deployed SH/SH2 config + live Bridge :18123; no secrets or full URLs output.")
    print("Logo host indicates artwork source; this is not an API-provider coverage census.")
    print("| Title | Type | SH meta | SH logo | SH2 raw meta | SH2 raw logo | Same ID | Bridge meta | Bridge logo | Logo source | Image HEAD |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    stats = Counter()
    for title, kind, media_id in sample:
        try:
            row = check_one(bridge, title, kind, media_id)
        except Exception as ex:
            print("| {} | {} | ERROR:{} | | | | | |".format(
                title.replace("|", "/")[:55], kind, type(ex).__name__))
            stats["error"] += 1
            continue
        cols = [row["label"], row["media"], row["sh_meta"], row["sh"],
                row["sh2_raw_meta"], row["sh2_raw_logo"], row["identity"],
                row["bridge_meta"], row["bridge"], row["origin"], row["reachable"]]
        print("| " + " | ".join(str(x).replace("|", "/")[:65] for x in cols) + " |", flush=True)
        stats["cases"] += 1
        stats["SH_meta_exists"] += row["sh_meta"] == "YES"
        stats["Bridge_meta_exists"] += row["bridge_meta"] == "YES"
        stats["SH_logo"] += row["sh_present"]
        stats["SH2_verified_logo"] += row["sh2_present"]
        stats["Bridge_logo"] += row["bridge_present"]
        stats["Bridge_gain"] += row["gain"]
        stats["Bridge_image_OK"] += row["reachable"] == "OK_IMAGE"
    print("SUMMARY:", json.dumps(dict(stats), sort_keys=True))
    print("NOTE: N/A or NOT_TESTED_HOST != missing logo; Bridge gain requires verified SH2 identity.")
    print("NOTE: SH2 raw movie data is diagnostic only. Same ID=NO means it is NOT eligible for fallback.")
    print("NOTE: Bridge v0.2 passes movies through SH unchanged; do not infer movie fallback from SH2 data.")
    print("NOTE: If Bridge logo exists but UI lacks logo, inspect AIOStreams ImageTags.Logo / client cache.")

if __name__ == "__main__":
    main()
