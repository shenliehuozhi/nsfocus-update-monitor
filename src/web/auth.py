"""JWT authentication."""

import os
import functools
from datetime import datetime, timedelta

import jwt
from flask import request, jsonify, g

JWT_SECRET = os.getenv('MONITOR_JWT_SECRET', 'dev-jwt-secret-change-me')
JWT_EXPIRY_HOURS = 24


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


def require_auth(f):
    """Decorator for routes that require authentication."""
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        auth_header = request.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            return jsonify({'code': 40100, 'message': '请先登录'}), 401

        token = auth_header.split('Bearer ')[1]
        payload = decode_token(token)
        if not payload:
            return jsonify({'code': 40101, 'message': '登录已过期，请重新登录'}), 401

        g.user_id = payload['user_id']
        g.username = payload['username']
        return f(*args, **kwargs)
    return wrapper


def optional_auth(f):
    """Decorator for routes that optionally accept auth."""
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        auth_header = request.headers.get('Authorization', '')
        if auth_header.startswith('Bearer '):
            token = auth_header.split('Bearer ')[1]
            payload = decode_token(token)
            if payload:
                g.user_id = payload['user_id']
                g.username = payload['username']
        return f(*args, **kwargs)
    return wrapper
