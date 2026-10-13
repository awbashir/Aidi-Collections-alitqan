"""Phase 5 P0 (no database): the calling service's configuration guards, the extension-first
Originate builder and the restricted dialplan generator."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from calling.config import ConfigError, load
from services.calling import dialplan
from services.calling.originate import extension_first_fields

pytestmark = pytest.mark.no_db

BASE = {"CALLING_ENABLED": "1", "CALLING_AMI_HOST": "pbx-test", "CALLING_AMI_USERNAME": "cawp-calling",
        "CALLING_AMI_SECRET": "pbx-test-ami-only", "CALLING_AGI_SECRET": "pbx-test-agi-only"}


def test_disabled_by_default_and_needs_nothing():
    cfg = load({})
    assert cfg.enabled is False and cfg.ami_host is None


def test_test_pbx_is_allowed():
    cfg = load(BASE)
    assert cfg.enabled and cfg.is_test_pbx and cfg.ami_tls and cfg.ami_port == 5039
    assert cfg.agi_allowed_peers == ("pbx-test",)
    assert "pbx-test-ami-only" not in repr(cfg)              # secrets never in repr/logs


@pytest.mark.parametrize("host", ["10.10.0.220", "issabel.example.internal", "192.168.1.10"])
def test_real_pbx_refused_until_commissioned(host):
    with pytest.raises(ConfigError, match="CALLING_PBX_COMMISSIONED"):
        load({**BASE, "CALLING_AMI_HOST": host})


def test_commissioned_pbx_needs_real_secrets_and_tls():
    env = {**BASE, "CALLING_AMI_HOST": "10.10.0.220", "CALLING_PBX_COMMISSIONED": "1"}
    with pytest.raises(ConfigError, match="test-only secrets"):
        load(env)
    env.update(CALLING_AMI_SECRET="a-real-secret-from-the-vault", CALLING_AGI_SECRET="another-real-secret-123")
    assert load(env).commissioned
    with pytest.raises(ConfigError, match="TLS|plaintext"):
        load({**env, "CALLING_AMI_TLS": "0"})


def test_other_required_settings():
    with pytest.raises(ConfigError):
        load({**BASE, "CALLING_AMI_HOST": ""})
    with pytest.raises(ConfigError):
        load({**BASE, "CALLING_AMI_SECRET": ""})
    with pytest.raises(ConfigError):
        load({**BASE, "CALLING_AGI_SECRET": "short"})
    with pytest.raises(ConfigError):
        load({**BASE, "CALLING_AMI_TLS": "0"})              # plaintext needs an explicit host allow
    assert load({**BASE, "CALLING_AMI_TLS": "0", "CALLING_AMI_PLAINTEXT_HOSTS": "pbx-test"}).ami_port == 5038
    assert load({**BASE, "CALLING_AGI_ENABLED": "0", "CALLING_AGI_SECRET": ""}).agi_enabled is False


def test_extension_first_originate_fields():
    aid = "11111111-1111-4111-8111-111111111111"
    fields = extension_first_fields(attempt_id=aid, extension="7201", destination="+966500000001",
                                    token="a" * 64, context="collections-human-outbound")
    assert fields == [
        ("Channel", "PJSIP/7201"), ("Context", "collections-human-outbound"), ("Exten", "+966500000001"),
        ("Priority", "1"), ("Async", "true"), ("Timeout", "30000"), ("ChannelId", f"cawp-{aid}"),
        ("Variable", f"__COLLECTIONS_ATTEMPT={aid}"), ("Variable", "COLLECTIONS_TOKEN=" + "a" * 64),
        ("Variable", f"PJSIP_HEADER(add,X-CAWP-Attempt)={aid}")]              # softphone auto-answer tag
    assert not any(k in ("Application", "Data", "CallerID") for k, _ in fields)
    for bad in ({"extension": "7201;x"}, {"destination": "0555"}, {"context": "from-internal"},
                {"token": "x"}, {"attempt_id": "nope"}, {"driver": "Local"}):
        args = dict(attempt_id=aid, extension="7201", destination="+966500000001", token="a" * 64,
                    context="collections-human-outbound")
        args.update(bad)
        with pytest.raises(ValueError):
            extension_first_fields(**args)


POLICY = {"context": "collections-human-outbound", "driver": "PJSIP", "trunk": "cawp-test-trunk",
          "max_call_seconds": 600, "ring_seconds": 20, "record": True,
          "calling_windows": [{"days": [0, 1, 2, 3, 4, 5, 6], "start": "00:00", "end": "24:00"}]}


def test_dialplan_gate_structure():
    text = dialplan.render(POLICY)
    lines = text.splitlines()
    agi = next(i for i, ln in enumerate(lines) if "AGI(" in ln)
    dial = next(i for i, ln in enumerate(lines) if ",Dial(" in ln)
    assert "AGI(${GLOBAL(COLLECTIONS_AGI_URL)})" in lines[agi]          # never a channel variable
    assert agi < dial
    # every gate check sits between the AGI and the Dial, and each one jumps to deny
    checks = [ln for ln in lines[agi:dial] if "GotoIf(" in ln and "GotoIfTime" not in ln]
    assert len(checks) >= 10 and all("?deny)" in ln for ln in checks)
    assert '"${COLLECTIONS_GATE}" != "ALLOW"' in text
    assert "PJSIP_ENDPOINT(cawp-test-trunk,allow_transfer)" in text
    assert "TIMEOUT(absolute)=${COLLECTIONS_GATE_LIMIT}" in text and "L($[${COLLECTIONS_GATE_LIMIT} * 1000])" in text
    assert "MixMonitor(${COLLECTIONS_RECORDING},b)" in text
    for state in ("agent_answered", "authorized", "customer_dialing", "dial_end", "denied", "customer_created",
                  "customer_answered", "customer_ended", "agent_ended"):
        assert f"State: {state}" in text, state
    # transfer / continuation options are never generated
    import re
    options = lines[dial].split("Dial(", 1)[1].split(",", 2)[2][:-1]        # after target and ring time
    while re.search(r"\([^()]*\)", options):
        options = re.sub(r"\([^()]*\)", "", options)                         # drop option arguments
    assert set(options) == {"i", "b", "U", "L"}, options


def test_dialplan_empty_windows_deny_everything_and_sip_variant():
    text = dialplan.render({**POLICY, "calling_windows": [], "driver": "SIP", "record": False})
    assert "GotoIfTime" not in text and "Dial(SIP/cawp-test-trunk/${COLLECTIONS_GATE_DESTINATION}" in text
    assert "MixMonitor" not in text and "PJSIP_ENDPOINT" not in text


@pytest.mark.parametrize("bad", [
    {"context": "from-internal"}, {"driver": "Local"}, {"trunk": "bad trunk"}, {"max_call_seconds": 10},
    {"ring_seconds": 0}, {"record": "yes"}, {"calling_windows": [{"days": [9], "start": "09:00", "end": "10:00"}]},
    {"extra": 1}, {"caller_id_gosub": "Bad Context"}, {"caller_id_gosub": 3}, {"post_call_context": "bad^ctx"},
])
def test_dialplan_policy_validation(bad):
    with pytest.raises(dialplan.DialplanPolicyError):
        dialplan.render({**POLICY, **bad})


def test_dialplan_optional_caller_id_gosub():
    plain = dialplan.render(POLICY)
    assert "Gosub(" not in plain
    text = dialplan.render({**POLICY, "caller_id_gosub": "cawp-cid-rotate"})
    lines = text.splitlines()
    gosub = lines.index(" same => n,Gosub(cawp-cid-rotate,s,1)")
    dial = next(i for i, l in enumerate(lines) if ",Dial(" in l)
    assert lines.index(" same => n,Set(CDR(userfield)=cawp:${COLLECTIONS_ATTEMPT})") < gosub < dial
    # only the comment and the Gosub line are added; every other line is unchanged
    added = [l for l in lines if l not in plain.splitlines()]
    assert len(added) == 2 and added[0].startswith("; Caller ID: Gosub(cawp-cid-rotate")


def test_dialplan_optional_post_call_context():
    plain = dialplan.render(POLICY)
    assert "F(" not in plain
    text = dialplan.render({**POLICY, "post_call_context": "cawp-survey"})
    dial = next(l for l in text.splitlines() if ",Dial(" in l)
    assert dial.endswith("L($[${COLLECTIONS_GATE_LIMIT} * 1000])F(cawp-survey^s^1))")
    changed = [l for l in text.splitlines() if l not in plain.splitlines()]
    assert len(changed) == 2 and changed[0].startswith("; Post-call: the customer goes to cawp-survey")


def test_test_pbx_dialplan_matches_the_generator():
    root = Path(__file__).resolve().parents[2] / "pbx-test"
    if not (root / "policy.json").is_file():
        pytest.skip("pbx-test/ is not mounted in this container (checked on the host run)")
    policy = json.loads((root / "policy.json").read_text())
    assert (root / "conf" / "extensions_collections.conf").read_text() == dialplan.render(policy)


def test_e164_from_contact_never_guesses():
    pytest.importorskip("flask_sqlalchemy")
    try:
        from services.calling.common import e164_from_contact
    except RuntimeError:
        pytest.skip("DATABASE_URL not set")
    assert e164_from_contact("966551234567") == "+966551234567"
    assert e164_from_contact("0551234567") == "+966551234567"
    assert e164_from_contact("00966551234567") == "+966551234567"
    assert e164_from_contact("abc") is None and e164_from_contact("12") is None and e164_from_contact(None) is None
