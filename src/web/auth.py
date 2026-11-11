"""JWT authentication.

浏览器走 HttpOnly Cookie（JS 读不到，避免 XSS 直接偷走 24h 凭证），
脚本/API 客户端仍可发送 Authorization: Bearer。Cookie 鉴权的非安全方法
必须带 X-CSRF-Token，与服务端下发的 nsm_csrf 双提交校验。
"""

import functools
import hmac
import os
import secrets
from datetime import datetime, timedelta

import jwt
from flask import request, jsonify, g

JWT_SECRET = os.getenv('MONITOR_JWT_SECRET', 'dev-jwt-secret-change-me')
JWT_EXPIRY_HOURS = 24

SESSION_COOKIE = 'nsm_session'
CSRF_COOKIE = 'nsm_csrf'
CSRF_HEADER = 'X-CSRF-Token'
_SAFE_METHODS = frozenset({'GET', 'HEAD', 'OPTIONS', 'TRACE'})


def create_token(user_id: int, username: str) -> str:
    payload = {
        'user_id': user_id,
        'username': username,
        'exp': datetime.utcnow() + timedelta(hours=JWT_EXPIRY_HOURS),
        'iat': datetime.utcnow(),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm='HS256')


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=['HS256'])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def request_token() -> tuple[str, str | None]:
    """返回 (token, 来源)。来源为 'cookie' / 'header' / None。

    Cookie 优先：浏览器登录后只带 Cookie，旧标签页里的 Bearer 仍可作为迁移兜底。
    """
    token = request.cookies.get(SESSION_COOKIE, '')
    if token:
        return token, 'cookie'
    auth_header = request.headers.get('Authorization', '')
    if auth_header.startswith('Bearer '):
        return auth_header[len('Bearer '):], 'header'
    return '', None


def _cookie_secure() -> bool:
    # 反向代理终止 TLS 时 request.is_secure 为 False，用环境变量显式开启
    if os.getenv('MONITOR_COOKIE_SECURE', '') == '1':
        return True
    return request.is_secure


def set_auth_cookies(response, token: str):
    """下发会话 Cookie 与可被 JS 读取的 CSRF 双提交 Cookie。"""
    secure = _cookie_secure()
    max_age = JWT_EXPIRY_HOURS * 3600
    response.set_cookie(SESSION_COOKIE, token, max_age=max_age, path='/',
                        httponly=True, samesite='Lax', secure=secure)
    response.set_cookie(CSRF_COOKIE, secrets.token_urlsafe(32), max_age=max_age,
                        path='/', httponly=False, samesite='Lax', secure=secure)
    return response


def clear_auth_cookies(response):
    response.delete_cookie(SESSION_COOKIE, path='/')
    response.delete_cookie(CSRF_COOKIE, path='/')
    return response


def _csrf_ok() -> bool:
    expected = request.cookies.get(CSRF_COOKIE, '')
    got = request.headers.get(CSRF_HEADER, '')
    return bool(expected and got and hmac.compare_digest(got, expected))


def require_auth(f):
    """Decorator for routes that require authentication."""
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        token, source = request_token()
        if not token:
            return jsonify({'code': 40100, 'message': '请先登录'}), 401

        payload = decode_token(token)
        if not payload:
            return jsonify({'code': 40101, 'message': '登录已过期，请重新登录'}), 401

        # Cookie 会被浏览器自动携带 → 非安全方法必须有双提交 CSRF 头；
        # Bearer 头不会被跨站自动携带，无需 CSRF。
        if source == 'cookie' and request.method not in _SAFE_METHODS and not _csrf_ok():
            return jsonify({'code': 40300, 'message': 'CSRF 校验失败，请刷新页面重试'}), 403

        g.user_id = payload['user_id']
        g.username = payload['username']
        g.auth_source = source
        g.auth_token = token
        return f(*args, **kwargs)
    return wrapper


def optional_auth(f):
    """Decorator for routes that optionally accept auth."""
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        token, source = request_token()
        if token:
            payload = decode_token(token)
            if payload:
                g.user_id = payload['user_id']
                g.username = payload['username']
                g.auth_source = source
                g.auth_token = token
        return f(*args, **kwargs)
    return wrapper
