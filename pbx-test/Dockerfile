# CAWP local test PBX (Phase 5 P0). TEST ONLY: never deploy, never point at a real trunk.
# Asterisk 20 LTS from Alpine 3.22, base image pinned by digest, packages pinned by version.
# Built by `docker compose --profile pbx-test build pbx-test`. Config: pbx-test/conf/,
# rendered at start by entrypoint.sh with secrets from the environment (never baked in).
FROM alpine:3.22@sha256:5291449c3df73caf6ed85e649dec1b9e818b39a5d8c871e97afc13e9cd5e8fa8

RUN apk add --no-cache \
        asterisk=20.11.1-r6 \
        asterisk-srtp=20.11.1-r6 \
        pjsua=2.15.1-r0 \
        openssl \
        bash \
        tzdata \
    && rm -rf /etc/asterisk/* \
    && mkdir -p /opt/pbx-test/conf /etc/asterisk/keys /var/spool/asterisk/monitor /var/log/asterisk/cdr-csv

COPY conf/ /opt/pbx-test/conf/
COPY entrypoint.sh /opt/pbx-test/entrypoint.sh
RUN chmod 0755 /opt/pbx-test/entrypoint.sh

ENV TZ=Asia/Riyadh
# 5038 AMI, 5039 AMI over TLS, 5060/udp SIP, 8089 HTTPS/WSS (WebRTC), 10000-10099/udp RTP
EXPOSE 5038 5039 5060/udp 8089 10000-10099/udp
ENTRYPOINT ["/opt/pbx-test/entrypoint.sh"]
