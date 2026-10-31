# GitHub-first workflow — HomeDocker Metadata Bridge

This is a **public** repository. No HomeDocker changes are needed to review code or run the automated tests. **Never commit real manifest UUIDs, credentials, watch history, hostnames or machine-specific addresses.**

## First publication

The repository is published through the connected GitHub integration. The code is reviewed against a synthetic test suite, then `main` is used for ongoing development. **Do not deploy `main` to HomeDocker yet.** Keep deployment gated behind a manually reviewed release and live-upstream verification.

Verify **Actions → CI** is green for the first commit. CI uses synthetic fixtures and placeholder URLs; it never reads real AIOMetadata UUIDs.

## How to track changes

- Branch for functional changes (`feature/episode-mapping` or `fix/search-identities`), then open a pull request into `main`.
- Let CI run before merging. CI validates Python syntax, unit tests and Compose configuration but **does not prove real-world ID/episode mapping**.
- Track acceptance scenarios as Issues: TVDB-only series, mixed-provider episode image overlay, movies/anime passthrough, yastream IDs, and Watch State handoff.
- Record verified manual mappings explicitly in `overrides.json`; do not let title similarity alone modify season/episode IDs.
- When validated against actual SH and SH2 endpoints, create a git tag such as `v0.1.0-rc1`. Tagging alone does not deploy or update HomeDocker.

## Secret handling

- Commit `.env.example` only. Keep `.env` on the HomeDocker host, never in GitHub.
- Never paste private SH / SH2 manifest URLs into issues, workflow files, commits, screenshots or CI logs.
- Use repository GitHub Actions secrets **only if future integration tests genuinely require them**; the initial CI does not need secrets.
- Preserve `AIOMetadata SH` Watch State integration and existing AIOStreams account. The Bridge does not implement `watch_state`.

## Deployment is a separate, later step

This codebase is deliberately not configured for continuous deployment. Once real metadata responses are checked, deploy using the documented HomeDocker `compose.yml`, wire the existing HTTPS reverse proxy, then change AIOStreams Resources:

| AIOStreams addon | Resources | Why |
|---|---|---|
| AIOMetadata SH | `Catalog` + `Watch State` | Preserve push/pull and old catalog routes |
| AIOMetadata SH2 | Optional Catalog or disabled in AIOStreams | Bridge still accesses its existing UUID |
| HomeDocker Metadata Bridge | `Metadata` + `Catalog` | Unified metadata and search |
| Existing stream addons | Unchanged | No playback pipeline replacement |

**Do not switch SH from Metadata to Watch State-only until the Bridge has been verified with actual content.** Preserve current metadata IDs where possible so playback progress and Next Up retain identity.
