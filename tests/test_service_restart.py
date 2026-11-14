"""Restart requests must never control a unit belonging to another process."""
import os
import subprocess
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest
from flask import Flask


URL = '/api/system/restart'
PID = 4321
SHOW_COMMAND = [
    'systemctl', 'show', '--property=MainPID', '--value', 'nsfocus-monitor.service',
]
RESTART_COMMAND = ['systemctl', '--no-block', 'restart', 'nsfocus-monitor.service']
SECRET = 'private-subprocess-output'


@pytest.fixture(autouse=True)
def safe_calls(monkeypatch):
    """Block real commands, threads, sleeps, and process signals in every test."""
    run = Mock(return_value=subprocess.CompletedProcess(
        SHOW_COMMAND, 0, stdout=f'{PID}\n', stderr='',
    ))
    popen = Mock(side_effect=AssertionError('Real subprocesses are forbidden'))
    thread = Mock()
    sleep = Mock()
    kill = Mock(side_effect=AssertionError('Process signals are forbidden'))
    monkeypatch.setattr(subprocess, 'run', run)
    monkeypatch.setattr(subprocess, 'Popen', popen)
    monkeypatch.setattr(threading, 'Thread', thread)
    monkeypatch.setattr(time, 'sleep', sleep)
    monkeypatch.setattr(os, 'getpid', Mock(return_value=PID))
    monkeypatch.setattr(os, 'kill', kill)
    yield SimpleNamespace(run=run, thread=thread, sleep=sleep, kill=kill)
    popen.assert_not_called()
    kill.assert_not_called()


@pytest.fixture
def client(safe_calls):
    from src.web.routes.system_routes import bp

    app = Flask(__name__)
    app.config['TESTING'] = True
    app.register_blueprint(bp)
    return app.test_client()


@pytest.fixture
def auth_headers(monkeypatch):
    from src.web import auth

    monkeypatch.setattr(auth, 'JWT_SECRET', 'restart-unit-test-secret-not-for-production')
    return {'Authorization': f'Bearer {auth.create_token(1, "admin")}'}


def assert_not_enqueued(safe_calls):
    safe_calls.thread.assert_not_called()
    safe_calls.sleep.assert_not_called()


@pytest.mark.parametrize('headers', [{}, {'Authorization': 'Bearer invalid-token'}])
def test_auth_required(client, safe_calls, headers):
    response = client.post(URL, headers=headers)

    assert response.status_code == 401
    assert response.get_json()['code'] != 0
    safe_calls.run.assert_not_called()
    assert_not_enqueued(safe_calls)


def test_cookie_auth_requires_csrf(client, auth_headers, safe_calls):
    client.set_cookie('nsm_session', auth_headers['Authorization'].split(' ', 1)[1])
    response = client.post(URL)

    assert response.status_code == 403
    assert response.get_json()['code'] != 0
    safe_calls.run.assert_not_called()
    assert_not_enqueued(safe_calls)


@pytest.mark.parametrize('main_pid', ['0\n', '9999\n', '', 'invalid', f'{PID}\n9999'])
def test_unmanaged_process_cannot_restart(client, auth_headers, safe_calls, main_pid):
    safe_calls.run.return_value.stdout = main_pid

    response = client.post(URL, headers=auth_headers)

    assert response.status_code == 409
    assert response.get_json()['code'] != 0
    safe_calls.run.assert_called_once_with(
        SHOW_COMMAND, capture_output=True, text=True, check=True, timeout=5, shell=False,
    )
    assert_not_enqueued(safe_calls)


@pytest.mark.parametrize('error', [
    FileNotFoundError(SECRET),
    PermissionError(SECRET),
    subprocess.CalledProcessError(1, SHOW_COMMAND, output=SECRET, stderr=SECRET),
    subprocess.TimeoutExpired(SHOW_COMMAND, 5, output=SECRET, stderr=SECRET),
    subprocess.SubprocessError(SECRET),
])
def test_verification_errors_fail_closed(client, auth_headers, safe_calls, caplog, error):
    safe_calls.run.side_effect = error

    response = client.post(URL, headers=auth_headers)

    assert response.status_code == 503
    assert response.get_json()['code'] != 0
    assert SECRET not in response.get_data(as_text=True)
    assert SECRET not in caplog.text
    safe_calls.run.assert_called_once_with(
        SHOW_COMMAND, capture_output=True, text=True, check=True, timeout=5, shell=False,
    )
    assert_not_enqueued(safe_calls)


def test_verified_pid_queues_delayed_fixed_unit_restart(client, auth_headers, safe_calls):
    response = client.post(
        URL + '?unit=other.service', headers=auth_headers,
        json={'unit': 'other.service', 'command': 'arbitrary-command'},
    )

    assert response.status_code == 200
    assert response.get_json()['code'] == 0
    safe_calls.run.assert_called_once_with(
        SHOW_COMMAND, capture_output=True, text=True, check=True, timeout=5, shell=False,
    )
    safe_calls.thread.assert_called_once()
    target = safe_calls.thread.call_args.kwargs['target']
    assert callable(target)
    safe_calls.thread.assert_called_once_with(target=target, daemon=True)
    safe_calls.thread.return_value.start.assert_called_once_with()
    safe_calls.sleep.assert_not_called()

    # Execute only the captured target, after the HTTP response, with all I/O mocked.
    safe_calls.run.reset_mock()
    sequence = Mock()
    sequence.attach_mock(safe_calls.sleep, 'sleep')
    sequence.attach_mock(safe_calls.run, 'run')
    target()

    assert sequence.mock_calls == [
        call.sleep(0.5),
        call.run(
            RESTART_COMMAND, capture_output=True, text=True, check=True,
            timeout=10, shell=False,
        ),
    ]
    safe_calls.kill.assert_not_called()


@pytest.mark.parametrize('error', [
    FileNotFoundError(SECRET),
    PermissionError(SECRET),
    subprocess.CalledProcessError(1, RESTART_COMMAND, output=SECRET, stderr=SECRET),
    subprocess.TimeoutExpired(RESTART_COMMAND, 10, output=SECRET, stderr=SECRET),
    subprocess.SubprocessError(SECRET),
])
def test_delayed_restart_failures_are_logged_safely(
    client, auth_headers, safe_calls, caplog, error,
):
    response = client.post(URL, headers=auth_headers)
    assert response.status_code == 200
    target = safe_calls.thread.call_args.kwargs['target']
    safe_calls.run.reset_mock()
    safe_calls.run.side_effect = error

    target()

    safe_calls.sleep.assert_called_once_with(0.5)
    safe_calls.run.assert_called_once_with(
        RESTART_COMMAND, capture_output=True, text=True, check=True,
        timeout=10, shell=False,
    )
    assert 'Failed to submit systemd service restart request' in caplog.text
    assert SECRET not in caplog.text
    assert SECRET not in response.get_data(as_text=True)
    assert response.get_json()['code'] == 0  # Accepted, not a promise of completion.


@pytest.mark.parametrize('failure_point', ['constructor', 'start'])
def test_thread_scheduling_failure_returns_error(
    client, auth_headers, safe_calls, caplog, failure_point,
):
    if failure_point == 'constructor':
        safe_calls.thread.side_effect = RuntimeError(SECRET)
    else:
        safe_calls.thread.return_value.start.side_effect = RuntimeError(SECRET)

    response = client.post(URL, headers=auth_headers)

    assert response.status_code == 503
    assert response.get_json()['code'] != 0
    assert SECRET not in response.get_data(as_text=True)
    assert SECRET not in caplog.text
    safe_calls.run.assert_called_once_with(
        SHOW_COMMAND, capture_output=True, text=True, check=True, timeout=5, shell=False,
    )
    safe_calls.sleep.assert_not_called()
