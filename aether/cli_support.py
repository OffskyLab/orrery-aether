"""Pure helpers for the `aether` CLI (spec §介面合約 3).

These functions have NO I/O side effects — they take data in and return data out
— so the CLI's `mcp setup` / `client setup` logic is unit-testable without Redis,
disk, or a real Claude Code. File reads/writes live in `cli.py`.
"""
from __future__ import annotations

import copy
import os
import re
from typing import Optional

import yaml


def sanitize_id(name: str) -> str:
    """A project id derived from a directory name: lowercase, non [a-z0-9_] → _,
    collapse repeats, strip leading/trailing _."""
    s = re.sub(r"[^a-z0-9_]+", "_", name.lower())
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "project"


def merge_mcp_config(existing: dict, server_name: str, server_entry: dict) -> dict:
    """Idempotently upsert one MCP server into a `.mcp.json` dict, preserving all
    other servers and any extra keys (e.g. `_comment`). Returns a NEW dict; does
    not mutate the input. Raises ValueError on a wrong-shaped input (so the caller
    refuses to write rather than clobber the file)."""
    if not isinstance(existing, dict):
        raise ValueError("`.mcp.json` root must be a JSON object")
    servers = existing.get("mcpServers")
    if servers is not None and not isinstance(servers, dict):
        raise ValueError("`.mcp.json` `mcpServers` must be an object")
    out = copy.deepcopy(existing)
    out.setdefault("mcpServers", {})
    out["mcpServers"][server_name] = server_entry
    return out


def build_mcp_server_entry(python_exe: str, server_script_abs: str, identity: str,
                           redis_db: int = 0, constellation_abs: Optional[str] = None,
                           redis_host: Optional[str] = None,
                           redis_port: Optional[int] = None,
                           extra_env: Optional[dict] = None) -> dict:
    """Build the `.mcp.json` entry for the Aether MCP server. `python_exe` MUST be
    an absolute python path (caller passes sys.executable) so Claude Code launches
    it in an interpreter that actually has the deps (spec cross-cutting (a))."""
    env = {"AETHER_REDIS_DB": str(redis_db)}
    if constellation_abs:
        env["AETHER_CONSTELLATION"] = constellation_abs
    if redis_host:
        env["AETHER_REDIS_HOST"] = redis_host
    if redis_port:
        env["AETHER_REDIS_PORT"] = str(redis_port)
    if extra_env:
        env.update(extra_env)
    return {
        "type": "stdio",
        "command": python_exe,
        "args": [server_script_abs, "--identity", identity],
        "env": env,
    }


def append_constellation_body(existing_text: str, body_id: str, fields: dict) -> tuple:
    """Append-only registration of a Body into constellation.yaml text (spec C7).

    Returns ``(new_text, action)`` where action ∈ {"appended", "exists"}.
    Preserves the existing file VERBATIM (header comments included) — it only
    appends. The new body block is rendered via ``yaml.safe_dump`` so a
    description containing quotes/colons/unicode is escaped correctly and can't
    break ``load_constellation`` (spec: do NOT hand-template YAML)."""
    try:
        data = yaml.safe_load(existing_text) or {}
    except yaml.YAMLError:
        data = {}
    bodies = (data.get("bodies") or {}) if isinstance(data, dict) else {}
    if body_id in bodies:
        return existing_text, "exists"

    # Render just this body, then indent it under `bodies:` (2 spaces).
    block = yaml.safe_dump({body_id: fields}, allow_unicode=True, sort_keys=False,
                           default_flow_style=False)
    indented = "".join(("  " + line if line.strip() else line)
                       for line in block.splitlines(keepends=True))

    text = existing_text
    if "bodies:" not in text:
        # No bodies section yet — create one.
        sep = "" if text.endswith("\n") or text == "" else "\n"
        new_text = f"{text}{sep}bodies:\n{indented}"
    else:
        sep = "" if text.endswith("\n") else "\n"
        new_text = f"{text}{sep}{indented}"
    if not new_text.endswith("\n"):
        new_text += "\n"
    return new_text, "appended"


# --- metadata inference (suggestions only; never silent — spec C6) ----------
_LANG_HINTS = [
    ("Package.swift", ["swift"]),
    ("pyproject.toml", ["python"]),
    ("setup.py", ["python"]),
    ("Cargo.toml", ["rust"]),
    ("go.mod", ["go"]),
]


def _first_paragraph(text: str) -> str:
    for block in re.split(r"\n\s*\n", text):
        # skip pure-heading / badge / html-comment lines
        cleaned = "\n".join(l for l in block.splitlines()
                            if l.strip() and not l.lstrip().startswith(("#", "<!--", "![", "[")))
        cleaned = cleaned.strip()
        if cleaned:
            return re.sub(r"\s+", " ", cleaned)[:300]
    return ""


def infer_metadata(project_dir: str) -> dict:
    """Suggest {description, capabilities} for a project. Returns empty values
    when nothing is found — NEVER invents (spec C6). Caller must let the user
    confirm/edit before writing."""
    description = ""
    for fname in ("CLAUDE.md", "README.md", "README.zh-TW.md"):
        path = os.path.join(project_dir, fname)
        if os.path.isfile(path):
            try:
                description = _first_paragraph(open(path, encoding="utf-8", errors="replace").read())
            except OSError:
                description = ""
            if description:
                break

    capabilities: list = []
    for fname, caps in _LANG_HINTS:
        if os.path.isfile(os.path.join(project_dir, fname)):
            capabilities = list(caps)
            break
    else:
        pkg = os.path.join(project_dir, "package.json")
        if os.path.isfile(pkg):
            try:
                import json
                data = json.load(open(pkg, encoding="utf-8"))
                deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
                capabilities = ["typescript"] if "typescript" in deps else ["javascript"]
                if "react" in deps:
                    capabilities.append("react")
            except (OSError, ValueError):
                capabilities = ["javascript"]

    return {"description": description, "capabilities": capabilities}
