# Base images are pinned by their per-architecture digests for supply-chain
# integrity. Selecting a named stage keeps local Supervisor rebuilds pinned too;
# Supervisor supplies BUILD_ARCH but does not supply a BASE_DIGEST build arg.
# Refresh both when bumping the Alpine 3.21 base:
#   amd64-base:3.21   sha256:995fc8a44059b3bac015603f63b56b1bf935c2227c5176823d9bb76b45fc2249
#   aarch64-base:3.21 sha256:5100b3a2e4741440e7bd587c124920fa3bf02287c4de3cb48aacef7049530ee1
ARG BUILD_ARCH=amd64
FROM --platform=linux/amd64 ghcr.io/home-assistant/amd64-base:3.21@sha256:995fc8a44059b3bac015603f63b56b1bf935c2227c5176823d9bb76b45fc2249 AS base-amd64
FROM --platform=linux/arm64 ghcr.io/home-assistant/aarch64-base:3.21@sha256:5100b3a2e4741440e7bd587c124920fa3bf02287c4de3cb48aacef7049530ee1 AS base-aarch64

FROM base-${BUILD_ARCH}

ARG BUILD_ARCH=amd64
ARG BUILD_VERSION=dev

LABEL io.hass.version="${BUILD_VERSION}" \
    io.hass.type="app" \
    io.hass.arch="${BUILD_ARCH}"

ENV LANG=C.UTF-8 \
    PYTHONUNBUFFERED=1

COPY app/requirements.txt /tmp/requirements.txt

# apk packages are intentionally left unpinned: exact version+revision strings
# are only resolvable against the pinned base image's Alpine 3.21 repository
# snapshot, which cannot be verified in this environment without a Docker
# builder. The base image digest above fixes the repository snapshot these
# packages resolve from, giving deterministic-enough builds.
RUN apk add --no-cache \
        bash \
        bluez \
        ca-certificates \
        dbus \
        python3 \
        py3-pip \
    && apk add --no-cache --virtual .build-deps \
        build-base \
        python3-dev \
    && pip3 install --no-cache-dir --break-system-packages --require-hashes -r /tmp/requirements.txt \
    && apk del .build-deps

COPY app /app
COPY --chmod=0644 icon.png logo.png /app/static/
COPY run.sh /run.sh

RUN chmod a+x /run.sh

CMD ["/run.sh"]
