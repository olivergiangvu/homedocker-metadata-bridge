# HomeDocker Unified Metadata Bridge — v0.2 (controlled trial)

> **GitHub-first:** This is a source-code pilot. Review it as a **public GitHub repository** and let CI pass before attempting HomeDocker deployment. See [docs/GITHUB_FIRST.md](docs/GITHUB_FIRST.md) and [docs/RELEASE_CHECKLIST.md](docs/RELEASE_CHECKLIST.md). No `.env`, personal UUIDs, API credentials or production watch-state data belong in Git. A read-only SH/SH2 contract probe has been completed; Bridge itself has **not yet been validated running against the live SH/SH2 endpoints**.

**Purpose:** Keep AIOMetadata SH as the **Watch State tracker**, move all metadata resolution to a separate Stremio-compatible bridge, enrich series using SH (TMDB) plus SH2 (TVDB), and pass through movies/anime **unchanged** from SH.

This is an implementation-ready *pilot*, not a proven Midnight Diner fix. Its merged HTTP endpoints must be tested against both real upstreams before AIOStreams cutover. Nothing in this package touches Restic/Backrest or rewrites existing watch data.

## What it does

- `GET /manifest.json`: Stremio-compatible `meta` plus `Hybrid Series Search` `catalog`; **no `watch_state`**.
- `GET /meta/movie/{id}.json`, `/meta/anime/{id}.json` and all non-series meta: byte-equivalent logical JSON passthrough from SH; no modifications.
- `GET /meta/series/{id}.json`: start from SH TMDB metadata by default; fill missing fields from SH2 and append unambiguous TVDB-only episodes, while preserving all SH episode IDs; fallback to SH2 when SH has no metadata.
- For **explicit allowlisted series IDs** (`prefer_tvdb_meta_ids`) and **verified strong common series identity**, use SH2 TVDB episodes, overlay matched episode thumbnails/overviews and series artwork from SH. The default TMDB-first merge also preserves SH IDs and adds TVDB-only episodes when their season/episode slots do not collide. An explicit TVDB override needs additional watch-state review.
- `GET /catalog/series/unified-series-search/search=TERM.json`: query search catalogs exposed by both AIOMetadata UUIDs; deduplicate verified cross-provider identity (IMDb/TMDB/TVDB), with TMDB artwork preference and optional alias expansion. Search only as good as the upstream catalogs; `alias` config is **not** a universal fuzzy search engine.
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

### Read-only redacted upstream diagnostic (recommended first)

The probe checks actual AIOMetadata v3.8.2 response fields, series identity keys, season counts, thumbnail/overview coverage and combined search. It **never prints full upstream URLs, UUIDs, titles or episode IDs**. It does not read Watch State or write upstream data.

```bash
cd /opt/homedocker/stacks/metadata-bridge
sudo bash -c '
  set -a; . ./.env; set +a
  python3 scripts/probe_upstreams.py --series-id tt6150576 --search "Midnight Diner"
'
```

This requires **Python 3.9+ on HomeDocker host** and the repository checkout; no Bridge container or AIOStreams configuration change is needed. Share only the probe's JSON report, not `.env`, manifest URLs or unredacted raw responses. The probe does not automatically approve a season/episode mapping.

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

14 tests validate manifest isolation, movie/anime passthrough, episode enrichment without changing video IDs, TVDB fallback, TVDB opt-in, search union, conflicting IDs and unverified season matching. These use synthetic data: they do not prove your real upstream mapping.


## 9. Real-data episode cohort preflight (read-only)

Run after configuring the two private upstream manifest URLs, but **before**
allowlisting any TVDB-first series or changing AIOStreams resources:

```bash
set -a; . ./.env; set +a
python3 scripts/probe_upstreams.py --series-id tt6150576 --search "Midnight Diner"
unset SH_MANIFEST_URL SH2_MANIFEST_URL
```

The `series.episode_cohorts` aggregate compares the two returned sets without
printing any title, `video.id`, provider ID, URL, watch progress or token.
Check `shared_same_season_episode`, `shared_different_season_episode`,
`shared_conflicting_air_dates`, and `tvdb_only_by_season`. A series identity
match alone is **not** evidence that a TVDB-only season has matching streams or
existing watch-state continuity. In particular, thumbnails in both providers
do not create a new enrichment benefit by themselves. Do not turn on
`prefer_tvdb_meta_ids` until a representative TVDB-only episode is verified
against the real stream addon and historical watch state.

## Unified Search & Meta (v0.2 pilot)

**Target:** One search card per *verified series identity*, and a single Bridge
Meta page regardless of whether the search result started in SH/TMDB or SH2/TVDB.
Do **not** merge by title alone: `Midnight Diner` (2009), `Midnight Diner` (2017)
and `Midnight Diner: Tokyo Stories` (2016) can be independent records.

- Search queries both upstreams (bounded to two series-search catalogs per
  provider), resolves up to 24 distinct preview IDs into upstream metadata,
  joins only matching IMDb/TMDB/TVDB identities without conflicting provider IDs,
  and prefers the SH/TMDB artwork and title. TVDB-only titles remain available.
  Performance note: metadata resolution adds upstream requests and can increase
  search latency on a cold cache; load-test with the real endpoints first.
- The result's canonical ID prefers IMDb when the upstream supplies it, or keeps
  the original provider ID otherwise. AIOStreams opens the Bridge `meta` route;
  SH remains the independent `watch_state` resource provider.
- For the Meta page, SH supplies the main series metadata; TVDB fills missing
  artwork/fields and contributes verified *new* season/episode slots. For the
  observed `tt6150576`, this means the intended 20 SH episode IDs + 10 TVDB-only
  S3 IDs (as long as the next local pilot reports the same identity and no slot
  collisions). Bridge never rewrites either source's `video.id`, `released`,
  season/episode coordinates or watched state.
- For shared episodes with mismatched release dates, no cross-source fields
  are mixed unless a substantive exact title matches. A missing thumbnail
  placeholder may be replaced when an unambiguous counterpart is found.
- Movie/anime metadata is still a logical JSON passthrough from SH, including
  anime titles whose Stremio resource type is `series` when SH exposes an
  anime provider/ID marker. All
  resources other than `meta`/`catalog` stay outside Bridge, including tracking.

### AIOStreams cutover: prevent duplicate Search catalogs

The search de-duplication happens **inside the Bridge catalog**. To make the
AIOStreams Series search show just these unified cards, enable the Bridge's
`Hybrid Series Search` (the only unified series-search catalog) and disable SH
and SH2's own **series search catalogs** in `Installed > Catalogs`. SH should
stay **enabled** with `Catalog + Watch State`; retain non-search browse catalogs
for Home/Discover. SH2 may be disabled within AIOStreams because Bridge queries
SH2's existing manifest directly. If an SH catalog serves both browse *and*
search, disable just its search function rather than disabling the browse
catalog. Leave SH's `Watch State` resource selected.

AIOStreams' separate **Media Servers** search section may still show its own
results, which Bridge cannot remove from the Desktop UI. If those results are
served by the AIOStreams Jellyfin-compatible server and it exclusively uses
Bridge for `meta`, their detail page will resolve via Bridge; a separately
configured external media server is outside this integration.

**Do not deploy to production solely because CI passes.** The live probe showed
20 shared video IDs but conflicting release dates on all 20. Before cutover,
validate the unified Bridge's 30 episodes, S3 `video.id` stream availability,
Continue Watching, Next Up and SH tracker push/pull. Keep saved config for
rollback. 

## Local pilot healthcheck: Docker unhealthy but HTTP 200

HomeDocker's v0.2 pilot responded HTTP 200 from inside the container,
but Docker's healthcheck exceeded its 4-second timeout 21 times.
This proves a healthcheck execution timeout, **not** an HTTP outage;
the exact underlying scheduling/runtime cause has not been confirmed.

The Compose test now uses lightweight Alpine BusyBox wget instead of starting
another Python interpreter; check timeout is 12 seconds. CI exercises the
same command with an unprivileged read-only image and mock HTTP server.

Bridge-only pilot update (SH, SH2, AIOStreams and Watch State untouched):

~~~bash
cd ~/metadata-bridge-preflight
git pull --ff-only
sudo docker compose -p metadata-bridge -f compose.yml config -q
sudo docker compose -p metadata-bridge -f compose.yml up -d --no-build --force-recreate
sudo docker compose -p metadata-bridge -f compose.yml ps
~~~

Check Docker state once the healthcheck has executed:

~~~bash
sudo docker inspect homedocker-metadata-bridge \
  --format '{{json .State.Health}}' |
jq '{Status, FailingStreak, Log: [.Log[-3:][]? | {ExitCode, Output}]}'
~~~

If it remains unhealthy, collect the health log and **do not** cut over.
Do not disable the healthcheck to hide the problem.
