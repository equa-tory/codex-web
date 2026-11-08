"""A private LAN client for the existing Codex app-server daemon."""
import asyncio
import base64
from collections import defaultdict, deque
from contextlib import asynccontextmanager, suppress
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from websockets.asyncio.client import unix_connect

ROOT = Path(__file__).resolve().parent
LOCAL = Path(os.environ.get('CODEX_WEB_STATE', ROOT / '.local'))
LOCAL.mkdir(mode=0o700, parents=True, exist_ok=True)
LOCAL.chmod(0o700)
CONFIG_PATH = LOCAL / 'config.json'
if not CONFIG_PATH.exists():
    config = {'installation': secrets.token_hex(8), 'password': secrets.token_urlsafe(18),
              'secret': secrets.token_hex(32), 'origins': ['http://192.168.1.50:8771', 'http://127.0.0.1:8771', 'http://localhost:8771']}
    fd = os.open(CONFIG_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(config, f, indent=2)
CONFIG_PATH.chmod(0o600)
CONFIG = json.loads(CONFIG_PATH.read_text())
COOKIE = 'codex_web_' + CONFIG['installation']
SOCKET = os.environ.get('CODEX_WEB_SOCKET', str(Path.home() / '.codex/app-server-control/app-server-control.sock'))
DB = sqlite3.connect(LOCAL / 'submissions.sqlite')
DB.execute('CREATE TABLE IF NOT EXISTS submissions (id TEXT PRIMARY KEY, thread TEXT, result TEXT)')
DB.commit()


def signed_cookie():
    payload = f'{int(time.time()) + 30 * 86400}:{secrets.token_hex(16)}'
    return payload + '.' + hmac.new(CONFIG['secret'].encode(), payload.encode(), hashlib.sha256).hexdigest()


def authenticated(value):
    try:
        payload, signature = value.rsplit('.', 1)
        expected = hmac.new(CONFIG['secret'].encode(), payload.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature, expected) and int(payload.split(':')[0]) > time.time()
    except (ValueError, AttributeError):
        return False


def csrf_token(cookie):
    return hmac.new(CONFIG['secret'].encode(), ('csrf:' + cookie).encode(), hashlib.sha256).hexdigest()


def check_origin(origin):
    return origin in CONFIG['origins']


class Daemon:
    def __init__(self):
        self.ws = None
        self.ready = asyncio.Event()
        self.calls = {}
        self.prompts = {}
        self.listeners = set()
        self.sequence = 0
        self.error = 'Connecting to Codex daemon'
        self.locks = defaultdict(asyncio.Lock)
        self.generation = 0
        self.new_threads = {}

    def broadcast(self, event):
        for queue in tuple(self.listeners):
            if queue.full():
                # A slow browser must refresh history; never lose server-side prompts.
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait({'method': 'web/resync', 'params': {}})
            else:
                queue.put_nowait(event)

    async def run(self):
        while True:
            try:
                async with unix_connect(SOCKET, max_size=64 * 1024 * 1024, ping_interval=20) as ws:
                    self.ws = ws
                    self.generation += 1
                    reader = asyncio.create_task(self.read(ws))
                    try:
                        await self.rpc('initialize', {'clientInfo': {'name': 'codex_web', 'title': 'Codex Web', 'version': '1.0.0'}, 'capabilities': {'experimentalApi': True}}, initializing=True)
                        await ws.send(json.dumps({'method': 'initialized', 'params': {}}))
                        self.error = None
                        self.ready.set()
                        self.broadcast({'method': 'web/connected', 'params': {}})
                        await reader
                    finally:
                        reader.cancel()
                        with suppress(asyncio.CancelledError):
                            await reader
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.error = f'Codex daemon unavailable: {type(exc).__name__}'
            finally:
                self.ready.clear()
                self.ws = None
                # Requests belong to the old transport. Rejoining a thread replays live requests.
                self.prompts.clear()
                self.new_threads.clear()
                for future in self.calls.values():
                    if not future.done():
                        future.set_exception(RuntimeError('Daemon connection lost; refresh before retrying.'))
                self.calls.clear()
                self.broadcast({'method': 'web/disconnected', 'params': {'error': self.error}})
            await asyncio.sleep(2)

    async def read(self, ws):
        async for raw in ws:
            event = json.loads(raw)
            if 'method' not in event:
                future = self.calls.pop(event.get('id'), None)
                if future and not future.done():
                    if 'error' in event:
                        future.set_exception(RuntimeError(event['error'].get('message', str(event['error']))))
                    else:
                        future.set_result(event.get('result', {}))
                continue
            method, params = event['method'], event.get('params', {})
            if 'id' in event:
                if method == 'currentTime/read':
                    await ws.send(json.dumps({'id': event['id'], 'result': {'currentTimeAt': int(time.time())}}))
                    continue
                key = f'{self.generation}:{event["id"]}'
                event['key'] = key
                self.prompts[key] = event
            if method == 'serverRequest/resolved':
                self.prompts = {k: v for k, v in self.prompts.items() if v['id'] != params.get('requestId')}
            if method == 'turn/completed':
                self.prompts = {k: v for k, v in self.prompts.items() if not (v.get('params', {}).get('threadId') == params.get('threadId') and v.get('params', {}).get('turnId') == params.get('turn', {}).get('id'))}
            if not method.startswith('codex/event/'):
                self.broadcast(event)

    async def rpc(self, method, params, initializing=False):
        if not initializing and not self.ready.is_set():
            raise RuntimeError(self.error or 'Codex daemon reconnecting')
        self.sequence += 1
        key = f'web-{self.sequence}'
        future = asyncio.get_running_loop().create_future()
        self.calls[key] = future
        try:
            await self.ws.send(json.dumps({'id': key, 'method': method, 'params': params}))
            return await asyncio.wait_for(future, 90)
        finally:
            self.calls.pop(key, None)

    def pending(self, thread):
        return [p for p in self.prompts.values() if p.get('params', {}).get('threadId') == thread]

    async def metadata(self, thread):
        return (await self.rpc('thread/read', {'threadId': thread}))['thread']

    async def history(self, thread, cursor=None, limit=20, metadata=None):
        meta = metadata or await self.metadata(thread)
        if meta.get('historyMode') == 'paginated':
            return await self.rpc('thread/turns/list', {'threadId': thread, 'limit': limit, 'cursor': cursor, 'sortDirection': 'desc', 'itemsView': 'full'})
        full = (await self.rpc('thread/read', {'threadId': thread, 'includeTurns': True}))['thread']['turns']
        end = int(cursor) if cursor else len(full)
        start = max(0, end - limit)
        return {'data': list(reversed(full[start:end])), 'nextCursor': str(start) if start else None}


daemon = Daemon()


@asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(daemon.run())
    yield
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
login_attempts = defaultdict(deque)


@app.middleware('http')
async def guard(request: Request, call_next):
    path = request.url.path
    if request.method not in ('GET', 'HEAD'):
        if not check_origin(request.headers.get('origin')):
            return JSONResponse({'detail': 'Origin rejected'}, 403)
    if path.startswith('/api/') and path != '/api/login':
        cookie = request.cookies.get(COOKIE)
        if not authenticated(cookie):
            return JSONResponse({'detail': 'Sign in required'}, 401)
        if request.method not in ('GET', 'HEAD') and not hmac.compare_digest(request.headers.get('x-csrf-token', ''), csrf_token(cookie)):
            return JSONResponse({'detail': 'CSRF token rejected'}, 403)
    try:
        response = await call_next(request)
    except RuntimeError as exc:
        response = JSONResponse({'detail': str(exc)}, 503)
    response.headers['Cache-Control'] = 'no-store' if path.startswith('/api/') else 'no-cache'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'same-origin'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    return response


class Login(BaseModel):
    password: str = Field(max_length=200)


@app.post('/api/login')
async def login(body: Login, request: Request):
    key = request.client.host
    attempts = login_attempts[key]
    now = time.monotonic()
    while attempts and attempts[0] < now - 60:
        attempts.popleft()
    if len(attempts) >= 10:
        raise HTTPException(429, 'Too many attempts. Wait a minute.')
    if not secrets.compare_digest(body.password, CONFIG['password']):
        attempts.append(now)
        raise HTTPException(401, 'Incorrect password')
    attempts.clear()
    response = JSONResponse({'ok': True})
    response.set_cookie(COOKIE, signed_cookie(), httponly=True, samesite='strict', secure=request.url.scheme == 'https', max_age=30 * 86400)
    return response


@app.get('/api/session')
async def session(request: Request):
    return {'installation': CONFIG['installation'], 'csrf': csrf_token(request.cookies[COOKIE]), 'connected': daemon.ready.is_set(), 'error': daemon.error}


@app.post('/api/logout')
async def logout():
    response = JSONResponse({'ok': True})
    response.delete_cookie(COOKIE)
    return response


@app.get('/api/options')
async def options(cwd: str | None = None):
    models, permissions, modes = await asyncio.gather(daemon.rpc('model/list', {}), daemon.rpc('permissionProfile/list', {'cwd': cwd}), daemon.rpc('collaborationMode/list', {}))
    return {'models': models['data'], 'permissions': permissions['data'], 'modes': modes['data']}


@app.get('/api/threads')
async def threads(q: str = '', archived: bool = False, active: bool = False, cursor: str | None = None, limit: int = 25):
    limit = min(max(limit, 1), 100)
    # Daemon title search omits cwd. Scan ordered pages before slicing matches.
    matched = []
    seen = set()
    loaded_ids = set()
    if active and not archived:
        loaded_page = await daemon.rpc('thread/loaded/list', {'limit': 10000})
        loaded_ids.update(loaded_page.get('data', []))
    scan_cursor = None
    try:
        skip = int(base64.urlsafe_b64decode(cursor).decode()) if cursor else 0
    except (ValueError, UnicodeError):
        raise HTTPException(400, 'Invalid cursor')
    if skip < 0:
        raise HTTPException(400, 'Invalid cursor')
    while True:
        page = await daemon.rpc('thread/list', {'archived': archived, 'limit': 100, 'cursor': scan_cursor, 'sortKey': 'updated_at', 'sortDirection': 'desc'})
        for thread in page['data']:
            # Revert creates several rollout files with the same logical thread id.
            if thread['id'] in seen:
                continue
            seen.add(thread['id'])
            if active and thread['id'] not in loaded_ids:
                continue
            title = thread.get('name') or (thread.get('preview') or '').split('\n')[0]
            haystack = f"{title} {thread.get('cwd', '')}".casefold()
            if q.casefold() in haystack:
                matched.append(thread)
        scan_cursor = page.get('nextCursor')
        if len(matched) > skip + limit or not scan_cursor:
            break
    data = matched[skip:skip + limit]
    more = len(matched) > skip + limit or bool(scan_cursor)
    return {'data': data, 'nextCursor': base64.urlsafe_b64encode(str(skip + limit).encode()).decode() if more else None}


class NewThread(BaseModel):
    cwd: str
    model: str | None = None
    permissions: str | None = None


@app.post('/api/threads')
async def new_thread(body: NewThread):
    cwd = Path(body.cwd).expanduser()
    if not cwd.is_absolute() or not cwd.is_dir():
        raise HTTPException(400, 'Choose an existing absolute working directory')
    result = await daemon.rpc('thread/start', {'cwd': str(cwd), 'model': body.model, 'permissions': body.permissions, 'historyMode': 'paginated'})
    daemon.new_threads[result['thread']['id']] = result
    return result


@app.post('/api/threads/{thread}/open')
async def open_thread(thread: str):
    empty = False
    archived = False
    try:
        resumed = await daemon.rpc('thread/resume', {'threadId': thread, 'excludeTurns': True})
    except RuntimeError as exc:
        # New empty threads have no rollout to resume until their first turn.
        if 'is archived' in str(exc):
            resumed = {'thread': await daemon.metadata(thread)}
            archived = True
        elif 'no rollout found' in str(exc) and thread in daemon.new_threads:
            resumed = daemon.new_threads[thread]
            empty = True
        else:
            raise
    meta = resumed['thread']
    history = {'data': [], 'nextCursor': None} if empty else await daemon.history(thread, metadata=meta)
    return {'thread': meta, 'history': history, 'pending': daemon.pending(thread), 'model': resumed.get('model'), 'permissions': resumed.get('activePermissionProfile'), 'collaborationMode': resumed.get('collaborationMode'), 'archived': archived}


@app.post('/api/threads/{thread}/unarchive')
async def unarchive(thread: str):
    await daemon.rpc('thread/unarchive', {'threadId': thread})
    return await open_thread(thread)


@app.get('/api/threads/{thread}/history')
async def history(thread: str, cursor: str | None = None, limit: int = 20):
    meta = await daemon.metadata(thread)
    try:
        page = await daemon.history(thread, cursor, min(max(limit, 1), 100), meta)
    except RuntimeError as exc:
        if 'no rollout found' not in str(exc) or thread not in daemon.new_threads:
            raise
        page = {'data': [], 'nextCursor': None}
    return {'thread': meta, 'history': page, 'pending': daemon.pending(thread)}


class Message(BaseModel):
    text: str = Field(min_length=1, max_length=200000)
    submissionId: str = Field(min_length=8, max_length=100)
    model: str | None = None
    permissions: str | None = None
    mode: str = 'default'
    effort: str | None = None


@app.post('/api/threads/{thread}/send')
async def send(thread: str, body: Message):
    async with daemon.locks[thread]:
        row = DB.execute('SELECT thread, result FROM submissions WHERE id=?', (body.submissionId,)).fetchone()
        if row:
            if row[0] != thread:
                raise HTTPException(409, 'Submission belongs to another thread')
            if row[1] is None:
                raise HTTPException(409, 'Submission may already have started. Refresh history; it will not be resent.')
            return json.loads(row[1])
        meta = await daemon.metadata(thread)
        if meta['status']['type'] == 'active':
            raise HTTPException(409, 'A turn is already running. Stop it or wait.')
        if not body.text.strip() or body.mode not in ('default', 'plan'):
            raise HTTPException(400, 'Invalid message or mode')
        model = body.model or meta.get('model')
        if not model:
            models = (await daemon.rpc('model/list', {}))['data']
            model = next((m['model'] for m in models if m['isDefault']), models[0]['model'])
        params = {'threadId': thread, 'input': [{'type': 'text', 'text': body.text, 'text_elements': []}], 'clientUserMessageId': body.submissionId,
                  'collaborationMode': {'mode': body.mode, 'settings': {'model': model, 'reasoning_effort': body.effort, 'developer_instructions': None}}}
        if body.permissions:
            params['permissions'] = body.permissions
        DB.execute('INSERT INTO submissions VALUES (?, ?, NULL)', (body.submissionId, thread))
        DB.commit()
        # Persist before sending. A transport failure is ambiguous and must never auto-resend.
        result = await daemon.rpc('turn/start', params)
        DB.execute('UPDATE submissions SET result=? WHERE id=?', (json.dumps(result), body.submissionId))
        DB.commit()
        return result


@app.post('/api/threads/{thread}/stop')
async def stop(thread: str):
    page = await daemon.history(thread, limit=1)
    active = next((t for t in page['data'] if t['status'] == 'inProgress'), None)
    if not active:
        raise HTTPException(409, 'No active turn found; refresh history')
    return await daemon.rpc('turn/interrupt', {'threadId': thread, 'turnId': active['id']})


class Restore(BaseModel):
    beforeTurnId: str


@app.post('/api/threads/{thread}/restore')
async def restore(thread: str, body: Restore):
    async with daemon.locks[thread]:
        meta = await daemon.metadata(thread)
        if meta['status']['type'] == 'active':
            raise HTTPException(409, 'Stop the active turn before returning to a message')
        if meta.get('historyMode') != 'paginated':
            raise HTTPException(409, 'This legacy session does not support thread/revert. Create a new session to use Return to message.')
        # Find the authoritative prompt before altering history; never trust browser turn IDs/text.
        cursor = None
        target = None
        while True:
            page = await daemon.history(thread, cursor, 100, meta)
            target = next((t for t in page['data'] if t['id'] == body.beforeTurnId), None)
            cursor = page.get('nextCursor')
            if target or not cursor:
                break
        if not target:
            raise HTTPException(409, 'Message no longer exists. Refresh history.')
        text = '\n'.join(c.get('text', '') for i in target['items'] if i['type'] == 'userMessage' for c in i.get('content', []) if c['type'] == 'text')
        if not text:
            raise HTTPException(400, 'Select a user message')
        await daemon.rpc('thread/revert', {'threadId': thread, 'beforeTurnId': target['id']})
        # Every revert rewrites identifiers. Always re-read before permitting the next restore.
        fresh = await open_thread(thread)
        return {**fresh, 'text': text}


@app.post('/api/threads/{thread}/compact')
async def compact(thread: str):
    async with daemon.locks[thread]:
        meta = await daemon.metadata(thread)
        if meta['status']['type'] == 'active':
            raise HTTPException(409, 'Wait for the turn to finish before compacting')
        return await daemon.rpc('thread/compact/start', {'threadId': thread})


class PromptReply(BaseModel):
    result: dict


@app.post('/api/prompts/{key}')
async def prompt_reply(key: str, body: PromptReply):
    prompt = daemon.prompts.get(key)
    if not prompt:
        raise HTTPException(409, 'This request is already resolved or expired. Refresh history.')
    result = body.result
    method = prompt['method']
    if method in ('item/commandExecution/requestApproval', 'item/fileChange/requestApproval'):
        if result.get('decision') not in ('accept', 'acceptForSession', 'decline', 'cancel'):
            raise HTTPException(400, 'Invalid approval decision')
    elif method == 'item/tool/requestUserInput':
        if not isinstance(result.get('answers'), dict):
            raise HTTPException(400, 'Answers required')
    elif method == 'mcpServer/elicitation/request':
        if result.get('action') not in ('accept', 'decline', 'cancel'):
            raise HTTPException(400, 'Invalid elicitation action')
    elif method == 'item/permissions/requestApproval':
        if result.get('scope') not in ('turn', 'session') or not isinstance(result.get('permissions'), dict):
            raise HTTPException(400, 'Invalid permissions')
    else:
        raise HTTPException(400, 'Unsupported request type. Answer in the desktop Codex client.')
    await daemon.ws.send(json.dumps({'id': prompt['id'], 'result': result}))
    daemon.prompts.pop(key, None)
    daemon.broadcast({'method': 'serverRequest/resolved', 'params': {'threadId': prompt['params'].get('threadId'), 'requestId': prompt['id']}})
    return {'ok': True}


@app.websocket('/ws')
async def websocket(ws: WebSocket):
    if not authenticated(ws.cookies.get(COOKIE)) or not check_origin(ws.headers.get('origin')):
        await ws.close(code=4403)
        return
    await ws.accept()
    queue = asyncio.Queue(maxsize=512)
    daemon.listeners.add(queue)
    subscribed = None

    async def forward():
        while True:
            event = await queue.get()
            params = event.get('params', {})
            thread = params.get('threadId') or params.get('thread', {}).get('id')
            if event['method'].startswith('web/') or thread == subscribed:
                await ws.send_json(event)

    task = asyncio.create_task(forward())
    try:
        await ws.send_json({'method': 'web/connected' if daemon.ready.is_set() else 'web/disconnected', 'params': {'error': daemon.error}})
        while True:
            message = await ws.receive_json()
            if not authenticated(ws.cookies.get(COOKIE)):
                await ws.close(code=4401)
                break
            if message.get('type') == 'subscribe':
                subscribed = message.get('threadId')
                await ws.send_json({'method': 'web/resync', 'params': {}})
            elif message.get('type') == 'ping':
                await ws.send_json({'method': 'web/pong', 'params': {}})
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        daemon.listeners.discard(queue)
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


@app.get('/')
async def index():
    return FileResponse(ROOT / 'static/index.html')


app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')
