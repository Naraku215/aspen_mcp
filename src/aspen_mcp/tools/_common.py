"""Shared COM navigation helpers for tools modules.

All functions must be called ON the COM thread (i.e. inside aspen.call()).
"""

from __future__ import annotations

from typing import Any


def normalize_path(path: str) -> str:
    """统一 / 与反斜杠分隔符、去首尾分隔符并折叠重复分隔符；不改对象名。

    非字符串输入属格式错误，抛 ValueError。
    """
    if not isinstance(path, str):
        raise ValueError("path 必须是字符串（格式错误）")
    parts = [p for p in path.replace("/", "\\").split("\\") if p]
    return "\\".join(parts)


def split_path(path: str) -> list[str]:
    """规范化后切分为路径段；空路径（根）返回空列表。"""
    normalized = normalize_path(path)
    return normalized.split("\\") if normalized else []


def walk(root: Any, *parts: str) -> Any | None:
    """Navigate an IHNode tree by path segments.

    Safe to call from within an aspen.call() lambda on the COM thread.
    """
    node = root
    for p in parts:
        try:
            node = node.Elements(p)
        except Exception:
            return None
        if node is None:
            return None
    return node

def ensure_parent(tree: Any, *parts: str) -> Any | None:
    """Ensure a path exists, creating intermediate nodes as needed.

    Handles the dummy-node trick for Blocks/Streams containers:
    creating a temporary child forces Aspen to allocate the container node
    when it would otherwise refuse direct .Add().
    """
    node = tree
    for p in parts:
        try:
            n = node.Elements(p)
            if n is None:
                raise Exception("none")
            node = n
        except Exception:
            try:
                node.Elements.Add(p)
                node = node.Elements(p)
            except Exception:
                # Dummy trick for Data-level containers
                name = parts[-1] if len(parts) > 0 else p
                try:
                    if name == "Blocks":
                        node.Elements.Add("_d_!MIXER")
                        node = node.Elements("Blocks")
                        node.Elements.Remove("_d_")
                        return node
                    elif name == "Streams":
                        node.Elements.Add("_d_")
                        node = node.Elements("Streams")
                        node.Elements.Remove("_d_")
                        return node
                except Exception:
                    pass
                return None
    return node


