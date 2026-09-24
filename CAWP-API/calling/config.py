"""Environment configuration of the `calling` service (no Flask, importable in tests).

Secrets (AMI secret, AGI shared key) come from the environment only. The service is OFF
unless CALLING_ENABLED=1, and it refuses to talk to any PBX other than the local test PBX
unless CALLING_PBX_COMMISSIONED=1 - an explicit operator assertion made after the Issabel
commissioning checklist (docs/issabel-commissioning.md) passed. 10.10.0.220 is therefore
never contacted by default, from development, tests or a mis-set variable.

    CALLING_ENABLED              0 | 1                    (default 0: idle, healthy, no sockets)
    CALLING_AMI_HOST             PBX host                 (required when enabled)
    CALLING_AMI_PORT             default 5039 with TLS, 5038 without
    CALLING_AMI_TLS              1 | 0                    (default 1)
    CALLING_AMI_CA_FILE          CA bundle for the PBX certificate (default: system store)
    CALLING_AMI_SERVER_NAME      TLS server name if it differs from the host
    CALLING_AMI_USERNAME         the dedicated AMI account
    CALLING_AMI_SECRET           its secret
    CALLING_AMI_PLAINTEXT_HOSTS  hosts allowed without TLS (default: none; loopback always)
    CALLING_TEST_PBX_HOSTS       hosts treated as the local test PBX (default pbx-test,cawp-pbx-test)
    CALLING_PBX_COMMISSIONED     0 | 1  required for any other host
    CALLING_AGI_ENABLED          1 | 0                    (default 1)
    CALLING_AGI_BIND / _PORT     default 0.0.0.0:4573
    CALLING_AGI_SECRET           shared key; the PBX global COLLECTIONS_AGI_KEY must match
    CALLING_AGI_ALLOWED_PEERS    CIDRs / host names allowed to connect (default: the AMI host)
    CALLING_HEARTBEAT_SECONDS    AMI Ping interval (default 10)
    CALLING_HEALTH_FILE          default /tmp/cawp-calling.alive
    CALLING_HEALTH_MAX_AGE       seconds (default 60)
    CALLING_HEALTH_AMI_GRACE     seconds the AMI may be down before unhealthy (default 180)
    CALLING_STATUS_PORT          internal JSON status port (default 8791; 0 = off; never published)
    CALLING_DATABASE_URL         optional override of DATABASE_URL (e.g. a test database)
    CALLING_DISPATCH_ENABLED     1 | 0  click-to-call dispatcher (P1, default 1)
    CALLING_RECONCILE_SECONDS    reconciliation pass interval (default 10; 0 = off)
    CALLING_CDR_SOURCE           none | csv | mysql  CDR lookup for reconciliation (default none)
    CALLING_CDR_CSV              the test PBX's Master.csv (default /pbx-test-cdr/Master.csv)
    CALLING_CAMPAIGNS_ENABLED    1 | 0  progressive campaign dispatcher (P2, default 1; needs the dispatcher)
    CALLING_CAMPAIGN_POLL_MS     campaign dispatcher poll interval (default 500)
  (API side, read by services/calling/dispatch.py: CALLING_CONTEXT, CALLING_DRIVER,
   CALLING_AGENT_RING_SECONDS, CALLING_MAX_CALL_SECONDS, CALLING_DISPATCH_TTL_SECONDS)
"""
from __future__ import annotations

import dataclasses
import os
import re

DEFAULT_TEST_PBX_HOSTS = ("pbx-test", "cawp-pbx-test")
PRODUCTION_PBX = "10.10.0.220"
# The test-only secrets in docker-compose.yml. They are refused for any other PBX.
TEST_ONLY_SECRETS = frozenset({"pbx-test-ami-only", "pbx-test-agi-only", "pbx-test-sip-only"})


class ConfigError(ValueError):
    pass


def _bool(env, name, default):
    v = env.get(name)
    if v is None or v.strip() == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _int(env, name, default, lo, hi):
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        v = int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be an integer") from None
    if not lo <= v <= hi:
        raise ConfigError(f"{name} must be {lo}..{hi}")
    return v


def _list(env, name, default=()):
    raw = env.get(name)
    if raw is None:
        return tuple(default)
    return tuple(x.strip() for x in raw.split(",") if x.strip())


@dataclasses.dataclass(frozen=True)
class CallingConfig:
    enabled: bool
    ami_host: str | None
    ami_port: int
    ami_tls: bool
    ami_ca_file: str | None
    ami_server_name: str | None
    ami_username: str | None
    ami_secret: str | None = dataclasses.field(repr=False)
    plaintext_hosts: tuple
    test_pbx_hosts: tuple
    commissioned: bool
    agi_enabled: bool
    agi_bind: str
    agi_port: int
    agi_secret: str | None = dataclasses.field(repr=False)
    agi_allowed_peers: tuple
    heartbeat_seconds: int
    health_file: str
    health_max_age: int
    health_ami_grace: int
    status_port: int
    dispatch_enabled: bool = True
    reconcile_seconds: int = 10
    campaigns_enabled: bool = True
    campaign_poll_ms: int = 500

    @property
    def is_test_pbx(self) -> bool:
        return (self.ami_host or "") in self.test_pbx_hosts

    def ami_config(self):
        from services.calling.ami import AmiConfig
        return AmiConfig(self.ami_host, self.ami_port, self.ami_username, self.ami_secret, tls=self.ami_tls,
                         ca_file=self.ami_ca_file, server_name=self.ami_server_name,
                         allow_plaintext_hosts=self.plaintext_hosts)


def load(env=None) -> CallingConfig:
    env = os.environ if env is None else env
    tls = _bool(env, "CALLING_AMI_TLS", True)
    host = (env.get("CALLING_AMI_HOST") or "").strip() or None
    cfg = CallingConfig(
        enabled=_bool(env, "CALLING_ENABLED", False),
        ami_host=host,
        ami_port=_int(env, "CALLING_AMI_PORT", 5039 if tls else 5038, 1, 65535),
        ami_tls=tls,
        ami_ca_file=(env.get("CALLING_AMI_CA_FILE") or "").strip() or None,
        ami_server_name=(env.get("CALLING_AMI_SERVER_NAME") or "").strip() or None,
        ami_username=(env.get("CALLING_AMI_USERNAME") or "").strip() or None,
        ami_secret=env.get("CALLING_AMI_SECRET") or None,
        plaintext_hosts=_list(env, "CALLING_AMI_PLAINTEXT_HOSTS"),
        test_pbx_hosts=_list(env, "CALLING_TEST_PBX_HOSTS", DEFAULT_TEST_PBX_HOSTS),
        commissioned=_bool(env, "CALLING_PBX_COMMISSIONED", False),
        agi_enabled=_bool(env, "CALLING_AGI_ENABLED", True),
        agi_bind=(env.get("CALLING_AGI_BIND") or "0.0.0.0").strip(),
        agi_port=_int(env, "CALLING_AGI_PORT", 4573, 1, 65535),
        agi_secret=env.get("CALLING_AGI_SECRET") or None,
        agi_allowed_peers=_list(env, "CALLING_AGI_ALLOWED_PEERS", (host,) if host else ()),
        heartbeat_seconds=_int(env, "CALLING_HEARTBEAT_SECONDS", 10, 2, 60),
        health_file=(env.get("CALLING_HEALTH_FILE") or "/tmp/cawp-calling.alive").strip(),
        health_max_age=_int(env, "CALLING_HEALTH_MAX_AGE", 60, 10, 3600),
        health_ami_grace=_int(env, "CALLING_HEALTH_AMI_GRACE", 180, 10, 86400),
        status_port=_int(env, "CALLING_STATUS_PORT", 8791, 0, 65535),
        dispatch_enabled=_bool(env, "CALLING_DISPATCH_ENABLED", True),
        reconcile_seconds=_int(env, "CALLING_RECONCILE_SECONDS", 10, 0, 3600),
        campaigns_enabled=_bool(env, "CALLING_CAMPAIGNS_ENABLED", True),
        campaign_poll_ms=_int(env, "CALLING_CAMPAIGN_POLL_MS", 500, 100, 10000),
    )
    if (env.get("CALLING_CDR_SOURCE") or "none").strip().lower() not in ("none", "csv", "mysql"):
        raise ConfigError("CALLING_CDR_SOURCE must be none, csv or mysql")
    if cfg.enabled:
        validate(cfg)
    return cfg


def validate(cfg: CallingConfig) -> None:
    """Refuse an enabled configuration that is incomplete or points at an uncommissioned PBX."""
    if not cfg.ami_host or not re.fullmatch(r"[A-Za-z0-9.:-]{1,253}", cfg.ami_host):
        raise ConfigError("CALLING_AMI_HOST is required")
    if not cfg.is_test_pbx and not cfg.commissioned:
        raise ConfigError(
            f"refusing to connect to PBX {cfg.ami_host!r}: only the local test PBX "
            f"({', '.join(cfg.test_pbx_hosts)}) is allowed until CALLING_PBX_COMMISSIONED=1 "
            "(set it only after docs/issabel-commissioning.md passed)")
    if not cfg.commissioned and PRODUCTION_PBX in _addresses(cfg.ami_host):
        raise ConfigError(f"refusing to connect: {cfg.ami_host!r} resolves to the production PBX {PRODUCTION_PBX}")
    if not cfg.ami_username or not cfg.ami_secret:
        raise ConfigError("CALLING_AMI_USERNAME and CALLING_AMI_SECRET are required")
    if not cfg.is_test_pbx and ({cfg.ami_secret, cfg.agi_secret} & TEST_ONLY_SECRETS):
        raise ConfigError("the test-only secrets of the local test PBX cannot be used with another PBX")
    if not cfg.ami_tls and cfg.ami_host not in cfg.plaintext_hosts and cfg.ami_host not in ("127.0.0.1", "::1", "localhost"):
        raise ConfigError("plaintext AMI is only allowed to loopback or a host in CALLING_AMI_PLAINTEXT_HOSTS")
    if not cfg.ami_tls and not cfg.is_test_pbx and cfg.ami_host not in ("127.0.0.1", "::1", "localhost"):
        raise ConfigError("a commissioned PBX needs AMI over TLS (or a local loopback tunnel)")
    if cfg.agi_enabled:
        if not cfg.agi_secret or len(cfg.agi_secret) < 16:
            raise ConfigError("CALLING_AGI_SECRET is required (16+ characters) when the AGI hook is enabled")
        if not cfg.agi_allowed_peers:
            raise ConfigError("CALLING_AGI_ALLOWED_PEERS is required")


def _addresses(host: str) -> set:
    """Resolved addresses of `host` (the literal itself included); empty when unresolvable."""
    import socket
    out = {host}
    try:
        out |= {ai[4][0] for ai in socket.getaddrinfo(host, None)}
    except OSError:
        pass
    return out
