"""Common test/push log writers shared by Email + robot channels.

`TestLogWriter` historically lived in email.py because SMTP handshakes need a
multi-step trace. After expanding test/push logging to all channels (DingTalk,
Feishu, WeCom, Apprise) we factor it out here so every notifier has a
consistent writer API: `info / warn / error / ok` + line buffer + disk append.

Two specialization paths today:
- `TestLogWriter`: in-channel test button — append-only, single file per channel
- `PushLogWriter`: manual push to one or more subs — separate file per push
  (so multiple pushes don't clobber one another's log)

`_NullLogWriter` is the production fallback — same interface, no disk I/O.
Switch by `config.get('_test_log_writer')` truthiness; notifier code can
blindly call `log.info(...)` in both modes.

File path convention (whitelisted in api_routes.get_log_file):
  /tmp/email_test_<channel_id>.log         (legacy, kept for back-compat)
  /tmp/<type>_test_<channel_id>.log       (dingtalk / feishu / wecom / apprise)
  /tmp/email_push_<channel_id>_<ts>.log   (legacy)
  /tmp/<type>_push_<channel_id>_<ts>.log  (new)
"""

from datetime import datetime


class TestLogWriter:
    """Append-only writer: trace rows to in-memory list + disk file."""

    # Tell pytest not to collect this as a test class (it has __init__).
    __test__ = False

    def __init__(self, channel_id: int, channel_name: str,
                 log_path: str = None,
                 header: str = 'Test Log'):
        self.channel_id = channel_id
        self.channel_name = channel_name
        self.path = log_path or f'/tmp/{header.lower().replace(" ", "_")}_{channel_id}.log'
        self.started_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self.lines: list[str] = []
        try:
            with open(self.path, 'w', encoding='utf-8') as f:
                f.write(f'=== {header}: {channel_name} (channel_id={channel_id}) ===\n')
                f.write(f'Started: {self.started_at}\n')
                f.write('=' * 60 + '\n')
        except OSError as e:
            # Disk full or permission denied — file missing is not fatal,
            # we still keep an in-memory trace that getChannelTestLog can show.
            import logging
            logging.getLogger(__name__).warning(
                f'Failed to initialize test log file {self.path}: {e}')

    def _append(self, level: str, msg: str) -> None:
        ts = datetime.now().strftime('%H:%M:%S.%f')[:-3]
        line = f'[{ts}] [{level:5s}] {msg}'
        self.lines.append(line)
        try:
            with open(self.path, 'a', encoding='utf-8') as f:
                f.write(line + '\n')
        except OSError:
            # Silent: log writes must never break the notifier path.
            pass

    def info(self, msg: str) -> None:
        self._append('INFO', msg)

    def warn(self, msg: str) -> None:
        self._append('WARN', msg)

    def error(self, msg: str) -> None:
        self._append('ERROR', msg)

    def ok(self, msg: str) -> None:
        self._append('OK', msg)


class PushLogWriter(TestLogWriter):
    """Variant of TestLogWriter used by manual push flows (per-attempt file)."""
    pass


class _NullLogWriter:
    """No-op writer — used in production (non-test) notifier dispatch."""

    def info(self, msg: str) -> None: pass
    def warn(self, msg: str) -> None: pass
    def error(self, msg: str) -> None: pass
    def ok(self, msg: str) -> None: pass


def get_log_writer(config: dict, channel_type: str, channel_id: int,
                   channel_name: str, log_path: str = None):
    """Return a TestLogWriter if config carries one; else _NullLogWriter.

    Notifier `send()` should always call this rather than checking config keys
    itself — keeps every notifier symmetric.
    """
    explicit = config.get('_test_log_writer')
    if explicit is not None:
        return explicit
    if config.get('_log_disabled'):
        return _NullLogWriter()
    # Fallback: zero-arg instantiation matches email.py semantics (no-op
    # writer in production). Caller can pass log_path to override.
    return _NullLogWriter()
