"""Resolve executor connection fields from an external credential process."""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from typing import Any

from ..config import Config

CREDENTIAL_PROCESS_TIMEOUT_SECONDS = 30
CONNECTION_FIELDS = frozenset({"host", "port", "database", "user", "password", "path"})


def resolve_credentials(config: Config) -> Config:
    """Return a config with credential-process output merged into its executor.

    The subprocess runs relative to the directory containing ``wlens.yml``.
    Its output is kept in memory and never copied into ``os.environ``. The
    original config is not mutated.
    """
    command = config.executor.credential_process
    if not command:
        return config

    try:
        completed = subprocess.run(
            command,
            cwd=config.repo_root,
            capture_output=True,
            text=True,
            timeout=CREDENTIAL_PROCESS_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError:
        raise ValueError("executor.credential_process executable was not found") from None
    except subprocess.TimeoutExpired:
        raise ValueError(
            "executor.credential_process timed out after "
            f"{CREDENTIAL_PROCESS_TIMEOUT_SECONDS} seconds"
        ) from None
    except OSError:
        raise ValueError("executor.credential_process could not be started") from None

    if completed.returncode != 0:
        raise ValueError(
            f"executor.credential_process exited with status {completed.returncode}"
        )

    try:
        payload = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError):
        raise ValueError("executor.credential_process must return valid JSON") from None

    if not isinstance(payload, dict):
        raise ValueError("executor.credential_process must return a JSON object")

    unsupported_fields = sorted(set(payload) - CONNECTION_FIELDS)
    if unsupported_fields:
        raise ValueError(
            "executor.credential_process returned unsupported fields: "
            + ", ".join(unsupported_fields)
        )

    updates = {name: _normalize_field(name, value) for name, value in payload.items()}
    executor = replace(config.executor, credential_process=[], **updates)
    return replace(config, executor=executor)


def _normalize_field(name: str, value: Any) -> str | int | None:
    if name == "port":
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            raise ValueError(
                "executor.credential_process returned an invalid value for port"
            ) from None

    if value is None:
        return None
    string_value = str(value)
    return string_value if string_value else None
