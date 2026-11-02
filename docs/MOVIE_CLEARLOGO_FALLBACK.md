# Movie ClearLogo: verified SH2 fallback (candidate)

## Real HomeDocker evidence (2026-11-02)
The live read-only logo audit of *Collision Course (2026)* and *Apocalypse Z: New World (2026)* produced identical results for both IMDb and TMDB URL variants:

- `SH meta=YES, SH logo=-`
- `SH2 raw meta=YES, SH2 raw logo=TMDB`
- `Same ID=YES`
- `Bridge meta=YES, Bridge logo=-`

This is a **specific movie bridge capability gap**, not proof that TMDB globally lacks logos. SH2 already serves a TMDB logo for both titles.

## Intended minimal change
On `GET /meta/movie/{id}.json`, request SH's **full JSON payload** as before. When SH contains no usable `logo` and is not marked anime, compare SH2 movie metadata via verified shared IMDb/TMDB/TVDB IDs. If identity is strongly verified and SH2 has usable `logo`, set **only** `payload.meta.logo` on a deep copy. Preserve the entire remaining SH JSON tree, including unknown properties, IDs, posters, backgrounds, movie videos, extras, history hints, and any extensions. If validation or upstream requests fail, return SH unchanged.

- Does not generate, guess, download, cache or proxy new artwork.
- Does not add a new network service, manifest, credential, environment variable, or volume.
- Does not change `movie` search/catalog, `series` merging or `anime` passthrough.
- Retains SH `Watch State` and does **not** write to either upstream.
- SH2 is queried **only for movies whose SH logo is absent**; requests use the existing 1800-second cache. A cold miss can add upstream latency, so monitor page load times after any controlled deployment.
- The patch does not change AIOStreams list/card logos or client artwork cache behavior.

## Validation / deployment gate

Review this PR and GitHub CI first. **Do not install a moving branch on production**. On HomeDocker, retain the existing `bridge.py` as a rollback copy outside the read-only volume source directory, and compare the current deployed source with the approved base commit before replacing it. Use your existing audited `/opt/homedocker/stacks/metadata-bridge` Compose workflow; changing source code on the host is insufficient until the container process restarts.

After a reviewed deployment, run a **read-only** local check (do not print private addon manifests or signed playback links):

```bash
for id in tt38793125 tmdb:1571955 tt37522541 tmdb:1507696; do
  printf '%s ' "$id"
  curl -fsS --max-time 20 "http://127.0.0.1:18123/meta/movie/${id}.json" |
    jq -c '{found:(.meta != null),logo_present:(.meta.logo|type=="string" and length>0),logo_source:(.meta.logo|if type=="string" and contains("image.tmdb.org") then "TMDB" else "other_or_none" end)}'
done
```

Check both films' **Detail pages**, not their Continue Watching tiles, and verify logo loads. Revert to the backed-up `bridge.py` and recreate/restart only the Bridge container if any other metadata field changes or video IDs differ.

Keep the PR **Draft** pending live verification.
