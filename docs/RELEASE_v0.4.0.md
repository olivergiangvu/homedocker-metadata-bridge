# HomeDocker Unified Metadata Bridge v0.4.0 — Stable

**Release date:** 2026-11-08  
**Tag:** `v0.4.0-stable`  
**Component:** **the existing** Docker Compose service `homedocker-metadata-bridge`, localhost port `18123`, Nginx HTTPS `18453`.

This release **integrates** previously separated Chinese Catalog English Meta and series-year staging functions as optional features in the **same existing Metadata Bridge service**. No separate Chinese/English Bridge container, port, image, database, UI fork or upstream addon change is required or intended. Release v0.3.0 Catalog/Collection, movie-logo and series-merge features are retained.

## Release changes

1. **English Detail for Chinese/Hong Kong/Taiwan catalogs**, when AIOStreams opens `tch:movie:<tmdb>[:<imdb>]` or `tch:series:<tmdb>[:<imdb>]`. The Bridge retrieves SH's already-configured English metadata. **TMDB ID and any supplied IMDb ID must both match**; no title-based guessing. `meta.id` remains the original `tch:` route ID, movies retain canonical `behaviorHints.defaultVideoId`, and series retain every `videos[].id`.
2. **Verified series premiere-year suffix** e.g. `Cause of Death (2026)`. Series-level year must be attested by identity-verified SH; if final SH/SH2 combined metadata contradicts SH, omit the hint. Missing or contradictory year is not guessed. Display title only, not new episodes or changed history IDs.
3. **Two backward-compatible flags in the existing private Bridge `.env`**, both default OFF for new installations:
   ```dotenv
   CHINESE_CATALOG_ENGLISH_META_ENABLED=1
   CHINESE_CATALOG_TITLE_YEAR_ENABLED=1
   ```
   The second flag has an effect only when the first is enabled. Production's already-enabled `CATALOG_PASSTHROUGH_ENABLED=1` and other env settings are retained.
4. **Manifest/Health version `0.4.0`** for all configurations; same addon ID `com.homedocker.metadata-bridge`, same generic Meta/Catalog resources. With English Meta flag ON, additionally advertises `{"name":"meta","types":["movie","series"],"idPrefixes":["tch:"]}` to AIOStreams. **No Bridge `watch_state` resource or write endpoints.**
5. The release is a source/Compose change for the existing Python 3.12 Alpine Docker container. Neither AIOMetadata SH/SH2 nor official AIOStreams nor Trakt+/Episode Info+ is rebuilt or changed.

## Evidence / test limits

- User-confirmed staged **18124 English Bridge** Movie `tch:movie:146:tt0190332` → *Crouching Tiger, Hidden Dragon*, `defaultVideoId=tt0190332`. Series *Renegade Immortal* → 202 episodes and *Midnight Diner: Tokyo Stories* → 30 episodes, unchanged identifiers. AIOStreams English Detail, artwork, playback and SH Watch State worked in UI.
- User-confirmed staged **18125 year hint** `tch:series:331912` → *Cause of Death (2026)*, releaseInfo `2026`, **25 episodes**, first `video.id=tt45680280:1:1`, health `0.4.1-rc1`. This is backend evidence; it is **not** proof that IINA automatically resolves *Cause of Death* after the displayed year reaches the player.
- v0.4.0 code promotes these exact features into one service with a single version. PR-specific CI included 61 offline tests, strict conflicting IDs/year cases and Compose validation. Release candidate must pass complete main CI again.
- **Not covered by this release:** Draft PR #18 Collection Similar; external-ID transfer from AIOStreams to IINA; promised successful scrobble of all same-name TV series; alteration to SH's Watch State backend.

## Upgrade: one existing container, preserving private configuration

**Only after the stable GitHub tag exists.** Do **not** deploy from moving `main` or a Draft PR branch.

1. Confirm existing v0.3.0 source `bridge.py` Git blob `91f85ae4402cf3b4637d0aa9de8c0c99eeb94fd5`, `compose.yml` blob `efa1c013da1da8dd4378f59fbe5c60e04a52ea05`, existing container `homedocker-metadata-bridge` healthy on `18123`. The upgrade helper refuses other baselines.
2. Check out the **immutable release tag in a separate worktree** on HomeDocker, not the live source tree:

   ```bash
   P="$HOME/metadata-bridge-preflight"
   R="$HOME/metadata-bridge-v0.4.0-stable"
   git -C "$P" fetch origin tag v0.4.0-stable
   git -C "$P" worktree add --detach "$R" v0.4.0-stable
   sudo bash "$R/scripts/upgrade_bridge_v0.4.0_homedocker.sh" --check
   ```

3. Only after the read-only preflight passes, apply **one-service controlled upgrade with automatic rollback**:

   ```bash
   sudo bash "$R/scripts/upgrade_bridge_v0.4.0_homedocker.sh" --apply
   ```

   Helper creates a private `0700` backup directory containing v0.3.0 source, Compose and original `.env`. It enables both flags without copying or printing SH/SH2 UUID credentials, replaces only existing live Bridge source/Compose/.env files and `--force-recreate`s **only** `metadata-bridge` within its current Compose project; it does **not** create new containers with a new service name. It checks v0.4.0 health, manifest prefix, unchanged legacy Catalog IDs, preserved Tokyo Stories episode IDs, *Cause of Death (2026)* 25 episodes and movie playback ID. On failed checks the helper restores all previous files and recreates v0.3.0.

   **This deliberately restarts the Bridge once.** Active AIOStreams requests at the moment of replacement may briefly fail; perform during low use. The existing SQLite Watch State in SH is not part of the Bridge service and is not changed.

4. **AIOStreams:** keep the **existing** `https://olivergiangvu.asuscomm.com:18453/manifest.json` installed Bridge addon (unchanged URL and addon ID), set its **Meta ON**. Keep Chinese & HK Catalog **Catalog ON / Meta OFF** so the explicit `tch:` prefix in Bridge is selected. Keep AIOMetadata SH **Watch State ON**; existing Catalog selections/order need not change. Remove the temporary `/english-staging/` addon from **test configuration** only after production English Detail is verified.

5. Validate on AIOStreams / IINA: English movie and TV Detail, *Cause of Death (2026)* English title/episodes, playback, Resume, SH Watch State, Trakt+ manual/exact-match safety and unrelated movie/series unaffected. **If the IINA title fails to carry year, do not enable permissive Trakt matching or treat a generic HLS filename as proof.**

## Manual rollback and cleanup

If a problem is found **after** the helper already reported success, use the backup path printed by `--apply`:

```bash
sudo bash "$R/scripts/upgrade_bridge_v0.4.0_homedocker.sh" --rollback /var/backups/homedocker-metadata-bridge/v040-REPLACE_TIMESTAMP
```

This checks that the backup is the reviewed v0.3.0 and live files are v0.4.0 before restoring. Do not manually delete the original private `.env`, SH data, AIOStreams configuration, or other Docker services.

**Only after full acceptance**:

- Disable/remove staging Bridge add-on manifest `/english-staging/manifest.json` from the AIOStreams test profile.
- Stop/remove containers **metadata-bridge-english-staging** (`18124`) and **metadata-bridge-year-staging** (`18125`). They were launched with `--rm`, so `sudo docker stop <name>` suffices. Do not stop `homedocker-metadata-bridge`.
- Remove only the temporary Nginx `location ^~ /english-staging/` block (keep `location /` proxying to `18123`). Back up the Nginx file, run `sudo nginx -t` and only then reload. Do not alter other vhosts or firewall ports.
- Remove worktrees only after safe backup retention and rollback window review. No new permanent English Bridge port, service or external HTTPS URL is needed.

## Security and version consistency

The `tch:` resolution is read-only and explicit-ID verified; no sensitive configured manifest URLs, API keys or stream URLs are logged or added to git. The release workflow uses a reviewed SHA-pinned `stable/v0.4.0` branch/tag, does not overwrite existing tags and publishes a normal **Latest Stable** GitHub Release. Neither SH Watch State nor user progress is migrated.

**Previous stable rollback release:** `v0.3.0-stable`. **New stable release:** `v0.4.0-stable`.
