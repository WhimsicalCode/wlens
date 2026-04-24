"""Preserve team-authored notes across regeneration.

Everything below the marker line in an existing file is kept verbatim when
the file is regenerated. Everything above is overwritten from source.
"""

from __future__ import annotations

from pathlib import Path

MANUAL_MARKER = "<!-- ↓ MANUAL NOTES BELOW (PRESERVED ACROSS REGENERATION) ↓ -->"
MANUAL_PLACEHOLDER = f"\n{MANUAL_MARKER}\n"


def read_manual_block(existing_path: Path) -> str:
    """Return the marker + everything below it, or the empty placeholder."""
    if not existing_path.exists():
        return MANUAL_PLACEHOLDER
    text = existing_path.read_text()
    idx = text.find(MANUAL_MARKER)
    if idx == -1:
        return MANUAL_PLACEHOLDER
    return "\n" + text[idx:].rstrip() + "\n"
