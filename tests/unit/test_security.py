"""Credenciales: nunca en logs; la ejecución solo apunta a testnet; .env fuera del repo."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from bot.config import ExecutionConfig, Settings
from bot.log import configure_logging, get_logger, redact, register_secrets
from tests.conftest import ROOT

SECRET = "tEsTnEtSeCrEt-1234567890abcdef"


def test_secrets_are_redacted_in_structured_logs(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO", "json")
    register_secrets(SECRET)
    log = get_logger("test")
    log.info("conexion", url=f"https://x?key={SECRET}", nested={"k": [SECRET]})
    try:
        raise RuntimeError(f"fallo con {SECRET}")
    except RuntimeError:
        log.exception("error")
    out = capsys.readouterr().out
    assert SECRET not in out
    assert "***" in out
    first = json.loads(out.splitlines()[0])
    assert first["url"].endswith("key=***")


def test_secrets_are_redacted_in_third_party_logs(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO", "json")
    register_secrets(SECRET)
    logging.getLogger("binance_common").warning("firma %s", SECRET)
    out = capsys.readouterr().out
    assert SECRET not in out


def test_redact_ignores_short_values() -> None:
    register_secrets("abc")  # demasiado corto para redactarse sin romper textos normales
    assert redact("abc def") == "abc def"


def test_execution_url_must_be_testnet() -> None:
    with pytest.raises(ValidationError):
        ExecutionConfig(testnet_enabled=True, testnet_url="https://api.binance.com")
    raw = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))
    assert "testnet" in Settings.model_validate(raw).execution.testnet_url


def test_env_file_is_ignored_and_example_has_no_values() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in gitignore
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    for line in example.splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            if "KEY" in key or "SECRET" in key or "TOKEN" in key or "PASSWORD" in key:
                assert value.strip() == "", f"{key} no debe tener valor en .env.example"


def test_no_hardcoded_credentials_in_source() -> None:
    import re

    pattern = re.compile(r"(api[_-]?key|secret)\s*=\s*['\"][A-Za-z0-9]{20,}['\"]", re.I)
    for path in Path(ROOT / "src").rglob("*.py"):
        assert not pattern.search(path.read_text(encoding="utf-8")), path
