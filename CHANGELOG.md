# Changelog

## Unreleased

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
