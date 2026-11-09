"""Start the local server, then open the workbench once it is ready.

If Crab Studio is already running with older code (for example after an update), that old server is stopped first,
so the new version is the one you use."""
import hashlib
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

url = 'http://127.0.0.1:8775'   # Crab Studio port (Bittle Studio uses 8765)
PORT = 8775
HERE = Path(__file__).resolve().parent
CODE = ['server.py', 'scripting.py', 'script_worker.py', 'crab_lab.py', 'engine.py', 'skills.py']


def code_version():
    """Same fingerprint as server.CODE_VERSION: changes whenever the Studio code changes."""
    h = hashlib.sha256()
    for name in CODE:
        h.update((HERE / name).read_bytes())
    return h.hexdigest()[:12]


def ready():
    try:
        with urllib.request.urlopen(url + '/api/model', timeout=1) as response:
            return response.status == 200
    except OSError:
        return False


def running_version():
    try:
        with urllib.request.urlopen(url + '/api/crab/version', timeout=2) as response:
            return json.loads(response.read()).get('version')
    except urllib.error.HTTPError:
        return 'old'
    except (OSError, ValueError):
        return None


def listening_pids():
    pids = set()
    if sys.platform == 'win32':
        out = subprocess.run(['netstat', '-ano', '-p', 'TCP'], capture_output=True, text=True, errors='replace').stdout
        for line in out.splitlines():
            parts = line.split()
            # TCP  127.0.0.1:8775  0.0.0.0:0  LISTENING  1234   (the state word is translated on some Windows versions)
            if len(parts) >= 5 and parts[0] == 'TCP' and parts[1].endswith(f':{PORT}') and parts[2].endswith(':0'):
                pids.add(parts[-1])
    else:
        out = subprocess.run(['lsof', '-ti', f'tcp:{PORT}', '-sTCP:LISTEN'], capture_output=True, text=True).stdout
        pids.update(out.split())
    return pids


def stop_old_server():
    print('An older Crab Studio is still running: stopping it so the new version starts.')
    for pid in listening_pids():
        if sys.platform == 'win32':
            subprocess.run(['taskkill', '/PID', pid, '/F'], capture_output=True)
        else:
            subprocess.run(['kill', pid], capture_output=True)
    for _ in range(40):
        if not ready():
            return True
        time.sleep(.25)
    return False


def start():
    process = subprocess.Popen([sys.executable, 'server.py'], cwd=HERE)
    try:
        for _ in range(120):
            if process.poll() is not None:
                raise SystemExit('Server could not start; see the error above.')
            if ready():
                webbrowser.open(url)
                break
            time.sleep(.5)
        else:
            print('Still starting. Open ' + url + ' once the server is ready.')
        process.wait()
    except KeyboardInterrupt:
        process.terminate()
        process.wait()


if ready() and running_version() not in (None, code_version()):
    if not stop_old_server():
        raise SystemExit('Could not stop the old Crab Studio. Close its black window, then start Crab Studio again.')
if ready():
    webbrowser.open(url)
    print('Crab Studio is already running. Reusing the existing simulation.')
else:
    start()
