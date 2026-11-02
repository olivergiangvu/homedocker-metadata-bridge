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

## Live result and next A/B step (2026-11-02)

The user ran the initial read-only audit on HomeDocker. Results:

- 30 sampled catalog pages, 576 preview occurrences, 543 with logos (94.3%), 33 missing.
- Existing Bridge recovered 5 missing unique works, 12 other works have strongly verified provider IDs eligible for a Fanart API comparison, 6 lacked verified identity in this cohort. Counts across these fields are not all disjoint preview occurrences; do not claim they sum to 33 unique items.
- Two AIOMetadata containers inspected. **No Fanart key in their Docker environment**. The audit therefore **made no Fanart API request** and cannot assess Fanart coverage. It also did not check whether a Fanart key is configured in the SH2 *per-user profile* `apiKeys.fanart`. Do not change SH2's Logo provider yet.

### Option A — use a Fanart personal key without touching the server config

Fanart.tv's current API documentation supports a personal API key with the `client-key` header (and a project key with `api-key`). The standalone audit now supports `--prompt-personal-key` through **/dev/tty + getpass**, so the key is entered hidden, kept in process memory, sent via an HTTP header, and never stored in a file, shell history, command-line arguments, Docker environment, or displayed in output. An SSH interactive TTY is required. Personal key registration/login: https://fanart.tv/get-an-api-key/.

```bash
/bin/bash <<'SH'
set -euo pipefail
REV="b984d5bdb71964f12309292952b6bbed50f58613"
F="$(mktemp)"
trap 'rm -f "$F"' EXIT
curl -fsSLo "$F" "https://raw.githubusercontent.com/olivergiangvu/homedocker-metadata-bridge/$REV/scripts/audit_sh2_fanart_logo_ab.py"
sudo python3 "$F" --prompt-personal-key
SH
```

Do not send a Fanart key to ChatGPT or paste it in a shared issue. The key will be requested **after** the cohort is gathered; input is not echoed.

### Option B — no key

Keep existing SH/SH2 art selections and defer Fanart A/B. If the SH2 web profile already has a Fanart key, this run only proves it was not in Docker env: inspect SH2 `Integrations → Fanart.tv` in the web UI yourself. Do not dump or expose SH/SH2 profile config JSON or API keys.

With a usable key, the audit only estimates Fanart's **HD-only potential** for the 12 eligible missing works. A true isolated AIOMetadata-profile A/B still must confirm the actual new metadata and absence of regressions on previously logo-present works before switching SH2 production.

## Decision rule for next action

- **Supported Fanart HD gain >= 3 unique currently missing titles** and few errors → create a **separate AIOMetadata test profile** that differs only in `Movie Logo = Fanart.tv` and `Series Logo = Fanart.tv`. Keep live SH + SH2 untouched and retain Bridge's pinned SH2 manifest URL until tested. Compare the same verified movie/series IDs plus a set of titles with existing logos, to detect regressions or undesired visual changes.
- **Legacy-only logos** → changing settings will not yield those logos in current AIOMetadata; do not patch upstream or expand Bridge APIs merely on their existence. Evaluate official upstream issue or a provider/config workaround if a meaningful cohort warrants it.
- **0–2 unique supported gains** → keep existing SH2 provider, do not add keys or maintenance burden just for a marginal improvement.
- **No existing server key / key from UI profile only** → run the same test in an isolated profile with its configured key or first identify key availability securely. Do **not** paste any key, private manifest URL or saved profile JSON into chat.
- **HTTP 401, 429 or network errors** → no coverage conclusion. Repair key/API access or retry a small sample rather than switching providers.

Rollout should never modify SH Watch State or addon catalog ownership, change official AIOStreams, or mutate SH's production profile to improve decorative logos. Film Detail and Home Hero logos are already correct for the known examples.
