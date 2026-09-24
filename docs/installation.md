# Installation

_Doc map: [README.md](README.md)._

Solverr runs as a Docker container. The image already contains both browsers it needs, so Docker is the easiest way to run it and the one this guide assumes. Running from source is covered at the end, for development or for a machine the image does not support.

## What you need

- **Docker**, with Docker Compose. On Windows and macOS that is [Docker Desktop](https://docs.docker.com/get-docker/); on Linux, the Docker Engine and its Compose plugin.
- **About 2.3 GB of disk** for the image.
- **Memory to spare.** Every browser Solverr opens uses a lot of it. [How it works](how-it-works.md#sessions) explains when a browser stays open and how Solverr closes the ones nobody is using.

## Run it with Docker Compose

1. Make a folder for Solverr and save this in it as `docker-compose.yml`:

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

   [examples/docker-compose.yml](../examples/docker-compose.yml) is the same file with every line explained.

   `shm_size` gives the browsers more shared memory than Docker's default. Leave it in: the browsers are more stable with it, and too little of it shows up as browser launch or out-of-memory errors.

2. Start it from that folder:

   ```bash
   docker compose up -d
   ```

3. Check that it answers. Open `http://localhost:8191/` in a browser, or run:

   ```bash
   curl http://localhost:8191/
   ```

   You should see a short JSON reply that starts with `{"msg": "FlareSolverr is ready!"`. That message is on purpose: apps written for FlareSolverr look for it.

Settings go under `environment:` in the same file. Every setting is optional; [Configuration](configuration.md) lists them all. The repository's own [`docker-compose.yml`](../docker-compose.yml) is the same setup built from source instead of pulled, with the common settings written out and commented.

## Run it with the Docker CLI

The same container without a compose file:

```bash
docker run -d \
  --name=solverr \
  -p 8191:8191 \
  --shm-size=512m \
  --restart unless-stopped \
  ghcr.io/unseensnick/solverr:latest
```

## Build the image yourself

To build from the source instead of pulling the published image, clone the repository and run this from its folder:

```bash
docker compose up -d --build
```

For the Docker CLI, run `docker build -t solverr .` first and use `solverr` as the image name.

## Keep it private

Solverr has no password. Anyone who can reach port 8191 can make it open any website through your connection, and anyone who can reach the passthrough port (if you turn it on) can make it fetch the sites you listed. Keep both ports on your own network: do not forward them from your router or publish them on a server's public address. If something outside your network really needs to reach Solverr, put a reverse proxy with authentication in front of it.

## Is it healthy?

The image reports its own health. `docker ps` shows `starting` for the first 90 seconds, then `healthy` once the API answers. The check calls `http://localhost:8191/health`, which answers `{"status": "ok"}`.

Healthy means the API is up. It does not prove that a solve would succeed, and Docker only reports health without acting on it: `restart: unless-stopped` does not restart a container that is stuck but still running.

## Debian hosts

On a Debian host, the browsers may fail to start when the host's `libseccomp2` is older than 2.5. Check it with `sudo apt-cache policy libseccomp2`, update it if needed, and restart the Docker daemon.

## Run from source

For development, or for an architecture the image does not cover. You need [uv](https://docs.astral.sh/uv/) and Python 3.14 (the version the image runs), plus a browser for each engine you want:

```bash
# create the environment and install the Python packages
uv venv --python 3.14
uv pip install -r requirements.txt

# Chrome engine: install Chrome or Chromium yourself (and Xvfb on Linux)
# Stealth engine: install the Firefox libraries and fetch Camoufox
uv run --no-project playwright install-deps firefox
uv run --no-project python -m invisible_playwright fetch

uv run --no-project python src/flaresolverr.py
```

Set `STEALTH_ENGINE=false` to run with Chrome only and skip the Camoufox and Firefox setup.

## Next

- [Connect your app](../README.md#3-point-your-app-at-it) to Solverr.
- [Configuration](configuration.md) for every setting.
