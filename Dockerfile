FROM python:3.14-slim-bookworm AS builder

# Build dummy packages to skip installing them and their dependencies
RUN apt-get update \
    && apt-get install -y --no-install-recommends equivs \
    && equivs-control libgl1-mesa-dri \
    && printf 'Section: misc\nPriority: optional\nStandards-Version: 3.9.2\nPackage: libgl1-mesa-dri\nVersion: 99.0.0\nDescription: Dummy package for libgl1-mesa-dri\n' >> libgl1-mesa-dri \
    && equivs-build libgl1-mesa-dri \
    && mv libgl1-mesa-dri_*.deb /libgl1-mesa-dri.deb \
    && equivs-control adwaita-icon-theme \
    && printf 'Section: misc\nPriority: optional\nStandards-Version: 3.9.2\nPackage: adwaita-icon-theme\nVersion: 99.0.0\nDescription: Dummy package for adwaita-icon-theme\n' >> adwaita-icon-theme \
    && equivs-build adwaita-icon-theme \
    && mv adwaita-icon-theme_*.deb /adwaita-icon-theme.deb

FROM python:3.14-slim-bookworm

# Copy dummy packages
COPY --from=builder /*.deb /

# Install dependencies and create flaresolverr user
# You can test Chromium running this command inside the container:
#    xvfb-run -s "-screen 0 1600x1200x24" chromium --no-sandbox
# The error traces is like this: "*** stack smashing detected ***: terminated"
# To check the package versions available you can use this command:
#    apt-cache madison chromium
WORKDIR /app
    # Install dummy packages
RUN dpkg -i /libgl1-mesa-dri.deb \
    && dpkg -i /adwaita-icon-theme.deb \
    # Install dependencies
    && apt-get update \
    && apt-get install -y --no-install-recommends chromium chromium-common chromium-driver xvfb dumb-init \
        procps curl vim xauth \
    # Remove temporary files and hardware decoding libraries
    && rm -rf /var/lib/apt/lists/* \
    && rm -f /usr/lib/x86_64-linux-gnu/libmfxhw* \
    && rm -f /usr/lib/x86_64-linux-gnu/mfx/* \
    # Create flaresolverr user
    && useradd --home-dir /app --shell /bin/sh flaresolverr \
    && mv /usr/bin/chromedriver chromedriver \
    && chown -R flaresolverr:flaresolverr . \
    # Create config dir
    && mkdir /config \
    && chown flaresolverr:flaresolverr /config

VOLUME /config

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Stealth engine: install Firefox runtime libraries and fetch the Camoufox
# browser into a shared, world-readable cache the non-root runtime user can use.
# XDG_CACHE_HOME is set globally so both the build-time fetch and the runtime
# resolve the same browser location.
ENV XDG_CACHE_HOME=/cache
RUN apt-get update \
    && playwright install-deps firefox \
    && mkdir -p /cache \
    && python -m invisible_playwright fetch \
    && chmod -R o+rwX /cache \
    && rm -rf /var/lib/apt/lists/*

USER flaresolverr

RUN mkdir -p "/app/.config/chromium/Crash Reports/pending"

COPY src .
COPY package.json ../

# Links the ghcr package to the repo (so the package page shows this README)
# and records license/description. release-docker.yml also injects these via
# docker/metadata-action.
LABEL org.opencontainers.image.source="https://github.com/unseensnick/Solverr" \
      org.opencontainers.image.description="Cloudflare/DDoS-GUARD bypass proxy with dual solving engines (Chrome + Camoufox) and automatic fallback" \
      org.opencontainers.image.licenses="GPL-3.0-only"

EXPOSE 8191
EXPOSE 8192
# Optional passthrough proxy (PASSTHROUGH_ENABLED=true); default PASSTHROUGH_PORT.
EXPOSE 8888

# Reports whether the API is still serving, so a wedged container shows as
# unhealthy instead of up. Deliberately cheap: /health does not drive a browser,
# so this says nothing about whether solving works. 127.0.0.1 rather than
# localhost, which resolves to ::1 on an IPv6-enabled network while the server
# listens on IPv4.
HEALTHCHECK --interval=5m --timeout=10s --start-period=90s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT:-8191}/health" || exit 1

# dumb-init avoids zombie chromium processes
ENTRYPOINT ["/usr/bin/dumb-init", "--"]

CMD ["/usr/local/bin/python", "-u", "/app/flaresolverr.py"]

# Local build
# docker build -t solverr:dev .
# docker run -p 8191:8191 solverr:dev

# Multi-arch build. amd64 and arm64 only: the stealth engine's Firefox has no
# build for linux/386 or linux/arm/v7, so adding them produces a broken image.
# Releases are built by .github/workflows/release-docker.yml; this is for testing.
# docker run --rm --privileged multiarch/qemu-user-static --reset -p yes
# docker buildx create --use
# docker buildx build -t solverr:dev --platform linux/amd64,linux/arm64 .
