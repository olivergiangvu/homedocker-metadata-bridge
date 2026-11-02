# ClearLogo coverage — production-safe A/B plan (HomeDocker)

## Established architecture (do not break)

- SH = AIOMetadata TMDB primary; **Watch State remains on SH**.
- SH2 = AIOMetadata TVDB secondary; Bridge reads its configured manifest privately.
- Bridge at `127.0.0.1:18123` provides **metadata only** to AIOStreams.
- Bridge v0.2 fills missing series `logo` from verified SH2. Movies and anime are still SH passthrough. Keep all series episode/video IDs and search identity rules unchanged.
- No logo configuration should modify Trakt, the Stremio watch-state resource, the stream resolver or IINA.

## Baseline probe — only read operations, no credentials printed

Over SSH, use a pinned audited GitHub commit. The script runs in the **already running** bridge container, whose environment already contains both private SH/SH2 manifest URLs. Neither the script nor its output prints those URLs or tokens, and no Docker Compose, .env, caches or source files change.

```bash
set -euo pipefail
AUDIT="https://raw.githubusercontent.com/olivergiangvu/homedocker-metadata-bridge/diagnostics/read-only-clearlogo-coverage/scripts/probe_logo_coverage.py"
curl -fsSL "$AUDIT" | sudo docker exec -i homedocker-metadata-bridge python3 - --self-test
curl -fsSL "$AUDIT" | sudo docker exec -i homedocker-metadata-bridge python3 - \
  --search "This City Never Sleeps" \
  --search "The Mystic Nine" \
  --search "The Night Manager" \
  --series tt6150576 \
  --movie tt0137523
```

To avoid trusting a mutable branch, pin the download URL to the commit SHA of the reviewed PR before production use. The probe examines SH and SH2 metadata, checks strong series identity and compares them with the **live** Bridge `/meta/` HTTP response. Unknown/unapproved image hosts are not requested. An image HEAD error is not proof the image fails in AIOStreams' own relay.

`NOT_RESOLVED` means an exact search title was unavailable or ambiguous; it does **not** mean the item lacks a logo. Supply a verified ID with `--series` or `--movie` and rerun.

## Recommended trial policy

| Scope | Primary (do not change) | Secondary trial | Why |
|---|---|---|---|
| Series | SH: TMDB metadata + TMDB logo | SH2: TVDB metadata, **Fanart.tv Logo** preference | SH logos win; verified SH2 fills only absent logos; Fanart.tv can fill before SH2's TVDB logo fallback |
| Movies | SH TMDB, passthrough | No live change yet | Bridge v0.2 does not enrich movies; changing SH's UUID/config can also affect Watch State |
| Anime | SH passthrough | No live change yet | Anime ID/season mapping is special; protect its current behavior |
| Poster, backdrop, episode art | Existing policies | Unchanged | Logo-only evaluation |
| Search, Watch State, stream providers | Existing | Unchanged | Never cross-link metadata artwork to playback/episode identity |

The fallback behavior is specific to AIOMetadata's code and depends on the configured provider, valid TVDB IDs, and an operational Fanart API key. **Fanart is not a universal fallback**. TVDB-direct logo selection has code paths returning null early; do not assume it improves union coverage.

### Critical immutable-UUID detail

Changing AIOMetadata's SH2 configuration may produce a **different manifest URL / configuration UUID**. The deployed Bridge does **not** discover that change automatically: `SH2_MANIFEST_URL` is fixed in the deployed Compose environment. A cloned/test SH2 configuration can be checked separately, but do not change production `.env` or AIOStreams yet. If a trial performs well, use the reviewed deployment process to update SH2's private URL in Bridge and re-check the health, strong identity, episode count and live logo result before a controlled rollout. SH/Watch State URL must remain intact.

## Interpretation

- **SH logo + Bridge logo:** Baseline already covers the item. Avoid replacement for coverage reasons.
- **SH empty, SH2 logo, Bridge logo:** Existing union is working; gains are from SH2's artwork source.
- **SH empty, SH2 logo, Bridge empty:** Inspect strong identity match; do not disable safety guard to force a logo.
- **Bridge logo, AIOStreams no logo:** Verify AIOStreams Jellyfin `ImageTags.Logo` and `/Items/{id}/Images/Logo`, then cache/format support; changing artwork providers will not solve a rendering issue.
- **All empty, but Strand/Lume has logo:** The clients may enrich artwork independently; check a verified TMDB/TVDB match and optional art-only provider, not a title-based guess.

## Follow-up decision gate

Run baseline first. Then perform a **separate trial** of SH2 Fanart logo preference without replacing SH2's active URL. Compare the same films, count validated extra logos and HTTP failures, verify no lost SH logos, and only then propose controlled production cutover. A future *optional* art-only Bridge extension can supply movie logo fallback, but **must not silently change movie/anime passthrough in v0.2**.
