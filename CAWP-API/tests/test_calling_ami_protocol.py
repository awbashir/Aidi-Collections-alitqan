"""AMI wire codec (no network, no database)."""
from __future__ import annotations

import pytest

from services.calling.ami import MAX_FRAME, MAX_LINE, AmiDecoder, AmiError, Frame, Greeting, encode_action

pytestmark = pytest.mark.no_db

GREET = b"Asterisk Call Manager/9.0.0\r\n"


def decode(*chunks):
    d = AmiDecoder()
    out = []
    for c in chunks:
        out += d.feed(c)
    return d, out


def test_greeting_and_frames_across_arbitrary_boundaries():
    data = GREET + b"Response: Success\r\nActionID: a1\r\nMessage: ok\r\n\r\nEvent: UserEvent\r\nAttemptId: x\r\n\r\n"
    for size in (1, 2, 3, 7, 64, len(data)):
        _, out = decode(*[data[i:i + size] for i in range(0, len(data), size)])
        assert isinstance(out[0], Greeting) and out[0].banner == "Asterisk Call Manager/9.0.0"
        assert [f.response or f.event for f in out[1:]] == ["Success", "UserEvent"]


def test_repeated_fields_and_empty_values_are_kept():
    _, out = decode(GREET, b"Event: VarSet\r\nVariable: A=1\r\nVariable: B=2\r\nAccountCode: \r\nKey:\r\n"
                           b"Other:tight\r\n\r\n")
    f = out[1]
    assert f.values("variable") == ["A=1", "B=2"]                       # case-insensitive, ordered
    assert f.get("AccountCode") == "" and f.get("Key") == "" and f.get("Other") == "tight"
    with pytest.raises(AmiError):
        f.get("Variable")                                               # single-value read refuses duplicates


def test_asterisk11_channel_variables_are_normalised():
    _, out = decode(GREET, b"Event: Newchannel\r\nChannel: SIP/401-0000ab12\r\n"
                           b"ChanVariable(SIP/401-0000ab12): DIALERID=\r\n"
                           b"ChanVariable(Local/123@from-queue-0000ab12;2): DIALERVAR=x\r\n"
                           b"ChanVariable(OutgoingSpoolFailed): DIALERID=\r\n\r\n")
    f = out[1]
    assert f.event == "Newchannel" and f.values("ChanVariable") == ["DIALERID=", "DIALERVAR=x", "DIALERID="]
    assert not [k for k, _ in f.fields if "(" in k]


@pytest.mark.parametrize("key", [b"ChanVariable()", b"ChanVariable(a b)", b"ChanVariable(x)(y)", b"chanvariable(x)",
                                 b"ChanVariable(" + b"a" * 151 + b")", b"Event(x)"])
def test_other_parenthesised_keys_still_poison(key):
    d = AmiDecoder()
    d.feed(GREET)
    with pytest.raises(AmiError):
        d.feed(b"Event: X\r\n" + key + b": v\r\n\r\n")


def test_unicode_split_inside_a_character():
    arabic = "عميل".encode()
    data = GREET + b"Event: X\r\nName: " + arabic + b"\r\n\r\n"
    cut = data.index(arabic) + 1                                        # in the middle of a code point
    _, out = decode(data[:cut], data[cut:])
    assert out[1].get("Name") == "عميل"


def test_latin1_values_do_not_poison_the_stream():
    _, out = decode(GREET, b"Event: Newchannel\r\nCallerIDName: Jos\xe9\r\n\r\nEvent: Next\r\n\r\n")
    assert out[1].get("CallerIDName").startswith("Jos") and out[2].event == "Next"


@pytest.mark.parametrize("bad", [
    b"Not a manager\r\n",                                               # wrong greeting
    GREET + b"Event: X\nKey: v\r\n\r\n",                                # bare LF
    GREET + b"Event: X\r\nno colon here\r\n\r\n",                       # no key
    GREET + b"Event: X\r\n1Key: v\r\n\r\n",                             # invalid key
    GREET + b"Event: X\r\nEvent: Y\r\n\r\n",                            # two routing values
    GREET + b"Response: Success\r\nActionID: 1\r\nActionID: 2\r\n\r\n",
    GREET + b"Event: X\r\nKey: a\x01b\r\n\r\n",                         # control character
    GREET + b"Event: X\r\nKey: " + b"a" * (MAX_LINE + 10) + b"\r\n\r\n",
    GREET + b"Event: X\r\n" + b"".join(b"K%d: v\r\n" % i for i in range(300)) + b"\r\n",
])
def test_malformed_input_poisons_the_decoder(bad):
    d = AmiDecoder()
    with pytest.raises(AmiError) as e:
        d.feed(bad)
    assert e.value.code == "ami_protocol_invalid"
    with pytest.raises(AmiError):
        d.feed(b"Event: Fine\r\n\r\n")                                   # stays poisoned


def test_frame_size_limit_and_truncated_end():
    d = AmiDecoder()
    d.feed(GREET)
    big = b"Event: X\r\n" + b"".join(b"Key%d: %s\r\n" % (i, b"v" * 4000) for i in range(20))
    assert len(big) > MAX_FRAME
    with pytest.raises(AmiError):
        d.feed(big)
    d2 = AmiDecoder()
    d2.feed(GREET + b"Event: X\r\n")
    with pytest.raises(AmiError):
        d2.end()                                                         # ended inside a frame


def test_encode_action_rules():
    assert encode_action([("Action", "Ping"), ("ActionID", "s:1")]) == b"Action: Ping\r\nActionID: s:1\r\n\r\n"
    ok = encode_action([("Action", "Originate"), ("ActionID", "1"), ("Variable", "A=1"), ("Variable", "B=2")])
    assert ok.count(b"Variable:") == 2
    for bad in (
        [("ActionID", "1")],                                             # no Action
        [("Action", "Ping")],                                            # no ActionID
        [("Action", "Ping"), ("Action", "Logoff"), ("ActionID", "1")],   # two actions
        [("Action", "Ping"), ("ActionID", "1"), ("Event", "X")],
        [("Action", "Ping"), ("ActionID", "1"), ("Response", "Success")],
        [("Action", "Ping"), ("ActionID", "1\r\nAction: Command")],      # header injection
        [("Action", "Ping"), ("ActionID", "1"), ("Data", "x\ny")],
        [("Action", "Ping"), ("ActionID", "1"), ("Bad Key", "x")],
    ):
        with pytest.raises(AmiError):
            encode_action(bad)


def test_frame_repr_never_shows_values():
    f = Frame([("Response", "Success"), ("Secret", "hunter2")])
    assert "hunter2" not in repr(f)
