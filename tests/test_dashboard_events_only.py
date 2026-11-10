"""仪表盘事件卡轻量刷新回归。

`/api/dashboard?events_only=1` 必须只查事件表并直接返回,
不能连带重算 timeline / product_stats / recent_deliveries。
"""
from flask import Flask


def test_events_only_returns_only_recent_events(monkeypatch):
    from src.web.routes import dashboard as dash
    from src.web.auth import create_token

    monkeypatch.setattr(
        dash, '_recent_events_for_dashboard',
        lambda limit=10: [{'id': 1, 'event_type': 'push_summary'}],
    )

    app = Flask(__name__)
    app.register_blueprint(dash.bp)
    token = create_token(user_id=1, username='test')
    resp = app.test_client().get(
        '/api/dashboard?events_only=1',
        headers={'Authorization': f'Bearer {token}'},
    )

    assert resp.status_code == 200
    data = resp.get_json()['data']
    assert set(data.keys()) == {'recent_events'}
    assert data['recent_events'][0]['id'] == 1
