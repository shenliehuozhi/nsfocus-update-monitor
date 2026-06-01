"""Structured logging configuration with runtime level control."""

import os
import logging
import logging.handlers
import threading
from typing import Optional

_logger = None
_file_handler: Optional[logging.FileHandler] = None
_console_handler: Optional[logging.StreamHandler] = None
_debug_restore_timer: Optional[threading.Timer] = None
_log_dir = None


def _resolve_level(name: str) -> int:
    """Resolve log level from env or string."""
    level_map = {
        'DEBUG': logging.DEBUG,
        'INFO': logging.INFO,
        'WARNING': logging.WARNING,
        'ERROR': logging.ERROR,
    }
    return level_map.get(name.upper(), logging.INFO)


def get_logger(name: str = 'monitor') -> logging.Logger:
    global _logger, _file_handler, _console_handler, _log_dir
    if _logger is not None:
        return _logger.getChild(name)

    _log_dir = os.getenv('MONITOR_LOG_DIR', '/tmp')
    os.makedirs(_log_dir, exist_ok=True)

    # Default level from env, fallback INFO
    default_level = _resolve_level(os.getenv('MONITOR_LOG_LEVEL', 'INFO'))

    fmt = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # File handler with rotation (10 MB, keep 5)
    _file_handler = logging.handlers.RotatingFileHandler(
        os.path.join(_log_dir, 'app.log'),
        maxBytes=10 * 1024 * 1024,
        backupCount=10,
        encoding='utf-8'
    )
    _file_handler.setLevel(default_level)
    _file_handler.setFormatter(fmt)

    # Console handler — force UTF-8 on Windows (default GBK can't encode Unicode symbols like ►)
    _console_handler = logging.StreamHandler()
    try:
        import sys as _sys
        if _sys.platform == 'win32':
            _console_handler.stream.reconfigure(encoding='utf-8')
    except Exception:
        pass
    _console_handler.setLevel(logging.INFO)
    _console_handler.setFormatter(fmt)

    _logger = logging.getLogger('monitor')
    _logger.setLevel(logging.DEBUG)  # Root logger DEBUG; handlers control actual output
    _logger.addHandler(_file_handler)
    _logger.addHandler(_console_handler)

    return _logger.getChild(name)


def get_log_dir() -> str:
    """Return the log directory path."""
    global _log_dir
    if _log_dir is None:
        _log_dir = os.getenv('MONITOR_LOG_DIR', '/tmp')
    return _log_dir


def get_current_level() -> str:
    """Return current file handler level as string."""
    global _file_handler
    if _file_handler is None:
        return 'INFO'
    level_map = {
        logging.DEBUG: 'DEBUG',
        logging.INFO: 'INFO',
        logging.WARNING: 'WARNING',
        logging.ERROR: 'ERROR',
    }
    return level_map.get(_file_handler.level, 'INFO')


def set_log_level(level: str, auto_restore_minutes: int = 30) -> str:
    """Set log level at runtime. If DEBUG, auto-restore to INFO after N minutes.
    
    Args:
        level: 'DEBUG', 'INFO', 'WARNING', 'ERROR'
        auto_restore_minutes: auto-restore timeout (only for DEBUG)
    
    Returns:
        New level string (e.g. 'DEBUG')
    """
    global _file_handler, _console_handler, _debug_restore_timer
    level_value = _resolve_level(level)

    if _file_handler:
        _file_handler.setLevel(level_value)

    # Cancel any pending restore timer
    if _debug_restore_timer:
        _debug_restore_timer.cancel()
        _debug_restore_timer = None

    # Auto-restore DEBUG to INFO after timeout
    if level_value == logging.DEBUG and auto_restore_minutes > 0:
        lg = get_logger('logger')
        lg.info(f'Debug mode enabled, auto-restore to INFO in {auto_restore_minutes}min')
        _debug_restore_timer = threading.Timer(
            auto_restore_minutes * 60,
            _auto_restore_info
        )
        _debug_restore_timer.daemon = True
        _debug_restore_timer.start()
    elif level_value != logging.DEBUG:
        lg = get_logger('logger')
        lg.info(f'Log level changed to {level}')

    return level.upper()


def _auto_restore_info():
    """Timer callback: restore log level to INFO."""
    global _file_handler, _debug_restore_timer
    if _file_handler:
        _file_handler.setLevel(logging.INFO)
    _debug_restore_timer = None
    lg = logging.getLogger('monitor.logger')
    lg.info('Debug mode auto-restored to INFO')
