# HomeDocker — Metadata-only HTTPS Bridge cutover

## Scope (agreed 2026-10-31)

Bridge v0.2 already supplies a Stremio-compatible `manifest.json`, `meta` and an optional catalog. **Do not add Watch State proxy**. AIOStreams can select resources per addon.

| Addon | AIOStreams selected Resources |
| --- | --- |
| AIOMetadata SH | `Catalog`, `Addon Catalog`, `Watch State` (plus other currently-used non-meta resources, if any) |
| AIOMetadata SH2 | `Catalog`, `Addon Catalog` (plus other currently-used non-meta resources, if any) |
| HomeDocker Unified Bridge | **`Metadata` only** |
| yastream and all stream addons | **unchanged** |

Remove only `Metadata` from SH and SH2 after Bridge is demonstrably reachable. Do not unset SH Watch State or alter tracker credentials, IDs or history. A blank Resources selection in AIOStreams may mean **all manifest resources**: explicitly choose `Metadata` for Bridge.

> Search caveat: keeping both original Catalog resources means AIOStreams may still display multiple search cards. Bridge's federated search catalog is not used unless its `Catalog` resource is enabled and the duplicate original *series-search* catalogs are disabled separately. Catalogs for Home/Discover and `Addon Catalog` can stay. Treat one-card unified search as a separate opt-in phase.

## Local Bridge is already running

- Docker `homedocker-metadata-bridge`, bound to **127.0.0.1:18123 on HomeDocker host**.
- Existing SH and SH2 URLs remain private in `.env`; SH contains the unchanged Watch State tracker.
- The localhost URL is **not** a remote manifest URL. Do not bind Bridge to a public interface to solve HTTPS.

## Inspect current HTTPS edge before writing configuration

Run these commands over SSH. They are read-only and do not reveal the upstream manifest UUID or API token:

~~~bash
sudo docker ps --format '{{.Names}} | {{.Image}} | {{.Ports}} | {{.Networks}}' | grep -Ei 'aio|metadata|nginx|caddy|traefik|swag|proxy|bridge' || true
sudo ss -H -lntp '( sport = :80 or sport = :443 or sport = :18446 or sport = :18123 or sport = :3232 or sport = :3240 )'
for s in nginx caddy apache2; do printf '%s: ' "$s"; systemctl is-active "$s" 2>/dev/null || true; done
~~~

Do **not** assume the external TLS port seen in another manifest is bound locally on that port; router NAT may map it to a different internal port. Determine whether TLS termination is host Nginx, a Docker reverse proxy, Caddy, a gateway/router or a direct addon service **before** choosing an implementation.

## HTTPS routing: host Nginx example only

If and **only if** existing HTTPS terminates in **Nginx running on HomeDocker host**, add one scoped, protected path to the appropriate existing TLS virtual host:

~~~nginx
location /metadata-bridge/ {
    proxy_pass http://127.0.0.1:18123/;
    proxy_set_header Host $host;
    proxy_connect_timeout 5s;
    proxy_read_timeout 30s;
    add_header Access-Control-Allow-Origin '*' always;
}
~~~

Then the remote manifest would be `https://<existing-host>[:TLS-port]/metadata-bridge/manifest.json`, and meta requests use the same prefix automatically. Do not use this exact snippet if TLS terminates **inside Docker**: `127.0.0.1` there points to that container, not the Ubuntu host. Configure an appropriate reachable upstream only after the network topology is inspected. If HTTPS is provided by a router directly forwarding to AIOMetadata rather than a reverse proxy, an independent TLS route or proxy may be needed.

For a public remote path, use appropriate access controls or a high-entropy unguessable location prefix and request limits; avoid publicizing credential-bearing manifest URLs. Ensure stable URL, valid certificate, and TLS from remote. Do not assume HTTP Basic Auth headers are supported by the addon client.

Validate `manifest.json` **and** a real `/meta/series/tt6150576.json` remotely (the manifest alone does not prove path rewriting). Keep the URL and secret path off public GitHub or chat. Verify name, `resources` and `meta.videos | length` from the JSON response without exposing tokens.

## AIOStreams UI cutover (after HTTPS passes)

1. AIOStreams **Installed → Add by URL**, paste new Bridge HTTPS Manifest URL.
2. Name `HomeDocker Unified Metadata`; choose Resources **Metadata only**, not blank. Set Timeout to 20,000–30,000 ms for a cold-cache test; tune after measuring actual latency.
3. Add Bridge and verify a representative movie, anime and series metadata can be obtained. If AIOStreams supports staging or variants, use a variant before the main config.
4. Edit **SH**: disable **Metadata only**; keep **Catalog + Addon Catalog + Watch State** (plus any other originally used resources).
5. Edit **SH2**: disable **Metadata only**; keep **Catalog + Addon Catalog** (plus any other originally used resources).
6. Check `Midnight Diner: Tokyo Stories` 30 episodes, direct stream availability, an existing CW/Next Up entry, and SH tracker status in a client that actually pushes AIOStreams `watch_state` (AIOStreams app/Jellyfin-compatible), not only Stremio playback.

Optional protection: AIOStreams Export before changing Resources; this is useful for rollback but **not necessary for deploying the Bridge container or HTTPS manifest**. You can revert individual resource toggles without restoring the whole backup.

## Rollback

1. Re-enable original SH `Metadata` resource (and SH2 `Metadata` if it was originally enabled).
2. Disable or remove **only the Bridge addon from AIOStreams**.
3. Do not alter SH/SH2 UUIDs, Watch State, databases or stream addons. The existing Bridge container may stay running in isolation.

**Cutover is not performed by this document.** Only the remote HTTPS endpoint and two metadata resource selections are needed. Search catalog deduplication is a separate optional change.
