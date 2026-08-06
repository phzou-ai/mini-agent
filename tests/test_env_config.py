from pathlib import Path

from vermay.env_config import (
    load_prefixed_env,
    parse_env_file,
)


def test_parse_env_file_filters_prefix_and_strips_quotes(tmp_path: Path):
    path = tmp_path / ".env"
    path.write_text(
        "\n".join(
            [
                "# comment",
                "VERMAY_SSH_HOST='server.example'",
                'VERMAY_SSH_USER="agent"',
                "OTHER_VALUE=ignored",
            ]
        ),
        encoding="utf-8",
    )

    assert parse_env_file(path, prefix="VERMAY_SSH_") == {
        "VERMAY_SSH_HOST": "server.example",
        "VERMAY_SSH_USER": "agent",
    }


def test_load_prefixed_env_uses_local_file_over_default(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("VERMAY_SSH_HOST", raising=False)
    (tmp_path / ".env").write_text("VERMAY_SSH_HOST=default-host\n", encoding="utf-8")
    (tmp_path / ".env.local").write_text("VERMAY_SSH_HOST=local-host\n", encoding="utf-8")

    assert load_prefixed_env("VERMAY_SSH_", root=tmp_path)["VERMAY_SSH_HOST"] == "local-host"


def test_load_prefixed_env_uses_shell_env_over_files(tmp_path: Path, monkeypatch):
    (tmp_path / ".env.local").write_text("VERMAY_SSH_HOST=local-host\n", encoding="utf-8")
    monkeypatch.setenv("VERMAY_SSH_HOST", "shell-host")

    assert load_prefixed_env("VERMAY_SSH_", root=tmp_path)["VERMAY_SSH_HOST"] == "shell-host"
