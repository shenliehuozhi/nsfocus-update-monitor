"""前端资源定位：index.html 只保留结构与 script 标签，逻辑在 static/js。

测试不再依赖单文件，统一通过这里按页面真实加载顺序读取 JS。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'src' / 'web'
INDEX_HTML = WEB / 'templates' / 'index.html'


def script_paths() -> list[Path]:
    html = INDEX_HTML.read_text(encoding='utf-8')
    srcs = re.findall(r'<script src="/static/([^"]+)"></script>', html)
    return [WEB / 'static' / src for src in srcs]


def js_source() -> str:
    return '\n'.join(p.read_text(encoding='utf-8') for p in script_paths())
