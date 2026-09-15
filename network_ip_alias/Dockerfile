ARG BUILD_FROM=ghcr.io/home-assistant/base:3.24-2026.08.0
FROM $BUILD_FROM

LABEL \
	io.hass.type="app" \
	io.hass.arch="aarch64|amd64"

RUN apk add --no-cache iproute2

COPY run.sh /
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]