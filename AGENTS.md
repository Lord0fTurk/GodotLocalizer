# AGENTS.md — GodotLocalizer Development & Architecture Guide

## 1. Project Overview & Mission
`GodotLocalizer` is a next-generation, autonomous desktop localization, text harvesting, and non-destructive patching suite specifically engineered for compiled and exported games built with the **Godot Engine** (Godot 3.x and Godot 4.x). 

Following the proven lineage of sister projects `RenLocalizer` and `UnityLocalizer`, `GodotLocalizer` provides reverse engineers, community translators, and modders with automated workflows to:
- Dissect `.pck` packages and embedded executables (`Oyun.exe`).
- Harvest strings across official translations, binary `.scn` scenes, GDScript bytecode (`.gdc`), and narrative plugins (Dialogic, Dialogue Manager).
- Deliver zero-touch static patches via **PCK Overlay** (`override.cfg`) or clean **PCK Repacking**.
- Dynamically resolve font glyph missing (tofu / `□`) errors across complex character sets (Turkish, Cyrillic, CJK, Arabic).
- Provide an enterprise-grade, multi-theme desktop GUI (PyQt6 / QML) supporting global i18n out of the box.

---

## 2. Technology Stack & Environment
- **Core Runtime:** Python 3.10+ (64-bit)
- **GUI Framework:** PyQt6 (with QtQuick / QML and Python Bridge)
- **Design System:** Obsidian / Daylight / Cyber Neon 3-Theme Matrix conforming to `DESIGN.md` (Accent Presets A-D)
- **Binary & Stream Processing:** Python standard library (`struct`, `io`, `hashlib`, `bytearray`, `mmap`, `zlib`) + optional external helper backend (`GDRE Tools` / `gdsdecomp` CLI)
- **Operating System Target:** Windows (Primary host), cross-platform compatible architecture (Linux / SteamOS / macOS)
- **Shell / Terminal Operations:** PowerShell (Native Windows execution; avoid bash-isms like `&&`, `export`, `rm -rf`)

---

## 3. Directory Layout & Module Structure
```
GodotLocalizer/
├── AGENTS.md                   # This project governance and architecture file
├── CHANGELOG.md                # Version history and release notes
├── ANALYSIS.md                 # Godot-format analysis and improvement roadmap
├── DESIGN.md                   # Multi-theme desktop design specification
├── pyproject.toml              # Project metadata, ruff and pytest configuration
├── requirements.txt            # Python dependencies (PyQt6, cryptography, fonttools)
├── run.py                      # Application bootstrap & entry point (GUI, or CLI: run.py translate ...)
├── build.py                    # PyInstaller build (+ frozen --self-check); used by release.yml
├── .github/workflows/ci.yml    # Lint + test matrix
├── .github/workflows/release.yml # Tag-triggered Windows/Linux builds attached to a GitHub release
├── tests/                      # pytest suite (byte-level fixtures built from the Godot spec)
└── src/
    ├── version.py              # Single source of the app version (pyproject reads it)
    ├── cli.py                  # Command-line interface (inspect, translate, status, export, install, revert)
    ├── core/                   # Low-level engines
    │   ├── pck/                # PCK reader/writer, crypto (AES-256-CFB), format constants
    │   ├── binary/             # RSRC reader, smaz, OptimizedTranslation rebuild
    │   ├── langpack.py         # external/mods/lang_* language packs (format learned from the game's own packs)
    │   ├── font_engine/        # Glyph coverage (cmap) checks
    │   ├── pipeline/           # orchestrator, harvest routing, translation runner, review session
    │   └── translators/        # Google, Bing, DeepL, LLM and LibreTranslate engines
    ├── harvesters/             # Text extractors (scene, binary scene, script, translation tables)
    ├── parsers/                # Dialogue Manager and Dialogic parsers + rewriters
    ├── patcher/                # overlay_patcher, override_cfg, repack_patcher, bootstrap_script
    ├── backend/                # QObject backends exposed to QML (app, editor, settings, locale)
    ├── gui/                    # bridge, i18n JSON files, QML views and components
    └── utils/                  # config, logger, paths, safe file writes
```

---

## 4. Architectural Standards & Coding Rules
1. **Single Responsibility Principle (SRP):** Functions must not exceed 50 lines without a modular split. Classes manage a single discrete domain.
2. **Type Safety & Documentation:** Explicit Python type hints (`from typing import Optional, List, Dict, BinaryIO, Tuple`) on all public signatures. Clean, concise docstrings.
3. **Zero-Breakage UI:** Adhere strictly to `DESIGN.md`. Do not break QML theme aliases. Do not hardcode `#FFFFFF` or `#000000`. Use `implicitWidth + padding` for i18n text flexibility.
4. **Binary Parsing Performance:** Leverage `io.BytesIO`, `memoryview`, and `struct.unpack_from` to minimize RAM copying when reading multi-gigabyte PCK files.
5. **Clean Patching Philosophy:** Default to non-destructive AOT methods (Method A: PCK Overlay + `override.cfg`). Never modify original game executables unless explicitly commanded.
