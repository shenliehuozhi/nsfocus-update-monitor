"""Version identification for nsfocus-monitor.

Goal:让用户和管理员在 web UI 上一眼看出"我跑的是哪个版本"。

唯一性 = git commit + 是否有未提交修改 + 进程启动时间
- 开发环境: `git rev-parse --short HEAD` + `git status --porcelain` 判断 dirty
- PyInstaller exe: `_MEIPASS` 没有 .git, 退到 `sys.executable` 的 mtime / size
- 都没有 → "unknown" + 进程启动时间(进程生命周期内稳定)

不依赖任何外部配置文件, 启动时算一次缓存到 app.config.
"""
import os
import sys
import subprocess
import hashlib
from datetime import datetime, timezone


def _run_git(*args: str) -> str | None:
    """Run a git command in the project root. Return stdout stripped, or None on failure."""
    try:
        # Project root: this file is src/core/version.py → 2 levels up
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
        result = subprocess.run(
            ('git',) + args,
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return None


def _exe_fingerprint() -> dict:
    """Fallback when .git is not available (PyInstaller onefile)."""
    exe = sys.executable
    try:
        st = os.stat(exe)
        # mtime + size gives a unique-ish stamp per build; combine into a short hash
        sig = f'{st.st_mtime_ns}-{st.st_size}'.encode()
        return {
            'source': 'exe',
            'commit': hashlib.sha1(sig).hexdigest()[:8],
            'exe_path': exe,
            'exe_mtime': datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
            'exe_size': st.st_size,
        }
    except OSError:
        return {'source': 'unknown', 'commit': 'unknown', 'exe_path': exe}


def get_version_info() -> dict:
    """Return a dict identifying this running instance.

    Stable for the lifetime of the process. Call once at startup, cache result.
    """
    # 1. Try git (dev environment or system with .git mounted)
    commit = _run_git('rev-parse', '--short', 'HEAD')
    branch = _run_git('rev-parse', '--abbrev-ref', 'HEAD')
    porcelain = _run_git('status', '--porcelain')
    if commit and branch is not None:
        dirty = bool(porcelain)
        info = {
            'source': 'git',
            'commit': commit,
            'branch': branch,
            'dirty': dirty,
        }
    else:
        info = _exe_fingerprint()

    # 2. Common fields
    info.update({
        'app_name': 'nsfocus-monitor',
        'process_start': datetime.now(timezone.utc).isoformat(),
        'python': sys.version.split()[0],  # "3.10.12"
        'platform': sys.platform,           # "linux" / "win32" / "darwin"
        'frozen': getattr(sys, 'frozen', False),
        'data_dir': os.environ.get('MONITOR_DATA_DIR', '?'),
    })
    return info


def short_version(info: dict) -> str:
    """One-line human-readable identifier for the header title bar.

    Examples:
        "b49f721"
        "b49f721-dirty"
        "exe@a1b2c3d4"
    """
    commit = info.get('commit', '?')
    if info.get('source') == 'git':
        return f'{commit}-dirty' if info.get('dirty') else commit
    if info.get('source') == 'exe':
        return f'exe@{commit}'
    return commit