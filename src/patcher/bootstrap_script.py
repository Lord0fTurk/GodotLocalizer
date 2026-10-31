"""Generates zero-touch Autoload runtime bootstrap scripts for Godot 3 and 4."""

import json
import re

DEFAULT_PATCH_PCK_NAME = "godotlocalizer_patch.pck"
TRANSLATIONS_RES_PATH = "res://godotlocalizer/translations.json"


def godot_locale(code: str) -> str:
    """Normalizes 'pt-BR' / 'zh-cn' style codes to Godot's ``language_Script_COUNTRY`` form."""
    parts = [p for p in re.split(r"[-_]", code.strip()) if p]
    cleaned: list[str] = []
    for index, part in enumerate(parts):
        part = re.sub(r"[^A-Za-z0-9]", "", part)
        if not part:
            continue
        if index == 0:
            cleaned.append(part.lower())
        elif len(part) == 4 and part.isalpha():
            cleaned.append(part.title())
        elif len(part) == 2 and part.isalpha():
            cleaned.append(part.upper())
        else:
            cleaned.append(part)
    return "_".join(cleaned) or "en"


def build_translations_payload(
    locale: str, messages: dict[str, str], optimized: list[dict[str, object]] | None = None
) -> bytes:
    """Serializes the runtime translation table that the bootstrap script registers."""
    payload: dict[str, object] = {"locale": godot_locale(locale), "messages": messages}
    if optimized:
        payload["optimized"] = optimized
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


class BootstrapScriptGenerator:
    """Creates GDScript autoload hooks for mounting patch PCKs and injecting fonts."""

    @classmethod
    def generate_godot4_bootstrap(
        cls,
        pck_file_name: str = DEFAULT_PATCH_PCK_NAME,
        target_locale: str = "tr",
        font_res_path: str | None = "res://patch_assets/fonts/LocalizedFont.ttf",
    ) -> str:
        """Generates a Godot 4.x compatible autoload script."""
        locale = godot_locale(target_locale)
        font_block = ""
        if font_res_path:
            font_block = f"""
    _install_fallback_font("{font_res_path}")
"""

        node_added_block = ""
        if font_res_path:
            node_added_block = """
func _install_fallback_font(font_path: String) -> void:
    # The font ships as a raw file without .import metadata, so it is built from its bytes.
    if not FileAccess.file_exists(font_path):
        return
    var custom_font := FontFile.new()
    custom_font.data = FileAccess.get_file_as_bytes(font_path)
    var def_theme := ThemeDB.get_project_theme()
    if def_theme and def_theme.has_default_font():
        _add_fallback(def_theme.default_font, custom_font)
    # Extend the engine's own fallback font instead of replacing it, so Latin text keeps its look.
    _add_fallback(ThemeDB.fallback_font, custom_font)
    get_tree().node_added.connect(_on_node_added.bind(custom_font))

func _add_fallback(font: Font, fallback: Font) -> void:
    if font == null or font == fallback or fallback in font.fallbacks:
        return
    var updated: Array[Font] = font.fallbacks.duplicate()
    updated.append(fallback)
    font.fallbacks = updated

func _on_node_added(node: Node, fallback_font: Font) -> void:
    if node is Control:
        _add_fallback(node.get_theme_font("font"), fallback_font)
"""
        return f"""# GodotLocalizer Runtime Bootstrap (Godot 4.x)
# Generated dynamically to mount localization overlay without altering game binaries.
extends Node

const TRANSLATIONS_PATH: String = "{TRANSLATIONS_RES_PATH}"

func _enter_tree() -> void:
    var exe_dir: String = OS.get_executable_path().get_base_dir()
    var patch_path: String = exe_dir.path_join("{pck_file_name}")

    if FileAccess.file_exists(patch_path):
        ProjectSettings.load_resource_pack(patch_path, true)

    _install_translations()
    TranslationServer.set_locale("{locale}")
{font_block}
func _install_translations() -> void:
    if not FileAccess.file_exists(TRANSLATIONS_PATH):
        return
    var data = JSON.parse_string(FileAccess.get_file_as_string(TRANSLATIONS_PATH))
    if not (data is Dictionary):
        return
    var table := Translation.new()
    table.locale = str(data.get("locale", ""))
    var messages: Dictionary = data.get("messages", {{}})
    for key in messages:
        table.add_message(key, messages[key])
    TranslationServer.add_translation(table)
    for item in data.get("optimized", []):
        var packed := OptimizedTranslation.new()
        packed.locale = str(item.get("locale", data.get("locale", "")))
        packed.set("hash_table", PackedInt32Array(item["hash_table"]))
        packed.set("bucket_table", PackedInt32Array(item["bucket_table"]))
        packed.set("strings", Marshalls.base64_to_raw(item["strings"]))
        TranslationServer.add_translation(packed)
{node_added_block}
""".strip()

    @classmethod
    def generate_godot3_bootstrap(
        cls,
        pck_file_name: str = DEFAULT_PATCH_PCK_NAME,
        target_locale: str = "tr",
        font_res_path: str | None = "res://patch_assets/fonts/LocalizedFont.ttf",
    ) -> str:
        """Generates a Godot 3.x compatible autoload script."""
        locale = godot_locale(target_locale)
        font_block = ""
        font_funcs = ""
        if font_res_path:
            font_block = f"""
    _install_fallback_font("{font_res_path}")
"""
            font_funcs = """
func _install_fallback_font(font_path):
    var f = File.new()
    if not f.file_exists(font_path):
        return
    var data = DynamicFontData.new()
    data.font_path = font_path
    get_tree().connect("node_added", self, "_on_node_added", [data])

func _on_node_added(node, fallback_data):
    if node is Control:
        var font = node.get_font("font")
        if font is DynamicFont:
            for i in range(font.get_fallback_count()):
                if font.get_fallback(i) == fallback_data:
                    return
            font.add_fallback(fallback_data)
"""
        return f"""# GodotLocalizer Runtime Bootstrap (Godot 3.x)
extends Node

const TRANSLATIONS_PATH = "{TRANSLATIONS_RES_PATH}"

func _enter_tree():
    var exe_dir = OS.get_executable_path().get_base_dir()
    var patch_path = exe_dir + "/{pck_file_name}"

    var f = File.new()
    if f.file_exists(patch_path):
        ProjectSettings.load_resource_pack(patch_path, true)

    _install_translations()
    TranslationServer.set_locale("{locale}")
{font_block}
func _install_translations():
    var f = File.new()
    if not f.file_exists(TRANSLATIONS_PATH):
        return
    f.open(TRANSLATIONS_PATH, File.READ)
    var parsed = JSON.parse(f.get_as_text())
    f.close()
    if parsed.error != OK or typeof(parsed.result) != TYPE_DICTIONARY:
        return
    var data = parsed.result
    var table = Translation.new()
    table.locale = str(data.get("locale", ""))
    var messages = data.get("messages", {{}})
    for key in messages:
        table.add_message(key, messages[key])
    TranslationServer.add_translation(table)
    for item in data.get("optimized", []):
        var packed = PHashTranslation.new()
        packed.locale = str(item.get("locale", data.get("locale", "")))
        packed.set("hash_table", PoolIntArray(item["hash_table"]))
        packed.set("bucket_table", PoolIntArray(item["bucket_table"]))
        packed.set("strings", Marshalls.base64_to_raw(item["strings"]))
        TranslationServer.add_translation(packed)
{font_funcs}""".strip()
