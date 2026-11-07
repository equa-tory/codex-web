"""Opt-in live integration test. Creates a disposable thread and uses model tokens.
Run from the checkout with .venv/bin/python scripts/verify_live.py.
"""
import asyncio
import hashlib
import json
from pathlib import Path
import time

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]


async def main():
    config = json.loads((ROOT / '.local/config.json').read_text())
    workspace = ROOT / '.local' / f'verification-{int(time.time())}'
    workspace.mkdir()
    sentinel = workspace / 'sentinel.txt'
    sentinel.write_text('Project files must remain unchanged by chat restore.\n')
    before = hashlib.sha256(sentinel.read_bytes()).hexdigest()
    result = {'workspace': str(workspace), 'restoreCycles': 0}
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox'])
        context = await browser.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True)
        page = await context.new_page()
        errors, events = [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        def websocket(ws):
            def frame(raw):
                try:
                    events.append(json.loads(raw))
                except (ValueError, TypeError):
                    pass
            ws.on('framereceived', frame)
        page.on('websocket', websocket)
        await page.goto('http://127.0.0.1:8771/')
        await page.fill('#password', config['password'])
        await page.click('#loginForm button')
        await page.locator('.session').first.wait_for()
        await page.click('#new')
        await page.get_by_label('Working directory', exact=True).fill(str(workspace))
        await page.get_by_label('Model', exact=True).select_option('gpt-6-luna')
        await page.get_by_label('Permissions', exact=True).select_option(':read-only')
        await page.get_by_role('button', name='Create session', exact=True).click()
        await page.locator('#dialog').wait_for(state='hidden')
        await page.wait_for_function('() => state.thread?.cwd && !state.loading')
        result['thread'] = await page.evaluate('state.thread.id')
        async def submit(text):
            await page.fill('#message', text)
            await page.click('#send')
            await page.wait_for_function('() => !state.sending && !state.loading && state.turns.length > 0 && state.turns.at(-1).status !== "inProgress"', timeout=120000)
            await page.wait_for_timeout(500)
        await submit('Reply with exactly CYCLE_ZERO. Do not use any tools or change files.')
        for i in range(3):
            if i == 1:
                await page.reload()
                await page.wait_for_function('() => state.thread && !state.loading && state.turns.length > 0')
            await page.click('#actions')
            await page.click('#returnMessage')
            page.once('dialog', lambda d: d.accept())
            await page.locator('#dialogBody button.wide').first.click()
            await page.locator('#dialog').wait_for(state='hidden')
            assert 'Reply with exactly' in await page.input_value('#message')
            await submit(f'Reply with exactly CYCLE_{i+1}. Do not use any tools or change files.')
            assert len(await page.evaluate('state.turns')) == 1
            assert before == hashlib.sha256(sentinel.read_bytes()).hexdigest()
            result['restoreCycles'] += 1
        # Duplicate HTTP submission is also rejected/cached server-side.
        assert len(list(workspace.iterdir())) == 1
        result['filesUnchanged'] = True
        result['streaming'] = any(e['method'] == 'item/agentMessage/delta' for e in events)
        await page.click('#actions')
        await page.click('#planMode')
        assert await page.inner_text('#modeLabel') == 'Plan'
        await page.fill('#message', 'Use request_user_input now to ask me which color I prefer, with Blue and Green choices. Wait for my answer. This is a UI integration test, so do not use other tools or change files.')
        await page.click('#send')
        await page.locator('.prompt').wait_for(timeout=120000)
        result['aiQuestion'] = True
        await page.screenshot(path=str(ROOT / '.local/question-mobile.png'))
        # Reload while the request is waiting. Type a free-text answer after reconnect.
        await page.reload()
        await page.locator('.prompt textarea').first.wait_for(timeout=30000)
        await page.locator('.prompt textarea').first.fill('Blue, with a little green')
        await page.get_by_role('button', name='Send answers', exact=True).click()
        await page.wait_for_function('() => !state.loading && state.turns.at(-1)?.status !== "inProgress"', timeout=120000)
        result['pendingRecoveredAfterReload'] = True
        await page.click('#actions')
        await page.click('#codeMode')
        await page.click('#modelLabel')
        await page.get_by_label('Model', exact=True).select_option('gpt-6.1-sol')
        await page.get_by_role('button', name='Use model', exact=True).click()
        assert 'gpt-6.1-sol' in await page.inner_text('#modelLabel')
        await submit('Reply exactly MODEL_OK. Do not use tools or change files.')
        assert await page.evaluate('state.thread.model') == 'gpt-6.1-sol'
        result['modelSelection'] = True
        # Restore test used Luna; keep the disposable test inexpensive for the long interrupted turn.
        await page.evaluate("state.model='gpt-6-luna'; saveSettings()")
        await page.fill('#message', 'Use a terminal command to run sleep 30, then reply DONE. Do not change any files.')
        await page.click('#send')
        await page.locator('#stop').wait_for(state='visible')
        await page.wait_for_timeout(1500)
        await page.click('#stop')
        await page.wait_for_function('() => !state.loading && state.turns.at(-1)?.status === "interrupted"', timeout=30000)
        result['interruption'] = True
        await page.evaluate("command('compact')")
        await page.wait_for_timeout(2000)
        await page.wait_for_function('() => state.thread?.status?.type !== "active"', timeout=120000)
        result['compaction'] = True
        # Follow a reduced visual viewport (keyboard geometry) and check touch sizes.
        await page.set_viewport_size({'width':390,'height':480})
        await page.focus('#message')
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert await page.locator('#message').evaluate('(n)=>parseFloat(getComputedStyle(n).fontSize) >= 16')
        assert await page.locator('#send').evaluate('(n)=>n.getBoundingClientRect().height >= 44')
        assert await page.locator('#composer').evaluate('(n)=>n.getBoundingClientRect().bottom <= innerHeight')
        result['mobileLayout'] = True
        assert not errors, errors
        result['browserErrors'] = errors
        assert before == hashlib.sha256(sentinel.read_bytes()).hexdigest()
        await browser.close()
    (ROOT / '.local/live-results.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
