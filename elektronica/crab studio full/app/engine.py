"""Local, single-session Bullet simulation. SI units; Z is up."""
import hashlib
import math
import threading
import time
import uuid
import xml.etree.ElementTree as ET
import xml.dom.minidom as minidom
from pathlib import Path

import numpy as np
import pybullet as p
import xacro

ROOT = Path(__file__).parent.resolve()          # app/: source code
PROJECT = ROOT.parent                           # repository root
ASSETS = PROJECT / 'robot-models'               # bundled robot models, served as /assets
DATA = PROJECT / 'user-data'                    # imports, autosave and script runs, served as /data
SAVED_MOTIONS = PROJECT / 'saved-motions'       # Bittle Link packs
if not DATA.exists() and (PROJECT / 'data').is_dir():
    (PROJECT / 'data').rename(DATA)             # keep sessions from before the folder reorganisation
DATA.mkdir(exist_ok=True)
SAVED_MOTIONS.mkdir(exist_ok=True)
# Saved projects store these URL prefixes, so they stay fixed even when folders move on disk.
URL_ROOTS = {'assets': ASSETS, 'data': DATA}


def vector(text, default=(0, 0, 0)):
    result = [float(x) for x in text.split()] if text else list(default)
    if len(result) != len(default) or not all(math.isfinite(x) for x in result):
        raise ValueError('Invalid vector in URDF')
    return result


def asset_url(path):
    path = Path(path).resolve()
    for prefix, base in URL_ROOTS.items():
        if path.is_relative_to(base):
            return '/' + prefix + '/' + path.relative_to(base).as_posix()
    raise ValueError('Mesh must be inside the project robot-models or user-data directory')


def url_path(url):
    """Disk location of an /assets/... or /data/... URL."""
    prefix, _, rest = url.lstrip('/').partition('/')
    if prefix not in URL_ROOTS:
        raise ValueError('Path must start with /assets or /data: ' + url)
    path = (URL_ROOTS[prefix] / rest).resolve()
    asset_url(path)
    return path


def asset_path(url):
    path = url_path(url)
    if not path.is_file():
        raise ValueError('Asset not found: ' + url)
    return path


# SHA-256 of earlier bundled Bittle URDFs. Projects store the robot's URDF text, so an unmodified copy of an old
# bundled file is swapped for the current bundled model when the project loads.
OUTDATED_BITTLE_SHA256 = {
    '0bb4261fe4826b994ca6a7d77a583c6024bb598a50377a8586ca7142305223e5',  # without head and neck
    '20edb00f2ece0aa41b9e823342a9a4efd21ce901e77b5c93920085d4eca83cb3',  # first head version, turned 40 deg at neck 0
    'fd3e16753188383288258af946a9ede563edb3c4aef37883ffccc298c289ec3a',  # narrow, unmirrored leg limits (clipped sit)
    '9aa8d2a2b39524ff432f1e29d4a61baf037175a59f74726a9c9ea0de55ac1b83',  # dog head (before the crab claw and shell)
    '54dfe8a5871fb561e808155bcf773e308852192de6d4051dc3381544353395eb',  # dog head, knee limits widened for the crab walk
}


def upgrade_bundled_robot(xml, directory):
    bundled = ASSETS / 'bittle'
    if Path(directory).resolve() == bundled.resolve():
        normalised = xml.replace('\r\n', '\n')
        for candidate in (xml, normalised, normalised.replace('\n', '\r\n')):
            if hashlib.sha256(candidate.encode('utf-8')).hexdigest() in OUTDATED_BITTLE_SHA256:
                return (bundled / 'bittle.urdf').read_text(encoding='utf-8')
    return xml


def _content_root(directory):
    directory = Path(directory).resolve()
    for base in (DATA, ASSETS):
        if directory.is_relative_to(base):
            parts = directory.relative_to(base).parts
            return base / parts[0] if parts else base
    raise ValueError('Robot assets must be inside the project assets or data directory')


def resolve_robot_asset(directory, filename):
    """Resolve relative and ROS package:// paths inside one imported bundle."""
    raw = filename.replace('\\', '/')
    directory = Path(directory).resolve()
    root = _content_root(directory)
    candidates = []
    if raw.startswith('package://'):
        package, _, relative = raw[len('package://'):].partition('/')
        if not package or not relative:
            raise ValueError('Invalid ROS package URI: ' + raw)
        for folder in [directory, *directory.parents]:
            if not folder.is_relative_to(root) and folder != root:
                break
            if folder.name == package:
                candidates.append(folder / relative)
            manifest = folder / 'package.xml'
            if manifest.is_file():
                try:
                    if (ET.parse(manifest).findtext('name') or '').strip() == package:
                        candidates.append(folder / relative)
                except ET.ParseError:
                    pass
            if folder == root:
                break
        for manifest in root.rglob('package.xml'):
            try:
                if (ET.parse(manifest).findtext('name') or '').strip() == package:
                    candidates.append(manifest.parent / relative)
            except ET.ParseError:
                continue
        candidates.append(root / package / relative)
    else:
        candidates.append(directory / raw)
    for candidate in candidates:
        candidate = candidate.resolve()
        if candidate.is_relative_to(root) and candidate.is_file():
            return candidate
    raise ValueError('Missing mesh: ' + raw + '. Keep the ROS package.xml and mesh folders in the imported ZIP.')


def expand_robot_xml(xml, directory):
    """Expand Xacro content while discarding simulator plugins Bullet cannot use."""
    if 'xacro:' not in xml and '${' not in xml:
        return xml
    try:
        document = minidom.parseString(xml)
        for element in list(document.getElementsByTagName('*')):
            if element.localName in ('gazebo', 'transmission', 'ros2_control') and element.parentNode:
                element.parentNode.removeChild(element)
        xacro.init_stacks(str(Path(directory) / 'studio-import.xacro'))
        xacro.process_doc(document)
        return document.toxml()
    except Exception as error:
        raise ValueError('Could not expand Xacro: ' + str(error)) from error


class Simulation:
    def __init__(self, client=None, xml=None, directory=None, fixed=False):
        self.lock = threading.RLock()
        self.client = p.connect(p.DIRECT) if client is None else client
        self.physics_hz = 240
        self.motion_hz = 50
        self.direction = 'forward'
        self.runtime_clock = 0.
        self.motion_elapsed = 0.
        self.skill = None  # simulated console command (skills.py plan) currently playing
        self.actors = {}
        self.scripts = []
        self.motions = []
        self.controls = []
        self.sensors = []
        self.sensor_values = {}
        self._sensor_velocities = {}
        self.running = False
        self.clock = 0.
        self.gravity = -9.81
        self.friction = .8
        self.fixed = fixed
        self.home_position = [0., 0., .20]
        self.home_rotation = [0., 0., 0.]
        self.robot = None
        self.objects = []
        self.frames = []
        self.playing = False
        self.loop = True
        self.playhead = 0.
        self.speed = 1.
        self.mapping = {}
        self.closed = False
        if client is None:
            p.setGravity(0, 0, self.gravity, physicsClientId=self.client)
            p.setTimeStep(1 / self.physics_hz, physicsClientId=self.client)
            p.setPhysicsEngineParameter(numSolverIterations=80, physicsClientId=self.client)
            self.ground = p.createMultiBody(0, p.createCollisionShape(p.GEOM_PLANE, physicsClientId=self.client), physicsClientId=self.client)
            p.changeDynamics(self.ground, -1, lateralFriction=self.friction, physicsClientId=self.client)
        self.load_robot(xml or (ASSETS / 'bittle/bittle.urdf').read_text(), directory or ASSETS / 'bittle')

    def load_robot(self, xml, directory):
        xml = expand_robot_xml(xml, directory)
        tree = ET.fromstring(xml)
        if tree.tag != 'robot':
            raise ValueError('Expected a URDF <robot> element')
        warnings = []
        visuals = []
        declared_masses = [float(node.get('value')) for node in tree.findall('link/inertial/mass')
                           if float(node.get('value')) > 0]
        declared_inertias = [float(node.get(axis)) for node in tree.findall('link/inertial/inertia')
                             for axis in ('ixx', 'iyy', 'izz') if float(node.get(axis, 0)) > 0]
        fallback_mass = float(np.median(declared_masses)) if declared_masses else .1
        fallback_inertia = float(np.median(declared_inertias)) if declared_inertias else max(1e-6, fallback_mass * 1e-4)
        for link in tree.findall('link'):
            if link.find('inertial') is None:
                warnings.append(link.attrib['name'] + ': no inertial data; using the model median as a stable fallback.')
                inertial = ET.SubElement(link, 'inertial')
                ET.SubElement(inertial, 'mass', {'value': str(fallback_mass)})
                ET.SubElement(inertial, 'inertia', {'ixx': str(fallback_inertia), 'iyy': str(fallback_inertia),
                                                     'izz': str(fallback_inertia), 'ixy': '0', 'ixz': '0', 'iyz': '0'})
            inertia = link.find('inertial/inertia')
            if inertia is not None:
                a = inertia.attrib
                m = np.array([[float(a['ixx']), float(a.get('ixy', 0)), float(a.get('ixz', 0))],
                              [float(a.get('ixy', 0)), float(a['iyy']), float(a.get('iyz', 0))],
                              [float(a.get('ixz', 0)), float(a.get('iyz', 0)), float(a['izz'])]])
                eigen = np.linalg.eigvalsh(m)
                if min(eigen) <= 0 or max(eigen) > sum(eigen) - max(eigen) + 1e-10:
                    warnings.append(link.attrib['name'] + ': supplied inertia is not physically valid.')
            for visual in link.findall('visual'):
                geo = visual.find('geometry')
                origin = visual.find('origin')
                item = {'link': link.attrib['name'], 'position': vector(origin.get('xyz') if origin is not None else None),
                        'quaternion': p.getQuaternionFromEuler(vector(origin.get('rpy') if origin is not None else None))}
                material = visual.find('material/color')
                if material is not None:
                    item['color'] = vector(material.get('rgba'), (1, 1, 1, 1))
                if geo is None or len(geo) == 0:
                    continue
                shape = geo[0]
                item['type'] = shape.tag
                item.update(shape.attrib)
                visuals.append(item)
        # Resolve every mesh before loading; XML cannot escape local asset roots.
        for mesh in tree.iter('mesh'):
            raw = mesh.get('filename', '').replace('\\', '/')
            path = resolve_robot_asset(directory, raw)
            mesh.set('filename', path.as_posix())
        for item in visuals:
            if item['type'] == 'mesh':
                item['url'] = asset_url(resolve_robot_asset(directory, item['filename']))
                item['scale'] = vector(item.get('scale'), (1, 1, 1))
        temp = DATA / ('runtime-' + uuid.uuid4().hex + '.urdf')
        temp.write_text(ET.tostring(tree, encoding='unicode'))
        try:
            new = p.loadURDF(str(temp), self.home_position, p.getQuaternionFromEuler(self.home_rotation), useFixedBase=self.fixed,
                             flags=p.URDF_USE_SELF_COLLISION | p.URDF_USE_SELF_COLLISION_EXCLUDE_PARENT,
                             physicsClientId=self.client)
        finally:
            temp.unlink(missing_ok=True)
        if self.robot is not None:
            p.removeBody(self.robot, physicsClientId=self.client)
        self.robot = new
        self.xml = xml
        self.directory = directory
        self.visuals = visuals
        self.warnings = warnings + ['Bullet estimates inertia from collision geometry; supplied masses are retained. Spring compliance and servo electronics are not modelled.']
        self.joints = []
        self.links = {p.getBodyInfo(new, physicsClientId=self.client)[0].decode(): -1}
        for i in range(p.getNumJoints(new, physicsClientId=self.client)):
            info = p.getJointInfo(new, i, physicsClientId=self.client)
            self.links[info[12].decode()] = i
            if info[2] not in (p.JOINT_FIXED,):
                if info[2] != p.JOINT_REVOLUTE:
                    self.warnings.append(info[1].decode() + ': only revolute joints have pose controls.')
                    continue
                lo, hi = info[8:10]
                if lo > hi:
                    lo, hi = -math.pi, math.pi
                self.joints.append({'id': i, 'name': info[1].decode(), 'lower': math.degrees(lo), 'upper': math.degrees(hi),
                                    'effort': max(0, info[10]), 'velocity': max(0, info[11])})
            p.changeDynamics(new, i, lateralFriction=self.friction, physicsClientId=self.client)
        p.changeDynamics(new, -1, lateralFriction=self.friction, physicsClientId=self.client)
        # Imported CAD URDFs commonly contain decorative collision shells that
        # overlap in their authored zero pose.  Leaving those pairs enabled makes
        # Bullet inject a large separating impulse as soon as physics starts.
        p.performCollisionDetection(physicsClientId=self.client)
        overlaps = set()
        for contact in p.getContactPoints(bodyA=new, bodyB=new, physicsClientId=self.client):
            link_a, link_b, distance = contact[3], contact[4], contact[8]
            if link_a != link_b and distance < -1e-5:
                overlaps.add(tuple(sorted((link_a, link_b))))
        for link_a, link_b in overlaps:
            p.setCollisionFilterPair(new, new, link_a, link_b, 0, physicsClientId=self.client)
        if overlaps:
            self.warnings.append(f'{len(overlaps)} initially overlapping self-collision pair(s) were disabled to prevent launch impulses.')
        self.targets = {j['name']: 0. for j in self.joints}
        self.running = self.playing = False
        self.frames = []
        self.playhead = self.clock = 0.
        # Right-side legs turn opposite to OpenCat's servo angles in this model (checked against Petoi's own skills).
        self.mapping = {j['name']: {'servo': self.default_servo(j['name']), 'sign': -1 if j['name'].startswith('right') else 1, 'offset': 0, 'verified': False} for j in self.joints}
        self.sensors = []
        self.sensor_values = {}
        self._sensor_velocities = {}
        self.pose(self.targets)

    def merge_mapping(self, saved):
        """Saved servo settings for joints that still exist; defaults for new joints (e.g. the neck)."""
        saved = saved if isinstance(saved, dict) else {}
        merged = {}
        for name, default in self.mapping.items():
            entry = saved.get(name)
            if not isinstance(entry, dict):
                merged[name] = default
            elif (not entry.get('verified') and entry.get('sign') == 1 and not entry.get('offset')
                  and entry.get('servo') == default['servo'] and default['sign'] == -1):
                merged[name] = {**entry, 'sign': -1}  # untouched old default: adopt the corrected right-side direction
            else:
                merged[name] = entry
        return merged

    @staticmethod
    def default_servo(name):
        if 'neck' in name:
            return 0  # OpenCat head pan (on the crab: swings the claw arm)
        if 'elbow' in name:
            return 1  # crab claw elbow, on OpenCat's head-tilt channel
        if 'pincer' in name:
            return 2  # crab pincer: placeholder channel for the future wire-pull servo
        for leg, index in [('left-front', 8), ('right-front', 9), ('right-back', 10), ('left-back', 11)]:
            if leg in name:
                return index + (4 if 'knee' in name else 0)
        return -1

    def pose(self, values):
        for j in self.joints:
            value = float(values.get(j['name'], self.targets[j['name']]))
            if not math.isfinite(value):
                raise ValueError('Joint angles must be finite')
            self.targets[j['name']] = max(j['lower'], min(j['upper'], value))
            if not self.running:
                p.resetJointState(self.robot, j['id'], math.radians(self.targets[j['name']]), physicsClientId=self.client)
        if not self.running and self.sensors:
            self.update_sensors(None)

    def add_sensor(self, definition):
        sensor_type = str(definition.get('type', 'imu')).lower()
        if sensor_type != 'imu':
            raise ValueError('The supported sensor type is IMU')
        link = str(definition.get('link') or next(iter(self.links)))
        if link not in self.links:
            raise ValueError('Sensor link is not part of this robot: ' + link)
        sensor_id = str(definition.get('id') or uuid.uuid4().hex)[:64]
        if any(item['id'] == sensor_id for item in self.sensors):
            raise ValueError('Duplicate sensor ID')
        name = str(definition.get('name') or 'Body IMU').strip()[:80]
        if not name or any(item['name'] == name for item in self.sensors):
            raise ValueError('Sensor names must be unique on a robot')
        sensor = {'id': sensor_id, 'name': name, 'type': sensor_type, 'link': link}
        self.sensors.append(sensor)
        self.update_sensors(None)
        return sensor

    def remove_sensor(self, sensor_id):
        sensor = next((item for item in self.sensors if item['id'] == sensor_id), None)
        if sensor is None:
            raise ValueError('Sensor not found')
        self.sensors.remove(sensor)
        self.sensor_values.pop(sensor_id, None)
        self._sensor_velocities.pop(sensor_id, None)

    def _link_motion(self, link):
        index = self.links[link]
        if index < 0:
            position, quaternion = p.getBasePositionAndOrientation(self.robot, physicsClientId=self.client)
            linear, angular = p.getBaseVelocity(self.robot, physicsClientId=self.client)
        else:
            state = p.getLinkState(self.robot, index, computeLinkVelocity=1, computeForwardKinematics=1,
                                   physicsClientId=self.client)
            position, quaternion, linear, angular = state[4], state[5], state[6], state[7]
        inverse = p.invertTransform([0, 0, 0], quaternion)[1]
        return position, quaternion, linear, angular, inverse

    def update_sensors(self, dt):
        for sensor in self.sensors:
            position, quaternion, linear, angular, inverse = self._link_motion(sensor['link'])
            previous = self._sensor_velocities.get(sensor['id'])
            acceleration = [0., 0., 0.] if not dt or previous is None else [
                (linear[i] - previous[i]) / dt for i in range(3)]
            # A stationary accelerometer reads upward against gravity.
            acceleration[2] -= self.gravity
            self._sensor_velocities[sensor['id']] = list(linear)
            self.sensor_values[sensor['id']] = {
                **sensor, 'position': list(position), 'orientation': list(p.getEulerFromQuaternion(quaternion)),
                'quaternion': list(quaternion), 'angular_velocity': list(p.rotateVector(inverse, angular)),
                'linear_acceleration': list(p.rotateVector(inverse, acceleration)),
            }

    def sensor_readings(self):
        # Orientation should also follow pose edits while physics is paused.
        if not self.running and self.sensors:
            self.update_sensors(None)
        return {key: dict(value) for key, value in self.sensor_values.items()}

    def sample(self, t):
        if not self.frames:
            return self.targets.copy()
        if t <= self.frames[0]['time']:
            return self.frames[0]['pose'].copy()
        for a, b in zip(self.frames, self.frames[1:]):
            if t <= b['time']:
                u = (t - a['time']) / (b['time'] - a['time'])
                if b.get('easing') == 'smooth':
                    u = u * u * (3 - 2 * u)
                return {k: a['pose'][k] + (b['pose'][k] - a['pose'][k]) * u for k in a['pose']}
        return self.frames[-1]['pose'].copy()

    def set_frames(self, frames):
        validated = []
        for frame in frames:
            t = float(frame['time'])
            if not math.isfinite(t) or t < 0 or t > 600:
                raise ValueError('Keyframe time must be between 0 and 600 seconds')
            pose = {}
            for j in self.joints:
                v = float(frame['pose'].get(j['name'], 0))
                if not math.isfinite(v) or not j['lower'] - .01 <= v <= j['upper'] + .01:
                    raise ValueError('Keyframe angle exceeds limits: ' + j['name'])
                pose[j['name']] = v
            validated.append({'time': t, 'pose': pose, 'easing': frame.get('easing', 'smooth')})
        validated.sort(key=lambda x: x['time'])
        if len({f['time'] for f in validated}) != len(validated):
            raise ValueError('Keyframes must have different times')
        self.frames = validated

    def configure_rates(self, physics_hz=240, motion_hz=50, direction='forward'):
        if not isinstance(physics_hz, (int, float)) or not math.isfinite(physics_hz) or not 60 <= physics_hz <= 1000:
            raise ValueError('Physics Hz must be between 60 and 1000')
        if not isinstance(motion_hz, (int, float)) or not math.isfinite(motion_hz) or not 1 <= motion_hz <= 240:
            raise ValueError('Motion Hz must be between 1 and 240')
        if direction not in ('forward', 'reverse'):
            raise ValueError('Direction must be forward or reverse')
        self.physics_hz, self.motion_hz, self.direction = physics_hz, motion_hz, direction
        p.setTimeStep(1 / physics_hz, physicsClientId=self.client)

    def drive_motors(self):
        for j in self.joints:
            p.setJointMotorControl2(self.robot, j['id'], p.POSITION_CONTROL,
                                   targetPosition=math.radians(self.targets[j['name']]), force=j['effort'],
                                   maxVelocity=j['velocity'], physicsClientId=self.client)

    def play_skill(self, plan):
        """Start a console command plan from skills.py; it replaces timeline playback and any previous skill."""
        self.playing = False
        self.skill = {**plan, 'index': 0, 'elapsed': 0., 'start': self.targets.copy()} if plan and plan['segments'] else None

    def advance_skill(self, dt):
        skill = self.skill
        if not skill:
            return
        skill['elapsed'] += dt
        while skill is self.skill:
            seconds, target = skill['segments'][skill['index']]
            t = min(1., skill['elapsed'] / seconds) if seconds > 0 else 1.
            # Postures and behaviours ease in and out like the firmware's cosine interpolation; gaits run linearly.
            k = (1 - math.cos(math.pi * t)) / 2 if skill.get('ease') else t
            self.pose({name: start + (target[name] - start) * k for name in target
                       for start in [skill['start'].get(name, self.targets.get(name, 0.))]})
            if t < 1:
                return
            skill['elapsed'] -= seconds
            skill['start'] = {**skill['start'], **target}
            skill['index'] += 1
            if skill['index'] >= len(skill['segments']):
                if skill['loop_from'] is None:
                    self.skill = None
                    return
                skill['index'] = skill['loop_from']

    def advance_motion(self, dt):
        if not (self.playing and self.frames):
            return
        self.playhead += dt * self.speed * (1 if self.direction == 'forward' else -1)
        end = self.frames[-1]['time']
        finished = self.playhead > end or self.playhead < 0
        if finished:
            self.playhead = (0 if self.direction == 'forward' else end) if self.loop else (end if self.direction == 'forward' else 0)
            self.playing = self.loop and end > 0
        self.motion_elapsed += dt
        if self.motion_elapsed >= 1 / self.motion_hz or finished:
            self.motion_elapsed %= 1 / self.motion_hz
            self.pose(self.sample(self.playhead))

    def tick(self):
        dt = 1 / self.physics_hz
        self.runtime_clock += dt
        self.advance_motion(dt)
        self.advance_skill(dt)
        for actor in self.actors.values():
            actor.runtime_clock = self.runtime_clock
            actor.running = self.running
            actor.advance_motion(dt)
            actor.advance_skill(dt)
        if self.running:
            self.drive_motors()
            for actor in self.actors.values():
                actor.drive_motors()
            p.stepSimulation(physicsClientId=self.client)
            self.clock += dt
            self.update_sensors(dt)
            for actor in self.actors.values():
                actor.update_sensors(dt)

    def worker(self):
        last = time.perf_counter()
        accumulator = 0.
        while not self.closed:
            now = time.perf_counter()
            accumulator += min(now - last, .1)
            last = now
            with self.lock:
                while accumulator >= 1 / self.physics_hz:
                    self.tick()
                    accumulator -= 1 / self.physics_hz
            time.sleep(.002)

    def reset(self):
        self.running = self.playing = False
        self.clock = self.playhead = 0.
        p.resetBasePositionAndOrientation(self.robot, self.home_position, p.getQuaternionFromEuler(self.home_rotation), physicsClientId=self.client)
        p.resetBaseVelocity(self.robot, [0, 0, 0], [0, 0, 0], physicsClientId=self.client)
        self.pose(self.sample(0) if self.frames else self.targets)
        self._sensor_velocities.clear()
        self.update_sensors(None)
        for obj in self.objects:
            p.resetBasePositionAndOrientation(obj['body'], obj['position'], p.getQuaternionFromEuler(obj['rotation']), physicsClientId=self.client)
            p.resetBaseVelocity(obj['body'], [0, 0, 0], [0, 0, 0], physicsClientId=self.client)
        for actor in self.actors.values():
            p.resetBasePositionAndOrientation(actor.robot, actor.home_position, p.getQuaternionFromEuler(actor.home_rotation), physicsClientId=self.client)
            p.resetBaseVelocity(actor.robot, [0, 0, 0], [0, 0, 0], physicsClientId=self.client)
            actor.running = actor.playing = False
            actor.playhead = actor.clock = 0.
            actor.pose(actor.sample(0) if actor.frames else actor.targets)
            actor._sensor_velocities.clear()
            actor.update_sensors(None)

    def set_robot_transform(self, position, rotation):
        position = vector(' '.join(map(str, position)))
        rotation = vector(' '.join(map(str, rotation)))
        if any(abs(value) > 100 for value in position) or any(abs(value) > math.tau * 4 for value in rotation):
            raise ValueError('Robot transform is outside the supported workspace')
        self.home_position, self.home_rotation = position, rotation
        p.resetBasePositionAndOrientation(self.robot, position, p.getQuaternionFromEuler(rotation), physicsClientId=self.client)
        p.resetBaseVelocity(self.robot, [0, 0, 0], [0, 0, 0], physicsClientId=self.client)

    def place_on_ground(self, clearance=.002):
        boxes = [p.getAABB(self.robot, i, physicsClientId=self.client)
                 for i in range(-1, p.getNumJoints(self.robot, physicsClientId=self.client))]
        lowest = min(box[0][2] for box in boxes)
        position = list(p.getBasePositionAndOrientation(self.robot, physicsClientId=self.client)[0])
        position[2] += float(clearance) - lowest
        self.set_robot_transform(position, self.home_rotation)
        return position

    def add_actor(self, definition):
        directory = url_path(definition['directory'])
        asset_url(directory / 'placeholder')
        auto_ground = 'position' not in definition or bool(definition.get('place_on_ground', False))
        position = vector(' '.join(map(str, definition.get('position', [.35, 0, .2]))))
        rotation = vector(' '.join(map(str, definition.get('rotation', [0, 0, 0]))))
        actor_id = definition.get('id', uuid.uuid4().hex)
        if actor_id == 'main' or actor_id in self.actors:
            raise ValueError('Duplicate robot ID')
        actor = Simulation(self.client, upgrade_bundled_robot(definition['xml'], directory), directory, bool(definition.get('fixed', False)))
        try:
            actor.actor_id = actor_id
            actor.actor_name = str(definition.get('name', 'Robot'))[:100]
            actor.home_position, actor.home_rotation = position, rotation
            p.resetBasePositionAndOrientation(actor.robot, position, p.getQuaternionFromEuler(rotation), physicsClientId=self.client)
            actor.pose(definition.get('targets', {}))
            actor.mapping = actor.merge_mapping(definition.get('mapping'))
            actor.set_frames(definition.get('frames', []))
            actor.motion_hz = float(definition.get('motion_hz', self.motion_hz))
            actor.direction = definition.get('direction', 'forward')
            if not 1 <= actor.motion_hz <= 240 or actor.direction not in ('forward', 'reverse'):
                raise ValueError('Invalid actor motion settings')
            if auto_ground:
                actor.place_on_ground()
            for sensor in definition.get('sensors', []):
                try:
                    actor.add_sensor(sensor)
                except ValueError as exc:
                    actor.warnings.append(f"Sensor {sensor.get('name', 'IMU')} was not restored: {exc}")
            self.actors[actor_id] = actor
            return actor
        except Exception:
            p.removeBody(actor.robot, physicsClientId=self.client)
            raise

    def actor_definition(self, visuals=False):
        result = {'id': self.actor_id, 'name': self.actor_name, 'xml': self.xml,
                  'directory': asset_url(self.directory / 'placeholder').rsplit('/', 1)[0],
                  'position': self.home_position, 'rotation': self.home_rotation, 'fixed': self.fixed,
                  'targets': self.targets.copy(), 'mapping': self.mapping, 'frames': self.frames,
                  'motion_hz': self.motion_hz, 'direction': self.direction, 'sensors': self.sensors}
        if visuals:
            result.update(visuals=self.visuals, joints=self.joints, links=list(self.links), warnings=self.warnings)
        return result

    def add_object(self, obj):
        obj = dict(obj)
        obj['id'] = obj.get('id', uuid.uuid4().hex)
        obj.setdefault('name', obj['type'].title())
        obj.setdefault('position', [.25, 0, .025])
        obj.setdefault('rotation', [0, 0, 0])
        obj.setdefault('size', [.10, .10, .05])
        obj.setdefault('mass', 0)
        obj.setdefault('friction', .8)
        obj.setdefault('color', '#74909d')
        numbers = obj['position'] + obj['rotation'] + obj['size'] + [obj['mass'], obj['friction']]
        if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in numbers) or min(obj['size']) <= 0 or obj['mass'] < 0 or obj['friction'] < 0:
            raise ValueError('Object dimensions must be positive; mass and friction must be nonnegative')
        if obj['type'] == 'box':
            shape = p.createCollisionShape(p.GEOM_BOX, halfExtents=[v / 2 for v in obj['size']], physicsClientId=self.client)
        elif obj['type'] == 'sphere':
            shape = p.createCollisionShape(p.GEOM_SPHERE, radius=obj['size'][0] / 2, physicsClientId=self.client)
        elif obj['type'] == 'mesh':
            path = asset_path(obj['url'])
            shape = p.createCollisionShape(p.GEOM_MESH, fileName=str(path), meshScale=obj['size'],
                                          flags=p.GEOM_FORCE_CONCAVE_TRIMESH if obj['mass'] == 0 else 0, physicsClientId=self.client)
        else:
            raise ValueError('Unsupported object type')
        if shape < 0:
            raise ValueError('Could not create collision mesh')
        obj['body'] = p.createMultiBody(obj['mass'], shape, basePosition=obj['position'], baseOrientation=p.getQuaternionFromEuler(obj['rotation']), physicsClientId=self.client)
        p.changeDynamics(obj['body'], -1, lateralFriction=obj['friction'], physicsClientId=self.client)
        self.objects.append(obj)
        return obj

    def state(self):
        transforms = {}
        for name, i in self.links.items():
            if i < 0:
                pos, quat = p.getBasePositionAndOrientation(self.robot, physicsClientId=self.client)
                dyn = p.getDynamicsInfo(self.robot, -1, physicsClientId=self.client)
                ip, iq = p.invertTransform(dyn[3], dyn[4])
                pos, quat = p.multiplyTransforms(pos, quat, ip, iq)
            else:
                s = p.getLinkState(self.robot, i, computeForwardKinematics=1, physicsClientId=self.client)
                pos, quat = s[4:6]
            transforms[name] = [pos, quat]
        objects = {o['id']: p.getBasePositionAndOrientation(o['body'], physicsClientId=self.client) for o in self.objects}
        p.performCollisionDetection(physicsClientId=self.client)
        contacts = p.getContactPoints(bodyA=self.robot, physicsClientId=self.client)
        pos, quat = p.getBasePositionAndOrientation(self.robot, physicsClientId=self.client)
        return {'transforms': transforms, 'objects': objects, 'actors': {k: a.state() for k, a in self.actors.items()},
                'physics_hz': self.physics_hz, 'motion_hz': self.motion_hz, 'runtime_clock': self.runtime_clock,
                'running': self.running, 'playing': self.playing,
                'time': self.clock, 'playhead': self.playhead, 'targets': self.targets,
                'angles': {j['name']: math.degrees(p.getJointState(self.robot, j['id'], physicsClientId=self.client)[0]) for j in self.joints},
                'height': pos[2], 'rpy': p.getEulerFromQuaternion(quat), 'contacts': len(contacts),
                'sensors': self.sensor_readings()}

    def model(self):
        return {'joints': self.joints, 'visuals': self.visuals, 'warnings': self.warnings, 'xml': self.xml,
                'objects': [{k: v for k, v in o.items() if k != 'body'} for o in self.objects],
                'frames': self.frames, 'mapping': self.mapping, 'fixed': self.fixed, 'gravity': self.gravity,
                'friction': self.friction, 'targets': self.targets, 'physics_hz': self.physics_hz,
                'motion_hz': self.motion_hz, 'direction': self.direction,
                'robot_position': self.home_position, 'robot_rotation': self.home_rotation,
                'sensors': self.sensors, 'links': list(self.links),
                'actors': [a.actor_definition(True) for a in self.actors.values()], 'scripts': self.scripts,
                'motions': self.motions, 'controls': self.controls}
