"""Unit tests for the pure CLI helpers (spec §驗收 Agent 必做 #4)."""
import pytest
import yaml

from aether.cli_support import (append_constellation_body, build_mcp_server_entry,
                                infer_metadata, merge_mcp_config, sanitize_id)


def test_sanitize_id():
    assert sanitize_id("EventStormingTool") == "eventstormingtool"
    assert sanitize_id("my-cool.project") == "my_cool_project"
    assert sanitize_id("  Weird__Name!! ") == "weird_name"
    assert sanitize_id("") == "project"


def test_merge_mcp_config_preserves_other_servers_and_is_idempotent():
    existing = {"mcpServers": {"event-storming": {"command": "node"}},
                "_comment": "keep me"}
    entry = {"type": "stdio", "command": "/abs/python", "args": ["s.py"]}
    merged = merge_mcp_config(existing, "aether", entry)
    assert merged["mcpServers"]["event-storming"] == {"command": "node"}  # preserved
    assert merged["mcpServers"]["aether"] == entry
    assert merged["_comment"] == "keep me"                                # extra key preserved
    assert existing == {"mcpServers": {"event-storming": {"command": "node"}}, "_comment": "keep me"}  # not mutated
    # idempotent
    assert merge_mcp_config(merged, "aether", entry) == merged


def test_merge_mcp_config_creates_servers_when_missing():
    assert merge_mcp_config({}, "aether", {"x": 1}) == {"mcpServers": {"aether": {"x": 1}}}


def test_merge_mcp_config_rejects_wrong_shape():
    with pytest.raises(ValueError):
        merge_mcp_config({"mcpServers": []}, "aether", {})   # list, not object
    with pytest.raises(ValueError):
        merge_mcp_config([], "aether", {})                   # root not object


def test_build_mcp_server_entry_uses_absolute_python_and_env():
    e = build_mcp_server_entry("/usr/bin/python3", "/abs/mcp_server.py", "proj-mcp",
                               redis_db=2, constellation_abs="/abs/c.yaml")
    assert e["command"] == "/usr/bin/python3"               # absolute, not bare python3
    assert e["args"] == ["/abs/mcp_server.py", "--identity", "proj-mcp"]
    assert e["env"]["AETHER_REDIS_DB"] == "2"
    assert e["env"]["AETHER_CONSTELLATION"] == "/abs/c.yaml"
    assert "AETHER_REDIS_HOST" not in e["env"]              # default host omitted


def test_append_constellation_body_appends_and_preserves_header():
    text = ("# constellation header — DO NOT LOSE\n"
            "# more comments\n\n"
            "bodies:\n"
            "  alpha:\n"
            "    description: \"a\"\n"
            "    capabilities: [\"x\"]\n"
            "    inbox: \"aether:inbox:alpha\"\n"
            "    working_dir: \"/p/alpha\"\n")
    fields = {"description": "b", "capabilities": ["y"],
              "inbox": "aether:inbox:beta", "working_dir": "/p/beta"}
    new_text, action = append_constellation_body(text, "beta", fields)
    assert action == "appended"
    assert "# constellation header — DO NOT LOSE" in new_text   # header verbatim
    assert "alpha:" in new_text                                 # existing body verbatim
    parsed = yaml.safe_load(new_text)
    assert set(parsed["bodies"]) == {"alpha", "beta"}           # both parse
    assert parsed["bodies"]["beta"]["description"] == "b"


def test_append_constellation_body_idempotent_when_exists():
    text = "bodies:\n  alpha:\n    description: \"a\"\n    capabilities: []\n    inbox: \"i\"\n    working_dir: \"/p\"\n"
    new_text, action = append_constellation_body(text, "alpha", {"description": "x"})
    assert action == "exists" and new_text == text


def test_append_constellation_body_escapes_special_chars():
    # A description with quotes/colons/unicode must not break load_constellation.
    nasty = 'Owns "schema": src/types/bundle.ts — 中文 & : colons'
    text = "bodies:\n  alpha:\n    description: \"a\"\n    capabilities: []\n    inbox: \"i\"\n    working_dir: \"/p\"\n"
    new_text, _ = append_constellation_body(text, "beta", {
        "description": nasty, "capabilities": ["a", "b"],
        "inbox": "aether:inbox:beta", "working_dir": "/p/beta"})
    parsed = yaml.safe_load(new_text)                           # must not raise
    assert parsed["bodies"]["beta"]["description"] == nasty     # round-trips exactly


def test_infer_metadata_swift(tmp_path):
    (tmp_path / "Package.swift").write_text("// swift\n")
    (tmp_path / "CLAUDE.md").write_text("# Title\n\nA Swift CLI that does things.\n")
    m = infer_metadata(str(tmp_path))
    assert m["capabilities"] == ["swift"]
    assert "Swift CLI" in m["description"]


def test_infer_metadata_empty_when_nothing_found(tmp_path):
    m = infer_metadata(str(tmp_path))
    assert m == {"description": "", "capabilities": []}        # never invents
