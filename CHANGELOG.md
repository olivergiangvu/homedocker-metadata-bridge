# Changelog

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
