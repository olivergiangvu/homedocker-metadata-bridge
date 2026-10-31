# HomeDocker Hybrid Metadata Bridge — v0.1 (controlled trial)

> **GitHub-first:** This is a source-code pilot. Review it as a **public GitHub repository** and let CI pass before attempting HomeDocker deployment. See [docs/GITHUB_FIRST.md](docs/GITHUB_FIRST.md) and [docs/RELEASE_CHECKLIST.md](docs/RELEASE_CHECKLIST.md). No `.env`, personal UUIDs, API credentials or production watch-state data belong in Git. The Bridge has **not yet been validated against the live SH/SH2 endpoints**.

**Purpose:** Keep AIOMetadata SH as the **Watch State tracker**, move all metadata resolution to a separate Stremio-compatible bridge, enrich series using SH (TMDB) plus SH2 (TVDB), and pass through movies/anime **unchanged** from SH.

This is an implementation-ready *pilot*, not a proven Midnight Diner fix. It must be tested against your two real AIOMetadata UUIDs before AIOStreams cutover. Nothing in this package touches Restic/Backrest or rewrites existing watch data.

## What it does

- `GET /manifest.json`: Stremio-compatible `meta` plus `Hybrid Series Search` `catalog`; **no `watch_state`**.
- `GET /meta/movie/{id}.json`, `/meta/anime/{id}.json` and all non-series meta: byte-equivalent logical JSON passthrough from SH; no modifications.
- `GET /meta/series/{id}.json`: start from SH TMDB metadata by default; optionally enrich missing episode thumbnail/overview from SH2 when series and episode identity can be established; fallback to SH2 when SH has no metadata.
- For **explicit allowlisted series IDs** (`prefer_tvdb_meta_ids`) and **verified strong common series identity**, use SH2 TVDB episodes, overlay matched episode thumbnails/overviews and series artwork from SH. This can expose TVDB episode structure, but it **changes episode IDs if SH2 uses different IDs**, so only do it after watch-state review.
- `GET /catalog/series/unified-series-search/search=TERM.json`: query search catalogs exposed by both AIOMetadata UUIDs; interleave and deduplicate by `type + id`, plus optional query alias expansion. Search only as good as the upstream catalogs; `alias` config is **not** a universal fuzzy search engine.
- Zero upstream writes, no SQL database, no thumbnail downloads, memory-only LRU cache (default 128 entries), read-only code mounts.

**Safety:** Never guess that TVDB S03E01 equals TMDB S03E01. Merge same-series requires common matching provider IDs with no conflicts. Per-episode matching requires a strong episode ID, date with supporting episode/title evidence, or matching title under an identical season layout. If unsure, preserve original fields. Bridge deliberately does not translate yastream stream requests.

## 1. Upload to server

On the HomeDocker server over SSH, clone the **reviewed GitHub release** (do not deploy a moving `main` branch in production):

```bash
set -Eeuo pipefail
sudo install -d -m 755 /opt/homedocker/stacks/metadata-bridge
sudo git clone https://github.com/olivergiangvu/homedocker-metadata-bridge.git \
  /opt/homedocker/stacks/metadata-bridge
cd /opt/homedocker/stacks/metadata-bridge
# Once a release exists: git checkout <reviewed-release-tag>
sudo cp .env.example .env
sudo chmod 600 .env
```

This is a **future deployment example only**. The repository has not been qualified against live UUID responses, so do not run it on HomeDocker yet.

**Do not run `docker compose up` until both manifest URLs are configured.**

## 2. Fill in the two existing UUID URLs (locally, private)

AIOStreams Configuration > Installed > Addons > **pencil Edit** of SH and SH2 > copy `Manifest URL` (not Reconfigure icon). On HomeDocker, edit `.env`:

```bash
sudo nano /opt/homedocker/stacks/metadata-bridge/.env
```

Change `SH_MANIFEST_URL` and `SH2_MANIFEST_URL` to their **internal AIOMetadata equivalents**, normally `http://127.0.0.1:3232/<same UUID/path>/manifest.json`. `127.0.0.1:3232` is known to be bound on this server. This pilot currently supports manifest URLs **without query strings**. If the actual manifest uses a query string or a nonstandard route, stop and adapt its URL handling before using it; do not strip credentials or guess a path.

**Never paste full UUID manifest URLs to a public forum or into chat logs.** They may be bearer configuration identifiers.

Test both **without printing URLs**:

```bash
cd /opt/homedocker/stacks/metadata-bridge
sudo bash -c '
set -a; . ./.env; set +a
for key in SH_MANIFEST_URL SH2_MANIFEST_URL; do
  url=${!key}
  if curl -fsS --max-time 12 "$url" | jq -e ".resources | type == \"array\"" >/dev/null; then
    echo "$key=PASS"
  else
    echo "$key=FAIL"; exit 1
  fi
done
'
```

No response body/credential is printed.

## 3. Run local-only pilot (no AIOStreams changes)

Preflight port and config:

```bash
sudo ss -lntp '( sport = :18123 )'  # should show no existing listener
cd /opt/homedocker/stacks/metadata-bridge
sudo docker compose -p metadata-bridge -f compose.yml config -q
sudo docker compose -p metadata-bridge -f compose.yml up -d --no-build
sudo docker ps --filter name=homedocker-metadata-bridge
curl -fsS http://127.0.0.1:18123/health | jq .
curl -fsS http://127.0.0.1:18123/manifest.json | jq '{id, resources, types, catalogs}'
```

If the port is in use, **do not start** until choosing an available port, updating LISTEN_PORT + healthcheck and reserving it according to HomeDocker's port convention. No local host directory data beyond the two readonly source files is mounted. `network_mode: host` is necessary to access AIOMetadata at host-loopback `127.0.0.1:3232`; the Bridge itself binds only host loopback `127.0.0.1:18123`.

Test real results **before changing AIOStreams**:

```bash
# Read-only: confirm SH and SH2 reachability, metadata and search.
curl -fsS 'http://127.0.0.1:18123/meta/series/tt6150576.json' |
  jq '{name:.meta.name, id:.meta.id, seasons:([.meta.videos[]? | .season] | unique),
       episode_count:(.meta.videos|length),
       thumbs:([.meta.videos[]? | select(.thumbnail != null and .thumbnail != "")] | length),
       descriptions:([.meta.videos[]? | select(.overview != null and .overview != "")] | length)}'

curl -fsS 'http://127.0.0.1:18123/catalog/series/unified-series-search/search=Midnight%20Diner.json' |
  jq '[.metas[]? | {id, name, type}] | .[:12]'
```

**Check 3 representative titles:** a known-good SH TMDB series; a TVDB-only series; *Midnight Diner*. Inspect their `meta.videos[].id` and `season`/`episode` before considering overrides. Also test one movie and one anime: compare SH and Bridge JSON with `jq -S` (logical comparison).

## 4. Expose via existing Nginx HTTPS (only after local pilot)

The addon's Custom Manifest URL must be reachable **from the AIOStreams container** and from clients where relevant. Prefer the already-running HomeDocker Nginx vhost, using a scoped location. **Example only:**

```nginx
location /metadata-bridge/ {
    proxy_pass http://127.0.0.1:18123/;
    proxy_set_header Host $host;
    proxy_connect_timeout 5s;
    proxy_read_timeout 25s;
}
```

This assumes Nginx runs on the host; if Nginx runs in Docker, `127.0.0.1` inside that container is **not the HomeDocker host** and must be adjusted after checking the current networking. Before editing: `sudo nginx -T` (read-only) to identify the correct server block; back it up; after editing validate `sudo nginx -t`, then `sudo systemctl reload nginx`. Do not invent a new hostname or overwrite unrelated locations. Confirm HTTPS externally returns the bridge manifest before cutover. The hypothetical path is:

```text
https://<your-existing-host>/metadata-bridge/manifest.json
```

## 5. Cutover in AIOStreams — only after read-only acceptance

**Keep existing AIOStreams account/configuration UUID, SH and its tracker accounts intact.** Export current AIOStreams configuration first, and write down two active Continue Watching episode IDs/positions.

Under **Installed > Addons > pencil Edit > Advanced Mode > Resources**:

| Addon | Resources after cutover | Status |
|---|---|---|
| AIOMetadata SH (TMDB) | **Catalog + Watch State** | ON; NEVER remove Watch State |
| AIOMetadata SH2 (TVDB) | optional Catalog, or **OFF within AIOStreams only** | Still used privately by Bridge via manifest URL |
| HomeDocker Hybrid Metadata | **Metadata + Catalog** | ON |
| yastream, other stream addons | unchanged | ON |

Add the bridge using **Add by URL** with the HTTPS manifest from step 4. Ensure **only Bridge supplies Metadata** before Save; avoid SH taking precedence again. AIOStreams UI `Resources` empty means **all declared resources**, not none.

Click Save / Save & Install as appropriate and refresh catalogs. In **AIOStreams Media Server > Trackers**, SH must still show `Records your plays` and `Imports your history` as before. For tracking, keep SH's existing manifest URL / identity / account integration; never swap it for SH2.

Test browsing, Continue Watching and Next Up. Play a known episode for a short time, pause and verify local progress + tracker push; confirm pull after reopening. **Do not change series episode identity of already-started shows without verifying watch-state continuity.**

## 6. TVDB base for a particular series (optional, NOT default)

When SH metadata is incomplete but SH2 has the desired season structure, first confirm the two records share a valid provider ID AND that the same episode is truly the same broadcast episode. Then edit `/opt/homedocker/stacks/metadata-bridge/overrides.json` to allowlist the exact requested meta ID:

```json
{
  "prefer_tvdb_meta_ids": ["ttSAMPLE"],
  "search_aliases": {
    "midnight diner": ["shinya shokudo", "深夜食堂"]
  }
}
```

The placeholder `ttSAMPLE` is not real. Do **not** automatically add Midnight Diner's ID based solely on name or season numbers. Recheck episode IDs and stream matching; a correct thumbnail does not mean yastream knows the correct stream.

After changing overrides.json, no restart is required for reading override content; current metadata is not cached as an assembled response by the bridge, only upstream responses are cached. Browser/AIOStreams may have their own caches.

## 7. Rollback and limitations

1. In AIOStreams, disable Bridge; restore SH's original Resources (Metadata + Catalog + Watch State), and SH2's original settings. Save.
2. Validate Continue Watching, Next Up and SH Tracker.
3. Only then, on SSH: `sudo docker compose -p metadata-bridge -f /opt/homedocker/stacks/metadata-bridge/compose.yml down`.
4. Do **not** delete SH/SH2 UUIDs, their databases or AIOStreams watch-state records.

Limitations:
- `v0.1` is a safe initial hybrid, not a universal cross-ID/season converter. Unverified matches are deliberately refused.
- SH remains default series metadata; SH2 can fill missing episode data when mapping is strong, or supply full series if SH has none.
- Verified exceptions may use TVDB episode structure; because video IDs can change, preserve tracking manually via acceptance tests.
- Search unions catalogs that support `search` and optionally expands aliases. It cannot guarantee every alternative title exists in both providers' search index.
- Bridge does **not** rewrite yastream stream calls, so yastream timeouts, wrong-provider season numbering and missing source availability remain independent problems.
- This package has no auto-updater; the Python 3.12 base image may be updated during planned Docker maintenance.

## 8. Local automated tests

From any machine with Python 3.12 or Python 3.11:

```bash
cd homedocker-metadata-bridge
python3 -m unittest discover -s tests -v
```

8 tests validate manifest isolation, movie/anime passthrough, episode enrichment without changing video IDs, TVDB fallback, TVDB opt-in, search union, conflicting IDs and unverified season matching. These use synthetic data: they do not prove your real upstream mapping.
