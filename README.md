# HomeDocker Unified Metadata Bridge — v0.4.0 Stable

> **Stable release (2026-11-08).** The Bridge runs with AIOMetadata SH + SH2 and official AIOStreams. No AIOStreams fork or custom image. SH continues to own **Watch State**. See [v0.4.0 Stable Release Notes](docs/RELEASE_v0.4.0.md), [Catalog Passthrough Cutover](docs/CATALOG_PASSTHROUGH_CUTOVER.md), and [Changelog](CHANGELOG.md). Never publish private manifest URLs, profile UUIDs, .env files, or API keys.

## v0.4.0 — Chinese Catalog English details (integrated, single service)

**One Docker service only:** `homedocker-metadata-bridge` on `127.0.0.1:18123`, through the existing HTTPS port `18453`. **No separate English Bridge, separate container, extra listening port, new DB, upstream addon fork or IINA plugin requirement.** Previous v0.3.0 functionality is preserved.

- **English Detail for Chinese/HK/Taiwan catalogs:** when a card has `tch:movie:<tmdb>[:<imdb>]` or `tch:series:<tmdb>[:<imdb>]`, the Bridge resolves it against existing **AIOMetadata SH**, requires exact TMDB and (when given) IMDb identity, and serves SH English movie/series metadata under the **original `tch:` page ID**. Movies preserve canonical `defaultVideoId`; series preserve every `videos[].id` and SH/SH2 enrichment behavior.
- **Series premiere-year hint:** optionally show an English Detail title such as `Cause of Death (2026)` based on identity-verified SH's **series-level year**, vetoing SH/SH2 conflicts. This is a *display/lookup hint*, not an external-ID handoff, and does not guarantee Trakt matching.
- **Safe opt-in flags** in the existing private `.env`: `CHINESE_CATALOG_ENGLISH_META_ENABLED=1` and `CHINESE_CATALOG_TITLE_YEAR_ENABLED=1`. Both default **OFF** for newly installed configurations. `CATALOG_PASSTHROUGH_ENABLED` and other SH/SH2 options are preserved.
- **Stremio manifest:** `v0.4.0`, existing Bridge addon ID `com.homedocker.metadata-bridge`, adds `meta` with `idPrefixes: ["tch:"]` **only when English Meta is enabled**, alongside original Meta/Catalog resources. **Bridge never advertises `watch_state`.**
- **AIOStreams resources:** Chinese & HK Catalog **Catalog ON, Meta OFF**; the **existing** Bridge addon **Meta ON** (Catalog as currently configured); SH **Watch State ON**. Remove the temporary staging Bridge addon after the production rollout has passed; do not replace the existing Bridge URL with a staging URL.
- **No guessing:** missing SH identity, conflicting TMDB/IMDb or unresolved year fail closed; same-name `Cause of Death (2022)` must not be auto-matched as the 2026 show.

**Live staging evidence:** `tch:movie:146:tt0190332` → *Crouching Tiger, Hidden Dragon*, playback `tt0190332`; `tch:series:223911:tt27432264` → *Renegade Immortal*, 202 episodes; `tch:series:92408:tt6150576` → *Midnight Diner: Tokyo Stories*, 30 episodes; `tch:series:331912` → **Cause of Death (2026)**, 25 episodes, first ID `tt45680280:1:1`. AIOStreams English Detail, playback and Watch State were observed in staging. **Full IINA auto-match for Cause of Death after adding the year is not yet proven end-to-end.**

For safe HomeDocker upgrade and rollback use [v0.4.0 Stable Release Notes](docs/RELEASE_v0.4.0.md). For the year hint limitations and future verified ID handoff, see [Year Hint Design](docs/CHINESE_SERIES_YEAR_HINT.md). PR #18 Collection Similar remains **out of scope**.

---

## Current production contract

- **Movie metadata:** SH remains authoritative; if its ClearLogo is missing, a **strongly ID-verified** SH2 movie logo is added as `meta.logo` only.
- **Series metadata:** conservative SH/SH2 merge, with existing SH episode and video IDs preserved; no speculative identity joins.
- **Catalog passthrough:** when `CATALOG_PASSTHROUGH_ENABLED=1`, mirror the configured SH and SH2 catalog definitions and page results using reversible provider-namespaced **catalog IDs**. Source filters/genre/skip and content IDs remain unchanged. Only absent preview logos are enriched, bounded by 12 lookups/page and four workers by default.
- **Collections:** full SH Collection metadata rewrites references in `collection.sources[].catalogId` to mirrored Bridge catalog IDs; **never rewrites collection items, content IDs or third-party addon links**.
- **Anime / SH Watch State:** no synthetic anime art, no Watch State proxy, no Bridge database writes. Existing SH tracker remains separate.
- **AIOStreams resources:** Bridge supplies **Metadata + Catalog**; SH retains **Watch State** and other needed resources, SH2 other needed resources. Keep the original source provider configs and existing catalog selections/order; do not enable all 198 catalogs just because Bridge advertises them.

### Prior v0.3.0 deployment fingerprints — historical reference

**Historical v0.3.0 baseline (before the v0.4.0 upgrade).** The prior HomeDocker Bridge was deployed with these Git blob fingerprints:

| File | Git blob SHA |
| --- | --- |
| `bridge.py` | `91f85ae4402cf3b4637d0aa9de8c0c99eeb94fd5` |
| `compose.yml` | `efa1c013da1da8dd4378f59fbe5c60e04a52ea05` |

Use `git hash-object` to verify. Current production enables `CATALOG_PASSTHROUGH_ENABLED=1` in a **private** `.env`. New installations default it to `0` until the catalogs/resources have been deliberately reviewed. Browser refresh or addon update may be required to pick up corrected Collection references.

Release evidence: 198/198 SH+SH2 source manifest catalogs mirrored; source page payload comparison altered missing logos only; Movie Detail and Home Hero ClearLogo visually accepted for two films; a previously empty Collection reloaded its member grid after source-reference fix and AIOStreams addon refresh. Search cold-cache latency and all Collection variants were not exhaustively benchmarked. Fanart.tv A/B showed only one compatible new logo in 12 current missing-logo candidates, so Fanart is **not** added as a production requirement.

**Stable does not mean 100% logo coverage.** Read [release notes](docs/RELEASE_v0.3.0.md) for scope and honest test limits and [rollback runbook](docs/CATALOG_PASSTHROUGH_CUTOVER.md) for safe service-only recovery.

---

## Archived v0.2 controlled-pilot instructions — historical reference only

> The following sections describe the **earlier v0.2 pilot** and may refer to metadata-only routing or testing steps that have since been superseded. **Do not follow them to reconfigure the current working v0.3.0 HomeDocker deployment.**

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


## Read-only AIOStreams stream-candidate audit (pre-cutover)

The Bridge pilot found four stream candidates each for Tokyo Stories S03E01
and S03E02 using existing AIOStreams stream endpoints. That does not prove
candidate quality, correct episodes or playback availability.

From the same HomeDocker clone (no `.env` changes, no AIOStreams edits):

~~~sh
cd ~/metadata-bridge-preflight
git pull --ff-only
python3 scripts/probe_stream_candidates.py
~~~

Enter the *existing* AIOStreams Manifest URL privately when prompted.
The script reads Bridge metadata and performs **read-only stream discovery**
for S03E01 and S03E02. It prints only candidate counts, locator categories,
the number of distinct/overlapping locators and counts of matching, mismatched
or unrecognized episode labels. It never prints private URLs, magnet hashes,
titles, media IDs, tokenized credentials or watch history.

The label regex supports common `S03E01` / `3x01` patterns but cannot
verify content, correct dubbing/subtitles, real playback or watch-state
continuity. An unknown label is not automatically an invalid stream.
Do not cut over metadata resources until a controlled manual playback
and SH Watch State regression test have been completed.


### Shared locator category diagnostic

The live S03E01/S03E02 audit found one shared opaque locator. New report
field `shared_locators_by_kind` distinguishes external landing-page links
(`externalUrl`) from direct stream links (`url`) without printing any actual
URL, stream name, torrent hash, secret or watch history.

A common external page may be innocuous. A shared direct `url` deserves
closer investigation; neither proves video correctness or availability.
Re-run the read-only candidate probe after `git pull --ff-only`.
