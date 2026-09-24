"""默认目录解析：env 优先，cwd 子目录不可写时回落用户可写目录。

审计与执行账本共用同一策略（LOCALAPPDATA\\aspen-mcp\\<子目录>），
避免两处实现漂移。
"""
from __future__ import annotations

import os


def default_directory(*, env_var: str, cwd_subdir: str, fallback_subdir: str) -> str:
    """env 优先；cwd 子目录不可写时回落 LOCALAPPDATA\\aspen-mcp\\<fallback>。"""
    override = os.environ.get(env_var)
    if override:
        return override
    cwd_dir = os.path.join(os.getcwd(), cwd_subdir)
    try:
        os.makedirs(cwd_dir, exist_ok=True)
        return cwd_dir
    except OSError:
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        fallback = os.path.join(base, "aspen-mcp", fallback_subdir)
        os.makedirs(fallback, exist_ok=True)
        return fallback