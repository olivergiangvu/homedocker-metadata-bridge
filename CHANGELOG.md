# Changelog

## 0.3.0 — Stable (2026-11-02)

- **Production-verified Movie ClearLogo:** when SH movie metadata has no valid logo, pull only `meta.logo` from SH2 after strong matching IDs. No title matching; no change to other metadata, playback IDs or Watch State. Anime passthrough unchanged.
- **Opt-in Catalog Passthrough:** mirror all configured SH then SH2 catalogs from their existing AIOMetadata manifests. Preserve each catalog's filters, order, media IDs and preview data. Namespace source catalog IDs to avoid SH/SH2 collisions.
- **Catalog logo enrichment:** supplement only missing preview logos from full metadata and verified cross-source fallback; bounded by `CATALOG_ENRICH_LIMIT=12`, workers 4 by default. Home Hero Rotate verified visually on two movies using official AIOStreams.
- **Collection member repair:** translate only SH-owned `collection.sources[].catalogId` references to the Bridge catalog namespace; retain member IDs, folder nesting, genres, artwork and external-addon references. Restored real Collection navigation in official AIOStreams after addon refresh.
- **No Watch State proxy:** SH Watch State remains owned and operated by SH, with no Bridge writes.
- **Validated on HomeDocker:** 198/198 manifest catalog descriptors and safe source page parity; movie Detail logos and Midnight Diner episode ID fingerprints preserved; multiple CI/test runs successful.
- **Coverage research:** 36-page cohort: 652/694 previews already with logo, 5 verified recovered unique titles; no recoverable previews outside current page cap. Fanart.tv tested on 12 verified still-missing works: one compatible HD logo, 11 not found. No production Fanart source change.
- **Security and rollback:** opt-in disabled by default for new instances; existing production enabled via its private `.env`; stage/enable/Collection deployment scripts protect known source hashes and back up previous state. No AIOStreams fork/custom image or SH/SH2 source edits.
- Read [Stable release notes](docs/RELEASE_v0.3.0.md) and [Catalog cutover](docs/CATALOG_PASSTHROUGH_CUTOVER.md) before changing an existing installation.

**Operational qualification:** samples and UI acceptance are not an exhaustive whole-library audit; cold-cache Search response-time targets and all Collection variants were not comprehensively benchmarked. No new API providers added.

## Unreleased — Docker pilot health reliability

- Use lightweight BusyBox health probe with 12-second Docker timeout instead of spawning an extra Python interpreter.
- Add CI smoke test for unprivileged read-only Alpine health probe.
- Document observed HTTP-200 versus Docker-unhealthy mismatch and safe Bridge-only recreation.


## Unreleased

- Unified SH/SH2 series search by verified IMDb/TMDB/TVDB identity, not display name.
- Canonical search results prefer SH title and artwork; TVDB is a fallback.
- Unified series Meta retains SH episodes and appends verified TVDB-only slots,
  including the 10 S3 entries seen by the `Midnight Diner` preflight.
- SH-managed anime series remain passthrough even when their resource type is `series`.
- Missing metadata fields fall back across providers without rewriting IDs,
  release dates, season numbers, or watch state.
- Extend offline tests for different ID namespaces, distinct same-name shows,
  20+10 episode union, identity conflicts and provider-only titles.
- Document deduplicated search catalog cutover and separate Media Servers results.


- Compare SH/SH2 episode cohorts by shared ID and season/episode, flag conflicting air dates and TVDB-only seasons without printing IDs.
- Add 20-vs-30 and conflicting-video-ID test fixtures (14 tests total).
- Fix real AIOMetadata v3.8.2 ID recognition (`imdb_id`, underscore-prefixed ids, and `meta.id`); reject self-contradictory IDs.
- Add offline integration tests with actual AIOMetadata field layout.
- Add redacted, read-only upstream probe for HomeDocker; no live connections or watch state mutations required during CI.

- Add GitHub Actions CI for offline tests, syntax and Compose validation.
- Add GitHub-first publishing workflow and secret-safe ignore rules.
- Preserve the independent SH Watch State tracker in the integration plan.

## 0.1.0 — controlled pilot

- Hybrid series metadata with conservative series/episode identity checks.
- Logical JSON passthrough for non-series metadata from SH.
- Search catalog federating SH and SH2.
- Read-only upstream requests, bounded in-memory response cache.
- No Watch State resource, no stream adapter, no HomeDocker data migration.
