# Solverr

[![Docker release](https://github.com/unseensnick/Solverr/actions/workflows/release-docker.yml/badge.svg?labelColor=27303D)](https://github.com/unseensnick/Solverr/actions/workflows/release-docker.yml) [![Container image](https://img.shields.io/github/v/release/unseensnick/Solverr?label=ghcr.io&logo=docker&logoColor=white&labelColor=27303D&color=2496ED)](https://github.com/unseensnick/Solverr/pkgs/container/solverr) [![License: GPL-3.0](https://img.shields.io/github/license/unseensnick/Solverr?labelColor=27303D&color=0877d2)](LICENSE)

Solverr gets your apps past Cloudflare and DDoS-GUARD protection pages, the "Just a moment..." and "Verify you are human" screens that stop an ordinary program from reading a website.

It is a small server that opens websites in real browsers, clears the challenge, and hands the page and its cookies back to your app. It answers the same API as [FlareSolverr](https://github.com/FlareSolverr/FlareSolverr), on the same port, so any app that supports FlareSolverr (indexer managers, the *arr apps, manga and novel readers) works with Solverr without changes.

What it adds over FlareSolverr:

- **Two browsers, and it switches between them for you.** FlareSolverr's Chromium clears most sites. A Camoufox browser from [Byparr](https://github.com/ThePhaseless/Byparr) clears the newer Cloudflare Turnstile and managed challenges Chromium gives up on. When one fails, Solverr tries the other, and it remembers which one works for each site.
- **Sessions that clean up after themselves.** A session keeps a browser open so repeat requests to a site skip the challenge. Solverr closes the ones apps forget about.
- **A passthrough proxy** for indexer apps that fail even after a successful solve, because they fetch the page again themselves.

## Quick start

You need [Docker](https://docs.docker.com/get-docker/) with Docker Compose.

### 1. Start Solverr

Make a folder, save this in it as `docker-compose.yml`, and run `docker compose up -d` from that folder. The same file with every line explained is [examples/docker-compose.yml](examples/docker-compose.yml).

```yaml
services:
  solverr:
    image: ghcr.io/unseensnick/solverr:latest
    container_name: solverr
    ports:
      - "8191:8191"
    shm_size: 512mb
    restart: unless-stopped
```

Solverr has no password, so keep port 8191 on your own network and never forward it from your router. See [Keep it private](docs/installation.md#keep-it-private).

### 2. Check that it works

Open `http://localhost:8191/` in a browser, or run:

```bash
curl http://localhost:8191/
```

You should see a short JSON reply starting with `{"msg": "FlareSolverr is ready!"`. That message is on purpose: apps built for FlareSolverr look for it.

To try a real solve:

```bash
curl -sX POST 'http://localhost:8191/v1' \
  -H 'Content-Type: application/json' \
  --data '{ "cmd": "request.get", "url": "https://www.google.com/", "maxTimeout": 60000 }'
```

The reply contains `"status": "ok"`, the page's HTML, and its cookies. Its message says `Challenge not detected!` because that site has no protection to clear, which is normal.

### 3. Point your app at it

In your app's FlareSolverr setting, enter Solverr's address:

- `http://solverr:8191` if the app runs in the same Docker network as Solverr.
- `http://<ip-of-the-machine>:8191` otherwise, for example `http://192.168.1.10:8191`.

In Prowlarr, go to **Settings > Indexers**, add a **FlareSolverr** indexer proxy with that address and a tag, then give the same tag to the indexers that need it.

If an indexer app still fails on a Cloudflare-protected site after this, use the [passthrough proxy](docs/passthrough.md) instead. [examples/docker-compose.indexers.yml](examples/docker-compose.indexers.yml) is a ready-made Solverr and Prowlarr setup for it, with one line to change.

## If a site keeps failing

The IP address Solverr connects from matters more than anything else. An address from a data centre or a VPS fails far more challenges than one from a home connection. If a site fails on both engines, a residential proxy (`PROXY_URL`) is the fix. [Troubleshooting](docs/troubleshooting.md) covers this and the other common problems.

Browsers also use a lot of memory. On a small machine, send fewer requests at once; [How it works](docs/how-it-works.md#memory) explains how Solverr keeps open browsers in check.

## Documentation

| Guide | What is in it |
| ----- | ------------- |
| [Installation](docs/installation.md) | Docker, the Docker CLI, building the image, running from source, the health check |
| [How it works](docs/how-it-works.md) | engines, fallback, sessions and cleanup, IP reputation, how long a solve takes |
| [API](docs/api.md) | every `/v1` command and parameter, and the response |
| [Passthrough proxy](docs/passthrough.md) | why indexer apps fail after a solve, and how to wire one up step by step |
| [Configuration](docs/configuration.md) | every setting, with its default |
| [Language and timezone](docs/language-and-timezone.md) | the language and timezone the browser reports, and the IP lookup services |
| [Troubleshooting](docs/troubleshooting.md) | fixes for the common problems |

## Contributing

Bug reports and pull requests are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request: it covers the setup, the tests, and the commit message standard that CI checks every commit against. Everyone taking part is expected to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## License

Solverr is licensed under the **GNU General Public License v3.0** (see [LICENSE](LICENSE)). It began as a fork of [FlareSolverr](https://github.com/FlareSolverr/FlareSolverr) (MIT) and its stealth engine derives from [Byparr](https://github.com/ThePhaseless/Byparr) (GPL-3.0); because Byparr is copyleft, the combined work is GPL-3.0. Upstream copyright notices are preserved in [NOTICE](NOTICE).
