"""Harvesting base classes and extracted string data containers."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class StringSourceType(str, Enum):
    """Categorizes the origin of a harvested string."""
    TRANSLATION = "translation"  # .csv, .po, .translation
    SCENE_UI = "scene_ui"        # .tscn, .scn Label/Button properties
    SCRIPT_CODE = "script_code"  # .gd / .gdc constant literals
    NARRATIVE = "narrative"      # Dialogic, Dialogue Manager


@dataclass(slots=True)
class HarvestedEntry:
    """Represents a discrete translatable text unit harvested from game assets.

    Attributes:
        key: Unique identifier or resource location (e.g. 'res://main.tscn::Label/text').
        source_text: The original language text extracted from the game.
        source_file: Virtual Godot path of origin (e.g. 'res://scenes/hud.tscn').
        source_type: Category of the source asset (scene, translation, dialogue).
        context: Optional extra context (e.g. node class name, character speaker).
        line_number: Optional line number for script/text based formats.
        metadata: Arbitrary extra key-value pairs needed for reconstruction.
        translation: Localized replacement text (empty until translated).
    """

    key: str
    source_text: str
    source_file: str
    source_type: StringSourceType
    context: str | None = None
    line_number: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    translation: str = ""
