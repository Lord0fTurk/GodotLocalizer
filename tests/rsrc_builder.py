"""Test-only writer for Godot binary resources, written from the format spec (independent of the reader)."""

import struct

NAME_INDEX_BITS = 18


def s(text: str) -> bytes:
    raw = text.encode("utf-8") + b"\x00"
    return struct.pack("<I", len(raw)) + raw


def v_nil() -> bytes:
    return struct.pack("<I", 1)


def v_bool(value: bool) -> bytes:
    return struct.pack("<II", 2, int(value))


def v_int(value: int) -> bytes:
    return struct.pack("<Ii", 3, value)


def v_float(value: float) -> bytes:
    return struct.pack("<If", 4, value)


def v_str(text: str) -> bytes:
    return struct.pack("<I", 5) + s(text)


def v_vec2(x: float, y: float) -> bytes:
    return struct.pack("<Iff", 10, x, y)


def v_dict(items: list[tuple[bytes, bytes]]) -> bytes:
    return struct.pack("<II", 26, len(items)) + b"".join(k + v for k, v in items)


def v_array(items: list[bytes]) -> bytes:
    return struct.pack("<II", 30, len(items)) + b"".join(items)


def v_bytes(data: bytes) -> bytes:
    return struct.pack("<II", 31, len(data)) + data + b"\x00" * (-len(data) % 4)


def v_ints(values: list[int]) -> bytes:
    return struct.pack("<II", 32, len(values)) + struct.pack(f"<{len(values)}i", *values)


def v_strings(values: list[str]) -> bytes:
    return struct.pack("<II", 34, len(values)) + b"".join(s(v) for v in values)


def v_internal(index: int) -> bytes:
    return struct.pack("<III", 24, 2, index)


def build_rsrc(
    type_name: str,
    props: list[tuple[str, bytes]],
    ver_major: int = 4,
    extra_resources: list[tuple[str, str, list[tuple[str, bytes]]]] | None = None,
    with_magic: bool = True,
) -> bytes:
    """Resources are (path, type, props); the main resource goes last, as in real files."""
    resources = [*(extra_resources or []), ("local://main", type_name, props)]
    names: list[str] = []
    for _p, _t, plist in resources:
        for name, _v in plist:
            if name not in names:
                names.append(name)

    head = (b"RSRC" if with_magic else b"") + struct.pack("<II", 0, 0) + struct.pack("<III", ver_major, 0, 3)
    head += s(type_name) + struct.pack("<Q", 0)
    head += struct.pack("<I", 0) + b"\x00" * 44 if ver_major == 4 else b"\x00" * 56  # flags + 11 / 14 reserved
    head += struct.pack("<I", len(names)) + b"".join(s(n) for n in names)
    head += struct.pack("<I", 0)  # external resources

    bodies: list[bytes] = []
    for _path, rtype, plist in resources:
        body = s(rtype) + struct.pack("<I", len(plist))
        body += b"".join(struct.pack("<I", names.index(n)) + v for n, v in plist)
        bodies.append(body)

    table_size = 4 + sum(len(s(path)) + 8 for path, _t, _p in resources)
    offset = len(head) + table_size
    table = struct.pack("<I", len(resources))
    for (path, _t, _p), body in zip(resources, bodies):
        table += s(path) + struct.pack("<Q", offset)
        offset += len(body)
    return head + table + b"".join(bodies) + b"RSRC"


def scene_bytes(
    names: list[str],
    variants: list[bytes],
    nodes: list[int],
    node_count: int,
    ver_major: int = 4,
) -> bytes:
    bundled = v_dict([
        (v_str("names"), v_strings(names)),
        (v_str("version"), v_int(3)),
        (v_str("node_count"), v_int(node_count)),
        (v_str("nodes"), v_ints(nodes)),
        (v_str("conn_count"), v_int(0)),
        (v_str("conns"), v_ints([])),
        (v_str("variants"), v_array(variants)),
    ])
    return build_rsrc("PackedScene", [("_bundled", bundled)], ver_major=ver_major)


def menu_scene(ver_major: int = 4) -> bytes:
    """Menu(Control) -> Title(Label text+tooltip), Play(Button text, visible)."""
    names = ["Menu", "Control", "Title", "Label", "text", "tooltip_text", "Play", "Button", "visible"]
    variants = [v_str("Hello World"), v_str("The game title"), v_str("Start Game"), v_bool(True)]
    n = lambda i, index=-1: i | ((index + 1) << NAME_INDEX_BITS)  # noqa: E731
    # parent, owner, type, name, instance, prop_count, (name, value) pairs, group_count, groups
    nodes = [
        -1, -1, 1, n(0), -1, 0, 0,                      # Menu
        0, 0, 3, n(2), -1, 2, 4, 0, 5, 1, 0,            # Title: text->v0, tooltip_text->v1
        0, 0, 7, n(6), -1, 2, 4, 2, 8, 3, 1, 0,         # Play: text->v2, visible->v3, one group
    ]
    return scene_bytes(names, variants, nodes, node_count=3, ver_major=ver_major)
