# Troubleshooting

_Doc map: [README.md](README.md)._

Start with the log: `docker logs solverr`. For more detail, set `LOG_LEVEL=debug` and try again. Attach a `debug` log if you report a bug.

## A site fails on both engines

The site is most likely refusing your IP address, not Solverr. An address from a data centre or a VPS fails far more challenges than one from a home connection, and no solver fixes that.

Open the site in a normal browser from the same network to confirm. The fix is a residential proxy: set `PROXY_URL` (and its username and password), or send a `proxy` with the request. See [Why your IP address matters most](how-it-works.md#why-your-ip-address-matters-most).

The same applies to "Cloudflare has blocked this request" or "IP banned" messages.

## The app shows no results, and the log says `Challenge not detected!` with a 200

An engine loaded the page but did not recognise a newer managed or Turnstile challenge on it, and returned it as if it were solved. Fallback exists to catch this and try the other engine, so check that `ENGINE_FALLBACK` is on (it is by default) and that the stealth engine is loaded (`STEALTH_ENGINE`, also on by default). If it still fails, the site is probably refusing your IP address: see [A site fails on both engines](#a-site-fails-on-both-engines).

## An indexer app still fails even though Solverr solved the page

Many apps fetch the page again themselves with the cookie Solverr gave them, and Cloudflare challenges that second fetch. Use the [passthrough proxy](passthrough.md), which hands the app the solved page so it never fetches again.

## Prowlarr marks the indexer as failing after a slow search

Prowlarr waits roughly 100 seconds for an indexer. A search slower than that is recorded as a failure, and Prowlarr then stops using the indexer for a while: searches return HTTP 400 with "All indexers are unavailable due to failures" until that wears off.

Through the passthrough, each request gets `PASSTHROUGH_TIMEOUT_MS` (90 seconds by default), which stays under Prowlarr's limit. Do not raise it above about 100000. If searches are regularly that slow, check the log for which engine solved them and how long each took: a site that challenges hard from your IP address is one cause.

## An indexer fails with "No title provided" on every result

The indexer's definition file lost characters it needs. A few definitions contain invisible characters, and copying and pasting the file's text (through a chat or some editors) strips them. Download the raw definition file again instead. See [Add an indexer to Prowlarr or Jackett](passthrough.md#add-an-indexer-to-prowlarr-or-jackett).

## The log says every IP lookup service failed

The warning looks like this: `could not discover the proxy egress IP via 3 endpoint(s) in 0.0s (last error: ... NameResolutionError ...)`.

The number counts every service that was tried. All of them failed, and only the last one's error is shown. Every service failing to resolve its name almost instantly means the container cannot resolve any hostname at that moment. Changing lookup services will not help; check the container's DNS, for example when its network runs through a VPN container.

Solverr carries on with the container's `TZ` and `en-US` in the meantime, so solving still works. See [When the lookup fails](language-and-timezone.md#when-the-lookup-fails).

## Out-of-memory errors, or the browser fails to start

Common on low-memory machines and in Proxmox LXC containers.

- Give the container more shared memory: `shm_size: 512mb` in `docker-compose.yml`, or `--shm-size=512m` with `docker run`.
- Lower `SESSION_MAX`, and keep `SESSION_TTL_MINUTES` modest, so idle browsers close sooner.
- Avoid sending many requests at once.

On a Debian host, also check `libseccomp2`: see [Debian hosts](installation.md#debian-hosts).

## Camoufox or Firefox errors on ARM or a NAS

Support for the stealth engine on ARM and NAS devices is best-effort. If it will not start, set `STEALTH_ENGINE=false` to run with Chrome only.

## The container says `healthy` but requests fail

The health check only proves the API answers; it does not try a solve. Docker also does not restart a container that is stuck but still running. Check the log, and restart the container by hand if needed. See [Is it healthy?](installation.md#is-it-healthy).
