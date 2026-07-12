ARG BUILD_ARCH=amd64
FROM ghcr.io/home-assistant/${BUILD_ARCH}-base:3.21

ARG BUILD_ARCH=amd64
ARG BUILD_VERSION=dev

LABEL io.hass.version="${BUILD_VERSION}" \
    io.hass.type="app" \
    io.hass.arch="${BUILD_ARCH}"

ENV LANG=C.UTF-8 \
    PYTHONUNBUFFERED=1

COPY app/requirements.txt /tmp/requirements.txt

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
