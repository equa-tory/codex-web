# Verification

Verified on the delivery host with Codex daemon 0.167.0 and Chromium mobile emulation (390 × 844, plus a reduced 390 × 480 visual viewport).

- Offline suite: 17 tests pass. Covers auth/cookie separation, origin and CSRF checks, unauthorized APIs/WebSockets, ordering/search/archive/history pagination and read-only archived sessions, uncertain and duplicate submissions, prompt replies, daemon RPC correlation and reconnect, empty sessions, and changing restore identifiers.
- Live daemon conversations: streamed replies; three restore/continue cycles, including after page reload; test project file SHA-256 unchanged across restores. The directory contained only its original sentinel file afterward.
- A real Plan-mode question recovered after reload and accepted a free-text response. Synthetic questions also accepted suggested choices.
- Plan/Code selection, daemon-provided model selection, Stop/interruption, and compaction exercised against the real daemon.
- A real command approval recovered after reload and accepted Allow once. The harmless `printf APPROVAL_OK` command completed.
- Synthetic browser checks: command/file approval payloads, permission denial, MCP string/integer/boolean/enum/multiselect values, and Markdown XSS removal.
- Archived-session browser: real archived history opens without changing archive status; Unarchive explicitly enables continuing it.
- Mobile browser: no JavaScript errors or horizontal overflow; readable 16px inputs, 44px primary buttons, Actions menu, persisted mode, and composer within the reduced viewport.
- Systemd user service enabled with automatic restart and user lingering. Listener bound to 0.0.0.0:8771; LAN HTTP checked; killing the service process triggered automatic restart, and its existing login cookie remained valid.

Physical iPhone/Android keyboard behavior, Safari zoom restrictions, and Add to Home Screen were not tested on hardware. Full PWA installation over HTTP depends on the browser. Legacy daemon history can be read/continued, but the daemon restricts `thread/revert` to paginated sessions.

Reproduce offline checks with `.venv/bin/python -m pytest -q`. The opt-in `scripts/verify_live.py` creates disposable Codex conversations and uses model tokens. `scripts/verify_forms.py` checks synthetic form UI without model calls. Screenshots, private configuration, test workspace files, and detailed live results stay under ignored `.local/`.
