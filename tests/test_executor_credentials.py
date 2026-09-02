"""Executor credential-process loading."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

from wlens.config import load_config
from wlens.executor import build_executor


def _write_config(tmp_path: Path, executor: dict) -> Path:
    config_path = tmp_path / "wlens.yml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "adapter": {"kind": "dbt"},
                "executor": {"kind": "redshift", **executor},
            },
            sort_keys=False,
        )
    )
    return config_path


def _write_process(tmp_path: Path, body: str) -> list[str]:
    process_path = tmp_path / "credentials.py"
    process_path.write_text(body)
    return [sys.executable, process_path.name]


def test_credential_process_is_lazy_and_resolves_from_config_directory(tmp_path: Path):
    command = _write_process(
        tmp_path,
        """
import json
from pathlib import Path

Path("process-called").write_text("yes")
print(json.dumps({
    "host": "warehouse.example.com",
    "port": 5439,
    "database": "analytics",
    "user": "reader",
    "password": "secret",
}))
""",
    )
    config = load_config(_write_config(tmp_path, {"credential_process": command}))

    assert config.executor.credential_process == command
    assert config.executor.host is None
    assert not (tmp_path / "process-called").exists()

    executor = build_executor(config)

    assert (tmp_path / "process-called").read_text() == "yes"
    assert executor.config.executor.host == "warehouse.example.com"
    assert executor.config.executor.port == 5439
    assert executor.config.executor.database == "analytics"
    assert executor.config.executor.user == "reader"
    assert executor.config.executor.password == "secret"
    assert executor.config.executor.credential_process == []
    assert config.executor.host is None, "resolving credentials must not mutate the loaded config"


def test_credential_process_overrides_only_returned_executor_fields(tmp_path: Path):
    command = _write_process(
        tmp_path,
        f"print({json.dumps(json.dumps({'user': 'dynamic-user', 'password': 'dynamic-pass'}))})\n",
    )
    config = load_config(
        _write_config(
            tmp_path,
            {
                "host": "warehouse.example.com",
                "port": 15439,
                "database": "analytics",
                "user": "static-user",
                "credential_process": command,
            },
        )
    )

    executor = build_executor(config)

    assert executor.config.executor.host == "warehouse.example.com"
    assert executor.config.executor.port == 15439
    assert executor.config.executor.database == "analytics"
    assert executor.config.executor.user == "dynamic-user"
    assert executor.config.executor.password == "dynamic-pass"


def test_unsupported_executor_does_not_run_credential_process(tmp_path: Path):
    command = _write_process(
        tmp_path,
        'from pathlib import Path; Path("process-called").write_text("yes")\n',
    )
    config = load_config(
        _write_config(tmp_path, {"kind": "not-a-warehouse", "credential_process": command})
    )

    with pytest.raises(ValueError, match="Unsupported executor kind"):
        build_executor(config)

    assert not (tmp_path / "process-called").exists()


def test_scalar_credential_process_is_normalized_to_one_argument(tmp_path: Path):
    config = load_config(_write_config(tmp_path, {"credential_process": "./credentials"}))

    assert config.executor.credential_process == ["./credentials"]


@pytest.mark.parametrize(
    "value",
    [[], 42, ["valid", 42]],
)
def test_invalid_credential_process_config_is_rejected(tmp_path: Path, value):
    with pytest.raises(ValueError, match="executor.credential_process"):
        load_config(_write_config(tmp_path, {"credential_process": value}))


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (
            'import sys; print("do-not-leak"); print("do-not-leak", file=sys.stderr); '
            "raise SystemExit(7)\n",
            "exited with status 7",
        ),
        ('print("do-not-leak invalid json")\n', "valid JSON"),
        ('print(\'["do-not-leak"]\')\n', "JSON object"),
        (
            'print(\'{"unexpected": "do-not-leak"}\')\n',
            "unsupported fields: unexpected",
        ),
        ('print(\'{"port": "do-not-leak"}\')\n', "invalid value for port"),
    ],
)
def test_credential_process_errors_do_not_expose_output(
    tmp_path: Path,
    body: str,
    message: str,
):
    command = _write_process(tmp_path, body)
    config = load_config(_write_config(tmp_path, {"credential_process": command}))

    with pytest.raises(ValueError, match=message) as error:
        build_executor(config)

    assert "do-not-leak" not in str(error.value)
    assert error.value.__cause__ is None
