#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python - <<'PY'
import socket
with socket.socket() as s:
    try:
        s.bind(('0.0.0.0', 8771))
    except OSError:
        raise SystemExit('Port 8771 is occupied. Stop the existing listener before installing.')
PY
.venv/bin/python - <<'PY'
from pathlib import Path
root = Path.cwd()
unit = Path.home() / '.config/systemd/user/codex-web.service'
unit.parent.mkdir(parents=True, exist_ok=True)
# Systemd expands percent specifiers even inside quoted paths.
def quote(p):
    return '"' + str(p).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'
unit.write_text(f'''[Unit]
Description=Codex Web for Phone
After=network.target

[Service]
Type=simple
WorkingDirectory={quote(root)}
ExecStart={quote(root / '.venv/bin/uvicorn')} app:app --host 0.0.0.0 --port 8771 --timeout-graceful-shutdown 10
Restart=always
RestartSec=3
UMask=0077
NoNewPrivileges=true

[Install]
WantedBy=default.target
''')
PY
systemctl --user daemon-reload
systemctl --user enable --now codex-web.service
printf '%s\n' 'Service installed. Credentials are in .local/config.json (mode 0600).'
