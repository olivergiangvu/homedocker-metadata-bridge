# HomeDocker Unified Metadata Bridge v0.3.0 — Stable

**Release date:** 2026-11-02
**Release ref:** `v0.3.0-stable`
**Component:** `homedocker-metadata-bridge`; official AIOStreams and AIOMetadata SH/SH2 remain unmodified.

## Scope

1. Verified movie `meta.logo` fallback from SH2 into SH when strong IMDb/TMDB/TVDB IDs match. Original movie payload except missing logo remains unchanged.
2. Optional passthrough of the source-defined SH + SH2 catalogs, with provider-namespaced **catalog IDs** but original preview ID, count, source order, filters and pagination preserved.
3. Only absent `metas[].logo` enriched from the same provider's full metadata or identity-verified second source. Bounded default: **12** items per source page, **4** workers.
4. SH **Collection** metadata `collection.sources[].catalogId` translated from its original catalog namespace to the Bridge's mirror namespace. Nested folders, `collection.items`, genre and external addon references remain unchanged. Restores the in-collection movie grids on official AIOStreams after refreshing the installed addon.
5. Series metadata merge behavior carried from v0.2; SH's episode identifiers remain stable.

No new artwork APIs, provider keys, add-on forks, AIOStreams custom builds, Watch State proxy or write operations.

## Verification evidence on HomeDocker

- Bridge source deployed and tested for Catalog opt-in: `bridge.py` **Git blob** `91f85ae4402cf3b4637d0aa9de8c0c99eeb94fd5`; `compose.yml` Git blob `efa1c013da1da8dd4378f59fbe5c60e04a52ea05`. Use `git hash-object` for this comparison, not SHA-256.
- Read-only and post-deployment checks passed for **198/198** manifest catalog definitions (SH 190, SH2 8) and sampled catalog pages whose **only** changed preview field was missing logo.
- Movie Detail ClearLogo and Home Hero Rotate ClearLogo visually confirmed on *Collision Course* and *Apocalypse Z: New World*; Full Metadata episode ID coordinates of *Midnight Diner* preserved.
- An AIOMetadata SH Collection (*Paul Thomas Anderson*) initially showed an empty member grid. With the Collection source-reference fix deployed and AIOStreams addon refreshed, the user verified the member grid appeared.
- Extended logo audit: **652/694** preview occurrences had a logo before enrichment across 36 catalog pages; **42** missing occurrences represented **28** unique works, 5 unique alternate-source recoveries; **0** recoverable missing preview IDs outside the configured limit of 12.
- Fanart.tv API read-only A/B for twelve verified missing works: **one** compatible HD logo found, eleven not found; insufficient benefit to warrant changing artwork provider globally.

These figures are scoped to sampled catalogs, not all library items. Presence of an image URL does not guarantee successful image delivery. No broad latency benchmark for cold-cache Search/complex catalog pages, no exhaustive Collection-source combination matrix, and no independent end-to-end Watch State write/read smoke were documented for this release. The code does not expose a Watch State endpoint or write to SH.

## Fresh deployment defaults and production differences

- `CATALOG_PASSTHROUGH_ENABLED=0` is the safe default for a newly deployed Bridge, retaining metadata-only behavior and the existing hybrid-series-search manifest. When enabled, its manifest and health report version `0.3.0`; with the feature disabled the compatibility manifest still reports `0.2.0`.
- HomeDocker production has `CATALOG_PASSTHROUGH_ENABLED=1` in the private Bridge `.env`. **Do not copy any public repo `.env` or replace the private file.**
- Sources are configured from the existing SH/SH2 manifest settings; **no fanart key** is required. The AIOStreams configuration should have Bridge **Metadata + Catalog**, SH retains **Watch State** plus other unrelated required resources, and SH2 maintains its unrelated required resources.
- The bridge advertises 198 catalogs; do not enable them all in the UI. AIOStreams user-specific chosen catalogs and display ordering remain controlled from AIOStreams. In particular, confirm that `Turn new catalogs on` is disabled for new Bridge catalogs.
- Avoid changing SH or SH2 UUID-based manifest URLs or catalog settings to install this stable release.

## Production release / rollback

This release **records code already deployed and accepted** on HomeDocker, not a new HomeDocker cutover or restart. To check local runtime without exposing secrets:

```bash
sudo docker inspect -f '{{.State.Status}}' homedocker-metadata-bridge
SOURCE="$(sudo docker inspect -f '{{range .Mounts}}{{if eq .Destination "/app/bridge.py"}}{{.Source}}{{end}}{{end}}' homedocker-metadata-bridge)"
sudo git hash-object "$SOURCE"
curl -fsS http://127.0.0.1:18123/health | python3 -c 'import json,sys;d=json.load(sys.stdin);print(d.get("status"), d.get("version"))'
```

Expected source blob `91f85ae4402cf3b4637d0aa9de8c0c99eeb94fd5` and health `ok 0.3.0`.

Private verified rollback snapshots already on HomeDocker:

- Source / Compose: `/var/backups/homedocker-metadata-bridge/catalog-stage-20261102T063534Z/`
- Catalog enable flag `.env`: `/var/backups/homedocker-metadata-bridge/catalog-enable-20261102T064046Z/.env`
- Collection hotfix previous source: `/var/backups/homedocker-metadata-bridge/collection-rebind-20261102T075126Z/bridge.py`

Never print or upload the private `.env`; no database/cache reset or SH Watch State changes. For a **UI-only rollback**, return Bridge to Metadata-only, reenable the original SH/SH2 catalog selections/resources, keeping SH Watch State on; do not overwrite Bridge source unless the service itself fails. See [Catalog cutover and rollback runbook](CATALOG_PASSTHROUGH_CUTOVER.md).

## Stable acceptance summary

**Accepted**: actual Movie Detail and Home Hero ClearLogo, real Collection member navigation, manifest/catalog parity, movie and episode metadata invariants, CI regression tests, no explicit Watch State resource.

**Monitored limitations**: cold-cache search/catalog latency, images whose URL may be invalid, non-identifiable rare titles, and external addon reference ownership. Fanart.tv remains a research-only optional source, not a dependency.

This version is *stable* for the current validated HomeDocker architecture; it makes no promise that every catalog or movie in the library has been exhaustively exercised.
