"""Opt-in real command approval check; creates a disposable thread and uses model tokens. Run from the checkout root."""
import asyncio,json,time
from pathlib import Path
import httpx
from websockets.asyncio.client import unix_connect
from playwright.async_api import async_playwright

async def rpc(ws,i,method,params):
 await ws.send(json.dumps({'id':i,'method':method,'params':params}))
 while True:
  r=json.loads(await ws.recv())
  if r.get('id')==i:
   if 'error' in r: raise Exception(r['error'])
   return r['result']

async def main():
 async with unix_connect(str(Path.home()/'.codex/app-server-control/app-server-control.sock'),max_size=64*1024*1024) as ws:
  await rpc(ws,1,'initialize',{'clientInfo':{'name':'codex_web_test','version':'1.0'},'capabilities':{'experimentalApi':True}})
  await ws.send(json.dumps({'method':'initialized','params':{}}))
  r=await rpc(ws,2,'thread/start',{'cwd':'/tmp','model':'gpt-6-luna','historyMode':'paginated','sandbox':'read-only','approvalPolicy':'on-request','approvalsReviewer':'user'})
  tid=r['thread']['id'];print('thread',tid,flush=True)
  # First turn materializes the rollout before the phone opens it.
  await rpc(ws,3,'turn/start',{'threadId':tid,'input':[{'type':'text','text':'Reply READY. No tools.','text_elements':[]}]})
  while True:
   event=json.loads(await ws.recv())
   if event.get('method')=='turn/completed':break
  async with async_playwright() as p:
   browser=await p.chromium.launch(args=['--no-sandbox'])
   page=await browser.new_page(viewport={'width':390,'height':844})
   await page.goto('http://127.0.0.1:8771/')
   await page.fill('#password',json.loads(Path('.local/config.json').read_text())['password'])
   await page.click('#loginForm button')
   await page.locator('.session').first.wait_for()
   await page.evaluate('(id)=>openThread(id)',tid)
   await page.fill('#message','This is an approval UI integration test. Use exec_command to run exactly printf APPROVAL_OK with sandbox_permissions="require_escalated" and justification="May I run the harmless approval UI test?". Do not change files. Wait for my approval. If using code mode, call the exec_command tool from there with these same parameters.')
   await page.click('#send')
   try:
    await page.get_by_role('button',name='Allow once',exact=True).wait_for(timeout=90000)
    await page.screenshot(path='.local/approval-mobile.png')
    await page.reload()
    await page.get_by_role('button',name='Allow once',exact=True).wait_for(timeout=30000)
    await page.get_by_role('button',name='Allow once',exact=True).click()
    await page.wait_for_function('() => !state.loading && state.turns.at(-1)?.status !== "inProgress"',timeout=60000)
    print('APPROVAL PASSED',flush=True)
    print((await page.locator('#messages').inner_text())[-2000:])
   except Exception:
    print('APPROVAL FAILURE',await page.locator('#messages').inner_text(),await page.locator('#pending').inner_text(),flush=True)
    raise
   finally: await browser.close()
asyncio.run(main())
