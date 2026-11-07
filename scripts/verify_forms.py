"""Browser checks with synthetic prompts; no model calls or prompt replies to Codex."""
import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=['--no-sandbox'])
        page = await browser.new_page(viewport={'width': 390, 'height': 844})
        await page.goto('http://127.0.0.1:8771/')
        await page.fill('#password', json.loads((ROOT / '.local/config.json').read_text())['password'])
        await page.click('#loginForm button')
        await page.locator('.session').first.wait_for()
        replies = []
        async def intercept(route):
            replies.append(route.request.post_data_json['result'])
            await route.fulfill(json={'ok': True})
        await page.route('**/api/prompts/**', intercept)
        await page.evaluate('''() => {
            state.thread={id:'synthetic',cwd:'/tmp',status:{type:'idle'}};
            $('app').classList.add('chat-open'); $('chat').hidden=false;
        }''')
        async def prompt(method, params):
            await page.evaluate('''({method, params})=>{
                state.pending=[{key:'synthetic',id:77,method,params}]; renderPrompts();
            }''', {'method': method, 'params': params})
        for method in ['item/commandExecution/requestApproval', 'item/fileChange/requestApproval']:
            await prompt(method, {'threadId': 'synthetic', 'reason': 'Synthetic UI test', 'command': 'printf test', 'changes': []})
            await page.get_by_role('button', name='Allow once', exact=True).click()
            await page.locator('.prompt').wait_for(state='hidden')
            assert replies[-1] == {'decision': 'accept'}
        await prompt('item/permissions/requestApproval', {'permissions': {'network': {'enabled': True}, 'fileSystem': None}})
        await page.get_by_role('button', name='Deny', exact=True).click()
        await page.locator('.prompt').wait_for(state='hidden')
        assert replies[-1] == {'permissions': {}, 'scope': 'turn'}
        await prompt('item/tool/requestUserInput', {'questions': [{'id': 'q', 'question': 'Choose one', 'options': [{'label': 'Blue', 'description': 'A calm color'}, {'label': 'Green', 'description': 'A fresh color'}]}]})
        await page.locator('.prompt input[value="Green"]').check()
        await page.get_by_role('button', name='Send answers', exact=True).click()
        await page.locator('.prompt').wait_for(state='hidden')
        assert replies[-1]['answers']['q']['answers'] == ['Green']
        schema = {'type': 'object', 'properties': {'name': {'type': 'string'}, 'count': {'type': 'integer', 'minimum': 1}, 'enabled': {'type': 'boolean'}, 'color': {'type': 'string', 'enum': ['red', 'blue']}, 'tags': {'type': 'array', 'items': {'enum': ['a', 'b']}}}, 'required': ['name', 'count']}
        await prompt('mcpServer/elicitation/request', {'mode': 'form', 'serverName': 'test', 'message': 'Synthetic MCP form', 'requestedSchema': schema})
        await page.get_by_label('name', exact=True).fill('Phone')
        await page.get_by_label('count', exact=True).fill('3')
        await page.get_by_label('enabled', exact=True).check()
        await page.get_by_label('color', exact=True).select_option('blue')
        await page.get_by_label('tags', exact=True).select_option(['a', 'b'])
        await page.get_by_role('button', name='Submit', exact=True).click()
        await page.locator('.prompt').wait_for(state='hidden')
        assert replies[-1] == {'action': 'accept', 'content': {'name': 'Phone', 'count': 3, 'enabled': True, 'color': 'blue', 'tags': ['a', 'b']}, '_meta': None}
        await page.evaluate('''() => {
            $('messages').replaceChildren(md('<img src=x onerror="window.xss=true"><script>window.xss=true</script>[bad](javascript:alert(1))'));
        }''')
        assert not await page.evaluate('window.xss === true')
        assert await page.locator('#messages img, #messages script, #messages a[href^="javascript:"]').count() == 0
        await browser.close()
        print('PASS: command/file approvals, permission denial, suggested answer, typed MCP form, Markdown XSS filtering')


if __name__ == '__main__':
    asyncio.run(main())
