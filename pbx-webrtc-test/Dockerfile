# CAWP WebRTC test PBX (Phase 5 P3). TEST ONLY: never deploy, never point at a real trunk.
# A copy of pbx-test/ (Asterisk 20 LTS from the same pinned Alpine base) adapted for the in-app
# browser softphone: WSS + DTLS-SRTP + ICE endpoints 7001-7005 whose digest credentials come from
# the application database through the REALTIME PJSIP auth backend (res_config_pgsql, sorcery
# `ps_auths` -> the view pbx_ps_auths, migration 0056). No trunk, no AGI, no pjsua.
#   docker compose --profile pbx-webrtc-test build pbx-webrtc-test
FROM alpine:3.22@sha256:5291449c3df73caf6ed85e649dec1b9e818b39a5d8c871e97afc13e9cd5e8fa8

RUN apk add --no-cache \
        asterisk=20.11.1-r6 \
        asterisk-srtp=20.11.1-r6 \
        asterisk-pgsql=20.11.1-r6 \
        openssl \
        bash \
        tzdata \
    && rm -rf /etc/asterisk/* \
    && mkdir -p /opt/pbx-webrtc-test/conf /etc/asterisk/keys

COPY conf/ /opt/pbx-webrtc-test/conf/
COPY entrypoint.sh /opt/pbx-webrtc-test/entrypoint.sh
RUN chmod 0755 /opt/pbx-webrtc-test/entrypoint.sh

ENV TZ=Asia/Riyadh
# 5038 AMI (test account, same privileges as production), 5060/udp SIP (digest probe),
# 8089 HTTPS/WSS (WebRTC), 10000-10199/udp RTP (compose network only)
EXPOSE 5038 5060/udp 8089 10000-10199/udp
ENTRYPOINT ["/opt/pbx-webrtc-test/entrypoint.sh"]
