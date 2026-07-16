# Base image pinned by digest for supply-chain integrity. Digests are
# per-architecture, so CI/publish supply the digest matching BUILD_ARCH via the
# BASE_DIGEST build arg. The 3.21 tag (Alpine 3.21) is retained for readability;
# the digest is what actually gets resolved. Refresh both when bumping the base:
#   amd64-base:3.21   sha256:995fc8a44059b3bac015603f63b56b1bf935c2227c5176823d9bb76b45fc2249
#   aarch64-base:3.21 sha256:5100b3a2e4741440e7bd587c124920fa3bf02287c4de3cb48aacef7049530ee1
ARG BUILD_ARCH=amd64
ARG BASE_DIGEST=sha256:995fc8a44059b3bac015603f63b56b1bf935c2227c5176823d9bb76b45fc2249
FROM ghcr.io/home-assistant/${BUILD_ARCH}-base:3.21@${BASE_DIGEST}

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
        dbus \
        python3 \
        py3-pip \
    && apk add --no-cache --virtual .build-deps \
        build-base \
        python3-dev \
    && pip3 install --no-cache-dir --break-system-packages --require-hashes -r /tmp/requirements.txt \
    && apk del .build-deps

COPY app /app
COPY run.sh /run.sh

RUN chmod a+x /run.sh

CMD ["/run.sh"]
