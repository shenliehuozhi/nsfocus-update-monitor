"""静态资源版本化：index.html 的 css/js 必须带内容哈希，且哈希正确才长缓存。"""
import hashlib
import re

from flask import Flask, request

from src.web.asset_versioning import asset_hash, static_cache_control, versioned_index
from tests.frontend_source import INDEX_HTML, WEB

STATIC = WEB / 'static'
EXPECTED_JS = [
    'core.js', 'data.js', 'data_push.js', 'pages.js', 'history.js',
    'products.js', 'collection.js', 'events_center.js', 'observability.js',
    'account.js', 'tools.js', 'bootstrap.js',
]


def test_hash_tracks_content(tmp_path):
    f = tmp_path / 'a.js'
    f.write_bytes(b'console.log(1)')
    first = asset_hash(str(tmp_path), 'a.js')
    assert first == hashlib.sha256(b'console.log(1)').hexdigest()[:12]
    assert asset_hash(str(tmp_path), 'a.js') == first

    f.write_bytes(b'console.log(2)')
    second = asset_hash(str(tmp_path), 'a.js')
    assert second != first
    assert second == hashlib.sha256(b'console.log(2)').hexdigest()[:12]


def test_hash_rejects_path_escape(tmp_path):
    outside = tmp_path.parent / 'outside.js'
    outside.write_text('x')
    assert asset_hash(str(tmp_path / 'root'), '../outside.js') is None


def test_index_assets_are_versioned_with_current_hash():
    body = versioned_index(INDEX_HTML.read_text(encoding='utf-8'), str(STATIC))
    refs = re.findall(r'(?:src|href)="/static/([^"?]+)\?v=([0-9a-f]{12})"', body)
    assert [rel for rel, _ in refs if rel.startswith('js/')] == [
        f'js/{name}' for name in EXPECTED_JS]
    assert ('css/app.css', asset_hash(str(STATIC), 'css/app.css')) in refs
    for rel, digest in refs:
        assert digest == asset_hash(str(STATIC), rel)
    assert not re.search(r'(?:src|href)="/static/[^"?]+"', body), body


def test_unknown_asset_keeps_original_url():
    html = '<script src="/static/js/nope.js"></script>'
    assert versioned_index(html, str(STATIC)) == html


def test_immutable_only_for_current_hash():
    rel = 'css/app.css'
    current = asset_hash(str(STATIC), rel)
    assert static_cache_control(str(STATIC), rel, current) == \
        'public, max-age=31536000, immutable'
    assert static_cache_control(str(STATIC), rel, None) is None
    assert static_cache_control(str(STATIC), rel, 'deadbeef') is None


def _static_client():
    app = Flask(__name__, static_folder=str(STATIC))

    @app.after_request
    def _cache(response):
        if request.endpoint == 'static':
            rel = (request.view_args or {}).get('filename', '')
            value = static_cache_control(app.static_folder, rel, request.args.get('v'))
            if value:
                response.headers['Cache-Control'] = value
        return response

    return app.test_client()


def test_static_route_honours_version():
    client = _static_client()
    current = asset_hash(str(STATIC), 'css/app.css')
    ok = client.get(f'/static/css/app.css?v={current}')
    assert ok.status_code == 200
    assert ok.headers['Cache-Control'] == 'public, max-age=31536000, immutable'

    plain = client.get('/static/css/app.css')
    assert plain.status_code == 200
    assert 'immutable' not in (plain.headers.get('Cache-Control') or '')

    stale = client.get('/static/css/app.css?v=deadbeef')
    assert stale.status_code == 200
    assert 'immutable' not in (stale.headers.get('Cache-Control') or '')


def test_index_route_revalidates_and_keeps_no_cache():
    from src.web.routes import register_routes
    app = Flask(__name__)
    register_routes(app)
    client = app.test_client()
    first = client.get('/')
    assert first.status_code == 200
    assert first.headers['Cache-Control'] == 'no-cache'
    assert b'/static/css/app.css?v=' in first.get_data()

    etag = first.headers['ETag']
    again = client.get('/', headers={'If-None-Match': etag})
    assert again.status_code == 304
    assert again.get_data() == b''
