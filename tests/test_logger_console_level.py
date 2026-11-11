"""console handler 级别：生产环境 stderr 落到无轮转文件，只保留 WARNING+。"""
import logging
from types import SimpleNamespace

from src.core import logger


def _fake_sys(tty):
    return SimpleNamespace(stderr=SimpleNamespace(isatty=lambda: tty))


def test_console_level_is_warning_when_stderr_redirected(monkeypatch):
    monkeypatch.delenv('MONITOR_CONSOLE_LOG_LEVEL', raising=False)
    monkeypatch.setattr(logger, 'sys', _fake_sys(False))
    assert logger._console_log_level() == logging.WARNING


def test_console_level_is_info_on_interactive_terminal(monkeypatch):
    monkeypatch.delenv('MONITOR_CONSOLE_LOG_LEVEL', raising=False)
    monkeypatch.setattr(logger, 'sys', _fake_sys(True))
    assert logger._console_log_level() == logging.INFO


def test_console_level_env_override(monkeypatch):
    monkeypatch.setenv('MONITOR_CONSOLE_LOG_LEVEL', 'debug')
    monkeypatch.setattr(logger, 'sys', _fake_sys(False))
    assert logger._console_log_level() == logging.DEBUG
