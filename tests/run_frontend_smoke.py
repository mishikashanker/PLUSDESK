"""Optional Node/happy-dom smoke test. It is a DOM test, not a browser render."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request
from alembic import command
from alembic.config import Config

if __name__ == '__main__':
    root = Path(__file__).resolve().parent.parent
    os.chdir(root)
    with tempfile.TemporaryDirectory() as temporary:
        url = 'sqlite:///' + (Path(temporary) / 'ui.db').as_posix()
        os.environ['DATABASE_URL'] = url
        command.upgrade(Config('alembic.ini'), 'head')
        environment = {**os.environ, 'MODE':'demo', 'DATABASE_URL':url,
            'DASHBOARD_USER':'admin','DASHBOARD_PASSWORD':'demo-pulsedesk',
            'SECURE_COOKIES':'false','SLACK_BOT_TOKEN':'','SEED_DEMO':'true',
            'EMBEDDED_WORKER':'true','PULSETEST_URL':'http://127.0.0.1:8765',
            'DIGEST_HOUR':'23','DIGEST_MINUTE':'59'}
        with open(Path(temporary) / 'server.log', 'w') as log:
            server = subprocess.Popen([sys.executable,'-m','uvicorn','app.main:create_app',
                '--factory','--host','127.0.0.1','--port','8765','--no-access-log'],
                env=environment,stdout=log,stderr=log)
            try:
                for _ in range(50):
                    try:
                        urllib.request.urlopen('http://127.0.0.1:8765/health/live',timeout=1)
                        break
                    except Exception:
                        time.sleep(.1)
                result = subprocess.run(['node','tests/frontend_smoke.mjs'],env=environment,timeout=55)
                raise SystemExit(result.returncode)
            finally:
                server.terminate()
                server.wait(timeout=5)
