"""CLI dispatcher + mcp/client setup tests (spec §驗收 Agent 必做 #5, #8)."""
import json
import os
import sys

import pytest
import yaml

from aether import cli


def test_routing_dispatches_to_handlers():
    p = cli.build_parser()
    assert p.parse_args(["who"]).func is cli.cmd_who
    assert p.parse_args(["server", "status"]).func is cli.cmd_server_status
    assert p.parse_args(["mcp", "setup"]).func is cli.cmd_mcp_setup
    assert p.parse_args(["client", "setup"]).func is cli.cmd_client_setup
    assert p.parse_args(["install-shim"]).func is cli.cmd_install_shim


def test_mcp_setup_writes_stable_identity_and_merges(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # an existing .mcp.json with another server must be preserved
    (tmp_path / ".mcp.json").write_text(json.dumps(
        {"mcpServers": {"event-storming": {"command": "node"}}, "_comment": "keep"}))

    rc = cli.main(["mcp", "setup", "--id", "MyProj"])
    assert rc == 0
    data = json.load(open(tmp_path / ".mcp.json"))
    assert data["mcpServers"]["event-storming"] == {"command": "node"}   # preserved
    assert data["_comment"] == "keep"
    aether = data["mcpServers"]["aether"]
    assert aether["args"][-1] == "myproj-mcp"                            # stable <project>-mcp, not random
    assert aether["command"] == sys.executable                          # absolute python (not bare python3)
    assert aether["args"][0].endswith("mcp_server.py")
    assert aether["env"]["AETHER_REDIS_DB"] == "0"


def test_mcp_setup_local_scope_writes_separate_gitignored_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rc = cli.main(["mcp", "setup", "--id", "p", "--scope", "local"])
    assert rc == 0
    assert (tmp_path / ".mcp.local.json").exists()       # local scope → gitignored name
    assert not (tmp_path / ".mcp.json").exists()


def test_mcp_setup_rejects_invalid_json_without_clobbering(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".mcp.json").write_text("{ not json")
    rc = cli.main(["mcp", "setup", "--id", "p"])
    assert rc == 1
    assert (tmp_path / ".mcp.json").read_text() == "{ not json"   # unchanged


def test_client_setup_appends_body(tmp_path, monkeypatch, r):
    # point the CLI at a throwaway constellation + the test Redis db
    const = tmp_path / "constellation.yaml"
    const.write_text("# header comment — keep\nbodies:\n")
    monkeypatch.setattr(cli, "CONSTELLATION_PATH", str(const))
    monkeypatch.chdir(tmp_path)

    rc = cli.main(["client", "setup", "--id", "myproj", "--description", "test proj",
                   "--capability", "x", "--capability", "y", "--yes",
                   "--redis-db", "15"])
    assert rc == 0
    text = const.read_text()
    assert "# header comment — keep" in text                 # header preserved
    parsed = yaml.safe_load(text)
    assert "myproj" in parsed["bodies"]
    assert parsed["bodies"]["myproj"]["description"] == "test proj"
    assert parsed["bodies"]["myproj"]["capabilities"] == ["x", "y"]
    assert parsed["bodies"]["myproj"]["working_dir"] == str(tmp_path)


@pytest.mark.parametrize("cmd, module", [
    (["send", "--to", "genesis", "--from", "x", "--text", "hi"], "aether.send_message"),
    (["observatory", "--redis-db", "9", "genesis"], "aether.run_observatory"),
    (["mcp", "serve", "--identity", "p-mcp"], "aether.mcp_server"),
])
def test_passthrough_forwards_leading_options(cmd, module, monkeypatch):
    # Regression for the alias bug: argparse.REMAINDER drops a forwarded arg that
    # starts with an option (bpo-17050). The pre-argparse passthrough must hand
    # the underlying script's main() everything after the command, verbatim.
    import importlib
    mod = importlib.import_module(module)
    captured = {}

    def fake_main(argv=None):
        captured["argv"] = argv
        return 0

    monkeypatch.setattr(mod, "main", fake_main)
    n = 2 if cmd[:2] == ["mcp", "serve"] else 1
    assert cli.main(cmd) == 0
    assert captured["argv"] == cmd[n:]          # leading --option survived


def test_install_shim_writes_executable(tmp_path):
    rc = cli.main(["install-shim", "--dir", str(tmp_path / "bin")])
    assert rc == 0
    shim = tmp_path / "bin" / "aether"
    assert shim.exists() and os.access(shim, os.X_OK)
    assert sys.executable in shim.read_text() and "cli.py" in shim.read_text()
