"""serverTime/fmtTZ 时区解析：naive 视为 UTC，Z/±HH:MM 按时区解析，统一显示 CST。"""
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DATA_JS = ROOT / 'src' / 'web' / 'static' / 'js' / 'data.js'


def _node_binary():
    import shutil
    found = shutil.which('node')
    if found:
        return found
    for cand in ('/root/.local/bin/node', '/usr/local/bin/node', '/usr/bin/node'):
        if Path(cand).exists():
            return cand
    return None


def _time_helpers_source() -> str:
    text = DATA_JS.read_text(encoding='utf-8')
    start = text.index('function _parseServerMs')
    end = text.index('// 渠道类型 chip')
    return text[start:end]


def test_server_time_formats_utc_and_offset_strings(tmp_path):
    node = _node_binary()
    if not node:
        pytest.skip('找不到 node，跳过前端时间解析测试')

    script = _time_helpers_source() + """
const assert = require('assert');
assert.strictEqual(serverTime('2026-11-12T03:04:05Z', true), '2026-11-12 11:04');
assert.strictEqual(serverTime('2026-11-12 03:04:05', true), '2026-11-12 11:04');
assert.strictEqual(serverTime('2026-11-12T03:04:05.123', true), '2026-11-12 11:04');
assert.strictEqual(fmtTZ('2026-11-12T11:04:05+08:00', true), '2026-11-12 11:04');
assert.strictEqual(fmtTZ('2026-11-12T03:04:05+00:00', false), '2026-11-12');
assert.strictEqual(serverTime('', true), '');
console.log('ok');
"""
    path = tmp_path / 'time_helpers.js'
    path.write_text(script, encoding='utf-8')
    result = subprocess.run([node, str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert 'ok' in result.stdout
