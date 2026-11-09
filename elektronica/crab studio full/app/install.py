"""Install Bittle Studio on Windows, macOS or Linux (called by the Setup launchers): create .venv, install the
Python packages and the 3D viewer library. Run again after getting a new version, it installs whatever changed.

Only the Python standard library is used here, so any Python 3.10-3.12 can run it.
The 3D viewer library (three.js) is downloaded straight from the npm registry and checked against
app/package-lock.json, so Node.js is not needed.
"""
import base64
import hashlib
import io
import json
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import venv
from pathlib import Path

APP = Path(__file__).resolve().parent
ROOT = APP.parent
VENV = ROOT / '.venv'
VENV_PYTHON = VENV / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')


def step(text):
    print(f'\n=== {text} ===', flush=True)


def fail(text):
    print(f'\nSetup stopped: {text}', flush=True)
    sys.exit(1)


def python_packages():
    if not (3, 10) <= sys.version_info[:2] <= (3, 12):
        print(f'Warning: this is Python {sys.version.split()[0]}. Bittle Studio is tested with Python 3.11 and 3.12; '
              'the physics package (pybullet) may not install on other versions.')
    if not VENV_PYTHON.exists():
        step(f'Creating the Python environment in {VENV.name}')
        venv.create(VENV, with_pip=True)
    step('Installing the Python packages (this can take a few minutes the first time)')
    if subprocess.call([str(VENV_PYTHON), '-m', 'pip', 'install', '--disable-pip-version-check', '-r', str(APP / 'requirements.txt')]):
        hint = {'darwin': ' On a Mac, run "xcode-select --install" once and try again.',
                'linux': ' On Linux, install your distribution\'s python3-venv and build-essential packages and try again.'}
        fail('the Python packages could not be installed.' + hint.get(sys.platform, ''))


def viewer_library():
    lock = json.loads((APP / 'package-lock.json').read_text(encoding='utf-8'))
    entry = lock['packages']['node_modules/three']
    target = APP / 'node_modules' / 'three'
    stamp = target / '.installed-version'
    if stamp.exists() and stamp.read_text().strip() == entry['version']:
        return
    step(f'Downloading the 3D viewer library (three.js {entry["version"]})')
    with urllib.request.urlopen(entry['resolved'], timeout=120) as response:
        data = response.read()
    algorithm, expected = entry['integrity'].split('-', 1)
    if base64.b64encode(hashlib.new(algorithm, data).digest()).decode() != expected:
        fail('the downloaded 3D viewer library does not match package-lock.json. Try again later.')
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        for member in archive.getmembers():
            # npm tarballs keep everything under package/; skip anything that would escape the target folder.
            name = Path(member.name)
            if name.parts[:1] != ('package',) or '..' in name.parts or not (member.isfile() or member.isdir()):
                continue
            destination = target.joinpath(*name.parts[1:])
            if member.isdir():
                destination.mkdir(parents=True, exist_ok=True)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(archive.extractfile(member).read())
    stamp.write_text(entry['version'])


def main():
    python_packages()
    viewer_library()
    (ROOT / 'saved-motions').mkdir(exist_ok=True)
    step('Ready')
    print('Start Bittle Studio with the "Start Bittle Studio" launcher in the launchers folder for your system.')


if __name__ == '__main__':
    main()
