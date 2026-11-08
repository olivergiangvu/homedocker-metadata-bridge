#!/usr/bin/env bash
# Upgrade the EXISTING HomeDocker Metadata Bridge service to v0.4.0-stable.
# This does not run additional services or touch Nginx/AIOStreams/SH.
# Modes: --check (read-only), --apply, --rollback <backup-dir>.
set -Eeuo pipefail
MODE=--check
if [[ $# -gt 0 ]]; then MODE="$1"; fi
[[ "$MODE" == --check || "$MODE" == --apply || "$MODE" == --rollback ]] ||
  { echo "Usage: $0 --check|--apply|--rollback <backup-dir>"; exit 2; }
[[ "$EUID" == 0 ]] || { echo 'Run as root via sudo' >&2; exit 2; }
for c in git docker curl python3; do command -v "$c" >/dev/null; done
docker compose version >/dev/null
DIR="$(cd "$(dirname "$0")/.." && pwd -P)"
OLD_SOURCE=91f85ae4402cf3b4637d0aa9de8c0c99eeb94fd5
OLD_COMPOSE=efa1c013da1da8dd4378f59fbe5c60e04a52ea05
NEW_SOURCE=d8e72b6a9d014d0bb3faee4702bf35b28db3fec5
NEW_COMPOSE=401b34d29e5c0c53b8c4e37f92a10ef869f38498
CONTAINER=homedocker-metadata-bridge
SERVICE=metadata-bridge
URL=http://127.0.0.1:18123
die(){ echo "ABORT: $*" >&2; exit 1; }
h(){ git hash-object "$1"; }
[[ "$(h "$DIR/bridge.py")" == "$NEW_SOURCE" &&
   "$(h "$DIR/compose.yml")" == "$NEW_COMPOSE" ]] || die 'Release file hashes do not match.'
[[ "$(git -C "$DIR" describe --tags --exact-match HEAD 2>/dev/null)" == v0.4.0-stable ]] ||
  die 'Only run from a checkout of the reviewed v0.4.0-stable Git tag.'
[[ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null)" == true ]] ||
  die 'Expected container is not running.'
PROJECT="$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' "$CONTAINER")"
SVC="$(docker inspect -f '{{index .Config.Labels "com.docker.compose.service"}}' "$CONTAINER")"
[[ "$PROJECT" != '<no value>' && -n "$PROJECT" && "$SVC" == "$SERVICE" ]] ||
  die 'Unexpected Compose project/service.'
SOURCE="$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/app/bridge.py"}}{{.Source}}{{end}}{{end}}' "$CONTAINER")"
[[ -n "$SOURCE" && -f "$SOURCE" ]] || die 'Expected Bridge bind mount missing.'
SOURCE="$(readlink -f "$SOURCE")"
LIVE_DIR="$(dirname "$SOURCE")"
COMPOSE="$LIVE_DIR/compose.yml"
ENVFILE="$LIVE_DIR/.env"
[[ "$(basename "$SOURCE")" == bridge.py && -f "$COMPOSE" &&
    -f "$ENVFILE" && -f "$LIVE_DIR/overrides.json" ]] || die 'Unexpected live stack.'
[[ "$(readlink -f "$DIR/bridge.py")" != "$SOURCE" ]] || die 'Do not use the live stack as a release checkout.'
cd "$LIVE_DIR"
recreate(){
  env -u CHINESE_CATALOG_ENGLISH_META_ENABLED -u CHINESE_CATALOG_TITLE_YEAR_ENABLED \
    docker compose -p "$PROJECT" -f "$COMPOSE" up -d --no-deps --no-build \
      --pull never --force-recreate "$SERVICE" >/dev/null
}
replace_file(){
  local from="$1" to="$2" tmp
  tmp="$(mktemp "$to.v040.XXXXXX")"
  cp -- "$from" "$tmp"
  chown --reference="$to" "$tmp"
  chmod --reference="$to" "$tmp"
  mv -f -- "$tmp" "$to"
}
version(){
 curl -fsS --max-time 8 "$URL/health" | python3 -c \
  'import json,sys; d=json.load(sys.stdin); assert d["status"]=="ok"; print(d["version"])'
}
fingerprints(){
python3 - <<'PY'
import hashlib,json,urllib.request
def read(path):
 with urllib.request.urlopen('http://127.0.0.1:18123'+path,timeout=30) as r:return json.load(r)
def sha(x):
 return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
m=read('/manifest.json')
s=read('/meta/series/tt6150576.json')['meta']
print(sha(m.get('catalogs'))+'|'+sha([(v.get('id'),v.get('season'),v.get('episode')) for v in s.get('videos',[]) if isinstance(v,dict)]))
PY
}

if [[ "$MODE" == --rollback ]]; then
 [[ $# == 2 ]] || die 'Pass --rollback <backup-dir>.'
 BACKUP="$(readlink -f "$2")"
 [[ "$BACKUP" == /var/backups/homedocker-metadata-bridge/v040-* &&
   -f "$BACKUP/bridge.py" && -f "$BACKUP/compose.yml" && -f "$BACKUP/.env" ]] ||
   die 'Invalid private backup directory.'
 [[ "$(h "$BACKUP/bridge.py")" == "$OLD_SOURCE" &&
    "$(h "$BACKUP/compose.yml")" == "$OLD_COMPOSE" ]] || die 'Backup hashes mismatch.'
 [[ "$(h "$SOURCE")" == "$NEW_SOURCE" &&
    "$(h "$COMPOSE")" == "$NEW_COMPOSE" ]] || die 'Live files are not reviewed v0.4.0.'
 replace_file "$BACKUP/bridge.py" "$SOURCE"
 replace_file "$BACKUP/compose.yml" "$COMPOSE"
 replace_file "$BACKUP/.env" "$ENVFILE"
 recreate
 echo 'Restored prior v0.3.0 source, Compose and original private flags.'
 exit 0
fi
[[ "$(h "$SOURCE")" == "$OLD_SOURCE" &&
   "$(h "$COMPOSE")" == "$OLD_COMPOSE" ]] || die 'Live files differ from verified v0.3.0.'
[[ "$(version)" == 0.3.0 ]] || die 'Expected healthy v0.3.0 before upgrade.'
# No private environment values are printed.
python3 -c 'import ast,sys; ast.parse(open(sys.argv[1],encoding="utf-8").read())' "$DIR/bridge.py"
env -u CHINESE_CATALOG_ENGLISH_META_ENABLED -u CHINESE_CATALOG_TITLE_YEAR_ENABLED \
 docker compose -p "$PROJECT" -f "$DIR/compose.yml" --env-file "$ENVFILE" config -q >/dev/null ||
 die 'Candidate Compose invalid.'
BEFORE="$(fingerprints)" || die 'Could not fingerprint catalog and episode IDs.'
echo 'PASS: v0.3.0 baseline hashes/health; v0.4.0 candidate hashes/Compose.'
[[ "$MODE" == --apply ]] || exit 0

BROOT=/var/backups/homedocker-metadata-bridge
mkdir -p "$BROOT"
chmod 700 "$BROOT"
BACKUP="$BROOT/v040-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -m 700 "$BACKUP"
cp -p "$SOURCE" "$BACKUP/bridge.py"
cp -p "$COMPOSE" "$BACKUP/compose.yml"
cp -p "$ENVFILE" "$BACKUP/.env"
chmod 600 "$BACKUP/.env"
[[ "$(h "$BACKUP/bridge.py")" == "$OLD_SOURCE" &&
   "$(h "$BACKUP/compose.yml")" == "$OLD_COMPOSE" ]] || die 'Backup validation failed.'
echo "Private rollback snapshot: $BACKUP"

CANDIDATE_ENV="$(mktemp "$BACKUP/.env.candidate.XXXXXX")"
python3 - "$ENVFILE" "$CANDIDATE_ENV" <<'PY'
import pathlib,re,sys
src,dst=[pathlib.Path(x) for x in sys.argv[1:]]
data=src.read_text()
for k in ('CHINESE_CATALOG_ENGLISH_META_ENABLED','CHINESE_CATALOG_TITLE_YEAR_ENABLED'):
 rex=re.compile(r'(?m)^[ \t]*(?:export[ \t]+)?'+k+r'[ \t]*=[^\r\n]*$')
 matches=list(rex.finditer(data))
 if len(matches)>1: raise SystemExit('Duplicate '+k)
 if matches:
  value=matches[0].group().split('=',1)[1].strip().strip('"').strip("'")
  if value!='0':raise SystemExit(k+' already enabled or nonstandard; inspect privately')
  data=data[:matches[0].start()]+k+'=1'+data[matches[0].end():]
 else:
  if data and not data.endswith('\n'):data+='\n'
  data+=k+'=1\n'
dst.write_text(data)
PY
chmod 600 "$CANDIDATE_ENV"
swapped=0
complete=0
recover(){
 local result=$?
 trap - EXIT
 if [[ "$swapped" == 1 && "$complete" != 1 ]]; then
   echo 'ROLLBACK: restoring v0.3.0 source/Compose/.env.' >&2
   replace_file "$BACKUP/bridge.py" "$SOURCE"
   replace_file "$BACKUP/compose.yml" "$COMPOSE"
   replace_file "$BACKUP/.env" "$ENVFILE"
   recreate || echo 'WARNING: manual recovery required after files restored.' >&2
 fi
 rm -f "$CANDIDATE_ENV"
 exit "$result"
}
trap recover EXIT
swapped=1
replace_file "$DIR/bridge.py" "$SOURCE"
replace_file "$DIR/compose.yml" "$COMPOSE"
replace_file "$CANDIDATE_ENV" "$ENVFILE"
rm -f "$CANDIDATE_ENV"
env -u CHINESE_CATALOG_ENGLISH_META_ENABLED -u CHINESE_CATALOG_TITLE_YEAR_ENABLED \
 docker compose -p "$PROJECT" -f "$COMPOSE" config -q >/dev/null || die 'Updated Compose invalid.'
recreate
ready=0
for i in $(seq 1 25); do
 if [[ "$(version 2>/dev/null || true)" == 0.4.0 ]]; then ready=1; break; fi
 sleep 2
done
[[ "$ready" == 1 ]] || die 'v0.4.0 health timeout.'
[[ "$(h "$SOURCE")" == "$NEW_SOURCE" && "$(h "$COMPOSE")" == "$NEW_COMPOSE" ]] ||
  die 'Unexpected live file hashes.'
[[ "$BEFORE" == "$(fingerprints)" ]] || die 'Catalog or Tokyo Stories episode IDs changed unexpectedly.'
python3 - <<'PY'
import json,urllib.request
def read(path):
 with urllib.request.urlopen('http://127.0.0.1:18123'+path,timeout=30) as r:
  assert r.status==200
  return json.load(r)
m=read('/manifest.json')
assert m['version']=='0.4.0' and m['id']=='com.homedocker.metadata-bridge'
assert any(isinstance(x,dict) and x.get('idPrefixes')==['tch:'] and x.get('name')=='meta' for x in m['resources'])
assert not any(x=='watch_state' or isinstance(x,dict) and x.get('name')=='watch_state' for x in m['resources'])
s=read('/meta/series/tch:series:331912.json')['meta']
assert s['id']=='tch:series:331912' and s['name']=='Cause of Death (2026)'
assert len(s.get('videos') or [])==25 and s['videos'][0]['id']=='tt45680280:1:1'
f=read('/meta/movie/tch:movie:146:tt0190332.json')['meta']
assert f['name']=='Crouching Tiger, Hidden Dragon'
assert f['behaviorHints']['defaultVideoId']=='tt0190332'
print('PASS: unified Bridge 0.4.0, movie and 25-episode English series, IDs, no watch_state.')
PY
complete=1
echo 'SUCCESS: upgraded ONLY existing Metadata Bridge on 127.0.0.1:18123.'
echo "Rollback command: sudo bash $DIR/scripts/upgrade_bridge_v0.4.0_homedocker.sh --rollback $BACKUP"
echo 'Do not remove staging until AIOStreams Detail/Resume/Trakt playback smoke tests pass.'
