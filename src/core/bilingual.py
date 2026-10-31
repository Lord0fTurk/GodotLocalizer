"""Bilingual display ("translation + original") that cannot break the game's text formatting.

Showing both languages is useful for learners and reviewers, but a naive ``{target}\\n({source})`` repeats every
placeholder. Godot formats such strings after translation (``tr("Score: %d") % points``): two ``%d`` and one value
raises an error, and a duplicated ``[color=red]`` leaves the markup unbalanced. So the original is appended only
to plain text; any string that carries a format specifier, variable or markup tag stays single-language.
"""

from __future__ import annotations

from src.core.syntax_guard import UnitySyntaxGuard

DEFAULT_TEMPLATE = "{target}\\n({source})"  # a typed "\n" is a line break: the setting is a one-line field


def normalize_template(template: str) -> str:
    """The user's template if it contains both fields, else the default."""
    chosen = template if "{target}" in template and "{source}" in template else DEFAULT_TEMPLATE
    return chosen.replace("\\n", "\n")


def combine(source: str, translation: str, template: str = DEFAULT_TEMPLATE) -> str:
    """``template`` with both texts, or ``translation`` unchanged when combining would be unsafe."""
    if not translation.strip() or source.strip() == translation.strip():
        return translation
    if UnitySyntaxGuard.protect(source)[1] or UnitySyntaxGuard.protect(translation)[1]:
        return translation
    return normalize_template(template).replace("{target}", translation).replace("{source}", source.strip())
