# PR #17 release blocker — AIOMetadata Collection members empty after Catalog passthrough

**2026-11-02: DO NOT promote Catalog Passthrough to stable until this regression is proven fixed live.**

## Observed

After Source Catalog cutover to Bridge, AIOMetadata SH collection tiles (e.g. one director folder) render their artwork and title, but AIOStreams Official shows `In this collection — Nothing here`. This is a functional regression separate from ClearLogo. The user reports the Collection catalog itself appeared via passthrough; only member resolution fails.

## Root cause established by source-code analysis

AIOMetadata `addon/lib/collectionBuilder/aiostreamsCollections.ts` generates a `type: "collection"` folder meta at `/meta/collection/aiom.collection:<collectionId>:<folderId>.json`. Its `collection.sources` entries contain:

```json
{"type":"movie","catalogId":"original-sh-catalog-id","genre":"optional-genre"}
```

AIOStreams `packages/core/src/main/resources.ts` calls `withQualifiedCollection` on fetched full meta, which automatically attaches the serving addon instance to each source. The `withQualifiedCollection` resolver in `packages/core/src/main/catalog.ts` does **not** rewrite the inner `catalogId` value. `collectionMembers` in `packages/core/src/jellyfin/library.ts` calls `engine.findAddonCatalog(source.type, source.catalogId)` and silently skips unknown source catalogs.

Bridge PR #17 namespaces every mirrored catalog descriptor id as `hd-sh-<base64(original-id)>` or `hd-tvdb-...`. Until this fix, it simply passes through `meta/collection/...`, leaving references to the original SH ids that **do not exist as Bridge catalog IDs**. If SH Catalog resource has been deactivated in AIOStreams, the old links cannot resolve.

The proximate cause is strongly supported by source, but confirmation against actual user collection members needs a live read-only probe. The UI itself does not prove SH's collection source array is populated.

## Candidate code fix (PR #17 Draft, not deployed)

`rebind_collection_sources(payload, "sh")` is called **only** for the Bridge's `meta/collection` response when `CATALOG_PASSTHROUGH_ENABLED=1`. It reads the already-cached source manifest and checks every source against exact `(type,catalogId)` pairs. For each SH-owned source, it creates a copy of the JSON and sets only `source.catalogId` to `proxied_catalog_id("sh", original)`; if `addonId` explicitly equals SH manifest ID, that field is removed so AIOStreams binds the Bridge instead. Any explicitly foreign addonId is **never hijacked**. Unknown catalog references are preserved, not fabricated. `collection.items`, child folder IDs, content IDs, genre, poster/background/logo and all unrelated metadata remain untouched. With feature OFF, original passthrough is unchanged.

**Tests:** full Collection HTTP meta, internal source rebind, external source untouched, original cached source immutable, nested folder, Collection catalog route and feature OFF behavior. GitHub CI successful after fix and tests. No changes to AIOStreams Official, SH config, SH2, streams, or Watch State.

## Live confirmation — read-only, no deploy

Run the pinned `scripts/probe_collection_sources.py` inside the **current production** Bridge container:

```bash
/bin/bash <<'SH'
set -euo pipefail
REV="2a9d9bc00c51784d345ab6f91d37b2a77f61a9a2"
F="$(mktemp)"
trap 'rm -f "$F"' EXIT
curl -fsSLo "$F" "https://raw.githubusercontent.com/olivergiangvu/homedocker-metadata-bridge/$REV/scripts/probe_collection_sources.py"
sudo docker exec -i homedocker-metadata-bridge python3 - < "$F"
SH
```

The script GETs configured SH Collection catalog pages and full metadata for up to 60 Collection folders including some child folders; prints only aggregate counts of SH raw ID matches and corresponding Bridge namespaced target matches. Does not mutate server state or print titles, UUIDs, source IDs, source URLs or manifest credentials.

Expected signs for this bug: `raw_source_matches_SH_manifest > 0` and `rebound_target_in_Bridge_manifest > 0`, and UI reports empty member list. This will justify a guarded Bridge-only hotfix with automatic rollback, then a browser acceptance check for a known Collection.

## Other findings

A separate 36-page ClearLogo cohort audit had 694 preview occurrences, 652 with logos (93.9%) before enrichment, 42 missing, 28 unique missing works fully probed, 5 verified alternate-source logos recovered, 12 without strong identity and 11 with no logo in either source. **Zero** missing previews outside the 12-enrichment-per-page limit. Do **not** increase CATALOG_ENRICH_LIMIT merely to chase these gaps. Prioritize the Collection regression.

## Stable gates

1. Confirm actual SH source references are mapped to advertised Bridge descriptors in read-only probe.
2. Deploy only the reviewed Bridge Collection fix using pinned blob guards and backup; no SH/SH2/AIOStreams source change.
3. Verify the Collection member list for one previously-empty folder (plus nested folder and genre filtering), with original card count, preview IDs and order.
4. Repeat movie Home Hero and Detail smoke, SH Watch State retention and performance smoke.
5. Only after these pass, mark PR ready for review / merge; leave release Draft in meantime.
