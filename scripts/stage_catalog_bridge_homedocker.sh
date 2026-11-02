#!/usr/bin/env bash
# Stage a reviewed Bridge catalog implementation with Catalog PASSTHROUGH OFF.
# STRICT source/Compose pins, live read-only parity preflight and rollback.
# Run as root on HomeDocker. Args: candidate bridge.py, candidate compose.yml.
set -Eeuo pipefail
OLD_BRIDGE=11b0f8f125bf9239dfb2a7ddb7a0ef40132e9ec9
OLD_COMPOSE=21a0fa4e12a72fe6b62c4abb162ec638b4f8d4b7
NEW_BRIDGE="b2703d6a5c3ce8c77d06b57d2d12cf005daeba0a"
NEW_COMPOSE=efa1c013da1da8dd4378f59fbe5c60e04a52ea05
CONTAINER=homedocker-metadata-bridge
SERVICE=metadata-bridge
BASE=http://127.0.0.1:18123
fail() { echo "ABORT: $*" >&2; exit 1; }
[[ "$EUID" == 0 && "$#" == 2 && -f "$1" && -f "$2" ]] || fail "sudo bash script candidate-bridge.py candidate-compose.yml"
for bin in docker git curl python3; do command -v "$bin" >/dev/null || fail "$bin missing"; done
docker compose version >/dev/null || fail "docker compose unavailable"
CANDIDATE="$(readlink -f "$1")"
CANDIDATE_COMPOSE="$(readlink -f "$2")"
[[ "$(git hash-object "$CANDIDATE")" == "$NEW_BRIDGE" ]] || fail "reviewed bridge.py SHA mismatch"
[[ "$(git hash-object "$CANDIDATE_COMPOSE")" == "$NEW_COMPOSE" ]] || fail "reviewed compose.yml SHA mismatch"
python3 -c 'import ast,sys; ast.parse(open(sys.argv[1]).read())' "$CANDIDATE" || fail "candidate python syntax"
[[ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null)" == true ]] || fail "Bridge not running"
PROJECT="$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' "$CONTAINER")"
LABEL="$(docker inspect -f '{{index .Config.Labels "com.docker.compose.service"}}' "$CONTAINER")"
[[ "$LABEL" == "$SERVICE" && -n "$PROJECT" && "$PROJECT" != "<no value>" ]] || fail "unexpected Compose service/project"
SOURCE="$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/app/bridge.py"}}{{.Source}}{{end}}{{end}}' "$CONTAINER")"
RW="$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/app/bridge.py"}}{{.RW}}{{end}}{{end}}' "$CONTAINER")"
[[ -n "$SOURCE" && -f "$SOURCE" && "$RW" == false ]] || fail "live bridge source is not a read-only bind-mounted file"
SOURCE="$(readlink -f "$SOURCE")"
ROOT="$(dirname "$SOURCE")"
COMPOSE="$ROOT/compose.yml"
[[ "$(basename "$SOURCE")" == bridge.py && -f "$COMPOSE" && -f "$ROOT/.env" && -f "$ROOT/overrides.json" ]] || fail "unexpected stack files"
[[ "$(git hash-object "$SOURCE")" == "$OLD_BRIDGE" ]] || fail "deployed source is not the verified PR #16, nothing changed"
[[ "$(git hash-object "$COMPOSE")" == "$OLD_COMPOSE" ]] || fail "local Compose differs from reviewed base; nothing changed"
[[ "$(docker inspect -f '{{range .Config.Env}}{{if eq . "CATALOG_PASSTHROUGH_ENABLED=1"}}on{{end}}{{end}}' "$CONTAINER")" != on ]] || fail "Catalog feature already enabled"
cd "$ROOT"
docker compose -p "$PROJECT" -f compose.yml config -q >/dev/null || fail "current Compose invalid"
echo "PASS: source, Compose, Docker mounts and pinned candidate verified"

# Boot code under a non-__main__ module name inside the EXISTING container.
# The candidate only issues read-only upstream GETs, with no source change.
docker exec -i -e CATALOG_PASSTHROUGH_ENABLED=1 "$CONTAINER" python3 -c '
import sys, time, copy
ns = {"__name__": "catalog_dry_run", "__file__": "candidate-bridge.py"}
exec(compile(sys.stdin.read(), "candidate-bridge.py", "exec"), ns)
m = type("Candidate", (), ns)
catalogs = m.source_catalogs()
orig = []
for provider in ("sh", "tvdb"):
    src = m.request_json(provider, "manifest.json") or {}
    orig.extend((provider, c) for c in src.get("catalogs", []) if isinstance(c, dict))
assert len(orig) == len(catalogs), "candidate mirrored only %d of %d source catalogs" % (len(catalogs),len(orig))
for (provider, raw), cloned in zip(orig, catalogs):
    assert cloned["id"] == m.proxied_catalog_id(provider, raw["id"])
    assert {k:v for k,v in cloned.items() if k != "id"} == {k:v for k,v in raw.items() if k != "id"}
print("PASS: all %d source catalog descriptors mirrored with order/filters preserved" % len(orig))
for provider in ("sh", "tvdb"):
    samples = 0
    for p,c in orig:
        if p != provider or c.get("type") not in ("movie","series"): continue
        if any(isinstance(x,dict) and x.get("isRequired") for x in c.get("extra") or []): continue
        raw = m.request_json(provider, "catalog/%s/%s.json" % (c["type"],c["id"])) or {}
        if not isinstance(raw.get("metas"),list): continue
        t = time.monotonic()
        enhanced = m.passthrough_catalog(provider, c["type"], c["id"])
        if not isinstance(enhanced,dict): raise AssertionError("invalid enhanced catalog")
        before=copy.deepcopy(raw)
        after=copy.deepcopy(enhanced)
        assert len(before["metas"]) == len(after["metas"])
        additions=0
        for original, new in zip(before["metas"],after["metas"]):
            if not isinstance(original,dict): continue
            if "logo" not in original and isinstance(new,dict) and "logo" in new:
                additions += 1
                new.pop("logo")
            elif isinstance(original.get("logo"),str) and not original["logo"] and isinstance(new,dict) and new.get("logo"):
                additions += 1
                new["logo"] = original["logo"]
        assert after == before, "non-logo catalog preview changed"
        samples += 1
        print("PASS: %s sample %d: %d records, +%d logos, unchanged other fields, %.1fs" % (
            provider.upper(), samples, len(before["metas"]), additions, time.monotonic()-t))
        if samples >= (3 if provider == "sh" else 1): break
    assert samples, "no browsable sample found for "+provider
' < "$CANDIDATE" || fail "READ-ONLY candidate SH/SH2 parity check failed; no files changed"

# Baseline without printing titles, IDs, upstream URLs, media, or credentials.
baseline="$(python3 - "$BASE" <<'PY'
import hashlib,json,sys,urllib.request
host=sys.argv[1]
def read(path):
    with urllib.request.urlopen(host+path,timeout=25) as res:
        assert res.status==200
        return json.load(res)
def digest(obj):
    return hashlib.sha256(json.dumps(obj,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
manifest=read('/manifest.json')
assert manifest.get('version')=='0.2.0'
print("MANIFEST="+digest({"id":manifest.get("id"),"resources":manifest.get("resources"),"catalogs":manifest.get("catalogs")}))
series=read('/meta/series/tt6150576.json')['meta']
print("EPISODES="+digest([(x.get('id'),x.get('season'),x.get('episode')) for x in series.get('videos',[]) if isinstance(x,dict)]))
for i,mid in enumerate(('tt38793125','tt37522541'),1):
    meta=read('/meta/movie/'+mid+'.json')['meta']
    assert isinstance(meta.get('logo'),str) and meta['logo'].startswith('https://')
    print("MOVIE%d="%i+digest(meta))
PY
)" || fail "live baseline fingerprints failed; no source changed"
mkdir -p /var/backups/homedocker-metadata-bridge
chmod 700 /var/backups/homedocker-metadata-bridge
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP="/var/backups/homedocker-metadata-bridge/catalog-stage-$STAMP"
mkdir -m 700 "$BACKUP"
cp -p "$SOURCE" "$BACKUP/bridge.py"
cp -p "$COMPOSE" "$BACKUP/compose.yml"
[[ "$(git hash-object "$BACKUP/bridge.py")" == "$OLD_BRIDGE" && "$(git hash-object "$BACKUP/compose.yml")" == "$OLD_COMPOSE" ]] || fail "backup verification failed"
echo "BACKUP: $BACKUP (source and Compose only, not .env)"

replace_file() {
  local source="$1" destination="$2" temp
  temp="$(mktemp "$destination.stage.XXXXXX")"
  cp "$source" "$temp"
  chown --reference="$destination" "$temp"
  chmod --reference="$destination" "$temp"
  mv -f "$temp" "$destination"
}
swapped=0
complete=0
restart_bridge() {
  docker compose -p "$PROJECT" -f compose.yml up -d --no-deps --no-build --pull never --force-recreate "$SERVICE"
}
cleanup() {
  local status=$?
  trap - EXIT
  if [[ "$swapped" == 1 && "$complete" != 1 ]]; then
    echo "ROLLBACK: restoring source+Compose and recreating Bridge" >&2
    replace_file "$BACKUP/bridge.py" "$SOURCE"
    replace_file "$BACKUP/compose.yml" "$COMPOSE"
    restart_bridge || echo "WARNING: rollback source restored, but restart needs manual attention" >&2
  fi
  exit "$status"
}
trap cleanup EXIT
swapped=1
replace_file "$CANDIDATE" "$SOURCE"
replace_file "$CANDIDATE_COMPOSE" "$COMPOSE"
docker compose -p "$PROJECT" -f compose.yml config -q >/dev/null || fail "updated Compose invalid"
restart_bridge
ready=0
for i in $(seq 1 15); do
  if curl -fsS --max-time 3 "$BASE/health" >/dev/null 2>&1; then ready=1; break; fi
  sleep 2
done
[[ "$ready" == 1 ]] || fail "Bridge health did not become ready"
[[ "$(docker inspect -f '{{range .Config.Env}}{{if eq . "CATALOG_PASSTHROUGH_ENABLED=0"}}off{{end}}{{end}}' "$CONTAINER")" == off ]] || fail "new feature not safely disabled"
[[ "$(git hash-object "$SOURCE")" == "$NEW_BRIDGE" ]] || fail "new Bridge source unexpected"
[[ "$(git hash-object "$COMPOSE")" == "$NEW_COMPOSE" ]] || fail "new Compose source unexpected"

BASELINE="$baseline" python3 - "$BASE" <<'PY'
import hashlib,json,os,sys,urllib.request
host=sys.argv[1]
old=dict(x.split('=',1) for x in os.environ["BASELINE"].splitlines())
def get(path):
    with urllib.request.urlopen(host+path,timeout=25) as r:
        assert r.status==200
        return json.load(r)
def sha(o): return hashlib.sha256(json.dumps(o,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
m=get('/manifest.json')
assert m['version']=='0.2.0'
assert old['MANIFEST']==sha({"id":m.get("id"),"resources":m.get("resources"),"catalogs":m.get("catalogs")}),"default catalog manifest changed"
e=get('/meta/series/tt6150576.json')['meta']
assert old['EPISODES']==sha([(v.get('id'),v.get('season'),v.get('episode')) for v in e.get('videos',[]) if isinstance(v,dict)]),"series video IDs changed"
for i,mid in enumerate(('tt38793125','tt37522541'),1):
    assert old["MOVIE%d"%i]==sha(get('/meta/movie/'+mid+'.json')['meta']), "movie metadata changed"
print("PASS: feature OFF, manifest, series episode IDs, movie metadata unchanged")
PY
complete=1
echo "SUCCESS: catalog implementation staged; CATALOG_PASSTHROUGH_ENABLED=0. AIOStreams untouched."
echo "Next gate: opt-in Bridge Catalog; then select existing catalogs in AIOStreams UI, retain SH Watch State."
