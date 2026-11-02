#!/usr/bin/env bash
# Enable Bridge catalog passthrough, validate live SH/SH2 parity, keep AIOStreams untouched.
# Automatic rollback restores .env and recreates only Metadata Bridge on failure.
set -Eeuo pipefail

SERVICE=metadata-bridge
CONTAINER=homedocker-metadata-bridge
BRIDGE_BLOB=b2703d6a5c3ce8c77d06b57d2d12cf005daeba0a
COMPOSE_BLOB=efa1c013da1da8dd4378f59fbe5c60e04a52ea05
BASE=http://127.0.0.1:18123

die() { printf 'ABORT: %s\n' "$*" >&2; exit 1; }
[[ "$EUID" == 0 && "$#" == 0 ]] || die "Run as root with no arguments"
for cmd in docker git python3 curl; do command -v "$cmd" >/dev/null || die "Missing $cmd"; done
docker compose version >/dev/null || die "Docker Compose missing"
[[ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null)" == true ]] || die "Bridge container not running"
PROJECT="$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' "$CONTAINER")"
LABEL="$(docker inspect -f '{{index .Config.Labels "com.docker.compose.service"}}' "$CONTAINER")"
SOURCE="$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/app/bridge.py"}}{{.Source}}{{end}}{{end}}' "$CONTAINER")"
[[ "$LABEL" == "$SERVICE" && -n "$PROJECT" && "$PROJECT" != "<no value>" ]] || die "Unexpected Compose service/project"
[[ -f "$SOURCE" ]] || die "Missing live source file"
SOURCE="$(readlink -f "$SOURCE")"
ROOT="$(dirname "$SOURCE")"
ENV_FILE="$ROOT/.env"
[[ -f "$ENV_FILE" && -f "$ROOT/compose.yml" ]] || die "Missing stack Compose/.env"
[[ "$(git hash-object "$SOURCE")" == "$BRIDGE_BLOB" ]] || die "Live bridge.py differs from reviewed PR #17"
[[ "$(git hash-object "$ROOT/compose.yml")" == "$COMPOSE_BLOB" ]] || die "Live Compose differs from reviewed PR #17"
[[ "$(docker inspect -f '{{range .Config.Env}}{{if eq . "CATALOG_PASSTHROUGH_ENABLED=1"}}ENABLED{{end}}{{end}}' "$CONTAINER")" != ENABLED ]] || die "Catalog already active"
cd "$ROOT"
# Compose environment overrides .env; always use .env as the authoritative flag.
unset CATALOG_PASSTHROUGH_ENABLED
docker compose -p "$PROJECT" -f compose.yml config -q >/dev/null || die "Compose validation failed"
[[ "$(curl -fsS --max-time 8 "$BASE/health" | python3 -c 'import json,sys; print(json.load(sys.stdin)["version"])')" == "0.2.0" ]] || die "Expected metadata-only Bridge v0.2 pre-cutover"
echo "PASS: candidate source, Compose and current metadata-only runtime"

BACKUP_ROOT=/var/backups/homedocker-metadata-bridge
mkdir -p "$BACKUP_ROOT"
chmod 700 "$BACKUP_ROOT"
BACKUP="$BACKUP_ROOT/catalog-enable-$(date -u +%Y%m%dT%H%M%SZ)"
[[ ! -e "$BACKUP" ]] || die "Backup timestamp collision"
mkdir -m 700 "$BACKUP"
cp -p -- "$ENV_FILE" "$BACKUP/.env"
chmod 600 "$BACKUP/.env"
echo "BACKUP: .env saved privately at $BACKUP/.env"

swapped=0
done_ok=0
replace_env() {
  local src="$1" temp
  temp="$(mktemp "$ENV_FILE.stage.XXXXXX")"
  cp -- "$src" "$temp"
  chown --reference="$ENV_FILE" "$temp"
  chmod --reference="$ENV_FILE" "$temp"
  mv -f -- "$temp" "$ENV_FILE"
}
recreate() {
  docker compose -p "$PROJECT" -f compose.yml up \
    -d --no-deps --no-build --pull never --force-recreate "$SERVICE"
}
rollback() {
  local status=$?
  trap - EXIT
  if [[ "$swapped" == 1 && "$done_ok" != 1 ]]; then
    echo "ROLLBACK: restoring old .env and recreating ONLY metadata-bridge" >&2
    replace_env "$BACKUP/.env"
    if recreate >/dev/null; then echo "ROLLBACK: original catalog-disabled Bridge restored" >&2
    else echo "WARNING: .env restored; Bridge recreate failed, manual recovery required" >&2; fi
  fi
  exit "$status"
}
trap rollback EXIT
STAGED_ENV="$(mktemp "$BACKUP/.env.candidate.XXXXXX")"
python3 - "$ENV_FILE" "$STAGED_ENV" <<'PY'
import pathlib, re, sys
source, dest = [pathlib.Path(x) for x in sys.argv[1:]]
raw = source.read_bytes()
text = raw.decode('utf-8')
pattern = re.compile(r'(?m)^([ \t]*)(?:export[ \t]+)?CATALOG_PASSTHROUGH_ENABLED[ \t]*=.*$')
matches = list(pattern.finditer(text))
assert len(matches) <= 1, "Duplicate Catalog enable variables; no change"
if matches:
    old = matches[0].group()
    assert re.fullmatch(r'[ \t]*(?:export[ \t]+)?CATALOG_PASSTHROUGH_ENABLED[ \t]*=[ \t]*["' + "'" + r']?0["' + "'" + r']?[ \t]*', old), "Catalog flag is not currently 0; inspect offline"
    text = text[:matches[0].start()] + 'CATALOG_PASSTHROUGH_ENABLED=1' + text[matches[0].end():]
else:
    text += ('' if not text or text.endswith('\n') else '\n') + 'CATALOG_PASSTHROUGH_ENABLED=1\n'
dest.write_bytes(text.encode('utf-8'))
PY

# Verify Compose resolves feature=1 without printing the private composed config.
replace_env "$STAGED_ENV"
rm -f -- "$STAGED_ENV"
swapped=1
env -u CATALOG_PASSTHROUGH_ENABLED docker compose -p "$PROJECT" -f compose.yml config --format json |
  python3 -c 'import json,sys; c=json.load(sys.stdin); assert c["services"]["metadata-bridge"]["environment"]["CATALOG_PASSTHROUGH_ENABLED"]=="1", "Compose flag not enabled"' ||
  die "Compose configuration does not enable Catalog"
recreate
ready=0
for i in $(seq 1 15); do
  if curl -fsS --max-time 3 "$BASE/health" >/dev/null 2>&1; then ready=1; break; fi
  sleep 2
done
[[ "$ready" == 1 ]] || die "Bridge did not recover"

# All requests are read-only GETs; print aggregate counts, never UUIDs/IDs/URLs.
docker exec -i "$CONTAINER" python3 - <<'PY'
import copy, json, urllib.request
import bridge

def local(path):
    with urllib.request.urlopen('http://127.0.0.1:18123'+path, timeout=55) as r:
        assert r.status == 200
        return json.load(r)

assert bridge.CATALOG_PASSTHROUGH_ENABLED, "container feature flag disabled"
health = local('/health')
assert health.get('status') == 'ok' and health.get('version') == '0.3.0'
manifest = local('/manifest.json')
expected = []
for prov in ('sh', 'tvdb'):
    source = bridge.request_json(prov, 'manifest.json') or {}
    expected.extend((prov, c) for c in source.get('catalogs', []) if isinstance(c, dict))
assert len(expected) == 198, "SH/SH2 manifest count unexpectedly changed"
actual = manifest.get('catalogs') or []
assert len(actual) == len(expected), "Bridge manifest drops source catalogs"
for (prov, c), mirrored in zip(expected, actual):
    assert mirrored.get('id') == bridge.proxied_catalog_id(prov, c['id'])
    assert {k:v for k,v in mirrored.items() if k!='id'} == {k:v for k,v in c.items() if k!='id'}
assert not any(x == 'watch_state' or isinstance(x,dict) and x.get('name')=='watch_state'
               for x in manifest.get('resources') or [])
print('PASS: 198/198 source catalog descriptors mirrored; no watch_state resource')

def verify_page(prov, c):
    path = 'catalog/'+c['type']+'/'+c['id']+'.json'
    src = bridge.request_json(prov,path)
    assert isinstance(src,dict) and isinstance(src.get('metas'),list)
    proxy = bridge.proxied_catalog_id(prov,c['id'])
    dst = local('/catalog/'+c['type']+'/'+proxy+'.json')
    assert len(dst.get('metas',[])) == len(src['metas'])
    unchanged = copy.deepcopy(dst)
    count = 0
    for original, enriched in zip(src['metas'],unchanged['metas']):
        if not isinstance(original,dict) or not isinstance(enriched,dict): continue
        if not bridge.usable_artwork(original.get('logo')) and bridge.usable_artwork(enriched.get('logo')):
            count += 1
            if 'logo' in original: enriched['logo'] = original['logo']
            else: enriched.pop('logo',None)
    assert unchanged == src, 'catalog payload changed outside of missing logos'
    return len(src['metas']),count

for prov,maximum in [('sh',3),('tvdb',1)]:
    checked=0
    for p,c in expected:
        if p != prov or c.get('type') not in ('movie','series'): continue
        if any(isinstance(e,dict) and e.get('isRequired') for e in c.get('extra') or []): continue
        if not isinstance(c.get('id'),str): continue
        try: count,added=verify_page(prov,c)
        except (TimeoutError, OSError, ValueError): continue
        checked+=1
        print('PASS: %s browse sample #%d: %d items, +%d logos, no other changes' %
              (prov.upper(),checked,count,added))
        if checked>=maximum: break
    assert checked>=1, 'no catalog page tested for '+prov
for mid in ('tt38793125','tt37522541'):
    m=local('/meta/movie/'+mid+'.json').get('meta') or {}
    assert isinstance(m.get('logo'),str) and m['logo'].startswith('https://')
s=local('/meta/series/tt6150576.json').get('meta') or {}
assert len(s.get('videos') or [])>=20
print('PASS: two movie Detail logos and Midnight Diner metadata still available')
PY

done_ok=1
echo "SUCCESS: Bridge Catalog Passthrough ENABLED and validated. AIOStreams still unchanged."
echo "NEXT: export AIOStreams config; select bridge Catalog + existing Home catalog rows; preserve SH Watch State."
