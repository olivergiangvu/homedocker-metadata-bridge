# HomeDocker — SH/SH2 Catalog passthrough + ClearLogo enrichment (controlled opt-in)

## Contract

**Do not fork, patch or rebuild official AIOStreams.** The only custom component is the existing HomeDocker Metadata Bridge. Keep AIOMetadata SH and SH2 as configured, with SH **Watch State** active. Catalog source definitions come from their existing UUID-scoped manifests; the bridge never edits those configurations.

When `CATALOG_PASSTHROUGH_ENABLED=0` (default), Bridge maintains its pre-existing **Metadata only** operation and legacy opt-in Hybrid Series Search. When enabled, it mirrors all valid SH catalogs first, then SH2 catalogs, retaining each **name, media type, extra/genre/search/skip descriptors and order within that provider**. To prevent collisions when both sources use the same catalog ID, only the **catalog ID** is reversibly namespaced on the Bridge manifest. Movie/series **media IDs are never rewritten**, and the full upstream catalog page is returned with only missing `metas[].logo` supplemented.

A source preview missing logo is enriched from that same configuration's full metadata first. If it too lacks a logo, the Bridge tries the other upstream **only with verified matching provider IDs**. It never guesses movie/series identity from titles. Existing logos and all other preview fields are preserved. Anime records are skipped. A failed metadata request preserves the original catalog preview. The metadata movie fallback from PR #16 is included because this branch is stacked on PR #16.

**Performance:** only the first `CATALOG_ENRICH_LIMIT=12` eligible logo-less previews per returned page are examined, with `CATALOG_ENRICH_WORKERS=4` concurrent reads (max six); metadata responses use the existing cache. This is *bounded*, not a blanket guarantee that every item has a logo. Pages may be slower on a cold cache. Dynamic manifests are refreshed on existing metadata-cache TTL, 30 minutes by default; AIOStreams may need Catalog settings refresh/reinstall to discover new catalog descriptors.

**Not covered:** Continue Watching or Next Up watch-history cards, external media server catalogs, and Home Hero slides drawn from these non-catalog lists. Only catalog-based Hero entries benefit. The Bridge never proxies `watch_state`, `addon_catalog`, streams, search user history, or accounts.

## Phase A: read-only inventory; do this BEFORE activating any feature

On HomeDocker via SSH:

```bash
AUDIT_URL="https://raw.githubusercontent.com/olivergiangvu/homedocker-metadata-bridge/feature/aiometadata-catalog-passthrough-logo/scripts/probe_catalog_inventory.py"
curl -fsSLo /tmp/homedocker-catalog-inventory.py "$AUDIT_URL"
sudo docker exec -i homedocker-metadata-bridge \
  python3 - --sample 3 < /tmp/homedocker-catalog-inventory.py
```

For production use, replace the branch URL with a **specific reviewed commit SHA**, not a moving branch. This probe runs in the existing container, using the existing SH/SH2 environment. It only performs upstream GETs; it prints counts/types and sample artwork coverage, never manifest URLs, catalog IDs, UUIDs, titles or image URLs. It does not require the feature branch deployed.

Review whether both manifests contain the expected number of configured catalogs. Some catalogs require a search, genre, or other required extra; the probe **skips them rather than guessing parameters**. A subset of browse pages is sampled, not entire libraries.

## Phase B: deploy Bridge code with feature OFF

**Precondition:** PR #16 movie logo candidate is already running and has been verified on two movie Detail pages. Before replacing any file, verify the live container and its bind source:

```bash
SOURCE="$(sudo docker inspect -f '{{range .Mounts}}{{if eq .Destination "/app/bridge.py"}}{{.Source}}{{end}}{{end}}' homedocker-metadata-bridge)"
echo "bridge source blob:"
sudo git hash-object "$SOURCE"
echo "current environment flag:"
sudo docker inspect homedocker-metadata-bridge --format '{{range .Config.Env}}{{if eq . "CATALOG_PASSTHROUGH_ENABLED=1"}}ENABLED{{end}}{{end}}'
```

Expected PR #16 source blob: `11b0f8f125bf9239dfb2a7ddb7a0ef40132e9ec9`. **Stop** if it differs; reconcile actual production source before any overwrite. Preserve existing `.env`, `overrides.json`, previous source and Compose as backups. Use commit-pinned copies of **both** `bridge.py` and `compose.yml`, verify their Git blob SHAs, and deploy using the existing Compose project and service labels. The updated Compose file includes these optional variables:

```ini
CATALOG_PASSTHROUGH_ENABLED=0
CATALOG_ENRICH_LIMIT=12
CATALOG_ENRICH_WORKERS=4
```

Keep the feature off until the old Metadata-only manifest and the two movie Detail pages are confirmed identical. Recreate **only** `metadata-bridge` (not SH/SH2/AIOStreams) so the bind-mounted `bridge.py` inode is reattached. Capture the old episode ID/season/episode fingerprints for *Midnight Diner* before and after.

## Phase C: activate Bridge Catalog and validate locally

Change only `CATALOG_PASSTHROUGH_ENABLED=1` in the existing Bridge Compose environment, recreate **only** Bridge, and inspect sanitized data:

```bash
curl -fsS http://127.0.0.1:18123/manifest.json |
  jq '{version, resources,
       catalog_count:(.catalogs|length),
       types:([.catalogs[]?.type]|group_by(.)|map({type:.[0],count:length}))}'

curl -fsS http://127.0.0.1:18123/health | jq '{status,version}'
```

Do **not** print raw `.env`, manifest URLs, or the full proxy catalog list on public GitHub/chat. Verify a representative proxied SH movie catalog, an SH2 series catalog and a browse page with `skip`/genre filters. For the missing-logo movie examples, compare `metas` IDs, item count/order, poster, backdrop and extras against direct SH/SH2 page and check only `logo` changed. Never infer success from a valid manifest alone.

## Phase D: controlled AIOStreams UI cutover

Before touching settings, **export the existing AIOStreams configuration**, note the current enabled catalog list/order, and record Continue Watching/Next Up state. The Bridge HTTPS URL and its metadata route are already in use; do not change hostname, port, TLS or manifest credentials.

In AIOStreams **Installed → Addons → Edit → Resources**:

| Addon | Target selected resources |
|---|---|
| Bridge | **Metadata + Catalog** |
| SH | **Watch State**, plus **Addon Catalog** and any existing unrelated resources needed |
| SH2 | **Addon Catalog** and unrelated resources as needed |
| Stream addons | Unchanged |

Remove SH/SH2 **normal Catalog** resource only after Bridge catalog pages are verified. Never blank the Resources selection; on some versions blank means **all**. SH Watch State must remain enabled.

Next, in **Installed → Catalogs** / Home settings, enable the mirrored Bridge catalogs you actually want, and reproduce the desired order. Bridge mirrors **catalog definitions** from AIOMetadata automatically but AIOStreams has its own per-catalog enablement/order; those preferences **cannot be migrated transparently** because the Bridge catalog IDs are new. SH and SH2 catalog names may be duplicated; select only the rows required. The legacy Bridge Hybrid Search catalog is intentionally not listed when passthrough mode is on, to avoid an extra synthetic search row.

Test Home Hero **Rotate** with catalog-derived *Collision Course* and *Apocalypse Z*, then Detail, catalog pagination/genre/search and existing SH Watch State. Web browser Home query stale time is about five minutes; expect old rows to persist briefly after the source switch. Don't clear SH/SH2 Redis or tracking caches globally.

## Rollback (independent layers)

1. AIOStreams: re-enable old SH/SH2 normal Catalog resources and the original catalog selections; disable **only Bridge Catalog**; leave Bridge Metadata and SH Watch State active.
2. Bridge: change flag back to `0`, recreate only Bridge and verify the old manifest/metadata.
3. If the new source itself fails, restore saved `bridge.py` and `compose.yml`, recreate only Bridge. Do not remove metadata caches, SH/SH2 manifest UUIDs, databases or Watch State.

**No production cutover should happen solely because GitHub CI passes.** This PR remains Draft pending a live manifest inventory and a representative AIOStreams Home Hero acceptance test.
