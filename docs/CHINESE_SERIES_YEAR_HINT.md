# English Chinese Catalog year hint — staging experiment

This is **not a verified external ID handoff**. It is an optional display-title disambiguation that makes the independently documented *premiere year* visible to existing IINA media-title parsers. It cannot prove the show by itself; Trakt+, TMDB and TVmaze must still verify the exact show/season/episode and fail closed if ambiguous.

## Why

AIOStreams v2.37 Custom Link currently offers `{title}`, `{filename}`, and `{returnUrl}`, but **not a verified `{tmdbId}` or `{year}` variable**. For a Chinese Catalog `tch:` item, Jellyfin's 32-hex `played` ID may be hashed (`b2`) and is not decodable offline. An HLS CDN playback like `index.m3u8` loses the exact `tch:` ID. No browser auth token, manifest credential, signed URL or stream URL should be forwarded as a pretend identity signal.

Example from real screenshots: `Cause of Death · S01E04 · Episode 4` from `index.m3u8` is ambiguous between a TVB 2026 25-episode series and a 2022 UK documentary. Both have `S01E04`; both English titles are **Cause of Death**, with generic episode names and plausible runtimes. The target Chinese catalog is mapped by KKPhim to TMDB **TV 331912**, but IINA cannot read this ID from the current player arguments.

## Implemented: opt-in year label, not external ID

New flag (default **OFF**) in addition to `CHINESE_CATALOG_ENGLISH_META_ENABLED=1`:

```dotenv
CHINESE_CATALOG_TITLE_YEAR_ENABLED=1
```

For *only* `tch:series:...` English Detail responses, derive premiere year from authoritative AIOMetadata SH series-level `released` / `releaseInfo` / `year`. All present, parseable signals must agree; conflicting or unavailable year → leave name unchanged. Year outside 1900–current-year+1 rejected. If a `(YYYY)` or `[YYYY]` suffix already exists, leave title unchanged (including mismatched suffix) rather than guessing. For 2026 TVB source with `released=2026-09-07`:

```
Before  meta.name = "Cause of Death"
After   meta.name = "Cause of Death (2026)"
        meta.id   = "tch:series:331912[:tt...]"
        videos[].id unchanged
```

AIOStreams's `fullTitle(item)` then supplies `Cause of Death (2026) · S01E04 · Episode 4` in an external-player label, without editing AIOStreams code. This is a **metadata presentation change in English Detail**, not a synthesized IMDb/TMDB token or a new playlist entry. Chinese source catalog card titles remain unchanged.

Trakt+ v0.2.9 parses the year from filenames/structured player titles, searches Trakt, then **must verify the exact candidate series and S/E**. Episode Info+ v1.3.4 parses show year and checks TMDB TV year plus the requested episode and runtime. Existing strict matching remains in force. If TVB 2026 is absent from a provider, fail closed and use manual Correct Match/Pick.

## Staging (no production changes)

1. Checkout this branch in a **separate Git worktree**, retaining English Bridge `18124` and production `18123`.
2. Run a temporary read-only pre-release container on loopback `18125`, with existing private `.env` injected (never displayed).
3. Set both flags above. Check `GET /health` and `GET /manifest.json` for `0.4.0` and the `tch:` prefix.
4. Real data: `/meta/series/tch:series:331912.json` must return a verified SH title `Cause of Death (2026)` and original `videos[].id`. If SH lacks the series, **do not fake an English Detail or alter upstream**.
5. For AIOStreams end-to-end, provide a separate secure HTTPS staging prefix only after backend checks; use disposable AIOStreams configuration. Test `S01E04`, and that the 2022 documentary remains distinguishable.
6. Test English movie detail, unrelated TV series and Watch State regression. Do not merge, release, or change IINA stable based solely on unit tests.

## Actual external-ID handoff (future separate design)

The robust long-term fix is a constrained, authenticated identity handoff from AIOStreams item metadata (e.g. `tmdb:331912`, media type, season, episode) to IINA. The current external player link does not contain such a field. A possible read-only server-side resolver would need properly scoped user authentication, exact Jellyfin item resolution and short-lived claims, and **must never expose private manifest URLs or a signed media stream URL**. Offline `a1` ID decoding works for *some* canonical Jellyfin IDs, **not for hashed `b2` ids** that custom `tch:` records use. Do not treat `played`, a token-like URL, title/year, or a CDN host as authentication or authoritative ID by itself.

**Rollback:** set `CHINESE_CATALOG_TITLE_YEAR_ENABLED=0` in the separate staging instance or stop/remove `18125` only. Neither production Bridge on `18123` nor English staging `18124` is changed.

## Stable v0.4.0 integration

This feature is now packaged as an **opt-in module inside the existing Metadata Bridge Docker service**, not an additional permanently deployed container. The production service remains `homedocker-metadata-bridge` on `127.0.0.1:18123` and existing HTTPS port `18453`. The two flags are read from the existing private Bridge `.env`, not a new configuration store. Once verified in production, remove the `18124`/`18125` staging containers and their temporary Nginx staging route; do not remove the existing `location /` proxy to `18123`.
