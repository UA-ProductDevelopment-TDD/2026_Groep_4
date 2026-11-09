"""Bittle Studio: local HTTP interface. Started by the "Start Bittle Studio" launchers (app/launcher.py)."""
import io
import json
import math
import os
import threading
import uuid
import zipfile
import xml.etree.ElementTree as ET
from contextlib import asynccontextmanager
from pathlib import Path

import pybullet as p
import httpx
import trimesh
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from engine import ASSETS, DATA, ROOT, SAVED_MOTIONS, Simulation, asset_path, asset_url, upgrade_bundled_robot, url_path, vector
from motion_export import build_motion, firmware_skill, python_motion, python_script, sequence_samples
from scripting import ScriptRunner, validate_scripts
import skills
from packs import list_packs, pack_path, save_pack, validate_controls

sim = None
runner = None
AUTOSAVE = False  # Enabled by the desktop entry point; tests use isolated in-memory worlds.
voice_api_key = os.environ.get('OPENAI_API_KEY', '')
VOICE_MODEL = os.environ.get('OPENAI_REALTIME_MODEL', 'gpt-realtime-2.1')


def validate_motions(items, world=None):
    world = world or sim
    if not isinstance(items, list) or len(items) > 100:
        raise ValueError('Motion library must be a list of at most 100 motions')
    result = []
    for raw in items:
        if not isinstance(raw, dict):
            raise ValueError('Invalid saved motion')
        item = dict(raw)
        item['id'] = str(item.get('id') or uuid.uuid4().hex)[:64]
        item['name'] = str(item.get('name', '')).strip()[:80]
        if not item['name']:
            raise ValueError('Every saved motion needs a name')
        item['motion_type'] = item.get('motion_type', 'behavior')
        if item['motion_type'] not in ('pose', 'behavior', 'gait'):
            raise ValueError('Motion type must be pose, behavior or gait')
        item['hz'] = float(item.get('hz', 50))
        item['speed'] = float(item.get('speed', 1))
        item['direction'] = item.get('direction', 'forward')
        if not 1 <= item['hz'] <= 240 or item['speed'] not in (.25, .5, 1, 2):
            raise ValueError('Invalid saved motion speed or Hz')
        if item['direction'] not in ('forward', 'reverse'):
            raise ValueError('Invalid saved motion direction')
        # Use the same joint-limit validation as the timeline.
        original = world.frames
        try:
            world.set_frames(item.get('frames', []))
            item['frames'] = world.frames
        finally:
            world.frames = original
        source_pose = item.get('pose') or (item['frames'][-1]['pose'] if item['frames'] else world.targets)
        item['pose'] = {}
        for joint in world.joints:
            value = float(source_pose.get(joint['name'], 0))
            if not math.isfinite(value) or not joint['lower'] - .01 <= value <= joint['upper'] + .01:
                raise ValueError('Saved pose exceeds joint limits: ' + joint['name'])
            item['pose'][joint['name']] = value
        result.append(item)
    if len({item['id'] for item in result}) != len(result):
        raise ValueError('Saved motion IDs must be unique')
    return result


def checkpoint():
    if AUTOSAVE:
        temporary = DATA / 'studio-session.tmp'
        temporary.write_text(json.dumps(get_project(), allow_nan=False), encoding='utf-8')
        temporary.replace(DATA / 'studio-session.json')


@asynccontextmanager
async def lifespan(app):
    global sim, runner
    sim = Simulation()
    runner = ScriptRunner(sim)
    if AUTOSAVE and (DATA / 'studio-session.json').is_file():
        try:
            load_project(json.loads((DATA / 'studio-session.json').read_text(encoding='utf-8')))
        except Exception as error:
            print('Could not restore the last session:', error)
    if AUTOSAVE:
        try:
            crab_sync_scripts()
            import crab_lab
            claw = crab_lab.claw_fitted()
            with sim.lock:
                crab_load_model(claw)
            table = json.loads(crab_lab.SKILLS_JSON.read_text(encoding='utf-8'))
            if table.get('claw') != claw:              # skill table from the other claw setting (or older Studio)
                results = crab_lab.load_results(claw)
                if results.get('gaits'):
                    crab_lab.write_skill_table(dict(results, claw=claw))
            crab_functions()
        except Exception as error:
            print('Could not update the crab scripts and functions:', error)
    # Every Studio start shows the robots in the zero pose; the restored timeline and settings are kept.
    for robot in (sim, *sim.actors.values()):
        robot.pose({joint['name']: 0. for joint in robot.joints})
    thread = threading.Thread(target=sim.worker, daemon=True)
    thread.start()
    yield
    runner.stop()
    checkpoint()
    sim.closed = True
    thread.join(2)
    p.disconnect(sim.client)


app = FastAPI(lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', 'testserver'])


@app.middleware('http')
async def local_only(request: Request, call_next):
    # Reject cross-origin browser mutations; bind to loopback in the launcher.
    origin = request.headers.get('origin')
    if request.method not in ('GET', 'HEAD') and origin and origin != str(request.base_url).rstrip('/'):
        return JSONResponse({'detail': 'Only this local workbench may change the simulation'}, 403)
    try:
        response = await call_next(request)
    except (ValueError, KeyError, TypeError, SyntaxError, ET.ParseError, zipfile.BadZipFile, p.error) as error:
        return JSONResponse({'detail': str(error)}, 400)
    # The page and its scripts must always be revalidated, or an update leaves the browser on stale code.
    if request.url.path == '/' or request.url.path.startswith('/web/'):
        response.headers['Cache-Control'] = 'no-cache'
    return response


@app.get('/')
def index():
    return FileResponse(ROOT / 'web/index.html')


@app.get('/api/model')
def model():
    with sim.lock:
        return sim.model()


@app.get('/api/state')
def state():
    with sim.lock:
        return sim.state()


def validate_mapping(robot, mapping):
    """Servo settings per joint, as edited in Servo setup. Unknown joints are dropped; missing ones keep defaults."""
    if not isinstance(mapping, dict):
        raise ValueError('Servo settings must be an object')
    clean = robot.merge_mapping({})
    for name in clean:
        raw = mapping.get(name)
        if not isinstance(raw, dict):
            continue
        servo, sign, offset = raw.get('servo', -1), raw.get('sign', 1), float(raw.get('offset', 0))
        if not isinstance(servo, int) or not -1 <= servo <= 15 or sign not in (-1, 1) or not math.isfinite(offset) or abs(offset) > 90:
            raise ValueError(f'Invalid servo settings for {name}')
        clean[name] = {'servo': servo, 'sign': sign, 'offset': offset, 'verified': bool(raw.get('verified', False))}
    return clean


def selected_robot(target='main'):
    if target in (None, '', 'main'):
        return sim
    actor = sim.actors.get(str(target))
    if actor is None:
        raise ValueError('Robot not found')
    return actor


@app.post('/api/command')
def command(body: dict):
    if body['action'] == 'reset':
        runner.stop()
    elif body['action'] in ('play', 'settings', 'seek', 'pose'):
        runner.ensure_idle()
    with sim.lock:
        action = body['action']
        robot = selected_robot(body.get('target'))
        if action in ('reset', 'pose', 'seek', 'play'):
            robot.skill = None  # direct joint control or the timeline takes over from a simulated console skill
        if action == 'run':
            sim.running = bool(body['value'])
        elif action == 'reset':
            sim.reset()
        elif action == 'pose':
            robot.pose(body['pose'])
        elif action == 'seek':
            robot.playing = False
            robot.playhead = max(0, min(600, float(body['time'])))
            robot.pose(robot.sample(robot.playhead))
        elif action == 'play':
            hz = float(body.get('hz', robot.motion_hz))
            direction = body.get('direction', robot.direction)
            if not 1 <= hz <= 240 or direction not in ('forward', 'reverse'):
                raise ValueError('Invalid motion Hz or direction')
            robot.motion_hz, robot.direction = hz, direction
            robot.playing = bool(body['value']) and len(robot.frames) > 1
            robot.loop = bool(body.get('loop', True))
            robot.speed = max(.1, min(2, float(body.get('speed', 1))))
            if body.get('from_start') or (robot.frames and (robot.playhead >= robot.frames[-1]['time'] or robot.playhead <= 0)):
                robot.playhead = 0 if robot.direction == 'forward' else robot.frames[-1]['time'] if robot.frames else 0
            robot.motion_elapsed = 0
            if robot.playing:
                robot.pose(robot.sample(robot.playhead))
        elif action == 'settings':
            if robot is not sim:
                hz = float(body.get('motion_hz', robot.motion_hz))
                direction = body.get('direction', robot.direction)
                if not 1 <= hz <= 240 or direction not in ('forward', 'reverse'):
                    raise ValueError('Invalid robot motion settings')
                robot.motion_hz, robot.direction = hz, direction
                return sim.state()
            gravity = float(body.get('gravity', sim.gravity))
            friction = float(body.get('friction', sim.friction))
            if not -30 <= gravity <= 0 or not 0 <= friction <= 3:
                raise ValueError('Gravity must be -30 to 0; friction must be 0 to 3')
            fixed = bool(body.get('fixed', sim.fixed))
            if fixed != sim.fixed:
                old = sim.fixed
                frames, targets, mapping, sensors = sim.frames, sim.targets.copy(), sim.mapping, sim.sensors
                sim.fixed = fixed
                try:
                    sim.load_robot(sim.xml, sim.directory)
                except Exception:
                    sim.fixed = old
                    raise
                sim.frames, sim.mapping = frames, mapping
                sim.pose(targets)
                for sensor in sensors:
                    sim.add_sensor(sensor)
            sim.gravity, sim.friction = gravity, friction
            for actor in sim.actors.values():
                actor.gravity = gravity
            p.setGravity(0, 0, gravity, physicsClientId=sim.client)
            for link in range(-1, p.getNumJoints(sim.robot, physicsClientId=sim.client)):
                p.changeDynamics(sim.robot, link, lateralFriction=friction, physicsClientId=sim.client)
            p.changeDynamics(sim.ground, -1, lateralFriction=friction, physicsClientId=sim.client)
            sim.configure_rates(body.get('physics_hz', sim.physics_hz), body.get('motion_hz', sim.motion_hz), body.get('direction', sim.direction))
        else:
            raise ValueError('Unknown action')
        return sim.state()


@app.post('/api/frames')
def frames(body: dict):
    runner.ensure_idle()
    with sim.lock:
        robot = selected_robot(body.get('target'))
        robot.set_frames(body['frames'])
        return {'frames': robot.frames}


@app.post('/api/urdf')
def urdf(body: dict):
    runner.ensure_idle()
    with sim.lock:
        sensors = list(sim.sensors)
        sim.load_robot(body['xml'], sim.directory)
        for sensor in sensors:
            if sensor.get('link') in sim.links:
                sim.add_sensor(sensor)
            else:
                sim.warnings.append(f"Sensor {sensor.get('name', 'IMU')} was removed because link {sensor.get('link')} no longer exists.")
        checkpoint()
        return sim.model()


@app.post('/api/objects')
def add_object(body: dict):
    runner.ensure_idle()
    with sim.lock:
        old = next((o for o in sim.objects if o['id'] == body.get('id')), None)
        new = sim.add_object(body)
        if old:
            p.removeBody(old['body'], physicsClientId=sim.client)
            sim.objects.remove(old)
        return {k: v for k, v in new.items() if k != 'body'}


@app.delete('/api/objects/{object_id}')
def delete_object(object_id: str):
    runner.ensure_idle()
    with sim.lock:
        obj = next((o for o in sim.objects if o['id'] == object_id), None)
        if not obj:
            raise HTTPException(404, 'Object not found')
        p.removeBody(obj['body'], physicsClientId=sim.client)
        sim.objects.remove(obj)
        return {'ok': True}


@app.post('/api/import')
async def import_asset(file: UploadFile = File(...), mode: str = 'replace'):
    runner.ensure_idle()
    if mode not in ('replace', 'add'):
        raise ValueError('Robot import mode must be add or replace')
    content = await file.read(100 * 1024 * 1024 + 1)
    if len(content) > 100 * 1024 * 1024:
        raise HTTPException(413, 'Maximum upload size is 100 MB')
    directory = DATA / uuid.uuid4().hex
    directory.mkdir()
    name = Path(file.filename.replace('\\', '/')).name
    path = directory / name
    suffix = path.suffix.lower()
    if suffix == '.zip':
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            if sum(i.file_size for i in archive.infolist()) > 250 * 1024 * 1024 or len(archive.infolist()) > 1000:
                raise ValueError('ZIP is too large after extraction')
            for member in archive.infolist():
                target = (directory / member.filename.replace('\\', '/')).resolve()
                if not target.is_relative_to(directory):
                    raise ValueError('ZIP contains an unsafe path')
                if not member.is_dir():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.read(member))
        urdf_files = [item for item in directory.rglob('*') if item.is_file() and item.name.lower().endswith('.urdf')]
        xacro_files = [item for item in directory.rglob('*') if item.is_file() and item.name.lower().endswith(('.xacro', '.urdf.xacro'))]
        files = urdf_files or xacro_files
        if len(files) != 1:
            raise ValueError('Robot ZIP must contain one .urdf or .xacro robot source')
        path = files[0]
        with sim.lock:
            if mode == 'add':
                actor = sim.add_actor({'xml': path.read_text(), 'directory': asset_url(path.parent / 'placeholder').rsplit('/', 1)[0], 'name': path.stem})
                return {'kind': 'actor', 'id': actor.actor_id}
            sim.load_robot(path.read_text(), path.parent)
            return {'kind': 'robot'}
    path.write_bytes(content)
    if suffix in ('.urdf', '.xacro'):
        with sim.lock:
            if mode == 'add':
                actor = sim.add_actor({'xml': content.decode('utf-8-sig'), 'directory': asset_url(directory / 'placeholder').rsplit('/', 1)[0], 'name': path.stem})
                return {'kind': 'actor', 'id': actor.actor_id}
            sim.load_robot(content.decode('utf-8-sig'), directory)
        return {'kind': 'robot'}
    if suffix not in ('.obj', '.stl', '.glb'):
        raise ValueError('Use OBJ, STL, GLB, URDF, Xacro, or a robot ZIP')
    # Flatten the mesh into a self-contained OBJ for consistent Bullet collision and display.
    mesh = trimesh.load(str(path), force='mesh', process=False)
    if not isinstance(mesh, trimesh.Trimesh) or len(mesh.vertices) == 0 or not bool(__import__('numpy').isfinite(mesh.vertices).all()):
        raise ValueError('File contains no valid triangle mesh')
    converted = directory / 'collision.obj'
    mesh.export(str(converted), include_texture=False)
    return {'kind': 'mesh', 'url': asset_url(converted), 'name': path.stem, 'bounds': mesh.bounds.tolist(),
            'note': 'Imported geometry; original textures are not retained. Choose metres or millimetres in the inspector.'}


@app.get('/api/project')
def get_project():
    with sim.lock:
        return {'format': 'bittle-studio', 'version': 1, 'xml': sim.xml, 'directory': asset_url(sim.directory / 'placeholder').rsplit('/', 1)[0],
                **{k: v for k, v in sim.model().items() if k in ('objects', 'frames', 'mapping', 'fixed', 'gravity', 'friction', 'targets', 'physics_hz', 'motion_hz', 'direction', 'scripts', 'motions', 'controls', 'robot_position', 'robot_rotation')},
                'sensors': sim.sensors,
                'actors': [a.actor_definition() for a in sim.actors.values() if not a.actor_id.startswith('try-')]}


@app.post('/api/project')
def load_project(body: dict):
    runner.ensure_idle()
    if body.get('format') != 'bittle-studio' or body.get('version') != 1:
        raise ValueError('Not a Bittle Studio project')
    # Validate the complete project in a separate Bullet world before changing the active one.
    candidate = Simulation()
    try:
        candidate.fixed = bool(body.get('fixed', False))
        candidate.home_position = vector(' '.join(map(str, body.get('robot_position', [0, 0, .2]))))
        candidate.home_rotation = vector(' '.join(map(str, body.get('robot_rotation', [0, 0, 0]))))
        directory = url_path(body['directory'])
        asset_url(directory / 'placeholder')
        candidate.load_robot(upgrade_bundled_robot(body['xml'], directory), directory)
        candidate.set_frames(body.get('frames', []))
        candidate.pose(body.get('targets', {}))
        candidate.mapping = candidate.merge_mapping(body.get('mapping'))
        for sensor in body.get('sensors', []):
            candidate.add_sensor(sensor)
        candidate.scripts = validate_scripts(body.get('scripts', []))
        candidate.motions = validate_motions(body.get('motions', []), candidate)
        candidate.controls = validate_controls(body.get('controls', []))
        candidate.configure_rates(body.get('physics_hz', 240), body.get('motion_hz', 50), body.get('direction', 'forward'))
        for definition in body.get('actors', []):
            if not str(definition.get('id', '')).startswith('try-'):     # trainer preview crabs are never saved
                candidate.add_actor(definition)
        for obj in body.get('objects', []):
            candidate.add_object(obj)
        candidate.gravity = float(body.get('gravity', -9.81))
        candidate.friction = float(body.get('friction', .8))
        if not -30 <= candidate.gravity <= 0 or not 0 <= candidate.friction <= 3:
            raise ValueError('Invalid physics settings')
        p.setGravity(0, 0, candidate.gravity, physicsClientId=candidate.client)
        for actor in candidate.actors.values():
            actor.gravity = candidate.gravity
        p.changeDynamics(candidate.ground, -1, lateralFriction=candidate.friction, physicsClientId=candidate.client)
        for i in range(-1, p.getNumJoints(candidate.robot, physicsClientId=candidate.client)):
            p.changeDynamics(candidate.robot, i, lateralFriction=candidate.friction, physicsClientId=candidate.client)
        with sim.lock:
            old_client = sim.client
            for key, value in candidate.__dict__.items():
                if key not in ('lock', 'closed'):
                    setattr(sim, key, value)
            p.disconnect(old_client)
        return sim.model()
    except Exception:
        p.disconnect(candidate.client)
        raise


@app.post('/api/export')
def export_motion(body: dict):
    with sim.lock:
        robot = selected_robot(body.get('target'))
        script, mapping, metadata = python_motion(robot, body)
        robot.mapping = mapping
        return PlainTextResponse(script)


@app.post('/api/motions/{motion_id}/export')
def export_saved_motion(motion_id: str, body: dict):
    """PetoiRobot Python script for a saved function, using its own type, Hz, speed and order; the timeline is untouched."""
    with sim.lock:
        item = next((item for item in sim.motions if item['id'] == motion_id), None)
        if item is None:
            raise ValueError('Saved motion not found')
        original, original_targets = sim.frames, sim.targets
        try:
            sim.set_frames(item['frames'])
            sim.targets = item['pose'].copy()
            settings = {key: item[key] for key in ('motion_type', 'hz', 'speed', 'direction')}
            script, mapping, _ = python_motion(sim, {**settings, 'mapping': body.get('mapping', sim.mapping)})
            sim.mapping = mapping
        finally:
            sim.frames, sim.targets = original, original_targets
        return PlainTextResponse(script)


@app.post('/api/motion-sequence/export')
def export_motion_sequence(body: dict):
    """One PetoiRobot script that plays several saved functions in the given order (gaits play one cycle)."""
    ids = body.get('ids')
    if not isinstance(ids, list) or not 1 <= len(ids) <= 30:
        raise ValueError('Choose between 1 and 30 functions')
    with sim.lock:
        mapping, parts = body.get('mapping', sim.mapping), []
        original, original_targets = sim.frames, sim.targets
        try:
            for motion_id in ids:
                item = next((m for m in sim.motions if m['id'] == motion_id), None)
                if item is None:
                    raise ValueError('Saved motion not found')
                sim.set_frames(item['frames'])
                sim.targets = item['pose'].copy()
                motion_type = 'behavior' if item['motion_type'] == 'gait' else item['motion_type']
                samples, mapping, metadata = build_motion(sim, {'motion_type': motion_type, 'hz': item['hz'], 'speed': item['speed'],
                                                                'direction': item['direction'], 'mapping': mapping}, hardware=True)
                parts.append((item['name'], samples, metadata))
        finally:
            sim.frames, sim.targets = original, original_targets
        samples, metadata = sequence_samples(parts)
        sim.mapping = mapping
        return PlainTextResponse(python_script(samples, mapping, metadata))


@app.post('/api/motion-code')
def motion_code(body: dict):
    with sim.lock:
        robot = selected_robot(body.get('target'))
        script, mapping, metadata = python_motion(robot, body, hardware=False)
        return {'source': script, 'metadata': metadata}


@app.post('/api/motion-samples')
def motion_samples(body: dict):
    """Return verified servo-space samples for an explicit browser hardware action."""
    with sim.lock:
        robot = selected_robot(body.get('target'))
        samples, mapping, metadata = build_motion(robot, body, hardware=True)
        robot.mapping = mapping
        checkpoint()
        return {'samples': samples, 'metadata': metadata}


@app.post('/api/skill')
def simulate_skill(body: dict):
    """Play a console command (k<skill>, m/i joint commands) or a saved Studio function in the simulator."""
    with sim.lock:
        robot = selected_robot(body.get('target'))
        if body.get('motion_id'):
            item = next((m for m in sim.motions if m['id'] == body['motion_id']), None)
            if item is None:
                raise ValueError('Saved motion not found')
            original = robot.frames
            try:
                robot.set_frames(item['frames'])
                end, speed = (robot.frames[-1]['time'] if robot.frames else 0), float(item.get('speed', 1)) or 1
                step = .02
                count = max(1, int(end / speed / step) + 1)
                poses = [robot.sample(min(end, i * step * speed)) for i in range(count)] if robot.frames else [item['pose']]
            finally:
                robot.frames = original
            plan = skills.motion_plan(item['name'], poses, step, item['motion_type'] == 'gait')
            robot.last_custom_plan = plan  # replayed by T, like the firmware's last uploaded skill
        elif str(body.get('command', '')).strip() == 'T':
            plan = getattr(robot, 'last_custom_plan', None)
        else:
            plan = skills.plan(body.get('command', ''), robot.mapping, float(body.get('speed') or 1))
        if plan is None:
            return {'simulated': False}
        robot.play_skill(plan)
        return {'simulated': True, 'name': plan['name'], 'loop': plan['loop_from'] is not None,
                'seconds': round(sum(s for s, _ in plan['segments']), 2)}


@app.post('/api/mapping')
def save_mapping(body: dict):
    """Persist Servo setup (servo numbers, directions, offsets, verified) without exporting anything."""
    with sim.lock:
        robot = selected_robot(body.get('target'))
        robot.mapping = validate_mapping(robot, body.get('mapping'))
        checkpoint()
        return {'mapping': robot.mapping}


@app.post('/api/motion-skill')
def motion_skill(body: dict):
    with sim.lock:
        robot = selected_robot(body.get('target'))
        skill, mapping, metadata = firmware_skill(robot, body)
        robot.mapping = mapping
        checkpoint()
        return {'skill': skill, 'metadata': metadata}


@app.post('/api/robot-transform')
def robot_transform(body: dict):
    runner.ensure_idle()
    with sim.lock:
        if sim.running:
            raise ValueError('Pause physics before moving the robot')
        sim.set_robot_transform(body['position'], body['rotation'])
        checkpoint()
        return sim.state()


@app.post('/api/sensors')
def add_sensor(body: dict):
    with sim.lock:
        robot = selected_robot(body.get('target'))
        sensor = robot.add_sensor(body)
        checkpoint()
        return sensor


@app.delete('/api/sensors/{target}/{sensor_id}')
def delete_sensor(target: str, sensor_id: str):
    with sim.lock:
        robot = selected_robot(target)
        robot.remove_sensor(sensor_id)
        checkpoint()
        return {'ok': True}


def _code_version():
    """Fingerprint of the Studio code; the launcher compares it to stop an older Crab Studio that is still running."""
    import hashlib
    h = hashlib.sha256()
    for name in ['server.py', 'scripting.py', 'script_worker.py', 'crab_lab.py', 'engine.py', 'skills.py']:
        h.update((Path(__file__).resolve().parent / name).read_bytes())
    return h.hexdigest()[:12]


CODE_VERSION = _code_version()


@app.get('/api/crab/version')
def crab_version():
    return {'version': CODE_VERSION}


@app.get('/api/crab/robot-skills')
def crab_robot_skills(speed: float = 1.):
    """Crab skills as OpenCat skill arrays: the Studio sends these to the real robot (K packet) instead of k<name>.
    speed (speed dial): gaits resampled to play faster or slower."""
    import crab_lab
    return crab_lab.robot_skills(speed)


@app.post('/api/crab/reload')
def crab_reload(body: dict | None = None):
    """Called by train_crab.py when it has learned new gaits: reload the skill table and update the crab gaits in
    My skills (Studio functions named 'Crab: ...')."""
    return crab_functions()


def crab_sync_scripts():
    """Put new versions of the crab scripts (crab/scripts/*.py) into the Python panel. A script you edited in the
    panel is only replaced when its file in crab/scripts changes (user-data/bundled-scripts.json remembers which
    version was last put in)."""
    import hashlib
    import crab_lab
    marker = DATA / 'bundled-scripts.json'
    try:
        seen = json.loads(marker.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        seen = {}
    files = [dict(f) for f in sim.scripts]
    for path in sorted((crab_lab.CRAB / 'scripts').glob('*.py')):
        source = path.read_text(encoding='utf-8')
        digest = hashlib.sha256(source.encode('utf-8')).hexdigest()
        if seen.get(path.name) == digest:
            continue
        existing = next((f for f in files if f['name'] == path.name), None)
        if existing:
            existing['source'] = source
        else:
            files.append({'name': path.name, 'source': source, 'target': 'main', 'enabled': True})
        seen[path.name] = digest
    sim.scripts = validate_scripts(files)
    marker.write_text(json.dumps(seen, indent=1), encoding='utf-8')


def crab_load_model(claw):
    """Put the crab model for this claw setting in the simulator (arm drawn and weighing, or gone), keeping the
    timeline, pose, Servo setup and sensors. The caller holds sim.lock."""
    import crab_lab
    model = crab_lab.model_path(claw)
    xml = model.read_text(encoding='utf-8')
    from engine import expand_robot_xml
    if getattr(sim, 'xml', None) in (xml, expand_robot_xml(xml, model.parent)):
        return False
    frames, targets, mapping, sensors = sim.frames, sim.targets.copy(), sim.mapping, list(sim.sensors)
    sim.load_robot(xml, model.parent)
    sim.frames, sim.mapping = frames, mapping
    sim.pose({k: v for k, v in targets.items() if k in {j['name'] for j in sim.joints}})
    for sensor in sensors:
        if sensor.get('link') in sim.links:
            sim.add_sensor(sensor)
    return True


@app.get('/api/crab/config')
def crab_config():
    import crab_lab
    claw = crab_lab.claw_fitted()
    results = crab_lab.load_results(claw)
    return {'claw': claw, 'learned': sorted(k for k, v in results.get('gaits', {}).items() if v.get('learned'))}


@app.post('/api/crab/config')
def set_crab_config(body: dict):
    """Claw fitted or not: switches the model in the simulator, the learned gaits (Move pad, Functions, what is sent
    to the robot) and the Arduino crab file to that setting."""
    import crab_lab
    runner.ensure_idle()
    claw = bool(body.get('claw'))
    crab_lab.set_claw_fitted(claw)
    results = crab_lab.load_results(claw)
    if not results.get('gaits'):
        results = crab_lab.seed_results()
    with sim.lock:
        crab_load_model(claw)
    crab_lab.write_skill_table(dict(results, claw=claw))
    written = [str(path) for path in crab_lab.write_firmware(results, claw)]
    crab_functions()
    with sim.lock:
        model = sim.model()
    return {**crab_config(), 'firmware': written, 'model': model}


def crab_functions():
    import crab_lab
    import skills
    skills.table.cache_clear()
    results = crab_lab.load_results(crab_lab.claw_fitted())
    with sim.lock:
        learned = [crab_lab.gait_motion('Crab: ' + entry['label'], entry, sim.mapping)
                   for task, entry in results.get('gaits', {}).items() if task in crab_lab.TASKS]
        learned.append(crab_lab.behavior_motion('Crab: pick up trash', crab_lab.pickup_skill(results), sim.mapping))
        names = {m['name'] for m in learned}
        sim.motions = validate_motions([m for m in sim.motions if m['name'] not in names] + learned)
        checkpoint()
        return {'ok': True, 'gaits': sorted(results.get('gaits', {})), 'motions': len(sim.motions)}


@app.get('/api/motions')
def motions():
    with sim.lock:
        return {'motions': sim.motions}


@app.post('/api/motions')
def save_motion(body: dict):
    with sim.lock:
        frames = sim.frames or [{'time': 0, 'pose': sim.targets.copy(), 'easing': 'smooth'}]
        item = {**body, 'id': uuid.uuid4().hex, 'frames': frames, 'pose': sim.targets.copy()}
        sim.motions = validate_motions([*sim.motions, item])
        checkpoint()
        return sim.motions[-1]


@app.get('/api/controls')
def controls():
    with sim.lock:
        return {'controls': sim.controls}


@app.put('/api/controls')
def save_controls(body: dict):
    with sim.lock:
        sim.controls = validate_controls(body.get('controls'))
        checkpoint()
        return {'controls': sim.controls}


@app.delete('/api/motions/{motion_id}')
def delete_motion(motion_id: str):
    with sim.lock:
        before = len(sim.motions)
        sim.motions = [item for item in sim.motions if item['id'] != motion_id]
        if len(sim.motions) == before:
            raise ValueError('Saved motion not found')
        checkpoint()
        return {'ok': True}


@app.post('/api/motions/{motion_id}/load')
def load_motion(motion_id: str):
    runner.ensure_idle()
    with sim.lock:
        item = next((item for item in sim.motions if item['id'] == motion_id), None)
        if item is None:
            raise ValueError('Saved motion not found')
        sim.set_frames(item['frames'])
        checkpoint()
        return {'frames': sim.frames, 'motion': item}


@app.post('/api/motions/{motion_id}/samples')
def saved_motion_samples(motion_id: str, body: dict):
    with sim.lock:
        item = next((item for item in sim.motions if item['id'] == motion_id), None)
        if item is None:
            raise ValueError('Saved motion not found')
        original, original_targets = sim.frames, sim.targets
        try:
            sim.set_frames(item['frames'])
            sim.targets = item['pose'].copy()
            request = {**item, **body}
            samples, mapping, metadata = build_motion(sim, request, hardware=True)
            sim.mapping = mapping
        finally:
            sim.frames = original
            sim.targets = original_targets
        checkpoint()
        return {'samples': samples, 'metadata': metadata}


def compile_saved_motion(item, body):
    """Firmware skill for a saved motion; the caller holds sim.lock."""
    original, original_targets = sim.frames, sim.targets
    try:
        sim.set_frames(item['frames'])
        sim.targets = item['pose'].copy()
        skill, mapping, metadata = firmware_skill(sim, {**item, **body})
        sim.mapping = mapping
    finally:
        sim.frames, sim.targets = original, original_targets
    return skill, metadata


@app.post('/api/motions/{motion_id}/skill')
def saved_motion_skill(motion_id: str, body: dict):
    with sim.lock:
        item = next((item for item in sim.motions if item['id'] == motion_id), None)
        if item is None:
            raise ValueError('Saved motion not found')
        skill, metadata = compile_saved_motion(item, body)
        checkpoint()
        return {'skill': skill, 'metadata': metadata}


@app.post('/api/controls/pack')
def controls_pack(body: dict):
    """Bittle Link pack: console buttons plus every saved motion compiled to a firmware skill,
    so a standalone console can run them without this server."""
    with sim.lock:
        skills = []
        for item in sim.motions:
            try:
                skill, metadata = compile_saved_motion(item, body)
            except ValueError as error:
                raise ValueError(f'Studio function “{item["name"]}” cannot be exported: {error}') from error
            skills.append({'id': item['id'], 'name': item['name'], 'motion_type': item['motion_type'],
                           'type': metadata['type'], 'signature': metadata['signature'], 'hz': metadata['firmware_hz'], 'skill': skill})
        checkpoint()
        return {'format': 'bittle-link-pack', 'version': 1, 'controls': sim.controls, 'skills': skills}


@app.get('/saved-motions/index.json')
def saved_motion_packs():
    return {'packs': list_packs(SAVED_MOTIONS)}


@app.get('/saved-motions/{filename}')
def saved_motion_pack(filename: str):
    path = pack_path(SAVED_MOTIONS, filename)
    if not path.is_file():
        raise HTTPException(404, 'Pack not found')
    return FileResponse(path, media_type='application/json')


@app.post('/saved-motions/save')
def save_motion_pack(body: dict):
    return save_pack(SAVED_MOTIONS, body.get('name', ''), body.get('pack'))


@app.get('/api/voice/config')
def voice_config():
    return {'configured': bool(voice_api_key), 'model': VOICE_MODEL}


@app.post('/api/voice/key')
def set_voice_key(body: dict):
    global voice_api_key
    key = str(body.get('key', '')).strip()
    if not key.startswith('sk-') or len(key) < 13:
        raise ValueError('Enter a valid OpenAI API key')
    voice_api_key = key
    return {'configured': True}


@app.delete('/api/voice/key')
def remove_voice_key():
    global voice_api_key
    voice_api_key = ''
    return {'configured': False}


@app.post('/api/voice/session')
async def voice_session(request: Request):
    if not voice_api_key:
        raise HTTPException(503, 'Set an OpenAI API key first')
    sdp = (await request.body()).decode('utf-8')
    if not sdp.startswith('v=0') or len(sdp) > 65536:
        raise ValueError('Invalid audio connection')
    from voice_tools import PERSONALITY, TOOLS
    session = {'type': 'realtime', 'model': VOICE_MODEL, 'instructions': PERSONALITY,
               'tools': TOOLS, 'tool_choice': 'auto', 'parallel_tool_calls': False,
               'output_modalities': ['audio'],
               'audio': {'input': {'transcription': {'model': 'gpt-4o-mini-transcribe', 'language': 'en'},
                                    'turn_detection': {'type': 'semantic_vad', 'eagerness': 'medium', 'create_response': True, 'interrupt_response': True}},
                         'output': {'voice': 'cedar'}}}
    files = {'sdp': (None, sdp), 'session': (None, json.dumps(session))}
    async with httpx.AsyncClient(timeout=30) as client:
        upstream = await client.post('https://api.openai.com/v1/realtime/calls',
                                     headers={'Authorization': 'Bearer ' + voice_api_key}, files=files)
    if upstream.status_code >= 400:
        message = 'The API key is invalid' if upstream.status_code == 401 else 'OpenAI could not start the voice session'
        raise HTTPException(upstream.status_code if upstream.status_code in (401, 429) else 502, message)
    return Response(upstream.text, media_type='application/sdp')


@app.get('/api/scripts')
def scripts():
    return {'files': sim.scripts, **runner.status()}


@app.put('/api/scripts')
def save_scripts(body: dict):
    files = validate_scripts(body['files'])
    with sim.lock:
        sim.scripts = files
        checkpoint()
    return {'files': files}


@app.post('/api/scripts/run')
def run_scripts(body: dict):
    return runner.start(body.get('ids'), body.get('physics', False), body.get('timeout', 120))


@app.post('/api/scripts/stop')
def stop_scripts():
    runner.stop()
    return runner.status()


@app.post('/api/actors')
def add_actor(body: dict):
    runner.ensure_idle()
    with sim.lock:
        if body.get('duplicate_main'):
            body = {'xml': sim.xml, 'directory': asset_url(sim.directory / 'placeholder').rsplit('/', 1)[0],
                    'name': 'Bittle copy', 'position': [.35, 0, .2]}
        return sim.add_actor(body).actor_definition(True)


@app.post('/api/actors/{actor_id}')
def edit_actor(actor_id: str, body: dict):
    runner.ensure_idle()
    with sim.lock:
        old = sim.actors[actor_id]
        definition = {**old.actor_definition(), **body}
        definition.pop('id', None)
        new = sim.add_actor(definition)
        sim.actors.pop(new.actor_id)
        new.actor_id = actor_id
        sim.actors[actor_id] = new
        p.removeBody(old.robot, physicsClientId=sim.client)
        return new.actor_definition(True)


@app.post('/api/actors/{actor_id}/ground')
def ground_actor(actor_id: str):
    runner.ensure_idle()
    with sim.lock:
        if sim.running:
            raise ValueError('Pause physics before placing a robot on the ground')
        actor = selected_robot(actor_id)
        actor.place_on_ground()
        checkpoint()
        return actor.actor_definition(True)


@app.delete('/api/actors/{actor_id}')
def delete_actor(actor_id: str):
    runner.ensure_idle()
    with sim.lock:
        actor = sim.actors.pop(actor_id)
        p.removeBody(actor.robot, physicsClientId=sim.client)
    return {'ok': True}


app.mount('/web', StaticFiles(directory=ROOT / 'web'), name='web')
app.mount('/assets', StaticFiles(directory=ASSETS), name='assets')
app.mount('/data', StaticFiles(directory=DATA), name='data')
# Crab Studio reuses Bittle Studio's 3D viewer library when it has not been installed here (Setup installs it).
VENDOR = ROOT / 'node_modules/three'
if not VENDOR.is_dir() and (ROOT.parent.parent / 'Bittle_Studio-master' / 'app' / 'node_modules' / 'three').is_dir():
    VENDOR = ROOT.parent.parent / 'Bittle_Studio-master' / 'app' / 'node_modules' / 'three'
app.mount('/vendor', StaticFiles(directory=VENDOR), name='vendor')

if __name__ == '__main__':
    AUTOSAVE = True
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=8775)
