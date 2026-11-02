#!/usr/bin/env bash
# Guarded SH Collection source-reference hotfix for an already-enabled Catalog Bridge.
# Dry-run the candidate against real SH Collection metadata BEFORE file changes.
# Then atomically replace Bridge code, recreate ONLY the Bridge container,
# verify the collection references in live HTTP and regressions; rollback on fail.
set -Eeuo pipefail
CONTAINER=homedocker-metadata-bridge
SERVICE=metadata-bridge
BASE=http://127.0.0.1:18123
OLD_BLOB=b2703d6a5c3ce8c77d06b57d2d12cf005daeba0a
NEW_BLOB=91f85ae4402cf3b4637d0aa9de8c0c99eeb94fd5
COMPOSE_BLOB=efa1c013da1da8dd4378f59fbe5c60e04a52ea05
fail() { printf 'ABORT: %s\n' "$*" >&2; exit 1; }
[[ "$EUID" == 0 && "$#" == 1 && -f "$1" ]] || fail "sudo bash script candidate-bridge.py"
for cmd in docker git python3 curl; do command -v "$cmd" >/dev/null || fail "$cmd missing"; done
docker compose version >/dev/null || fail "Docker Compose unavailable"
NEW="$(readlink -f "$1")"
[[ "$(git hash-object "$NEW")" == "$NEW_BLOB" ]] || fail "reviewed candidate blob differs; no change"
python3 -c 'import ast,sys; ast.parse(open(sys.argv[1]).read())' "$NEW" || fail "candidate syntax invalid"
[[ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null)" == true ]] || fail "Bridge not running"
PROJECT="$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' "$CONTAINER")"
LABEL="$(docker inspect -f '{{index .Config.Labels "com.docker.compose.service"}}' "$CONTAINER")"
SOURCE="$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/app/bridge.py"}}{{.Source}}{{end}}{{end}}' "$CONTAINER")"
RW="$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/app/bridge.py"}}{{.RW}}{{end}}{{end}}' "$CONTAINER")"
[[ -n "$PROJECT" && "$PROJECT" != "<no value>" && "$LABEL" == "$SERVICE" ]] || fail "unexpected Bridge Compose identity"
[[ -f "$SOURCE" && "$RW" == false ]] || fail "no read-only bridge.py bind mount"
SOURCE="$(readlink -f "$SOURCE")"
[[ "$(basename "$SOURCE")" == "bridge.py" ]] || fail "unexpected source filename"
ROOT="$(dirname "$SOURCE")"
[[ -f "$ROOT/compose.yml" && -f "$ROOT/.env" && -f "$ROOT/overrides.json" ]] || fail "expected stack files missing"
[[ "$(git hash-object "$SOURCE")" == "$OLD_BLOB" ]] || fail "live source is not known v0.3, no change"
[[ "$(git hash-object "$ROOT/compose.yml")" == "$COMPOSE_BLOB" ]] || fail "Compose not known v0.3, no change"
[[ "$(docker inspect -f '{{range .Config.Env}}{{if eq . "CATALOG_PASSTHROUGH_ENABLED=1"}}enabled{{end}}{{end}}' "$CONTAINER")" == enabled ]] || fail "Catalog feature is not enabled"
cd "$ROOT"
docker compose -p "$PROJECT" -f compose.yml config -q >/dev/null || fail "Compose invalid"
[[ "$(curl -fsS --max-time 8 "$BASE/health" | python3 -c 'import sys,json;print(json.load(sys.stdin)["version"])')" == 0.3.0 ]] || fail "wrong Bridge health"

echo "PASS: candidate blob, live v0.3 source, Compose and Catalog opt-in checked"
# This does NOT execute candidate __main__ and does not write state. It reads
# configured SH catalog+meta GETs to prove at least one live source is eligible.
docker exec -i "$CONTAINER" python3 -c '
import sys, types, copy, urllib.parse
mod=types.ModuleType("collection_candidate")
exec(compile(sys.stdin.read(),"reviewed_bridge.py","exec"),mod.__dict__)
source_manifest=mod.request_json("sh","manifest.json") or {}
known={(c["type"],c["id"]) for c in source_manifest.get("catalogs",[])
       if isinstance(c,dict) and isinstance(c.get("type"),str) and isinstance(c.get("id"),str)}
collections=[c for c in source_manifest.get("catalogs",[]) if isinstance(c,dict)
             and c.get("type")=="collection" and isinstance(c.get("id"),str)]
looked=0
matched=0
for cat in collections:
    data=None
    for suffix in ("", "/genre=None"):
        path="catalog/collection/"+urllib.parse.quote(cat["id"],safe="-_.")+suffix+".json"
        p=mod.request_json("sh",path)
        if isinstance(p,dict) and isinstance(p.get("metas"),list):
            data=p
            break
    if data is None: continue
    pending=[x for x in data["metas"][:8] if isinstance(x,dict)]
    while pending and looked < 65 and not matched:
        row=pending.pop(0)
        ident=row.get("id")
        if not isinstance(ident,str) or not mod._VALID_ID.fullmatch(ident): continue
        orig=mod.request_json("sh","meta/collection/"+urllib.parse.quote(ident,safe=":-_.")+".json")
        if not isinstance(orig,dict) or not isinstance(orig.get("meta"),dict): continue
        collection=orig["meta"].get("collection")
        if not isinstance(collection,dict): continue
        looked += 1
        if isinstance(collection.get("items"),list):
            pending.extend(x for x in collection["items"][:8] if isinstance(x,dict))
        original_sources=collection.get("sources") or []
        candidates=[s for s in original_sources if isinstance(s,dict)
             and (s.get("type"),s.get("catalogId")) in known
             and not s.get("addonId")]
        if not candidates: continue
        revised=mod.rebind_collection_sources(orig,"sh")
        assert orig is not revised, "source expected to be deep-copied"
        assert orig["meta"]["collection"]["items"] == revised["meta"]["collection"]["items"], "nested folders changed"
        b=copy.deepcopy(revised)
        b["meta"]["collection"]["sources"]=copy.deepcopy(original_sources)
        assert b == orig, "non-source fields changed"
        for old,new in zip(original_sources,revised["meta"]["collection"]["sources"]):
            if old in candidates:
                assert new["catalogId"]==mod.proxied_catalog_id("sh",old["catalogId"]), "wrong mapped source ID"
        matched += len(candidates)
        break
    if matched: break
assert matched >= 1, "No real source reference verified. Do not deploy until diagnosed."
print("PASS: %d real SH Collection source refs proven and source-only transformed (%d folders checked)" % (matched,looked))
' < "$NEW" || fail "LIVE READ-ONLY COLLECTION PREFLIGHT FAILED; no file changed"

# Post-deploy regression snapshots; no movie/series IDs in stdout.
baseline="$(python3 - "$BASE" <<'PY'
import hashlib,json,sys,urllib.request
base=sys.argv[1]
def get(url):
    with urllib.request.urlopen(base+url,timeout=25) as resp:
        assert resp.status==200
        return json.load(resp)
def digest(v):
    return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
m=get('/manifest.json')
assert len(m["catalogs"])==198 and m["version"]=="0.3.0"
print("MANIFEST="+digest(m))
series=get('/meta/series/tt6150576.json')["meta"]
print("EPISODES="+digest([(i.get("id"),i.get("season"),i.get("episode")) for i in series.get("videos",[]) if isinstance(i,dict)]))
for n,mid in enumerate(("tt38793125","tt37522541"),1):
    v=get("/meta/movie/"+mid+".json")["meta"]
    assert str(v.get("logo","")).startswith("https://")
    print("MOVIE%d="%n + digest(v))
PY
)" || fail "live baseline validation failed; no file changed"

BKROOT=/var/backups/homedocker-metadata-bridge
mkdir -p "$BKROOT"
chmod 700 "$BKROOT"
BACKUP="$BKROOT/collection-rebind-$(date -u +%Y%m%dT%H%M%SZ)"
[[ ! -e "$BACKUP" ]] || fail "backup already exists"
mkdir -m 700 "$BACKUP"
cp -p "$SOURCE" "$BACKUP/bridge.py"
[[ "$(git hash-object "$BACKUP/bridge.py")" == "$OLD_BLOB" ]] || fail "backup integrity error"
echo "BACKUP: $BACKUP/bridge.py (no .env copied or printed)"
swap() {
    local temp
    temp="$(mktemp "$SOURCE.new.XXXXXX")"
    cp "$1" "$temp"
    chown --reference="$SOURCE" "$temp"
    chmod --reference="$SOURCE" "$temp"
    mv -f "$temp" "$SOURCE"
}
recreate() {
    docker compose -p "$PROJECT" -f compose.yml up -d \
       --no-deps --no-build --pull never --force-recreate "$SERVICE"
}
swapped=0
complete=0
cleanup() {
    local status=$?
    trap - EXIT
    if [[ "$swapped" == 1 && "$complete" != 1 ]]; then
        echo "ROLLBACK: restoring original bridge.py and recreating Bridge only" >&2
        swap "$BACKUP/bridge.py"
        recreate || echo "WARNING: rollback source restored; recreate needs manual attention" >&2
    fi
    exit "$status"
}
trap cleanup EXIT
swapped=1
swap "$NEW"
recreate
ready=0
for i in $(seq 1 15); do
    if curl -fsS --max-time 4 "$BASE/health" >/dev/null 2>&1; then ready=1; break; fi
    sleep 2
done
[[ "$ready" == 1 ]] || fail "Bridge health failed"
[[ "$(git hash-object "$SOURCE")" == "$NEW_BLOB" ]] || fail "new bridge.py blob mismatch"
BASELINE="$baseline" python3 - "$BASE" <<'PY'
import json,sys,os,hashlib,urllib.request
base=sys.argv[1]
before=dict(x.split("=",1) for x in os.environ["BASELINE"].splitlines())
def get(path):
    with urllib.request.urlopen(base+path,timeout=25) as r:
        assert r.status==200
        return json.load(r)
def digest(v):
    return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()
m=get("/manifest.json")
assert digest(m)==before["MANIFEST"],"Catalog manifest changed"
s=get("/meta/series/tt6150576.json")["meta"]
assert digest([(i.get("id"),i.get("season"),i.get("episode")) for i in s.get("videos",[]) if isinstance(i,dict)])==before["EPISODES"]
for n,mid in enumerate(("tt38793125","tt37522541"),1):
    assert digest(get("/meta/movie/"+mid+".json")["meta"])==before["MOVIE%d"%n],"movie detail changed"
print("PASS: unchanged manifest, Midnight Diner episodes and two movie logos")
PY
docker exec -i "$CONTAINER" python3 - <<'PY'
import bridge, urllib.parse, urllib.request, json
sh=bridge.request_json("sh","manifest.json") or {}
known={(c["type"],c["id"]) for c in sh.get("catalogs",[]) if isinstance(c,dict)
       and isinstance(c.get("type"),str) and isinstance(c.get("id"),str)}
found=0
for cat in [x for x in sh.get("catalogs",[]) if isinstance(x,dict) and x.get("type")=="collection"]:
    rows=None
    for suffix in ("","/genre=None"):
        body=bridge.request_json("sh","catalog/collection/"+urllib.parse.quote(cat["id"],safe="-_.")+suffix+".json")
        if isinstance(body,dict) and isinstance(body.get("metas"),list):
            rows=body["metas"]
            break
    if not rows: continue
    queue=[r for r in rows[:8] if isinstance(r,dict)]
    while queue and found<1:
        item=queue.pop(0)
        mid=item.get("id")
        if not isinstance(mid,str) or not bridge._VALID_ID.fullmatch(mid): continue
        full=bridge.request_json("sh","meta/collection/"+urllib.parse.quote(mid,safe=":-_.")+".json")
        coll=(full or {}).get("meta",{}).get("collection") if isinstance(full,dict) else None
        if not isinstance(coll,dict): continue
        queue.extend(c for c in (coll.get("items") or [])[:8] if isinstance(c,dict))
        pairs=[s for s in coll.get("sources",[]) if isinstance(s,dict)
               and (s.get("type"),s.get("catalogId")) in known and not s.get("addonId")]
        if not pairs: continue
        with urllib.request.urlopen("http://127.0.0.1:18123/meta/collection/"+urllib.parse.quote(mid,safe=":-_.")+".json",timeout=20) as resp:
            served=json.load(resp)
        sc=served["meta"]["collection"]["sources"]
        for original,rewritten in zip(coll.get("sources",[]),sc):
            if original in pairs:
                target=bridge.proxied_catalog_id("sh",original["catalogId"])
                assert rewritten["catalogId"]==target and (original["type"],target) in {
                    (c["type"],c["id"]) for c in bridge.manifest()["catalogs"]
                }, "live Collection source not advertised"
                found+=1
        break
    if found: break
assert found,"no real collection source successfully verified via live HTTP"
print("PASS: %d actual Collection sources resolve to advertised Bridge catalogs" % found)
PY
complete=1
echo "SUCCESS: Collection catalog source references repaired on Bridge only."
echo "AIOStreams Official, SH/SH2 settings and SH Watch State were not changed."
