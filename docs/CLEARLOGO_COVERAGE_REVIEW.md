# ClearLogo coverage assessment before PR #17 stable promotion

## Status and observed facts

Production Bridge v0.3 opt-in Catalog Passthrough has been enabled and HTTP validated (198/198 SH+SH2 catalog definitions, no watch_state proxy). AIOStreams Official Home Hero **Rotate** now visibly renders ClearLogo for **Collision Course** and **Apocalypse Z: New World**, while their movie Detail pages remain correct.

The first live source samples were:

| Source | Catalog type | Total preview rows | With logo before | Missing | Recovered via matching upstream |
| --- | --- | ---: | ---: | ---: | ---: |
| SH page 1 | movie | 20 | 19 | 1 | 1, from SH2 |
| SH page 2 | series | 20 | 20 | 0 | 0 |
| SH page 3 | movie | 20 | 16 | 4 | 4, from SH2 |
| SH2 page 1 | series | 19 | 16 | 3 | 0 |

That yields **71/79 (89.9%)** logo-present previews before Bridge enrichment and **76/79 (96.2%)** potentially present after, *on these sampled pages only*. Identical repeated probes do not constitute independent samples. Image URL presence is not proof that the image returns HTTP 200 or successfully renders. SH2's three unresolved series require more diagnostic evidence before changing providers.

## Current algorithm and bounded coverage

For each source catalog page, `passthrough_catalog` preserves all preview fields, order, IDs, catalog extras and pagination, and checks the **first 12 eligible missing-logo previews** (configurable `CATALOG_ENRICH_LIMIT`, hard upper bound 24). It fetches the full metadata of the same source first. If the logo is still missing, it uses the other existing AIOMetadata source **only after matching strong shared IDs**, never by name/year. Four workers are permitted by current Compose. This does not add logos to anime or non-movie/series catalog types and does not enrich Watch State or Continue Watching cards. Avoid changing the supported identity rules to guess a logo based on title similarity.

Potential *coverage* improvement from raising the page cap exists only if there are **recoverable logos outside that cap**. Increasing the cap without seeing those cases is likely to increase cold-page latency and number of upstream reads for no benefit. Metadata misses are not negatively cached, so there is also potential for repeat latency on unresolvable entries.

Potential *upstream-artwork* improvement: AIOMetadata's existing **Art Providers** settings select movie/series logo source separately. TMDB, TVDB and Fanart.tv (with a valid configured API key) are supported. English Art Only can change the language-selection policy. **Original Language Poster Fallback is for poster, not a general solution to missing logos**. Evaluate alternate artwork providers in an isolated comparison config first; do not modify SH's production profile or Watch State to chase rare logo misses.

Potential *quality* improvement: `usable_artwork` currently rejects known placeholder markers but does not HEAD-check/GET actual image URLs. Audit counts usable URLs, not decoded images. Fetching hundreds of remote images would add unwanted load and may trigger API/rate limits.

Potential *performance* risk before Stable: catalog enrichment also runs for `/catalog/.../search=...` pages. Search queries change on every user term, so they can cause more uncached full metadata lookups than browse Hero. Measure Search latency independently, and consider a separate default-off search-enrichment mode if this becomes problematic. Do not silently disable search enrichment in the running release without a dedicated acceptance test.

## Next live audit (read-only, no deploy)

From HomeDocker SSH, run this exact **commit-pinned** version of the standalone cohort-audit script in the *existing* running Bridge container. The script imports current `/app/bridge.py` and only issues upstream GET requests (which may warm in-memory caches). It does **not** print titles, movie IDs, catalog IDs, manifest URLs, image paths, UUIDs, tokens or env vars.

```bash
REV=3d94ea2c994a279e876180fa4eb51b718ba80bc1
URL="https://raw.githubusercontent.com/olivergiangvu/homedocker-metadata-bridge/$REV/scripts/audit_catalog_logo_coverage.py"
F="$(mktemp)"
trap 'rm -f "$F"' EXIT
curl -fsSLo "$F" "$URL"
sudo docker exec -i homedocker-metadata-bridge \
  python3 - --per-source 8 --genre-samples 3 --pages 2 \
  --meta-probes 36 --workers 3 --limit 12 < "$F"
```

The command samples up to eight directly browsable catalogs and three genre-required catalogs **per source**, at most two pages if the source advertises `skip`, and probes at most 36 unique logo-less IDs using up to three concurrent workers. Search-required catalogs and anime are excluded from coverage scanning; any missing metadata leaves originals unchanged. Expect the run to be longer than the earlier 3-catalog inventory. **No production code or configuration change.**

Relevant output:
- `pre_enrichment_logos` and `pre_enrichment_missing`: sampled occurrences (duplicates across catalog rows are possible).
- `own_source_recovered_unique`, `cross_source_recovered_unique`: sampled unique missing IDs where enrichment is possible.
- `known_recovered_within_current_limit_unique`: recoverable missing IDs where cap=12 would allow checking, across sampled pages.
- `known_recoverable_but_outside_limit_unique`: recoverable missing IDs that never appear within the cap on sampled pages; potential reason to raise it.
- `no_verified_logo_in_either_source`: a gap that increasing the cap **cannot** fix.
- `unverified_identity` and `own_metadata_unavailable`: identity or metadata availability constraints, not automatically safe for fallback.
- `preview_logo_not_https`: suspicious artwork URL shape; not image-delivery proof.
- `unsampled_missing_candidates`: unresolved due audit budget; do not extrapolate beyond this cohort.
- `sampling_wall_seconds`: total audit time, **not** a real-world latency percentile.

## Stable promotion gates

1. Match AIOStreams Official Home Hero and Detail visuals for sampled movies with SH2-fallback logos.
2. Validate provider namespaced catalog IDs, order, search/genre/skip and collection pages; keep SH Watch State separately.
3. Audit broader logo coverage, especially whether raising the 12-preview cap would actually recover additional art.
4. Check real Home/Discover and Search responsiveness (including cold cache) before adjusting concurrency or cap.
5. Test rollback in writing, keep commit- and blob-pinned backups, and update release documentation with observed evidence. Avoid API keys, Fanart integration or added custom upstreams until a measurable gap justifies them.

**Decision remains: retain PR #17 Draft pending the wider live coverage audit and performance acceptance.**
