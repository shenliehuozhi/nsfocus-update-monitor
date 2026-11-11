"""CSP 响应头：脚本与样式都必须仅同源（内联处理器改 data-on-*，动态宽度走 CSSOM）。"""
from flask import Flask

from src.app import CSP_POLICY, apply_security_headers


def _client():
    app = Flask(__name__)
    app.after_request(apply_security_headers)

    @app.get('/')
    def index():
        return 'ok'

    return app.test_client()


def test_csp_header_present_on_pages():
    r = _client().get('/')
    assert r.status_code == 200
    assert r.headers['Content-Security-Policy'] == CSP_POLICY


def test_csp_blocks_inline_script_and_style():
    directives = {p.strip().split(' ')[0]: p.strip() for p in CSP_POLICY.split(';')}
    assert directives['default-src'] == "default-src 'self'"
    assert directives['script-src'] == "script-src 'self'"
    assert directives['style-src'] == "style-src 'self'"
    assert "'unsafe-inline'" not in directives['script-src']
    assert "'unsafe-inline'" not in directives['style-src']
    assert directives['object-src'] == "object-src 'none'"
    assert directives['frame-ancestors'] == "frame-ancestors 'none'"
