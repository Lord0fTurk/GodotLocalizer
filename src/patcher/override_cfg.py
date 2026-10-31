"""Line-ending agnostic editing of Godot ``override.cfg`` files.

Only the ``GodotLocalizerBootstrap`` entry is ever added or removed, so settings and
autoloads that the game or other tools put in the same file stay untouched.
"""

AUTOLOAD_KEY = "GodotLocalizerBootstrap"
AUTOLOAD_SECTIONS = ("autoload", "autoload_prepend")


def _split(content: str) -> list[str]:
    return content.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def _join(lines: list[str]) -> str:
    text = "\n".join(lines).strip("\n")
    return text + "\n" if text.strip() else ""


def _is_header(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("[") and stripped.endswith("]")


def _drop_empty_sections(lines: list[str]) -> list[str]:
    """Removes autoload section headers that no longer hold any entry."""
    result: list[str] = []
    for index, line in enumerate(lines):
        if line.strip() in {f"[{name}]" for name in AUTOLOAD_SECTIONS}:
            body = []
            for follower in lines[index + 1:]:
                if _is_header(follower):
                    break
                body.append(follower)
            if not any(item.strip() for item in body):
                continue
        result.append(line)
    return result


def remove_autoload(content: str) -> str:
    """Returns ``content`` without the GodotLocalizer autoload entry."""
    lines = [ln for ln in _split(content) if not ln.strip().startswith(f"{AUTOLOAD_KEY}=")]
    return _join(_drop_empty_sections(lines))


def _insert_entry(lines: list[str], section: str, entry: str) -> None:
    header = f"[{section}]"
    for index, line in enumerate(lines):
        if line.strip() == header:
            lines.insert(index + 1, entry)
            return
    while lines and not lines[-1].strip():
        lines.pop()
    if lines:
        lines.append("")
    lines.extend([header, entry])


def add_autoload(content: str, script_res_path: str, prepend: bool) -> str:
    """Registers the bootstrap autoload, replacing any previous GodotLocalizer entry.

    With ``prepend`` the entry is also listed under ``[autoload_prepend]`` so engines that
    support it start the bootstrap before the game's own autoloads; engines that do not
    know the section simply ignore it and use the regular ``[autoload]`` entry.
    """
    lines = _split(remove_autoload(content))
    entry = f'{AUTOLOAD_KEY}="*{script_res_path}"'
    if prepend:
        _insert_entry(lines, "autoload_prepend", entry)
    _insert_entry(lines, "autoload", entry)
    return _join(lines)
