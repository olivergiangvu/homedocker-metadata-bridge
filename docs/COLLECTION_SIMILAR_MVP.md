# Optional Collection siblings in More Like This — experimental

This feature is **off by default**. Do not deploy to the working HomeDocker stable stack until CI, a separate staging instance and real AIOStreams acceptance pass. It does **not** alter the upstream AIOMetadata images, the AIOStreams web UI, playable video IDs, or SH Watch State.

## Why

AIOStreams v2.37 Web requests **20** similar items in `packages/web/src/lib/queries.ts` (`Limit: 20`). Its backend permits up to 50 per API request, but **no ENV increases the web UI's fixed 20**. AIOMetadata's `SIMILAR_CATALOG_SIZE` only changes its own response cap.

The web app presents the *first nonempty* `similar` catalog in configured addon order. This is a **single** row; separately installed catalogs are not concatenated.

## What Bridge adds

- Optional `catalog/movie/hd-collection-similar/similar=<id>.json` for a movie.
- Resolve the movie to a **verified TMDB ID** from SH metadata, look up `/movie/{id}` and `belongs_to_collection`, then `/collection/{id}`.
- Add the other movies in that official TMDB collection and fill remaining slots with existing SH `aiom.similar` recommendations, up to 20 entries.
- Remove duplicates when their known TMDB IDs match. Do not infer a universe from title similarity, cast or genres.
- Series uses SH `aiom.similar` unchanged; a series franchise/universe cannot currently be inferred from TMDB movie collections.
- If TMDB API is not configured or responds with an error, return existing SH recommendations. If both are empty, AIOStreams can use its next `similar` source.

## Optional private environment

In a *staging copy* of the Bridge Compose project, supply:

```dotenv
COLLECTION_SIMILAR_ENABLED=1
# ONE of the two private credentials, never committed:
TMDB_READ_ACCESS_TOKEN=
TMDB_API_KEY=
```

The v4 TMDB Read Access Token (Authorization Bearer) is preferred; the v3 API key is also supported. Do not store credentials in Git or issue logs. `compose.yml` forwards the optional fields; changes to environment need a Bridge container recreation **only after acceptance**.

Keep `COLLECTION_SIMILAR_ENABLED=0` in production until tests pass. The GitHub release tag remains immutable.

## Acceptance checklist

1. Existing `/health`, `/manifest.json` and SH Watch State are unaffected when the flag is off.
2. With flag on, manifest lists `hd-collection-similar` for movie and series, *required* `similar` extra; excludes `watch_state`.
3. A known collection movie (for example, `tt2250912`, Spider-Man: Homecoming) returns actual sequel/sibling movie cards along with SH recommendations.
4. A stand-alone movie without a TMDB collection returns SH recommendations.
5. Verify every emitted `tmdb:<id>` can open via Bridge `/meta/movie/tmdb:<id>.json` and then resolves playback via existing addons. Reject the rollout if client routing fails.
6. Configure Bridge's Similar catalog **ahead of SH's `aiom.similar` in AIOStreams catalog precedence**; otherwise the first nonempty SH result will conceal the Bridge results. Do not disable SH Watch State.
7. Verify a series page still receives SH recommendations. No invented series universe.
8. Confirm v0.2.0 stable production is unchanged during this preflight.

Rollback: turn the feature flag off in staging or disable Bridge's Similar catalog in AIOStreams. Do not touch SH tracker, database or video IDs.

## Limitations

The feature currently supports **exact official TMDB movie collections** only. Broad cinematic universes such as the MCU, including TV series, require a separate verified franchise mapping, not a guess based on title/genre. Collection labels are not rendered as separate headings; all results appear inside the single existing More Like This row.
