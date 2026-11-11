"""静态资源内容哈希与 index.html 版本化。

index.html 始终以 no-cache 回源，因此每次加载都能拿到当期哈希；带
?v=<当前哈希> 的 /static 响应可以安全地长缓存，文件内容一变哈希就变。
"""
import hashlib
import os
import re
import threading
import time

HASH_LEN = 12
# 文件系统时间戳只有毫秒级粒度：刚改过的文件直接重算，避免同尺寸改写命中旧哈希。
_FRESH_WINDOW_NS = 2_000_000_000
_ASSET_REF = re.compile(r'(?P<attr>src|href)="(?P<url>/static/(?P<rel>[^"?#]+))"')

_lock = threading.Lock()
_hashes: dict = {}


def asset_hash(static_dir: str, rel_path: str) -> str | None:
    root = os.path.realpath(static_dir)
    path = os.path.realpath(os.path.join(root, rel_path))
    if not path.startswith(root + os.sep):
        return None
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = (path, st.st_mtime_ns, st.st_size, st.st_ino)
    fresh = time.time_ns() - st.st_mtime_ns < _FRESH_WINDOW_NS
    if not fresh:
        with _lock:
            cached = _hashes.get(key)
        if cached:
            return cached
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            digest.update(chunk)
    value = digest.hexdigest()[:HASH_LEN]
    with _lock:
        for old in [k for k in _hashes if k[0] == path]:
            _hashes.pop(old, None)
        _hashes[key] = value
    return value


def versioned_index(html: str, static_dir: str) -> str:
    def _replace(m):
        digest = asset_hash(static_dir, m.group('rel'))
        if not digest:
            return m.group(0)
        return f'{m.group("attr")}="{m.group("url")}?v={digest}"'

    return _ASSET_REF.sub(_replace, html)


def static_cache_control(static_dir: str, rel_path: str, requested: str | None) -> str | None:
    if not requested:
        return None
    current = asset_hash(static_dir, rel_path)
    if current and requested == current:
        return 'public, max-age=31536000, immutable'
    return None
