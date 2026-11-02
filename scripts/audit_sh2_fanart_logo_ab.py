#!/usr/bin/env python3
"""HomeDocker read-only A/B coverage probe: SH+SH2 versus SH2 Fanart.tv logo.

Run as root on the Docker host. Secrets stay in memory; output ONLY counts.
Uses existing keys already set in AIOMetadata container environment.
Does not edit configs, source, Docker, caches or Watch State. 
"""
import argparse
import collections
import concurrent.futures
import getpass
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request

BRIDGE_CONTAINER = "homedocker-metadata-bridge"
MAX_FANART_REQUESTS = 36
ALLOWED_KEY_VARS = (
    "FANART_API_PROJECT_KEY",
    "FANART_API_KEY",
    "BUILT_IN_FANART_API_KEY",
)
COLLECT = r'''
import collections,json,re,urllib.parse
import bridge
TYPES=("movie","series")
def valid(c):
    if not isinstance(c,dict) or c.get("type") not in TYPES: return False
    if not isinstance(c.get("id"),str) or not bridge._VALID_ID.fullmatch(c["id"]): return False
    req=[e for e in c.get("extra",[]) if isinstance(e,dict) and e.get("isRequired")]
    if len(req)>1 or any(e.get("name")!="genre" for e in req): return False
    if req and not any(isinstance(o,str) and o for o in req[0].get("options",[])): return False
    return True
def picked(c):
    required=[e for e in c.get("extra",[]) if isinstance(e,dict) and e.get("isRequired")]
    genre=required[0]["options"][0] if required else None
    return ("genre="+urllib.parse.quote(genre,safe="")) if genre else ""
counts=collections.Counter()
candidate_ids={}
seen_missing=set()
for src in ("sh","tvdb"):
    m=bridge.request_json(src,"manifest.json") or {}
    cs=[c for c in m.get("catalogs",[]) if valid(c)]
    plain={t:[] for t in TYPES}; genre={t:[] for t in TYPES}
    for c in cs: (genre if picked(c) else plain)[c["type"]].append(c)
    selection=[]
    for group,limit in ((plain,8),(genre,3)):
        for i in range(limit):
            for typ in TYPES:
                if i<len(group[typ]): selection.append(group[typ][i])
    if src=="tvdb": selection=selection[:6]
    for c in selection:
        off=0
        for page in range(2):
            extra=picked(c)
            if off:
                if not any(e.get("name")=="skip" for e in c.get("extra",[]) if isinstance(e,dict)): break
                extra += ("&" if extra else "")+"skip="+str(off)
            path="catalog/{}/{}".format(c["type"],urllib.parse.quote(c["id"],safe="-_."))
            if extra: path+="/"+extra
            payload=bridge.request_json(src,path+".json")
            if not isinstance(payload,dict) or not isinstance(payload.get("metas"),list): break
            counts["pages"]+=1
            rows=payload["metas"]
            for row in rows:
                if not isinstance(row,dict): continue
                counts["preview_occurrences"]+=1
                if bridge.usable_artwork(row.get("logo")):
                    counts["with_logo"]+=1
                    continue
                counts["missing_logo"]+=1
                mid=row.get("id")
                if not isinstance(mid,str) or not bridge._VALID_ID.fullmatch(mid): continue
                if (row.get("type") or c["type"])!=c["type"]: continue
                key=(c["type"],mid)
                if key in seen_missing: continue
                seen_missing.add(key)
                if len(seen_missing)>60: continue
                own=bridge.get_metadata(src,c["type"],mid)
                if not own: counts["own_meta_missing"]+=1; continue
                if bridge.is_sh_anime_series(own,mid): counts["anime_excluded"]+=1; continue
                if bridge.usable_artwork(own.get("logo")):
                    counts["own_full_logo"]+=1;continue
                ids=bridge.ids_of(own)
                if not ids: counts["unverified_identity"]+=1; continue
                other=bridge.probe_other("tvdb" if src=="sh" else "sh",c["type"],mid,own)
                if other and bridge.usable_artwork(other.get("logo")):
                    counts["already_bridge_recoverable"]+=1;continue
                counts["still_missing_verified_id"]+=1
                canon=ids.get("tmdb" if c["type"]=="movie" else "tvdb")
                if canon:
                    candidate_ids[(c["type"],canon)]=True
                else:
                    counts["missing_fanart_lookup_id"]+=1
            off+=len(rows)
            if not rows: break
print(json.dumps({"counts":dict(counts),"candidates":[{"type":k[0],"id":k[1]} for k in candidate_ids]}))
'''

def cmd(argv, *, input_text=None):
    p = subprocess.run(argv, input=input_text, text=True, capture_output=True, check=False, timeout=150)
    if p.returncode:
        raise RuntimeError("Container command failed; exit status {}".format(p.returncode))
    return p.stdout


def inspect_key():
    names = cmd(["docker", "ps", "--format", "{{.Names}}"]).splitlines()
    candidates = [x for x in names if "aiometadata" in x.lower() and BRIDGE_CONTAINER not in x]
    if not candidates:
        return None, "NO_AIOMETADATA_CONTAINER_FOUND", 0
    source_count=0
    for name in candidates:
        inspect = json.loads(cmd(["docker", "inspect", name]))
        envlines = (inspect[0].get("Config") or {}).get("Env") or []
        values = dict(s.split("=", 1) for s in envlines if "=" in s)
        source_count += 1
        # Prefer project key; other keys may be a project or personal key.
        for key in ALLOWED_KEY_VARS:
            val=values.get(key,"").strip()
            if val:
                return val, "AIOMETADATA_ENV_{}".format(key), source_count
    return None, "NO_SERVER_ENV_FANART_KEY",source_count


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def get_fanart(kind, canonical_id, key, *, key_kind="project"):
    if not re.fullmatch(r"[0-9]{1,12}",canonical_id):
        return "INVALID_ID"
    target = "movies" if kind=="movie" else "tv"
    url="https://webservice.fanart.tv/v3.2/{}/{}".format(target,canonical_id)
    try:
        auth_header = "client-key" if key_kind == "personal" else "api-key"
        req=urllib.request.Request(url,headers={
            auth_header:key,"Accept":"application/json",
            "User-Agent":"HomeDocker-ClearLogo-Coverage-Audit/1.0"})
        with urllib.request.build_opener(NoRedirect()).open(req,timeout=12) as r:
            body=json.load(r)
    except urllib.error.HTTPError as exc:
        if exc.code in (404,400): return "NOT_FOUND"
        if exc.code==401: return "AUTH_ERROR"
        if exc.code==429: return "RATE_LIMITED"
        return "HTTP_OTHER"
    except (TimeoutError, OSError, ValueError):
        return "NETWORK_ERROR"
    if not isinstance(body,dict): return "BAD_RESPONSE"
    used=("hdmovielogo" if kind=="movie" else "hdtvlogo")
    alternate=("movielogo" if kind=="movie" else "clearlogo")
    used_images=body.get(used)
    alt_images=body.get(alternate)
    if isinstance(used_images,list) and any(isinstance(i,dict) and i.get("url") for i in used_images):
        return "SUPPORTED_LOGO_AVAILABLE"
    if isinstance(alt_images,list) and any(isinstance(i,dict) and i.get("url") for i in alt_images):
        return "ONLY_LEGACY_LOGO_AVAILABLE"
    return "NO_LOGO"

def main():
    parser = argparse.ArgumentParser(description="Read-only Fanart.tv coverage. Never pass keys as command-line arguments.")
    key_options = parser.add_mutually_exclusive_group()
    key_options.add_argument("--prompt-personal-key", action="store_true",
                             help="Prompt from this process's controlling TTY; hidden input")
    key_options.add_argument("--personal-key-stdin", action="store_true",
                             help="Read one key from stdin pipe, captured with hidden shell read; never command-line args")
    args = parser.parse_args()
    print("READ-ONLY A/B: current SH+SH2 versus Fanart.tv selection in SH2; no secrets or IDs printed.",flush=True)
    try: raw=cmd(["docker","exec","-i",BRIDGE_CONTAINER,"python3","-"],input_text=COLLECT)
    except Exception:
        print("ERROR: cannot inspect Bridge source cohorts (nothing changed)")
        return 1
    try: data=json.loads(raw)
    except (TypeError,ValueError):
        print("ERROR: source cohort response invalid (nothing changed)")
        return 1
    stats=data.get("counts") or {}
    candidates=data.get("candidates") or []
    print("CURRENT_BASELINE",json.dumps(stats,sort_keys=True))
    print("FANART_ELIGIBLE_UNIQUE_IDS",len(candidates))
    if not candidates:
        print("RESULT: no verified missing titles with Fanart lookup IDs in sample.")
        return 0
    key_kind = "project"
    if args.personal_key_stdin:
        try:
            # A pipe is deliberately required: never echo the key while a user
            # types, never inspect a shell argument or environment variable.
            if sys.stdin.isatty():
                print("ERROR: --personal-key-stdin expects a private pipe; no changes")
                return 1
            key = sys.stdin.readline(4096).strip()
            if not key or len(key) > 2048:
                print("ERROR: missing or oversized key; no changes")
                return 1
        except (OSError, UnicodeError, ValueError):
            print("ERROR: failed to read supplied key; no changes")
            return 1
        key_kind = "personal"
        print("AUTH_SOURCE: one-use personal key from anonymous pipe (not saved)")
    elif args.prompt_personal_key:
        try:
            # /dev/tty is required so a piped/heredoc invocation cannot echo
            # the key to stdout or silently fall back to shell input.
            with open("/dev/tty", "r+") as terminal:
                key = getpass.getpass("Fanart.tv PERSONAL API key (hidden, memory only): ",
                                      stream=terminal).strip()
        except (OSError, EOFError, KeyboardInterrupt):
            print("ERROR: interactive TTY unavailable or input cancelled; no changes")
            return 1
        key_kind = "personal"
        print("AUTH_SOURCE: interactive personal key (not saved)")
    else:
        try:
            key, source, count = inspect_key()
        except Exception:
            print("KEY_STATUS: inspection failed; no keys printed")
            return 1
        print("AIOMETADATA_SERVER_KEY_PRESENT",bool(key))
        print("AIOMETADATA_CONTAINERS_SCANNED",count)
        if not key:
            print("RESULT: no Fanart key found in container environment; profile/UI keys were NOT inspected.")
            print("Do NOT switch SH2 logo provider yet; it may fall back entirely to current TMDB logos.")
            return 0
    if not key:
        print("ERROR: no API key supplied; no changes")
        return 1
    candidate=candidates[:MAX_FANART_REQUESTS]
    out=collections.Counter()
    # No image body downloads. API only returns artwork metadata lists.
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        items=pool.map(lambda c:get_fanart(c["type"],c["id"],key,key_kind=key_kind),candidate)
        for label in items: out[label]+=1
    print("FANART_API_COHORT",json.dumps(dict(out),sort_keys=True))
    print("POTENTIAL_ADD_ON_SH2_FANART",out["SUPPORTED_LOGO_AVAILABLE"])
    print("LEGACY_FANART_LOGOS_NOT_SELECTED_BY_AIOMETADATA",out["ONLY_LEGACY_LOGO_AVAILABLE"])
    print("AUTH_OR_NETWORK_ERRORS",sum(out[k] for k in ("AUTH_ERROR","RATE_LIMITED","HTTP_OTHER","NETWORK_ERROR","BAD_RESPONSE")))
    print("LIMITATIONS: sample-only, actual SH2 meta A/B not yet performed; artwork URLs not downloaded.")
    print("LIMITATIONS: switching provider may change which EXISTING logos are selected, so verify no regressions before cutover.")
    return 0

if __name__=="__main__":
    try:sys.exit(main())
    except Exception:
        print("ERROR: audit failed without modifying any configuration; no secrets printed")
        sys.exit(1)
