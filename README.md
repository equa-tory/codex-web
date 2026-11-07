# Codex Web for Phone

A small, password-protected FastAPI interface to the Codex daemon already running on your computer. Open **http://192.168.1.50:8771** on the same LAN.

Browse sessions by last activity, search titles or full working paths, filter archived sessions, and continue a conversation. Archived sessions open for reading; tap Unarchive to continue them. Replies stream with Markdown, code blocks, and expandable tool activity. Questions, approvals, and MCP forms appear above the composer. Closing the browser leaves the turn running.

## Run

Requires Python 3.12+, a running Codex app-server daemon, and systemd for the persistent service. Developed against daemon **0.167.0**, using its generated experimental protocol. The backend attaches to its Unix WebSocket at `~/.codex/app-server-control/app-server-control.sock`; it does not launch another Codex process or read your OAuth tokens.

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app:app --host 0.0.0.0 --port 8771
```

On first start, `.local/config.json` is created with a generated password, signing secret, installation ID, and allowed origins. Its permissions are `0600`; `.local/` is `0700` and ignored by Git. Read the password locally:

```sh
.venv/bin/python -c 'import json; print(json.load(open(".local/config.json"))["password"])'
```

The default allowed origins are `http://192.168.1.50:8771`, `http://127.0.0.1:8771`, and `http://localhost:8771`. If the machine address changes, update `origins` in that private file and restart. `CODEX_WEB_STATE` and `CODEX_WEB_SOCKET` can override the runtime directory and socket path. Use exactly one Uvicorn worker so there is one daemon connection and one prompt owner.

For a persistent service, stop any manual listener first and run:

```sh
./scripts/install-service.sh
systemctl --user status codex-web
systemctl --user restart codex-web
systemctl --user stop codex-web
journalctl --user -u codex-web -f
```

The installer checks port 8771 immediately before installing a service for the current checkout. `codex-web.service` is the delivered service for `/home/equa/git/codex-web`. The service restarts on failure. User lingering must be enabled for operation after logout (`loginctl show-user "$USER" -p Linger`); it is already enabled on the delivery machine.

## Chat controls

- **Actions → Plan / Code** chooses the mode for the next submitted turn. The selected mode remains beside the composer.
- **Actions → Return to message** selects a previous user prompt, removes its turn and later history with `thread/revert`, then places the prompt in the composer. Stop or finish active work first. **Files are not restored.** Each restore refreshes history and turn IDs, allowing repeated restore/continue cycles.
- `/new` opens a working-directory, model, and permissions form. The directory defaults to the current session. Models, reasoning efforts, modes, and permission profiles come from the daemon.
- `/resume`, `/model`, `/plan`, `/permissions`, `/status`, `/compact`, and `/help` are available from `/`. Unknown commands open help. Ctrl/Cmd+Enter sends; Enter adds a line.
- **Stop** interrupts the active turn. The same session remains usable afterward.

New sessions use paginated history. Older `legacy` sessions can be read and continued, with locally paginated display; the daemon only supports `thread/revert` for paginated sessions, so Return to message is disabled for legacy history.

## Reconnect and authentication

The browser uses an installation-specific, HttpOnly, SameSite=Strict cookie. Drafts, selected session, and next-turn settings use installation-specific browser-storage keys. Mutation endpoints require an authenticated cookie, an exact allowed Origin, and a session-bound CSRF header. WebSockets require authentication and an allowed Origin and only carry subscriptions and events; they cannot invoke arbitrary daemon RPCs. Markdown is sanitized with a locally vendored DOMPurify. Static assets require no third-party connection.

One persistent backend WebSocket correlates RPC replies and retains outstanding questions and approvals across browser disconnects. Reconnecting refreshes authoritative history and prompts without resending input. If the daemon connection drops, an outage banner appears and the backend retries. Rejoining a thread uses `thread/resume` to recover live state.

Every message has a persisted submission ID. Duplicate submissions return their original result. An interrupted HTTP/RPC exchange is treated as uncertain and never automatically retried; the UI asks you to check history before allowing a new submission. IDs are written before contacting the daemon so service restarts cannot accidentally replay a message.

This is a trusted-LAN app served over HTTP. Passwords, chat, and cookies are not encrypted on the network. Use an HTTPS reverse proxy or a private encrypted tunnel when network confidentiality is needed, and add its exact origin to configuration. Do not publish the local configuration, runtime data, or Codex session files.

## Home Screen and phone behavior

On iPhone, open in Safari → Share → Add to Home Screen. On Android, use the browser menu → Add to Home screen. A manifest, PNG icons, and standalone metadata are included. Full PWA installation depends on browser support and generally requires HTTPS; no service worker caches authenticated conversations.

The app follows the visual viewport when the keyboard opens, uses safe-area insets and touch targets of at least 44px for primary controls, and keeps inputs at 16px or larger to prevent focus zoom. Viewport and gesture handlers disable pinch zoom where supported. iOS can override zoom restrictions; physical iPhone/Android keyboard and Home Screen behavior still require device testing.

## Verify

```sh
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
node --check static/app.js
```

The offline tests exercise authentication, cookie isolation, API/WebSocket rejection, origin/CSRF checks, path/title search and ordering, archive and history pagination, duplicate IDs, ambiguous submissions, pending prompts, daemon RPC correlation/reconnection, empty sessions, and three restore cycles with changing turn IDs.

The opt-in live test creates a disposable conversation and spends model tokens. It needs the running service and Chromium:

```sh
.venv/bin/python -m playwright install chromium
.venv/bin/python scripts/verify_live.py
.venv/bin/python scripts/verify_approval.py
```

It checks actual streaming, three restore/continue cycles (including reload), unchanged project files, a Plan-mode question answered after reload, model selection, Plan/Code switching, interruption, compaction, and mobile viewport/composer geometry. Results and screenshots are stored under ignored `.local/`. Run `.venv/bin/python scripts/verify_forms.py` for synthetic browser form and Markdown checks without model calls. See [VERIFICATION.md](VERIFICATION.md) for the delivery results.

The initial implementation follows the [official Codex app-server lifecycle](https://developers.openai.com/siwc/token-sharing-open-source/codex-app-server), with method payloads checked against TypeScript generated by the installed daemon. This protocol includes experimental fields and may need adaptation after daemon upgrades.

Vendored libraries: marked 15.0.12 (MIT) and DOMPurify 3.2.6 (MPL-2.0 or Apache-2.0); licenses are in `static/vendor/`.
