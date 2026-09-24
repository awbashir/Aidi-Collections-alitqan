"""Phase 6 Q0: masking of customer identifiers (services/ai/masking.py). Pure unit tests (no DB).

Arabic and English text, Arabic-Indic and Persian digits, phone prefixes and separators, identity
numbers, IBANs, stable placeholders per request, the in-memory reverse map, and what must NOT be
masked (amounts, dates, short numbers).

Run: docker compose --profile test run --rm -e TEST_DB_PREFIX=cawp_test_ai0 api-test tests/test_ai_masking.py
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.no_db


def masker(classes=None, **known):
    from services.ai.masking import Identifiers, Masker
    m = Masker() if classes is None else Masker(classes)
    return m.add(Identifiers(**{k: tuple(v) for k, v in known.items()}))


def test_known_arabic_name_with_spelling_variants_diacritics_and_parts():
    m = masker(names=["محمد عبد الله السبيعي"])
    out = m.mask("العميل محمد عبدالله السبيعي قال إن محمّد سيدفع، وأكد السبيعى ذلك. إن شاء الله")
    assert "محمد" not in out and "محمّد" not in out and "السبيع" not in out
    assert out.startswith("العميل [NAME_1] قال إن [NAME_")
    assert "إن شاء الله" in out                       # "الله" alone is never a name part
    assert out.count("[NAME_") == 3
    # an attached conjunction stays outside the placeholder
    assert masker(names=["أحمد"]).mask("وأحمد وافق") == "و[NAME_1] وافق"
    # hamza/alef variants of the same name share the placeholder
    m2 = masker(names=["أحمد"])
    assert m2.mask("احمد") == m2.mask("إحمد") == "[NAME_1]"


def test_known_english_name_case_insensitive_and_whole_words_only():
    m = masker(names=["John Smith"])
    out = m.mask("JOHN SMITH called; john agreed. Johnson and smithy are other words.")
    assert out == "[NAME_1] called; [NAME_2] agreed. Johnson and smithy are other words."


def test_phone_variants_share_one_placeholder():
    m = masker(phones=["966501234567"])
    variants = ["0501234567", "٠٥٠١٢٣٤٥٦٧", "+966 50 123 4567", "00966-50-123-4567", "966501234567", "۰۵۰۱۲۳۴۵۶۷"]
    outs = [m.mask(f"رقمه {v} شكرا") for v in variants]
    assert set(outs) == {"رقمه [PHONE_1] شكرا"}
    assert m.count == 1


def test_phone_patterns_without_known_values():
    m = masker()
    out = m.mask("mobile ٠٥٥٩٨٧٦٥٤٣, office 011 234 5678, UK +44 20 7946 0958")
    assert out == "mobile [PHONE_1], office [PHONE_2], UK [PHONE_3]"


def test_identity_numbers_in_arabic_indic_digits():
    m = masker(identity_numbers=["1012345678"])
    assert m.mask("رقم الهوية ١٠١٢٣٤٥٦٧٨") == "رقم الهوية [ID_NUMBER_1]"
    assert m.mask("id 1012345678") == "id [ID_NUMBER_1]"
    # an unknown Saudi identity/iqama number is found by pattern
    assert m.mask("الإقامة ٢٣٤٥٦٧٨٩٠١") == "الإقامة [ID_NUMBER_2]"


def test_ibans_and_accounts():
    m = masker(accounts=["ACC-778899"])
    out = m.mask("IBAN SA03 8000 0000 6080 1016 7519 and sa0380000000608010167519, account acc 778899")
    assert out == "IBAN [ACCOUNT_1] and [ACCOUNT_1], account [ACCOUNT_2]"
    # a foreign IBAN is masked when its checksum is valid ...
    assert masker().mask("GB82 WEST 1234 5698 7654 32") == "[ACCOUNT_1]"
    # ... and a look-alike with a wrong checksum is not treated as one
    assert masker().mask("GB00 WEST 1234 5698 7654 32") == "GB00 WEST 1234 5698 7654 32"
    # an IBAN in Arabic-Indic digits
    assert masker().mask("الآيبان SA٠٣٨٠٠٠٠٠٠٠٦٠٨٠١٠١٦٧٥١٩") == "الآيبان [ACCOUNT_1]"
    # other long digit runs are treated as account numbers
    assert masker().mask("ref 123456789012") == "ref [ACCOUNT_1]"


def test_amounts_dates_and_short_numbers_are_not_masked():
    text = "المبلغ 12,500.50 ريال بتاريخ 2026-09-24 الساعة 10:30، القسط 3 من 12، amount 1500 SAR, case 42"
    assert masker(names=["Ali Hassan"]).mask(text) == text


def test_stable_placeholders_across_messages_and_unmask_nested():
    m = masker(names=["سارة القحطاني"], phones=["0551112222"])
    a, b = m.mask_all(["اتصلت سارة القحطاني من 0551112222", "سارة القحطاني: رقمي +966551112222"])
    assert a == "اتصلت [NAME_1] من [PHONE_1]"
    assert b == "[NAME_1]: رقمي [PHONE_1]"
    draft = {"summary": "تم التواصل مع [NAME_1] على [PHONE_1]", "steps": ["اتصل بـ [PHONE_1]", {"who": "[NAME_1]"}],
             "count": 2, "unknown": "[PHONE_7]"}
    back = m.unmask(draft)
    assert back == {"summary": "تم التواصل مع سارة القحطاني على 0551112222",
                    "steps": ["اتصل بـ 0551112222", {"who": "سارة القحطاني"}], "count": 2,
                    "unknown": "[PHONE_7]"}          # an invented placeholder is never guessed


def test_single_spelling_round_trip_is_exact():
    text = "Customer Fatima Al-Harbi, id 1098765432, phone 0509998877, IBAN SA4420000001234567891234."
    m = masker(names=["Fatima Al-Harbi"], identity_numbers=["1098765432"], phones=["0509998877"])
    masked = m.mask(text)
    for secret in ("Fatima", "Harbi", "1098765432", "0509998877", "SA4420000001234567891234"):
        assert secret not in masked
    assert m.unmask(masked) == text


def test_disabled_classes_are_left_alone():
    m = masker(classes={"name", "identity_number", "account"}, names=["Ali Hassan"], phones=["0501234567"])
    assert m.mask("Ali Hassan 0501234567") == "[NAME_1] 0501234567"


def test_literal_placeholders_in_the_input_are_never_reused():
    m = masker(phones=["0501234567"])
    out = m.mask_all(["the template says [PHONE_1]", "call 0501234567"])
    assert out == ["the template says [PHONE_1]", "call [PHONE_2]"]
    assert m.unmask("[PHONE_1] / [PHONE_2]") == "[PHONE_1] / 0501234567"


def test_employee_names_and_policy_classes():
    from services.ai.masking import classes_for_policy
    flags = {"name": True, "identity_number": False, "phone": True, "account": True}
    assert classes_for_policy(flags, employee_names_allowed=False) == {"name", "phone", "account", "employee_name"}
    assert classes_for_policy(flags, employee_names_allowed=True) == {"name", "phone", "account"}
    m = masker(employee_names=["Noura Saleh"])
    assert m.mask("Agent Noura Saleh greeted the customer") == "Agent [EMPLOYEE_1] greeted the customer"


def test_the_reverse_map_is_not_printable():
    from services.ai.masking import Identifiers
    m = masker(names=["Secret Person"], phones=["0501234567"])
    m.mask("Secret Person 0501234567")
    assert "Secret" not in repr(m) and "0501234567" not in repr(m)
    assert "Secret" not in repr(Identifiers(names=("Secret Person",)))


def test_unknown_class_is_refused():
    from services.ai.masking import Masker
    with pytest.raises(ValueError):
        Masker({"name", "email"})
