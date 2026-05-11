"""Value-level PII obfuscation for rendered sample-row cells.

Complement to render/pii.py (which redacts whole columns by name). This
module runs after column-level redaction and scrubs PII shapes embedded
inside otherwise-innocuous values — e.g. an email pasted into a workspace
name, or a UUID inside a description field.

Defaults cover only the shapes that are always problematic: email,
phone, IP. URLs and UUIDs are deliberately excluded — UUIDs are warehouse
primary keys everywhere, URLs are not always PII, and scrubbing either
costs more in readability than it gains. Projects can append extra
patterns (including UUID or URL rules) via `output.obfuscate` in
wlens.yml.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ObfuscationRule:
    name: str
    pattern: re.Pattern[str]
    replacement: str


# IP before phone: `1.2.3.4` should render as `<ip>`, not be partially matched.
DEFAULT_RULES: tuple[ObfuscationRule, ...] = (
    ObfuscationRule(
        name="email",
        pattern=re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),
        replacement="<email>",
    ),
    ObfuscationRule(
        name="ip",
        pattern=re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
        replacement="<ip>",
    ),
    # Phone is split into two rules so neither matches DD-MM-YYYY / MM-DD-YYYY
    # date strings. Real phones have ≥10 digits; common date formats have ≤8.
    ObfuscationRule(
        # International with explicit +country-code: `+1 555-123-4567`,
        # `+44 20 7946 0958`. The leading `+` is the disambiguator.
        name="phone_intl",
        pattern=re.compile(
            r"\+\d{1,3}[\s.-]?\d{1,4}[\s.-]?\d{2,9}[\s.-]?\d{2,9}"
        ),
        replacement="<phone>",
    ),
    ObfuscationRule(
        # North American 3-3-4 shape: `(555) 123-4567`, `555-123-4567`,
        # `555.123.4567`. The strict 3-3-4 grouping rules out date formats,
        # whose leading day/month group is 1-2 digits.
        name="phone_na",
        pattern=re.compile(r"\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}"),
        replacement="<phone>",
    ),
)


def compile_rules(extras: list[dict] | None) -> tuple[ObfuscationRule, ...]:
    """Return DEFAULT_RULES plus any user-supplied extras (appended)."""
    rules: list[ObfuscationRule] = list(DEFAULT_RULES)
    for i, extra in enumerate(extras or []):
        if not isinstance(extra, dict) or "pattern" not in extra or "replacement" not in extra:
            raise ValueError(
                f"output.obfuscate[{i}] must have `pattern` and `replacement`. Got: {extra!r}"
            )
        rules.append(
            ObfuscationRule(
                name=str(extra.get("name") or f"extra_{i}"),
                pattern=re.compile(str(extra["pattern"])),
                replacement=str(extra["replacement"]),
            )
        )
    return tuple(rules)


def obfuscate(value: str, rules: tuple[ObfuscationRule, ...] = DEFAULT_RULES) -> str:
    """Apply rules in order; return the scrubbed string."""
    for rule in rules:
        value = rule.pattern.sub(rule.replacement, value)
    return value
