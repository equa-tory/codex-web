"""Offline checks: auth boundaries, pagination, restore, deduplication and prompts."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
import sqlite3
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

os.environ['CODEX_WEB_STATE'] = tempfile.mkdtemp(prefix='codex-web-test-')
import app as web

ORIGIN = 'http://127.0.0.1:8771'


@pytest.fixture
def client(monkeypatch):
    async def idle():
        await asyncio.sleep(3600)
    monkeypatch.setattr(web.daemon, 'run', idle)
    db = sqlite3.connect(':memory:', check_same_thread=False)
    db.execute('CREATE TABLE submissions (id TEXT PRIMARY KEY, thread TEXT, result TEXT)')
    monkeypatch.setattr(web, 'DB', db)
    web.daemon.prompts.clear()
    with TestClient(web.app, base_url=ORIGIN) as client:
        client.headers['Origin'] = ORIGIN
        assert client.post('/api/login', json={'password': web.CONFIG['password']}).status_code == 200
        client.headers['X-CSRF-Token'] = client.get('/api/session').json()['csrf']
        yield client


def test_auth_cookie_and_origin(client):
    cookie = client.cookies.get(web.COOKIE)
    assert web.COOKIE.startswith('codex_web_') and len(web.COOKIE) > 20
    assert web.authenticated(cookie)
    assert not web.authenticated(cookie + 'x')
    assert (web.CONFIG_PATH.stat().st_mode & 0o777) == 0o600
    assert client.post('/api/logout', headers={'Origin': 'https://evil.test'}).status_code == 403
    assert client.post('/api/logout', headers={'X-CSRF-Token': 'bad'}).status_code == 403
    with TestClient(web.app, base_url=ORIGIN) as guest:
        assert guest.get('/api/session').status_code == 401
        assert guest.get('/api/threads').status_code == 401
        assert guest.post('/api/threads/x/send', json={}).status_code == 403
        with pytest.raises(Exception):
            with guest.websocket_connect('/ws', headers={'Origin': ORIGIN}):
                pass
    with pytest.raises(Exception):
        with client.websocket_connect('ws://127.0.0.1:8771/ws', headers={'Origin': 'https://evil.test'}):
            pass
    assert client.get('/').headers['Content-Security-Policy'].find("frame-ancestors 'none'") >= 0


def test_cookie_collision(client):
    with TestClient(web.app, base_url=ORIGIN) as other:
        other.cookies.set('session', 'another-app')
        assert other.get('/api/session').status_code == 401
    assert client.get('/api/session').status_code == 200


def test_search_path_title_pagination(client, monkeypatch):
    threads = [{'id': str(i), 'name': 'Alpha' if i == 3 else 'Beta', 'preview': '', 'cwd': '/project/phone' if i < 3 else '/other', 'updatedAt': 100-i} for i in range(5)]
    calls = []
    async def rpc(method, params):
        calls.append((method, params))
        return {'data': [threads[0], *threads], 'nextCursor': None}
    monkeypatch.setattr(web.daemon, 'rpc', rpc)
    page = client.get('/api/threads?q=PHONE&limit=2').json()
    assert [t['id'] for t in page['data']] == ['0', '1']
    page2 = client.get('/api/threads', params={'q': 'PHONE', 'limit': 2, 'cursor': page['nextCursor']}).json()
    assert [t['id'] for t in page2['data']] == ['2']
    assert page2['nextCursor'] is None
    assert client.get('/api/threads?q=alpha').json()['data'][0]['id'] == '3'
    threads[0]['preview'] = 'First line\nHidden body search phrase'
    assert client.get('/api/threads?q=Hidden%20body').json()['data'] == []
    client.get('/api/threads?archived=true')
    assert calls[-1][1]['archived'] is True
    assert all(p['sortKey'] == 'updated_at' and p['sortDirection'] == 'desc' for m,p in calls)


def test_history_pagination(client, monkeypatch):
    async def rpc(method, params):
        if method == 'thread/read':
            return {'thread': {'id': 'x', 'historyMode': 'paginated'}}
        assert method == 'thread/turns/list'
        assert params['cursor'] == 'older'
        assert params['itemsView'] == 'full'
        return {'data': [{'id': 'fresh'}], 'nextCursor': 'earlier'}
    monkeypatch.setattr(web.daemon, 'rpc', rpc)
    response = client.get('/api/threads/x/history?cursor=older&limit=3')
    assert response.status_code == 200
    assert response.json()['history']['nextCursor'] == 'earlier'


def test_restore_refreshes_identifiers_three_times(client, monkeypatch):
    generation = 0
    calls = []
    async def rpc(method, params):
        nonlocal generation
        calls.append((method, params))
        thread = {'id': 'x', 'status': {'type': 'idle'}, 'historyMode': 'paginated', 'cwd': '/tmp'}
        if method in ('thread/read', 'thread/resume'):
            return {'thread': thread}
        if method == 'thread/turns/list':
            return {'data': [{'id': f'turn-{generation}', 'items': [{'type': 'userMessage', 'content': [{'type': 'text', 'text': 'edit me'}]}]}], 'nextCursor': None}
        if method == 'thread/revert':
            assert params['beforeTurnId'] == f'turn-{generation}'
            generation += 1
            return {'thread': thread}
    monkeypatch.setattr(web.daemon, 'rpc', rpc)
    for i in range(3):
        response = client.post('/api/threads/x/restore', json={'beforeTurnId': f'turn-{i}'})
        assert response.status_code == 200
        assert response.json()['text'] == 'edit me'
        assert response.json()['history']['data'][0]['id'] == f'turn-{i+1}'
    assert sum(method == 'thread/revert' for method,p in calls) == 3


def test_restore_rejects_active_and_stale(client, monkeypatch):
    monkeypatch.setattr(web.daemon, 'metadata', AsyncMock(return_value={'status': {'type': 'active'}, 'historyMode': 'paginated'}))
    assert client.post('/api/threads/x/restore', json={'beforeTurnId': 'old'}).status_code == 409
    monkeypatch.setattr(web.daemon, 'metadata', AsyncMock(return_value={'status': {'type': 'idle'}, 'historyMode': 'paginated'}))
    monkeypatch.setattr(web.daemon, 'history', AsyncMock(return_value={'data': [], 'nextCursor': None}))
    assert client.post('/api/threads/x/restore', json={'beforeTurnId': 'old'}).status_code == 409


def test_duplicate_send_and_mode(client, monkeypatch):
    calls = []
    async def rpc(method, params):
        calls.append((method, params))
        if method == 'thread/read':
            return {'thread': {'status': {'type': 'idle'}, 'model': 'model-from-daemon'}}
        return {'turn': {'id': 'new'}}
    monkeypatch.setattr(web.daemon, 'rpc', rpc)
    message = {'text': 'Hello', 'submissionId': 'unique-test-id', 'mode': 'plan', 'permissions': ':read-only'}
    first = client.post('/api/threads/x/send', json=message)
    second = client.post('/api/threads/x/send', json=message)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    starts = [p for m,p in calls if m == 'turn/start']
    assert len(starts) == 1
    assert starts[0]['collaborationMode']['mode'] == 'plan'
    assert starts[0]['permissions'] == ':read-only'


def test_uncertain_submission_never_resent(client, monkeypatch):
    async def rpc(method, params):
        if method == 'thread/read':
            return {'thread': {'status': {'type': 'idle'}, 'model': 'model'}}
        raise RuntimeError('connection lost')
    monkeypatch.setattr(web.daemon, 'rpc', rpc)
    message = {'text': 'Hello', 'submissionId': 'uncertain-test-id'}
    assert client.post('/api/threads/x/send', json=message).status_code == 503
    assert client.post('/api/threads/x/send', json=message).status_code == 409


@pytest.mark.parametrize('method,result', [
    ('item/commandExecution/requestApproval', {'decision': 'accept'}),
    ('item/fileChange/requestApproval', {'decision': 'decline'}),
    ('item/permissions/requestApproval', {'permissions': {}, 'scope': 'turn'}),
    ('item/tool/requestUserInput', {'answers': {'q': {'answers': ['Free text']}}}),
    ('mcpServer/elicitation/request', {'action': 'accept', 'content': {'field': 'value'}, '_meta': None}),
])
def test_prompt_delivery(client, monkeypatch, method, result):
    ws = type('WS', (), {'send': AsyncMock()})()
    monkeypatch.setattr(web.daemon, 'ws', ws)
    web.daemon.prompts['1:8'] = {'id': 8, 'method': method, 'params': {'threadId': 'x'}}
    response = client.post('/api/prompts/1:8', json={'result': result})
    assert response.status_code == 200
    assert json.loads(ws.send.call_args.args[0]) == {'id': 8, 'result': result}
    assert '1:8' not in web.daemon.prompts
    assert client.post('/api/prompts/1:8', json={'result': result}).status_code == 409


def test_pending_survives_browser_disconnect(client):
    web.daemon.prompts['persist'] = {'id': 9, 'method': 'item/tool/requestUserInput', 'params': {'threadId': 'x'}}
    with client.websocket_connect('ws://127.0.0.1:8771/ws', headers={'Origin': ORIGIN}) as ws:
        ws.receive_json()
        ws.send_json({'type': 'subscribe', 'threadId': 'x'})
        assert ws.receive_json()['method'] == 'web/resync'
    assert len(web.daemon.pending('x')) == 1


def test_empty_thread_before_first_rollout(client, monkeypatch):
    thread = {'id': 'empty', 'historyMode': 'paginated', 'status': {'type': 'idle'}}
    web.daemon.new_threads['empty'] = {'thread': thread, 'model': 'test'}
    async def rpc(method, params):
        if method == 'thread/resume':
            raise RuntimeError('no rollout found for thread id empty')
        raise AssertionError('Empty new thread should not fetch nonexistent rollout')
    monkeypatch.setattr(web.daemon, 'rpc', rpc)
    result = client.post('/api/threads/empty/open', json={})
    assert result.status_code == 200
    assert result.json()['history']['data'] == []


@pytest.mark.asyncio
async def test_daemon_rpc_correlation_requests_and_outage(tmp_path, monkeypatch):
    from websockets.asyncio.server import unix_serve
    socket = str(tmp_path / 'daemon.sock')
    monkeypatch.setattr(web, 'SOCKET', socket)
    instance = web.Daemon()
    peers = []
    async def server(ws):
        peers.append(ws)
        async for raw in ws:
            message = json.loads(raw)
            if message.get('method') == 'initialize':
                await ws.send(json.dumps({'id': message['id'], 'result': {}}))
            elif message.get('method') == 'test':
                await ws.send(json.dumps({'method': 'item/tool/requestUserInput', 'id': 42, 'params': {'threadId': 'x', 'questions': []}}))
                await ws.send(json.dumps({'id': message['id'], 'result': {'answer': message['params']['value']}}))
    async with unix_serve(server, socket):
        task = asyncio.create_task(instance.run())
        try:
            await asyncio.wait_for(instance.ready.wait(), 3)
            results = await asyncio.gather(*(instance.rpc('test', {'value': i}) for i in range(5)))
            assert [r['answer'] for r in results] == list(range(5))
            assert len(peers) == 1
            assert len(instance.pending('x')) == 1
            assert instance.pending('x')[0]['key'] == '1:42'
            await peers[0].close()
            for _ in range(100):
                if not instance.ready.is_set():
                    break
                await asyncio.sleep(.01)
            assert not instance.ready.is_set()
            with pytest.raises(RuntimeError):
                await instance.rpc('test', {})
            await asyncio.wait_for(instance.ready.wait(), 4)
            assert len(peers) == 2
            assert instance.generation == 2
        finally:
            task.cancel()
            from contextlib import suppress
            with suppress(asyncio.CancelledError):
                await task


def test_archived_session_reads_without_resuming(client, monkeypatch):
    calls = []
    async def rpc(method, params):
        calls.append(method)
        if method == 'thread/resume':
            raise RuntimeError('session x is archived. Run codex unarchive first.')
        if method == 'thread/read':
            return {'thread': {'id': 'x', 'status': {'type': 'notLoaded'}, 'historyMode': 'paginated'}}
        if method == 'thread/turns/list':
            return {'data': [], 'nextCursor': None}
    monkeypatch.setattr(web.daemon, 'rpc', rpc)
    response = client.post('/api/threads/x/open', json={})
    assert response.status_code == 200
    assert response.json()['archived'] is True
    assert 'thread/unarchive' not in calls
