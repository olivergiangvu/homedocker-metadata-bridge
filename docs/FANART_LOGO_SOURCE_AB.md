# A/B research: AIOMetadata Fanart.tv fallback without changing SH or SH2

## Current baseline (2026-11-02, live HomeDocker)

The Bridge Catalog+ClearLogo enhancement works in Home Hero and film Detail, and a previously empty SH Collection regained its members after exact source-reference rebinding and an AIOStreams addon update/reload.

In the latest wider **read-only** logo audit, 36 catalog pages yielded **694 preview occurrences**. **652/694** already included a usable logo URL; **42** were missing across **28 distinct IDs**; five of those IDs could be recovered with the existing identity-verified SH2 fallback. The other **23** split into **12 unverified IDs** and **11 without verified logos in SH or SH2**. **Zero** recoverable items fell beyond the current per-page limit of 12, so do not increase `CATALOG_ENRICH_LIMIT`.

This is a sampled preview cohort, not a whole-library number; the script did not fetch each image to verify its 200/status/content.

## Native AIOMetadata source code behavior

Source `cedya77/aiometadata`:

- `configure/src/lib/artProviders.ts`: Movie/Series **Logo** can be `TMDB`, `TVDB`, `Fanart.tv`, `IMDb/Cinemeta`, or `Meta Provider`.
- `addon/utils/parseProps.js`: the chosen artwork provider is tried, **then original meta-provider logo** (`fallbackLogoUrl`) is returned if none found. Where no meta logo exists, a verified IMDb metahub logo can be the automatic fallback. So Fanart selection is *priority artwork provider*, not a strictly add-only fallback: it may replace an existing logo with a different image or language.
- `addon/utils/fanart.ts`: Film lookup uses a **TMDB ID**, show lookup uses **TVDB ID**. The deployed selector uses **`hdmovielogo` for movies and `hdtvlogo` for series** (with language and likes preference).
- Fanart's API ALSO exposes `movielogo` (movie) and `clearlogo` (TV), but the above AIOMetadata selector currently does **not** try these legacy artwork buckets. Existence in the legacy bucket is **not** immediately a gain from changing AIOMetadata's provider setting.
- Fanart.tv requires a valid key; the AIOMetadata web UI shows a warning if a Fanart provider is selected without a configured key. The Node addon supports environment project keys as well as per-user `apiKeys.fanart`.
- `English Art Only` affects logo language preference. Do not change language filters and provider simultaneously, or the A/B comparison becomes confounded. `Original Language Poster Fallback` is described as poster-specific and is **not** a reason to change the entire SH configuration for logos.

## Read-only host-side A/B scan

Commit-pin the standalone research utility and run it via `sudo python3` **on HomeDocker host** (unlike the earlier metadata audit). Python standard library only; no pip install or source edit.

```bash
/bin/bash <<'SH'
set -euo pipefail
REV="354b34619235eef6c685a30f1c043278f2e814b1"
F="$(mktemp)"
trap 'rm -f "$F"' EXIT
curl -fsSLo "$F" "https://raw.githubusercontent.com/olivergiangvu/homedocker-metadata-bridge/$REV/scripts/audit_sh2_fanart_logo_ab.py"
sudo python3 "$F"
SH
```

It performs, in memory:

1. Reads the current SH/SH2 catalog samples and missing-logo IDs through the existing Bridge container; only strong canonical IDs usable by Fanart are retained, and already-recovered SH2 logos are excluded.
2. Checks **key presence only** from Docker environment variables of existing AIOMetadata containers (prefer `FANART_API_PROJECT_KEY`, then `FANART_API_KEY` or `BUILT_IN_FANART_API_KEY`); never prints or persists their values. It **does not** extract per-user UI-profile keys from the database. If no server environment key exists, stops and reports that the test is inconclusive.
3. With an available key, queries at most **36** distinct remaining titles over HTTPS against Fanart's **official read-only** movie/TV image-metadata endpoints. Maximum **2** concurrent requests, 12s per call, no redirect follow, no image downloads and no key in the URL.
4. Emits only aggregate counts: `SUPPORTED_LOGO_AVAILABLE` (HD buckets that AIOMetadata currently knows how to select), `ONLY_LEGACY_LOGO_AVAILABLE` (art exists but is *not* selected by current AIOMetadata Fanart code), `NO_LOGO`, errors and no-access conditions.

No SH, SH2, AIOStreams or Bridge configuration/image/source is changed; no Docker restart. The program may warm Bridge's in-memory metadata request cache, and sends image-metadata GETs to Fanart.tv as part of the authorized availability research.

**Important limitation:** This probes *potential coverage*, not a complete A/B run of SH2's actual metadata generated under a temporary alternate settings profile. A positive Fanart result must still be tested with a separate, isolated AIOMetadata profile before changing the live SH2 profile; a negative result does not cover catalogs or titles outside the sample or keys stored only in UI configuration.

## Decision rule for next action

- **Supported Fanart HD gain >= 3 unique currently missing titles** and few errors → create a **separate AIOMetadata test profile** that differs only in `Movie Logo = Fanart.tv` and `Series Logo = Fanart.tv`. Keep live SH + SH2 untouched and retain Bridge's pinned SH2 manifest URL until tested. Compare the same verified movie/series IDs plus a set of titles with existing logos, to detect regressions or undesired visual changes.
- **Legacy-only logos** → changing settings will not yield those logos in current AIOMetadata; do not patch upstream or expand Bridge APIs merely on their existence. Evaluate official upstream issue or a provider/config workaround if a meaningful cohort warrants it.
- **0–2 unique supported gains** → keep existing SH2 provider, do not add keys or maintenance burden just for a marginal improvement.
- **No existing server key / key from UI profile only** → run the same test in an isolated profile with its configured key or first identify key availability securely. Do **not** paste any key, private manifest URL or saved profile JSON into chat.
- **HTTP 401, 429 or network errors** → no coverage conclusion. Repair key/API access or retry a small sample rather than switching providers.

Rollout should never modify SH Watch State or addon catalog ownership, change official AIOStreams, or mutate SH's production profile to improve decorative logos. Film Detail and Home Hero logos are already correct for the known examples.
