#!/usr/bin/env bash
# Guarded one-service deployment. Argument: commit-pinned candidate bridge.py.
set -Eeuo pipefail

SERVICE=metadata-bridge
CONTAINER=homedocker-metadata-bridge
OLD_BLOB=d99707a1d5376a6963834b25e2885afc2f8812c5
NEW_BLOB=11b0f8f125bf9239dfb2a7ddb7a0ef40132e9ec9
BASE=http://127.0.0.1:18123
BACKUP_DIR=/var/backups/homedocker-metadata-bridge

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ "$EUID" -eq 0 && "$#" -eq 1 && -f "$1" ]] || die "Run: sudo bash deploy_movie_logo_homedocker.sh /path/to/pinned/bridge.py"
for program in docker git curl python3 stat readlink mktemp; do command -v "$program" >/dev/null || die "Missing $program"; done
docker compose version >/dev/null || die "Docker Compose plugin missing"

CANDIDATE="$(readlink -f "$1")"
[[ "$(git hash-object "$CANDIDATE")" == "$NEW_BLOB" ]] || die "Candidate source SHA mismatch; no change"
[[ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null)" == true ]] || die "Bridge not running"
PROJECT="$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' "$CONTAINER")"
LABEL_SERVICE="$(docker inspect -f '{{index .Config.Labels "com.docker.compose.service"}}' "$CONTAINER")"
[[ "$LABEL_SERVICE" == "$SERVICE" && -n "$PROJECT" && "$PROJECT" != "<no value>" ]] || die "Unexpected Docker Compose service labels"
SOURCE="$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/app/bridge.py"}}{{.Source}}{{end}}{{end}}' "$CONTAINER")"
RW="$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/app/bridge.py"}}{{.RW}}{{end}}{{end}}' "$CONTAINER")"
[[ -f "$SOURCE" && "$RW" == false ]] || die "Cannot verify live read-only bridge.py mount"
SOURCE="$(readlink -f "$SOURCE")"
[[ "$(basename "$SOURCE")" == bridge.py ]] || die "Unexpected source filename"
ROOT="$(dirname "$SOURCE")"
[[ -f "$ROOT/compose.yml" && -f "$ROOT/.env" && -f "$ROOT/overrides.json" ]] || die "Missing Compose stack files beside mounted source"
[[ "$(git hash-object "$SOURCE")" == "$OLD_BLOB" ]] || die "Live bridge.py differs from tested baseline; no file changed"
cd "$ROOT"
docker compose -p "$PROJECT" -f compose.yml config -q >/dev/null || die "Compose config failed"
MODE="$(stat -c %a "$SOURCE")"
UID_OLD="$(stat -c %u "$SOURCE")"
GID_OLD="$(stat -c %g "$SOURCE")"
echo "PASS: pinned candidate + live source match reviewed blobs; Compose preflight PASS"

# Collect no private URLs or metadata. Only in-memory fingerprints of existing fields.
BASELINE="$(python3 - "$BASE" <<'PY'
import json,hashlib,sys,urllib.request
host=sys.argv[1]
def get(path):
    with urllib.request.urlopen(host+path,timeout=25) as r:
        assert r.status==200
        return json.load(r)['meta']
def sha(v):
    return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
series=get('/meta/series/tt6150576.json')
videos=[(v.get('id'),v.get('season'),v.get('episode')) for v in series.get('videos',[]) if isinstance(v,dict)]
assert videos and all(v[0] for v in videos)
print('SERIES='+sha(videos))
for n,mid in enumerate(('tt38793125','tmdb:1571955','tt37522541','tmdb:1507696'),1):
    meta=get('/meta/movie/'+mid+'.json')
    assert isinstance(meta,dict) and meta.get('id') and not meta.get('logo')
    meta.pop('logo',None)
    print('MOVIE'+str(n)+'='+sha(meta))
PY
)" || die "Live baseline probe failed; no source changed"
echo "PASS: movie metadata and Midnight Diner video fingerprints captured"

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
BACKUP="$BACKUP_DIR/bridge.py.$(date -u +%Y%m%dT%H%M%SZ).before-movie-logo"
[[ ! -e "$BACKUP" ]] || die "Backup timestamp collision"
cp -p "$SOURCE" "$BACKUP"
chmod 600 "$BACKUP"
[[ "$(git hash-object "$BACKUP")" == "$OLD_BLOB" ]] || die "Backup verification failed"
echo "BACKUP: $BACKUP"

SWAPPED=0
DONE=0
STAGE=
recreate() {
  docker compose -p "$PROJECT" -f compose.yml up -d --no-deps --no-build --pull never --force-recreate "$SERVICE"
}
cleanup() {
  local status=$?
  trap - EXIT
  [[ -z "$STAGE" || ! -e "$STAGE" ]] || rm -f "$STAGE"
  if [[ "$SWAPPED" == 1 && "$DONE" != 1 ]]; then
    echo "ROLLBACK: restoring original source and recreating only the Bridge" >&2
    local temp
    temp="$(mktemp "$SOURCE.rollback.XXXXXX")"
    cp "$BACKUP" "$temp"
    chmod "$MODE" "$temp"
    chown "$UID_OLD:$GID_OLD" "$temp"
    mv -f "$temp" "$SOURCE"
    if recreate >/dev/null; then echo "ROLLBACK: original Bridge restored" >&2
    else echo "ERROR: backup restored, but service recreate failed; manual recovery required" >&2; fi
  fi
  exit "$status"
}
trap cleanup EXIT
STAGE="$(mktemp "$SOURCE.candidate.XXXXXX")"
cp "$CANDIDATE" "$STAGE"
chmod "$MODE" "$STAGE"
chown "$UID_OLD:$GID_OLD" "$STAGE"
[[ "$(git hash-object "$STAGE")" == "$NEW_BLOB" ]] || die "Staged code mismatch"
mv -f "$STAGE" "$SOURCE"
STAGE=
SWAPPED=1
recreate

ready=0
for i in $(seq 1 15); do
  if curl -fsS --max-time 3 "$BASE/health" >/dev/null 2>&1; then ready=1; break; fi
  sleep 2
done
[[ "$ready" == 1 ]] || die "Bridge health check failed"
[[ "$(git hash-object "$SOURCE")" == "$NEW_BLOB" ]] || die "Source changed after deploy"

BASELINE="$BASELINE" python3 - "$BASE" <<'PY'
import json,hashlib,os,sys,urllib.request
host=sys.argv[1]
before=dict(line.split('=',1) for line in os.environ['BASELINE'].splitlines())
def get(path):
    with urllib.request.urlopen(host+path,timeout=25) as r:
        assert r.status==200
        return json.load(r)['meta']
def sha(v):
    return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
series=get('/meta/series/tt6150576.json')
videos=[(v.get('id'),v.get('season'),v.get('episode')) for v in series.get('videos',[]) if isinstance(v,dict)]
assert sha(videos)==before['SERIES'], 'Series episode identities changed'
print('PASS: Midnight Diner episode identities unchanged')
for n,(mid,label) in enumerate((
  ('tt38793125','Collision Course IMDb'),
  ('tmdb:1571955','Collision Course TMDB'),
  ('tt37522541','Apocalypse Z IMDb'),
  ('tmdb:1507696','Apocalypse Z TMDB')),1):
    meta=get('/meta/movie/'+mid+'.json')
    assert isinstance(meta,dict)
    logo=meta.pop('logo',None)
    assert isinstance(logo,str) and logo.startswith('https://'),label+' logo missing'
    assert sha(meta)==before['MOVIE'+str(n)],label+' other SH metadata changed'
    print('PASS: '+label+' logo added; all non-logo metadata unchanged')
PY
DONE=1
echo "SUCCESS: only Metadata Bridge movie.logo fallback deployed; other containers/Watch State untouched"
echo "NEXT: verify Collision Course and Apocalypse Z Detail artwork in AIOStreams"
