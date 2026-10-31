# Production cutover checklist (not executed)

## Gate 1 — GitHub source review

- [ ] Public repository exists with clean Git history and `.env` not tracked.
- [ ] GitHub Actions CI completed successfully.
- [ ] SH and SH2 UUIDs, passwords, API keys are absent from commits and logs.
- [ ] Bridge manifest declares `meta` and `catalog`, **not** `watch_state` or `stream`.

## Gate 2 — Live upstream read-only verification

- [ ] Run `scripts/probe_upstreams.py` on HomeDocker and review its redacted output.
- [ ] Verify AIOMetadata provider IDs using `imdb_id` / `_imdbId` / `_tmdbId` / `_tvdbId` as well as `ids`, not just `meta.ids`.

- [ ] SH manifest URL is accessible from the Bridge host without query/path rewriting.
- [ ] SH2 manifest URL is accessible from the Bridge host.
- [ ] SH `movie` and `anime` JSON logically equal Bridge output (`jq -S`).
- [ ] Known-good SH series retains `meta.id`, `videos[].id`, seasons and episode numbers.
- [ ] TVDB-only series opens and uses the right root ID.
- [ ] Midnight Diner metadata, episode counts, original air dates and alternative season numbering are manually inspected; no automatic override without proof.
- [ ] Hybrid search results include names from both upstreams where available.

## Gate 3 — Watch State / identity preservation

- [ ] Record three existing Continue Watching item IDs and playback positions.
- [ ] Record current Next Up for those series.
- [ ] Confirm SH is connected under AIOStreams Media Server → Trackers, recording and importing as before.
- [ ] Only Bridge offers `Metadata` after cutover; SH remains enabled with `Watch State`.
- [ ] Play/pause a known episode and verify local resume position remains correct.
- [ ] Verify SH still records playback and imports history after cutover.
- [ ] Test yastream streams independently: matching series thumbnails do **not** guarantee stream IDs are playable.

## Gate 4 — Rollback readiness

- [ ] AIOStreams configuration exported before any changes.
- [ ] Original SH and SH2 manifest URLs preserved privately.
- [ ] Document SH's original selected Resources.
- [ ] Rollback means disable Bridge, restore SH `Metadata + Catalog + Watch State`, save, recheck history.
- [ ] No SQL delete, volume reset, UUID rotation or image rebuild is part of this cutover.
