"""Bittle Link packs: console buttons plus compiled firmware skills, stored as JSON in saved-motions/.

Standard library only, so the lightweight Bittle Link server can use it without the simulator.
"""
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

PACK_FORMAT = 'bittle-link-pack'
CONTROL_COLORS = ('green', 'blue', 'amber', 'red', 'violet', 'grey')
MAX_PACK_BYTES = 2_000_000


def validate_controls(items):
    """Console buttons: a name plus a sequence of Petoi commands or saved Studio functions."""
    if not isinstance(items, list) or len(items) > 60:
        raise ValueError('The console holds at most 60 custom buttons')
    result = []
    for raw in items:
        if not isinstance(raw, dict):
            raise ValueError('Invalid console button')
        name = str(raw.get('name', '')).strip()[:40]
        if not name:
            raise ValueError('Every console button needs a name')
        color = raw.get('color', 'green')
        if color not in CONTROL_COLORS:
            raise ValueError('Unknown console button colour')
        steps = raw.get('steps')
        if not isinstance(steps, list) or not 1 <= len(steps) <= 30:
            raise ValueError(f'“{name}” needs between 1 and 30 steps')
        clean = []
        for step in steps:
            if not isinstance(step, dict) or step.get('kind') not in ('command', 'motion'):
                raise ValueError(f'“{name}” contains an invalid step')
            wait = int(step.get('wait_ms', 0))
            if not 0 <= wait <= 60000:
                raise ValueError('Step waits must be 0–60000 ms')
            if step['kind'] == 'command':
                command = str(step.get('command', '')).strip()
                if not command or len(command) > 64 or any(not ' ' <= ch <= '~' for ch in command):
                    raise ValueError(f'“{name}” has a command that is not a printable Petoi command')
                clean.append({'kind': 'command', 'command': command, 'wait_ms': wait})
            else:
                motion_id = str(step.get('motion_id', ''))[:64]
                if not motion_id:
                    raise ValueError(f'“{name}” has a Studio function step without a function')
                clean.append({'kind': 'motion', 'motion_id': motion_id, 'wait_ms': wait})
        result.append({'id': str(raw.get('id') or uuid.uuid4().hex)[:64], 'name': name, 'color': color,
                       'repeat': bool(raw.get('repeat', False)), 'steps': clean})
    if len({item['id'] for item in result}) != len(result):
        raise ValueError('Console button IDs must be unique')
    return result


def validate_pack(pack):
    if not isinstance(pack, dict) or pack.get('format') != PACK_FORMAT or pack.get('version') != 1:
        raise ValueError('This is not a Bittle Link pack')
    skills = []
    for raw in pack.get('skills', []):
        values = raw.get('skill') if isinstance(raw, dict) else None
        if not raw.get('id') or not raw.get('name') or not isinstance(values, list) or not values \
                or any(not isinstance(v, int) or isinstance(v, bool) or not -128 <= v <= 255 for v in values):
            raise ValueError('The pack contains an invalid skill')
        skills.append({'id': str(raw['id'])[:64], 'name': str(raw['name'])[:80],
                       'motion_type': raw.get('motion_type', raw.get('type', 'behavior')),
                       'type': raw.get('type', raw.get('motion_type', 'behavior')),
                       'signature': str(raw.get('signature', '')), 'hz': float(raw.get('hz', 20) or 20), 'skill': values})
    return {'format': PACK_FORMAT, 'version': 1, 'name': str(pack.get('name', ''))[:80],
            'exported': pack.get('exported') or datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'controls': validate_controls(pack.get('controls', [])), 'skills': skills}


def pack_filename(name):
    """A safe file name inside saved-motions/ for a human pack name."""
    stem = re.sub(r'[^A-Za-z0-9 _-]+', '', str(name)).strip().replace(' ', '-')[:60].strip('-_')
    if not stem:
        raise ValueError('Give the pack a name using letters or numbers')
    return stem + '.json'


def pack_path(folder, filename):
    path = (Path(folder) / filename).resolve()
    if path.parent != Path(folder).resolve() or path.suffix != '.json':
        raise ValueError('Invalid pack file name')
    return path


def list_packs(folder):
    packs = []
    for path in sorted(Path(folder).glob('*.json'), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            if data.get('format') != PACK_FORMAT:
                continue
            packs.append({'file': path.name, 'name': data.get('name') or path.stem,
                          'controls': len(data.get('controls', [])), 'skills': len(data.get('skills', [])),
                          'modified': datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec='seconds')})
        except (OSError, ValueError, AttributeError):
            continue
    return packs


def save_pack(folder, name, pack):
    clean = validate_pack({**pack, 'name': name})
    path = pack_path(folder, pack_filename(name))
    text = json.dumps(clean, indent=1, ensure_ascii=False)
    if len(text.encode('utf-8')) > MAX_PACK_BYTES:
        raise ValueError('The pack is too large')
    temporary = path.with_suffix('.tmp')
    temporary.write_text(text, encoding='utf-8')
    temporary.replace(path)
    return {'file': path.name, 'name': clean['name'], 'controls': len(clean['controls']), 'skills': len(clean['skills'])}
