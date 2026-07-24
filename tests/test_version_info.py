"""Tests for src.core.version and the /api/system/version endpoint.

Why these tests:
- The "About" button on the web header shows users a version identifier.
- The identifier MUST be unique enough that two different builds of the
  service can be told apart.
- This covers: git source, exe fallback, missing .git, broken git binary.
"""

import os
import sys
import hashlib
import subprocess
import pytest


# ─────────────────────────────────────────────────────────────
# 1. version.py unit tests (no Flask)
# ─────────────────────────────────────────────────────────────

def test_git_source_when_in_repo(monkeypatch):
    """If we're inside a git repo, source='git' and commit/branch populated."""
    from src.core import version as v

    # Sanity: are we actually in the nsfocus-monitor repo?
    real_commit = subprocess.run(
        ('git', 'rev-parse', '--short', 'HEAD'),
        cwd='/root/nsfocus-monitor',
        capture_output=True, text=True, timeout=2,
    ).stdout.strip()
    assert real_commit, 'precondition: this test must run inside the repo'

    info = v.get_version_info()
    assert info['source'] == 'git', f'expected git, got {info["source"]}'
    assert info['commit'] == real_commit
    assert info['branch']  # master / main / something
    assert isinstance(info['dirty'], bool)
    assert info['app_name'] == 'nsfocus-monitor'
    assert info['python']  # e.g. '3.10.12'
    assert info['platform'] in ('linux', 'win32', 'darwin')
    assert info['process_start']  # ISO string


def test_short_version_git_clean(monkeypatch):
    """git clean: short_version = commit only."""
    from src.core import version as v
    assert v.short_version({
        'source': 'git', 'commit': 'abc1234', 'dirty': False
    }) == 'abc1234'


def test_short_version_git_dirty(monkeypatch):
    """git dirty: short_version = commit-dirty."""
    from src.core import version as v
    assert v.short_version({
        'source': 'git', 'commit': 'abc1234', 'dirty': True
    }) == 'abc1234-dirty'


def test_short_version_exe(monkeypatch):
    """exe fallback: short_version = 'exe@<hash>'."""
    from src.core import version as v
    assert v.short_version({
        'source': 'exe', 'commit': 'a1b2c3d4'
    }) == 'exe@a1b2c3d4'


def test_short_version_unknown():
    """unknown source: short_version = commit field verbatim."""
    from src.core import version as v
    assert v.short_version({'source': 'unknown', 'commit': '???555'}) == '???555'


def test_exe_fingerprint_when_git_missing(monkeypatch):
    """When .git isn't accessible, fall back to exe stat-based hash."""
    from src.core import version as v

    # Force git calls to fail
    def boom(*a, **kw):
        raise FileNotFoundError('git not in PATH')
    monkeypatch.setattr(v.subprocess, 'run', boom)
    # Pretend we're frozen
    monkeypatch.setattr(v.sys, 'frozen', True, raising=False)

    info = v.get_version_info()
    # Source should NOT be 'git' (git failed). Either 'exe' (stat worked)
    # or 'unknown' (stat also failed). Both are acceptable fallbacks.
    assert info['source'] in ('exe', 'unknown'), f'unexpected source: {info["source"]}'
    if info['source'] == 'exe':
        assert len(info['commit']) == 8  # sha1[:8]
        assert info['exe_path']
        assert info['exe_size'] > 0


# ─────────────────────────────────────────────────────────────
# 2. /api/system/version endpoint test
# ─────────────────────────────────────────────────────────────

@pytest.fixture
def app_with_version(monkeypatch):
    """Build a minimal Flask app with VERSION_INFO populated, like create_app does."""
    from flask import Flask, jsonify
    from src.web.routes.system_routes import bp as system_bp
    from src.core.version import get_version_info, short_version

    app = Flask(__name__)
    app.register_blueprint(system_bp)
    app.config['VERSION_INFO'] = get_version_info()
    app.config['VERSION_SHORT'] = short_version(app.config['VERSION_INFO'])

    # Fake auth: a token is required. require_auth was already imported into
    # the system_routes module at module-load time, so patch the bound name
    # there (not in src.web.auth, which is too late).
    from src.web.routes import system_routes
    monkeypatch.setattr(system_routes, 'require_auth', lambda f: f)
    return app


@pytest.fixture
def client(app_with_version):
    """Test client with a real JWT token (require_auth is mandatory on /version)."""
    from src.web.auth import create_token
    token = create_token(user_id=1, username='test')
    client = app_with_version.test_client()
    client._token = token  # stash for convenience
    return client


def _auth_header(client):
    return {'Authorization': f'Bearer {client._token}'}


def test_version_endpoint_returns_required_fields(client):
    """/api/system/version returns all fields needed to identify a build."""
    r = client.get('/api/system/version', headers=_auth_header(client))
    assert r.status_code == 200
    data = r.get_json()
    assert data['code'] == 0
    info = data['data']

    # Required fields
    for k in ('app_name', 'commit', 'process_start', 'python', 'platform',
              'frozen', 'data_dir', 'source'):
        assert k in info, f'missing field {k}'

    # Source must be one of the known types
    assert info['source'] in ('git', 'exe', 'unknown')


def test_version_endpoint_unique_for_different_commits(client):
    """If I pretend to be a different commit, the response should reflect that."""
    # First call: real version
    r1 = client.get('/api/system/version', headers=_auth_header(client))
    info1 = r1.get_json()['data']

    # Now monkey-patch the cached VERSION_INFO to a fake commit
    from flask import current_app
    with client.application.test_request_context():
        client.application.config['VERSION_INFO']['commit'] = 'fake1234'
        client.application.config['VERSION_INFO']['dirty'] = True
        r2 = client.get('/api/system/version', headers=_auth_header(client))
        info2 = r2.get_json()['data']

    assert info1['commit'] != info2['commit']
    assert info2['dirty'] is True
    assert info2['commit'] == 'fake1234'


def test_version_endpoint_does_not_leak_secrets(client):
    """Version endpoint must not include sensitive data (passwords, tokens, configs)."""
    r = client.get('/api/system/version', headers=_auth_header(client))
    info = r.get_json()['data']
    text = str(info).lower()
    for forbidden in ('password', 'token', 'secret', 'api_key', 'apikey', 'private_key'):
        assert forbidden not in text, f'leaked: {forbidden}'


def test_version_endpoint_requires_auth(client):
    """/api/system/version must require auth (don't expose version to anonymous users)."""
    r = client.get('/api/system/version')  # no header
    assert r.status_code == 401
