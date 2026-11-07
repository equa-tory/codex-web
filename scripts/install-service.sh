#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
# Authenticate before interrupting the working user service.
sudo -v
if [[ ! -x .venv/bin/uvicorn ]]; then
    python3 -m venv .venv
    .venv/bin/python -m pip install -r requirements.txt
fi
unit_file="$(mktemp --suffix=.service)"
trap 'rm -f "$unit_file"' EXIT
.venv/bin/python - "$unit_file" <<'PY'
import os
import pwd
import grp
import sys
from pathlib import Path
root = Path.cwd()
user = pwd.getpwuid(os.getuid())
def quote(p):
    return '"' + str(p).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'
Path(sys.argv[1]).write_text(f'''[Unit]
Description=Codex Web for Phone
After=network.target

[Service]
Type=simple
User={user.pw_name}
Group={grp.getgrgid(user.pw_gid).gr_name}
Environment={quote('HOME=' + user.pw_dir)}
WorkingDirectory={str(root).replace('%', '%%')}
ExecStart={quote(root / '.venv/bin/uvicorn')} app:app --host 0.0.0.0 --port 8771 --timeout-graceful-shutdown 10
Restart=always
RestartSec=3
UMask=0077
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
''')
PY
systemd-analyze verify --man=no "$unit_file"
sudo install -m 0644 "$unit_file" /etc/systemd/system/codex-web.service
sudo systemctl daemon-reload
systemctl --user disable --now codex-web.service 2>/dev/null || true
sudo systemctl enable codex-web.service
sudo systemctl restart codex-web.service
sudo systemctl --no-pager status codex-web.service
