"""Per-request masking of customer identifiers before text is sent to an AI provider.

    masker = Masker(classes={"name", "identity_number", "phone", "account", "employee_name"})
    masker.add(Identifiers(names=["محمد عبد الله السبيعي"], phones=["966501234567"],
                           identity_numbers=["1012345678"], accounts=["ACC-778899"]))
    sent = masker.mask("اتصل محمد من ٠٥٠١٢٣٤٥٦٧")        # -> "اتصل [NAME_2] من [PHONE_1]"
    shown = masker.unmask(model_output)                   # placeholders -> the original text

* **Stable placeholders per request**: the same value gets the same placeholder everywhere in
  the request, whatever its spelling: Arabic-Indic or Persian digits, spaces or dashes inside a
  number, `+966` / `00966` / `0` phone prefixes, Arabic letter variants (أ/إ/آ/ا, ى/ي, ة/ه) and
  diacritics in names.
* **The reverse map lives only in this object** (memory of one request). It is never logged,
  stored or returned; `repr()` hides it.
* **What is found**: the known values the caller passes (the case's name, identity number,
  account and contacts, employee names), plus patterns that need no list: Saudi mobile and
  landline numbers, international `+`/`00` numbers, Saudi identity/iqama numbers (10 digits
  starting with 1 or 2), IBANs (`SA` + 22 digits always; other countries when the mod-97 check
  passes), and other runs of 9+ digits (treated as account numbers). Names cannot be found by
  pattern: only known names are masked.
* A class that is not enabled (the policy allows sending it) is left as it is.
* A placeholder-like text already present in the input (`[PHONE_1]`) is never reused, so
  unmasking cannot turn a literal into a customer value. A placeholder the model invents is
  left as it is (never guessed).
"""
from __future__ import annotations

import dataclasses
import re
import unicodedata

CLASSES = ("name", "identity_number", "phone", "account", "employee_name")
POLICY_CLASSES = ("name", "identity_number", "phone", "account")
LABELS = {"name": "NAME", "identity_number": "ID_NUMBER", "phone": "PHONE", "account": "ACCOUNT",
          "employee_name": "EMPLOYEE"}
_LABEL_CLASS = {v: k for k, v in LABELS.items()}
PLACEHOLDER_RE = re.compile(r"\[(NAME|ID_NUMBER|PHONE|ACCOUNT|EMPLOYEE)_(\d{1,6})\]")

# ---------------------------------------------------------------------------- characters
_ARABIC_INDIC = "٠١٢٣٤٥٦٧٨٩"
_PERSIAN = "۰۱۲۳۴۵۶۷۸۹"
_TO_ASCII = str.maketrans(_ARABIC_INDIC + _PERSIAN, "0123456789" * 2)
D = "[0-9\u0660-\u0669\u06F0-\u06F9]"          # any decimal digit we accept
SEP = "[ \t\u00A0\\-]?"                        # optional separator inside a number
_NOT_DIGIT_BEFORE = rf"(?<!{D})"
_NOT_DIGIT_AFTER = rf"(?!{D})"
_ALNUM = "[0-9A-Za-z\u0660-\u0669\u06F0-\u06F9]"

_DIACRITICS = "\u064B-\u0652\u0670\u0640"      # harakat, dagger alef, tatweel
_DIACRITICS_RE = re.compile(f"[{_DIACRITICS}]")
_OPT_DIACRITICS = f"[{_DIACRITICS}]*"
_ARABIC_GROUPS = ("اأإآٱ", "يىئ", "ةه", "وؤ")
_ARABIC_EQUIV = {ch: grp for grp in _ARABIC_GROUPS for ch in grp}
_ARABIC_CANON = {ch: grp[0] for grp in _ARABIC_GROUPS for ch in grp}
_COMPOUND_PREFIXES = {"عبد", "ابو", "أبو", "بو", "ام", "أم", "abd", "abdul", "abu", "umm"}
_NAME_STOPWORDS = {"بن", "ابن", "بنت", "ال", "آل", "الله", "الرحمن", "bin", "bint", "binti", "ibn", "al", "el",
                   "the", "mr", "mrs", "ms", "dr"}


def ascii_digits(text: str) -> str:
    return text.translate(_TO_ASCII)


def digits_only(text: str) -> str:
    return re.sub(r"\D", "", ascii_digits(text))


def normalize_name(text: str) -> str:
    t = unicodedata.normalize("NFC", text)
    t = _DIACRITICS_RE.sub("", t)
    t = "".join(_ARABIC_CANON.get(ch, ch) for ch in t)
    return re.sub(r"\s+", "", t).casefold()


def phone_key(text: str) -> str:
    d = digits_only(text)
    if d.startswith("00"):
        d = d[2:]
    if d.startswith("966") and len(d) >= 12:
        return d[3:]
    if d.startswith("0") and len(d) in (9, 10):
        return d[1:]
    return d


def _digit_class(ch: str) -> str:
    i = int(ascii_digits(ch))
    return f"[{i}{_ARABIC_INDIC[i]}{_PERSIAN[i]}]"


def _iban_ok(value: str) -> bool:
    v = ascii_digits(re.sub(r"[\s\-]", "", value)).upper()
    if not re.fullmatch(r"[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}", v):
        return False
    rearranged = v[4:] + v[:4]
    number = "".join(str(int(ch, 36)) for ch in rearranged)
    return int(number) % 97 == 1


# ---------------------------------------------------------------------------- patterns
_IBAN_SA = re.compile(rf"(?<!{_ALNUM})[Ss][Aa]{SEP}(?:{D}{SEP}){{21}}{D}(?!{_ALNUM})")
_IBAN_ANY = re.compile(rf"(?<!{_ALNUM})[A-Z]{{2}}{SEP}{D}{D}(?:{SEP}[A-Z0-9\u0660-\u0669\u06F0-\u06F9]){{11,30}}(?!{_ALNUM})")
_SAUDI_ID = re.compile(rf"{_NOT_DIGIT_BEFORE}[12\u0661\u0662\u06F1\u06F2](?:{SEP}{D}){{9}}{_NOT_DIGIT_AFTER}")
_SA_MOBILE = re.compile(
    rf"(?<![0-9\u0660-\u0669\u06F0-\u06F9+])"
    rf"(?:(?:\+|00|[\u0660\u06F0]{{2}}){SEP}{_digit_class('9')}{SEP}{_digit_class('6')}{SEP}{_digit_class('6')}{SEP}"
    rf"|{_digit_class('9')}{_digit_class('6')}{_digit_class('6')}{SEP}"
    rf"|{_digit_class('0')})?"
    rf"{_digit_class('5')}(?:{SEP}{D}){{8}}{_NOT_DIGIT_AFTER}")
_SA_LANDLINE = re.compile(
    rf"{_NOT_DIGIT_BEFORE}{_digit_class('0')}{_digit_class('1')}[1-7\u0661-\u0667\u06F1-\u06F7](?:{SEP}{D}){{7}}{_NOT_DIGIT_AFTER}")
_INTL_PHONE = re.compile(rf"(?<![0-9\u0660-\u0669\u06F0-\u06F9+])(?:\+|00){SEP}{D}(?:{SEP}{D}){{7,13}}{_NOT_DIGIT_AFTER}")
_LONG_DIGITS = re.compile(rf"{_NOT_DIGIT_BEFORE}{D}{{9,}}{_NOT_DIGIT_AFTER}")

# (class, compiled, priority, key function, extra check). Lower priority number wins a tie.
_DETECTORS = (
    ("account", _IBAN_SA, 1, lambda m: ascii_digits(re.sub(r"[\s\-]", "", m)).upper(), None),
    ("account", _IBAN_ANY, 1, lambda m: ascii_digits(re.sub(r"[\s\-]", "", m)).upper(), _iban_ok),
    ("identity_number", _SAUDI_ID, 2, digits_only, None),
    ("phone", _SA_MOBILE, 3, phone_key, None),
    ("phone", _SA_LANDLINE, 3, phone_key, None),
    ("phone", _INTL_PHONE, 4, phone_key, None),
    ("account", _LONG_DIGITS, 5, digits_only, None),
)


@dataclasses.dataclass(frozen=True)
class Identifiers:
    """Known values to mask (for one case, one call, ...). Empty/short values are ignored."""
    names: tuple = ()
    identity_numbers: tuple = ()
    phones: tuple = ()
    accounts: tuple = ()
    employee_names: tuple = ()

    def __repr__(self):
        return (f"Identifiers(names={len(self.names)}, identity_numbers={len(self.identity_numbers)}, "
                f"phones={len(self.phones)}, accounts={len(self.accounts)}, "
                f"employee_names={len(self.employee_names)})")

    def merged(self, other: "Identifiers | None") -> "Identifiers":
        if other is None:
            return self
        return Identifiers(*(tuple(getattr(self, f.name)) + tuple(getattr(other, f.name))
                             for f in dataclasses.fields(self)))


def _name_pattern(name: str) -> str | None:
    parts = []
    for ch in _DIACRITICS_RE.sub("", unicodedata.normalize("NFC", name.strip())):
        if ch.isspace():
            if parts and parts[-1] != r"\s*":
                parts.append(r"\s*")
        elif ch in _ARABIC_EQUIV:
            parts.append(f"[{_ARABIC_EQUIV[ch]}]{_OPT_DIACRITICS}")
        elif ch in "-.'’":
            parts.append(r"[\-.'’\s]?")
        elif "\u0600" <= ch <= "\u06FF":
            parts.append(re.escape(ch) + _OPT_DIACRITICS)
        else:
            parts.append(re.escape(ch))
    if not parts:
        return None
    # a name may carry an attached Arabic proclitic (و ف ب ل ك): "ولمحمد" is not matched, "ومحمد" is
    return r"(?:(?<!\w)|(?<=(?<!\w)[وفبلك]))" + "".join(parts) + r"(?!\w)"


def name_variants(full_name: str) -> list[str]:
    """The full name plus its meaningful parts ("عبد الله" kept together), longest first."""
    full = re.sub(r"\s+", " ", _DIACRITICS_RE.sub("", full_name or "")).strip()
    if len(normalize_name(full)) < 3:
        return []
    tokens, out = full.split(" "), [full]
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.casefold() in _COMPOUND_PREFIXES and i + 1 < len(tokens):
            tok = tok + " " + tokens[i + 1]
            i += 1
        i += 1
        if tok.casefold() in _NAME_STOPWORDS or len(normalize_name(tok)) < 3:
            continue
        if tok not in out:
            out.append(tok)
    return sorted(out, key=lambda t: -len(t))


def _value_pattern(value: str) -> str | None:
    """A known account/identity value: digits in any script, optional separators, any case."""
    chars = [c for c in ascii_digits(value.strip()) if c.isalnum()]
    if len(chars) < 4:
        return None
    pieces = [(_digit_class(c) if c.isdigit() else f"[{re.escape(c.upper())}{re.escape(c.lower())}]") for c in chars]
    return f"(?<!{_ALNUM})" + SEP.join(pieces) + f"(?!{_ALNUM})"


def _phone_pattern(value: str) -> str | None:
    key = phone_key(value)
    if len(key) < 6:
        return None
    digits = SEP.join(_digit_class(c) for c in key)
    if len(key) == 9 and key.startswith("5"):       # Saudi mobile: any national/international prefix
        prefix = (rf"(?:(?:\+|00|[\u0660\u06F0]{{2}}){SEP}{_digit_class('9')}{SEP}{_digit_class('6')}{SEP}"
                  rf"{_digit_class('6')}{SEP}|{_digit_class('9')}{_digit_class('6')}{_digit_class('6')}{SEP}"
                  rf"|{_digit_class('0')})?")
    else:
        prefix = rf"(?:\+|00)?{SEP}{_digit_class('0')}?"
    return rf"(?<![0-9\u0660-\u0669\u06F0-\u06F9+]){prefix}{digits}{_NOT_DIGIT_AFTER}"


class Masker:
    """One request's masking state. Not thread-safe; create one per provider call."""

    def __init__(self, classes=POLICY_CLASSES + ("employee_name",)):
        unknown = set(classes) - set(CLASSES)
        if unknown:
            raise ValueError(f"unknown masking classes {sorted(unknown)}")
        self.classes = frozenset(classes)
        self._known: list[tuple[str, re.Pattern, str]] = []     # (class, pattern, key)
        self._by_key: dict[tuple[str, str], str] = {}           # (class, key) -> placeholder
        self._original: dict[str, str] = {}                     # placeholder -> first surface text
        self._counters = {c: 0 for c in CLASSES}
        self._reserved: set[str] = set()

    def __repr__(self):
        return f"Masker(classes={sorted(self.classes)}, placeholders={len(self._original)})"

    @property
    def count(self) -> int:
        """How many distinct values were replaced (for the audit row; not the values)."""
        return len(self._original)

    def add(self, identifiers: Identifiers | None) -> "Masker":
        if identifiers is None:
            return self
        # every class claims its spans (so a phone the policy allows is not re-caught as a digit run);
        # only enabled classes are replaced
        for cls, values in (("name", identifiers.names), ("employee_name", identifiers.employee_names)):
            for value in values or ():
                if not isinstance(value, str):
                    continue
                for variant in name_variants(value):
                    pat = _name_pattern(variant)
                    if pat:
                        self._known.append((cls, re.compile(pat, re.IGNORECASE), normalize_name(variant)))
        for cls, values, builder, keyfn in (
                ("identity_number", identifiers.identity_numbers, _value_pattern, lambda v: ascii_digits(re.sub(r"[\W_]", "", v)).upper()),
                ("account", identifiers.accounts, _value_pattern, lambda v: ascii_digits(re.sub(r"[\W_]", "", v)).upper()),
                ("phone", identifiers.phones, _phone_pattern, phone_key)):
            for value in values or ():
                if not isinstance(value, str) or not value.strip():
                    continue
                pat = builder(value)
                if pat:
                    self._known.append((cls, re.compile(pat), keyfn(value)))
        # longest known values first, so "the full name" beats one of its parts
        self._known.sort(key=lambda k: -len(k[1].pattern))
        return self

    def _placeholder(self, cls: str, key: str, surface: str) -> str:
        ph = self._by_key.get((cls, key))
        if ph is not None:
            return ph
        while True:
            self._counters[cls] += 1
            ph = f"[{LABELS[cls]}_{self._counters[cls]}]"
            if ph not in self._reserved:
                break
        self._by_key[(cls, key)] = ph
        self._original[ph] = surface
        return ph

    def _spans(self, text: str) -> list[tuple[int, int, str, str]]:
        found = []   # (start, end, priority, class, key)
        for cls, pat, key in self._known:
            for m in pat.finditer(text):
                if m.end() > m.start():
                    found.append((m.start(), m.end(), 0, cls, key))
        for cls, pat, prio, keyfn, check in _DETECTORS:
            for m in pat.finditer(text):
                if check is not None and not check(m.group(0)):
                    continue
                found.append((m.start(), m.end(), prio, cls, keyfn(m.group(0))))
        found.sort(key=lambda f: (f[0], -(f[1] - f[0]), f[2]))
        chosen, last_end = [], -1
        for start, end, _prio, cls, key in found:
            if start >= last_end:
                chosen.append((start, end, cls, key))
                last_end = end
        return chosen

    def mask(self, text: str) -> str:
        if not isinstance(text, str) or not text:
            return text
        self._reserved.update(m.group(0) for m in PLACEHOLDER_RE.finditer(text))
        out, pos = [], 0
        for start, end, cls, key in self._spans(text):
            if cls not in self.classes:
                continue
            out.append(text[pos:start])
            out.append(self._placeholder(cls, key, text[start:end]))
            pos = end
        out.append(text[pos:])
        return "".join(out)

    def mask_all(self, texts: list[str]) -> list[str]:
        """Mask several texts of one request (placeholders already in ANY of them are reserved first)."""
        for t in texts:
            if isinstance(t, str):
                self._reserved.update(m.group(0) for m in PLACEHOLDER_RE.finditer(t))
        return [self.mask(t) for t in texts]

    def unmask(self, value):
        """Placeholders -> original text, in a string or recursively in dict values / lists."""
        if isinstance(value, str):
            return PLACEHOLDER_RE.sub(lambda m: self._original.get(m.group(0), m.group(0)), value)
        if isinstance(value, list):
            return [self.unmask(v) for v in value]
        if isinstance(value, tuple):
            return tuple(self.unmask(v) for v in value)
        if isinstance(value, dict):
            return {k: self.unmask(v) for k, v in value.items()}
        return value


def classes_for_policy(masking: dict, *, employee_names_allowed: bool) -> set[str]:
    """Masking classes from the policy's flags {"name", "identity_number", "phone", "account"}.
    Employee names are masked unless the call declares, and the policy allows, `employee_names`."""
    classes = {c for c in POLICY_CLASSES if masking.get(c, True)}
    if not employee_names_allowed:
        classes.add("employee_name")
    return classes
