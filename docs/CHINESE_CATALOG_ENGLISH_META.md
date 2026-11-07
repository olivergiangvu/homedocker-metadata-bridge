# Chinese Catalog — English Details via Metadata Bridge (staging)

**Experimental, disabled by default. No upstream forks or web UI changes.**

## Desired behavior

Keep Chinese & HK Catalog's CN/HK/TW catalog rows, poster images and discovery. When opening a catalog card such as `tch:movie:146:tt0190332`, use **English metadata already available from AIOMetadata SH** rather than Chinese-specific detail metadata. SH remains the sole Watch State provider. The Metadata Bridge still does not serve a `watch_state` resource.

Chinese catalog **cards may retain Chinese names/posters**; this feature changes only the *detail page*. It does not translate catalog titles, overwrite user data or rewrite playable episode IDs.

## Source ID and validation

Chinese & HK Catalog v1.6.4 uses two forms:
- `tch:movie:<tmdb_id>:<imdb_id>` and `tch:series:<tmdb_id>:<imdb_id>`
- `tch:movie:<tmdb_id>` and `tch:series:<tmdb_id>` (no IMDb mapping).

Bridge parses the exact declared type and IDs; asks SH for `tt...` and/or `tmdb:<id>`. The SH response must explicitly attest the TMDB ID, and if an IMDb ID was supplied, that IMDb ID too. **Inconsistent IDs fail closed (404).** No fuzzy name/year matching or other language source fallback.

- Movie detail: SH metadata + existing verified logo fallback; `meta.id` stays `tch:...` so the client opens this exact card; `behaviorHints.defaultVideoId` stays canonical `tt...` or `tmdb:<id>`.
- Series detail: existing Bridge SH-first series merge; all original episode `videos[].id` remain unchanged. No invented episodes.
- If SH does not support the canonical ID or cannot attest its identity, this release candidate returns 404 rather than showing a different title.

## Configuration

New Bridge Compose environment (default OFF):

```dotenv
CHINESE_CATALOG_ENGLISH_META_ENABLED=0
```

To test in **a separate staging instance**, set this flag to `1` and keep SH/SH2 manifest URLs unchanged. Do not change production or force-recreate a stable service before acceptance.

In **AIOStreams addon configuration**:
- Chinese & HK Catalog: `Catalog` ON, `Meta` OFF.
- Metadata Bridge: `Meta` ON.
- Retain SH's existing Watch State addon and existing resource settings.

With the feature enabled, Bridge's manifest advertises a dedicated `meta` resource with `idPrefixes: ["tch:"]`, alongside its existing general `meta` resource. This makes English staging Bridge the matching-prefix provider instead of an arbitrary generic metadata addon. AIOStreams v2.37 tries matching prefixes first; then general providers. **If Chinese & HK Catalog Meta remains ON, its explicit `tch:` prefix wins and the detail stays Chinese.** If Bridge Meta is OFF, the `tch:` detail may be missing entirely.

## Pre-production acceptance

1. Build and unit tests pass on a Draft PR; Compose validates with non-secret placeholder URLs.
2. With flag OFF, responses, manifest and resources remain identical to baseline.
3. With flag ON, from a separate staging Bridge, verify `/meta/movie/tch:movie:146:tt0190332.json` returns English SH name and canonical `defaultVideoId=tt0190332`.
4. Verify a Chinese drama/series with an IMDb ID displays English series metadata, season list and *unchanged* episode video IDs.
5. Verify a title without an IMDb ID. If SH has no TMDB-indexed metadata, record it as unsupported, **do not blindly enable the feature for the whole catalog**.
6. Verify series with extended SH2-only episodes remain intact, and SH Watch State for the selected title remains correct.
7. Verify title-identity conflict returns 404, does not silently display a different title.
8. Verify existing non-`tch:` movies and series unchanged.
9. Test AIOStreams Detail with the real Chinese catalog in a disposable **staging AIOStreams configuration**, not production user preferences.

## Rollback

If staging fails: disable Chinese addon Meta mapping and/or set `CHINESE_CATALOG_ENGLISH_META_ENABLED=0` on the staging Bridge and recreate only that staging service. Production Bridge and AIOStreams remain unchanged. Do not merge or release the PR until it passes.

## Limitations

- This is a detail-page metadata adapter, **not** an English replacement for the CN/HK/TW catalog rows themselves.
- Requires SH to recognize and return verifiably matching IDs, including for no-IMDb titles; not guaranteed for every local TV entry.
- Watch State identity for `tch:` detail pages needs live client acceptance testing before production: this adapter does not modify AIOStreams/SH tracking code.
