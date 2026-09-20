from __future__ import annotations

import webbrowser
from pathlib import Path

import pytest

from cpdatakit import cli


def test_ui_cli_uses_loopback_available_port_and_no_browser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    observed: dict[str, object] = {}

    def fake_run(app, *, host: str, port: int, log_level: str) -> None:
        observed.update(app=app, host=host, port=port, log_level=log_level)

    monkeypatch.setattr("uvicorn.run", fake_run)
    monkeypatch.setattr(
        webbrowser,
        "open",
        lambda url: pytest.fail(f"browser should not open in no-browser mode: {url}"),
    )

    status = cli.main(["ui", "--workspace", str(tmp_path), "--no-browser"])

    assert status == 0
    assert observed["host"] == "127.0.0.1"
    assert isinstance(observed["port"], int) and observed["port"] > 0
    assert observed["log_level"] == "info"
    assert observed["app"].state.workspace == tmp_path.resolve()
    assert f"http://127.0.0.1:{observed['port']}" in capsys.readouterr().out


def test_ui_cli_rejects_non_loopback_host(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["ui", "--workspace", str(tmp_path), "--host", "0.0.0.0", "--no-browser"])

    assert exit_info.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


@pytest.mark.parametrize("failure", ["browser", "server"])
def test_ui_startup_failure_releases_workspace(tmp_path, monkeypatch, failure):
    from cpdatakit.web import create_app

    def reject(*args, **kwargs):
        raise OSError("startup failed")

    monkeypatch.setattr(webbrowser, "open", reject if failure == "browser" else lambda url: True)
    monkeypatch.setattr("uvicorn.run", reject)
    with pytest.raises(OSError, match="startup failed"):
        cli.main(["ui", "--workspace", str(tmp_path)])
    restarted = create_app(tmp_path)
    restarted.state.close()
