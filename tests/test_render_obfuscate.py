"""Value-level PII obfuscation for rendered sample-row cells."""

from __future__ import annotations

import pytest

from wlens.render.obfuscate import DEFAULT_RULES, compile_rules, obfuscate


# ─── Defaults: positive matches ─────────────────────────────────────────────


def test_email_default():
    assert obfuscate("contact foo@bar.com today") == "contact <email> today"
    assert obfuscate("foo.bar+baz@example.co.uk") == "<email>"


def test_uuid_passes_through():
    # UUIDs are not obfuscated by default — they're warehouse primary keys.
    s = "row id 550e8400-e29b-41d4-a716-446655440000 done"
    assert obfuscate(s) == s


def test_url_passes_through():
    # URLs are not obfuscated by default — not every URL is PII.
    s = "see https://example.com/path?x=1"
    assert obfuscate(s) == s


def test_ip_default():
    assert obfuscate("from 192.168.0.1 yesterday") == "from <ip> yesterday"


def test_phone_default_us_styles():
    assert obfuscate("call (555) 123-4567") == "call <phone>"
    assert obfuscate("call 555-123-4567") == "call <phone>"
    assert obfuscate("call 555.123.4567") == "call <phone>"


def test_phone_default_intl():
    assert obfuscate("ring +1 555-123-4567") == "ring <phone>"
    assert obfuscate("UK: +44 20 7946 0958") == "UK: <phone>"


# ─── Defaults: known-tricky negatives ───────────────────────────────────────


def test_email_local_only_not_matched():
    # No domain → not an email.
    assert obfuscate("alice@") == "alice@"


def test_version_string_not_phone():
    assert obfuscate("upgraded to v2.1.0 today") == "upgraded to v2.1.0 today"


@pytest.mark.parametrize(
    "date",
    [
        "2024-01-15",   # ISO YYYY-MM-DD
        "15-01-2024",   # DD-MM-YYYY
        "01-15-2024",   # MM-DD-YYYY
        "15.01.2024",   # DD.MM.YYYY
        "01.15.2024",   # MM.DD.YYYY
        "2024.01.15",   # YYYY.MM.DD
        "01/15/2024",   # slash-separated (separator not even in our class)
    ],
)
def test_date_formats_not_phone(date):
    assert obfuscate(f"released {date}") == f"released {date}"


def test_bare_digits_not_phone():
    # No separators → not phone.
    assert obfuscate("order 5551234567") == "order 5551234567"


# ─── Rule precedence ────────────────────────────────────────────────────────


def test_ip_takes_precedence_over_phone():
    # `1.2.3.4` is a valid IP shape; should render as <ip>, not <phone>.
    assert obfuscate("from 1.2.3.4") == "from <ip>"


def test_multi_pattern_in_one_string():
    s = "Contact foo@bar.com from 10.0.0.5 at +44 20 7946 0958"
    out = obfuscate(s)
    assert "<email>" in out
    assert "<ip>" in out
    assert "<phone>" in out
    assert "@" not in out
    assert "10.0.0.5" not in out


# ─── Extras via compile_rules ───────────────────────────────────────────────


def test_compile_rules_returns_defaults_when_no_extras():
    assert compile_rules(None) == DEFAULT_RULES
    assert compile_rules([]) == DEFAULT_RULES


def test_compile_rules_appends_extras():
    rules = compile_rules(
        [{"pattern": r"\bemp-\d{6}\b", "replacement": "<employee_id>"}]
    )
    assert len(rules) == len(DEFAULT_RULES) + 1
    assert rules[-1].replacement == "<employee_id>"
    out = obfuscate("hired emp-123456 last week", rules=rules)
    assert out == "hired <employee_id> last week"


def test_compile_rules_rejects_missing_keys():
    with pytest.raises(ValueError, match="pattern.*replacement"):
        compile_rules([{"pattern": r"\d+"}])
    with pytest.raises(ValueError, match="pattern.*replacement"):
        compile_rules([{"replacement": "<x>"}])
