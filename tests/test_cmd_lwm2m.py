"""Tests for dawnpy-lwm2m CLI command wiring."""

from click.testing import CliRunner

import dawnpy_lwm2m.commands.cmd_lwm2m as cmd_lwm2m_mod
from dawnpy_lwm2m.commands.cmd_lwm2m import cmd_lwm2m


def test_lwm2m_console_accepts_descriptor(monkeypatch, tmp_path):
    """The console should pass descriptor and endpoint through."""
    descriptor = tmp_path / "descriptor.yaml"
    descriptor.write_text("ios: []\n", encoding="utf-8")
    calls = []

    def _run_console(
        *,
        host="0.0.0.0",
        port=5683,
        endpoint=None,
        timeout=30.0,
        descriptor_path=None,
    ):
        calls.append((host, port, endpoint, timeout, descriptor_path))

    monkeypatch.setattr(cmd_lwm2m_mod, "run_console", _run_console)

    result = CliRunner().invoke(
        cmd_lwm2m,
        [
            str(descriptor),
            "--host",
            "127.0.0.1",
            "--port",
            "5684",
            "--endpoint",
            "ntfc-wakaama",
            "--timeout",
            "5",
        ],
    )

    assert result.exit_code == 0
    assert calls == [("127.0.0.1", 5684, "ntfc-wakaama", 5.0, str(descriptor))]


def test_lwm2m_help_documents_descriptor_console():
    """CLI help should expose descriptor-backed console usage."""
    result = CliRunner().invoke(cmd_lwm2m, ["--help"])

    assert result.exit_code == 0
    assert "DESCRIPTOR" in result.output
    assert "--endpoint" in result.output
    assert "descriptor-driven" in result.output
