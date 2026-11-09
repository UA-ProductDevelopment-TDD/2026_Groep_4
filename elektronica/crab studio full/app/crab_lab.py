"""Crab Lab: Bittle X learns crab gaits in Bittle Studio's own physics (PyBullet).

Every gait is made by a small model with a handful of numbers (body height, how far out the feet stand, stride, foot
lift, frames per step cycle, how long each foot stays on the ground, the timing of the four legs, body tilt and, for
turning, how far each side steps). The optimiser (CMA-ES) tries many of these, runs each one in a headless copy of the
Studio simulation, and keeps what moves the robot furthest for the least energy without wobbling or falling. Every
candidate is tried twice: on the normal floor and on a slippery floor with a 30 % heavier claw, and only its worse
result counts, so what it learns does not depend on luck.

Used by the code panel script train_crab.py; the Studio server uses it to load the results.
"""
import json
import math
import random
import time
from pathlib import Path

import numpy as np
import pybullet as p

APP = Path(__file__).resolve().parent
CRAB = APP.parent
FIRMWARE = CRAB / 'firmware'
SKILLS_JSON = APP / 'petoi_skills.json'
MODELS = CRAB / 'robot-models' / 'bittle'
CONFIG = CRAB / 'user-data' / 'crab-config.json'


# ============================================================================================ claw fitted or not
# Crab Studio keeps two of everything: a model, learned gaits and firmware gaits with the claw arm fitted, and without.
# The switch (Robot control → Move → Claw) is stored in user-data/crab-config.json.
def claw_fitted():
    try:
        return bool(json.loads(CONFIG.read_text(encoding='utf-8')).get('claw', False))
    except (OSError, ValueError):
        return False


def set_claw_fitted(claw):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps({'claw': bool(claw)}), encoding='utf-8')


def claw_word(claw):
    return 'claw fitted' if claw else 'no claw'


def model_path(claw, preview=False):
    return MODELS / (('crab_preview_' if preview else 'bittle_') + ('claw' if claw else 'noclaw') + '.urdf')


def results_path(claw):
    return CRAB / 'results' / ('learned_gaits_claw.json' if claw else 'learned_gaits_noclaw.json')


def _migrate_results():
    """Older Crab Studio kept one learned_gaits.json, trained with the arm on: it becomes the claw set, and also the
    starting point (not the score to beat) for the no-claw set."""
    old = CRAB / 'results' / 'learned_gaits.json'
    claw_file, free_file = results_path(True), results_path(False)
    if old.is_file() and not claw_file.is_file():
        claw_file.write_text(old.read_text(encoding='utf-8'), encoding='utf-8')
    if claw_file.is_file() and not free_file.is_file():
        data = json.loads(claw_file.read_text(encoding='utf-8'))
        for entry in data.get('gaits', {}).values():
            if entry.get('learned'):
                entry['score'] = -1e9          # learned with the arm: start from it, but any new result replaces it
                entry['note'] = 'copied from the claw gaits; retrain without the claw'
        free_file.write_text(json.dumps(data, indent=1), encoding='utf-8')


try:
    _migrate_results()
except OSError:
    pass
CLAW = claw_fitted()                 # fixed for one training run (the trainer and its crabs read it once)
RESULTS = results_path(CLAW)

L1, L2 = 0.0492, 0.057            # Bittle X thigh and shin, m
FRAME_S = 0.02                    # firmware plays one gait frame per 20 ms
LEGS = ['LF', 'RF', 'RB', 'LB']   # OpenCat order: shoulders are servos 8-11, knees 12-15
FRONT = {'LF': True, 'RF': True, 'RB': False, 'LB': False}
LEFT = {'LF': True, 'RF': False, 'RB': False, 'LB': True}
KNEE_LIMIT = (-80, 195)
SHOULDER_LIMIT = {True: (-195, 80), False: (-80, 195)}

# name, low, high: the numbers the optimiser may change
PARAMS = [
    ('height', .085, .102),    # hip to floor, m
    ('reach', .012, .036),     # foot outward of its hip (front feet toward the head, rear toward the tail), m
    ('stride', .012, .052),    # step length, m
    ('lift', .006, .020),      # foot lift, m
    ('frames', 12, 32),        # frames per step cycle (x 20 ms)
    ('duty', .45, .78),        # share of the cycle a foot is on the floor
    ('phase_rf', 0., 1.),      # when each leg steps, relative to the left front leg
    ('phase_rb', 0., 1.),
    ('phase_lb', 0., 1.),
    ('pitch', -.008, .008),    # front feet lower (+) or higher (-): leans the body
    ('roll', -.010, .010),     # left legs longer (+): leans the body away from the claw side
    ('scale_lf', -1., 1.),     # stride of each leg (turning gaits only; straight gaits use 1 for all)
    ('scale_rf', -1., 1.),
    ('scale_rb', -1., 1.),
    ('scale_lb', -1., 1.),
]
SCALES = ['scale_lf', 'scale_rf', 'scale_rb', 'scale_lb']
NAMES = [n for n, _, _ in PARAMS]
LOW = np.array([lo for _, lo, _ in PARAMS], float)
HIGH = np.array([hi for _, _, hi in PARAMS], float)

# What each command has to do. Dog forward (toward the head) = the crab scuttles to its right.
TASKS = {
    'wkF': {'label': 'Crab walk right', 'direction': 1, 'mode': 'efficient', 'vmin': .06},
    'bk':  {'label': 'Crab walk left', 'direction': -1, 'mode': 'efficient', 'vmin': .06},
    'trF': {'label': 'Crab run right', 'direction': 1, 'mode': 'fast', 'vmin': .09},
    'wkL': {'label': 'Walk right, turning left', 'direction': 1, 'mode': 'curve', 'yaw': 15, 'vmin': .03},
    'wkR': {'label': 'Walk right, turning right', 'direction': 1, 'mode': 'curve', 'yaw': -15, 'vmin': .03},
    'bkL': {'label': 'Walk left, turning left', 'direction': -1, 'mode': 'curve', 'yaw': 15, 'vmin': .03},
    'bkR': {'label': 'Walk left, turning right', 'direction': -1, 'mode': 'curve', 'yaw': -15, 'vmin': .03},
    'vtL': {'label': 'Spin left on the spot', 'direction': 1, 'mode': 'spin', 'yaw': 30},
    'vtR': {'label': 'Spin right on the spot', 'direction': 1, 'mode': 'spin', 'yaw': -30},
}
ORDER = ['wkF', 'bk', 'trF', 'wkL', 'wkR', 'bkL', 'bkR', 'vtL', 'vtR']

# Starting points: the hand-made crab gaits (they already walk), so learning can only improve on them.
BASE = {'height': .095, 'reach': .026, 'stride': .036, 'lift': .014, 'frames': 20, 'duty': .5,
        'phase_rf': .5, 'phase_rb': 0., 'phase_lb': .5, 'pitch': 0., 'roll': 0.,
        'scale_lf': 1., 'scale_rf': 1., 'scale_rb': 1., 'scale_lb': 1.}


def sides(left, right):
    return {'scale_lf': left, 'scale_lb': left, 'scale_rf': right, 'scale_rb': right}


TURN = {'reach': .012, 'stride': .05, 'lift': .012, 'frames': 16}   # turning works best with the feet closer in
SEEDS = {
    'wkF': {}, 'bk': {}, 'trF': {'frames': 18},
    'wkL': dict(sides(-.2, 1.), duty=.65, **TURN), 'wkR': dict(sides(1., -.2), duty=.65, **TURN),
    'bkL': dict(sides(1., -.2), duty=.65, **TURN), 'bkR': dict(sides(-.2, 1.), duty=.65, **TURN),
    'vtL': dict(sides(-1., 1.), **TURN), 'vtR': dict(sides(1., -1.), **TURN),
}

# Test conditions: the result that counts is the worse of the first two; the third is only checked at the end.
# Floors to test every gait on. With the claw fitted the arm's weight varies too; without it only the grip does.
CONDITIONS = [
    {'name': 'normal floor', 'friction': .8, 'claw_mass': 1.0},
    {'name': 'slippery floor, heavy claw', 'friction': .55, 'claw_mass': 1.3},
    {'name': 'grippy floor, light claw', 'friction': 1.0, 'claw_mass': .7},
] if CLAW else [
    {'name': 'normal floor', 'friction': .8, 'claw_mass': 1.0},
    {'name': 'slippery floor', 'friction': .55, 'claw_mass': 1.0},
    {'name': 'grippy floor', 'friction': 1.0, 'claw_mass': 1.0},
]


# ============================================================================================ gait model
def ik(y, z, knee_forward):
    """Foot (y toward the head, z up, relative to the hip) -> (shoulder, knee) in OpenCat degrees; clamped flag."""
    d = math.hypot(y, z)
    clamped = d > L1 + L2 - 1e-4
    d = min(d, L1 + L2 - 1e-4)
    alpha = math.atan2(-y, -z)
    beta = math.acos(max(-1., min(1., (L1 ** 2 + d ** 2 - L2 ** 2) / (2 * L1 * d))))
    thigh = alpha - beta if knee_forward else alpha + beta
    ky, kz = -L1 * math.sin(thigh), -L1 * math.cos(thigh)
    shin = math.atan2(-(y - ky), -(z - kz))
    s = math.degrees(thigh)
    return s, math.degrees(shin) - s + 90, clamped


def neutral(g, leg):
    h = g['height'] + (g['pitch'] if FRONT[leg] else -g['pitch']) + (g['roll'] if LEFT[leg] else -g['roll'])
    return (g['reach'] if FRONT[leg] else -g['reach']), -h


def stance_row(g):
    row = [0] * 16
    for i, leg in enumerate(LEGS):
        s, k, _ = ik(*neutral(g, leg), FRONT[leg])
        row[8 + i], row[12 + i] = round(s), round(k)
    return row


def gait_rows(g, direction):
    """Frames of 8 servo angles (shoulders LF RF RB LB, knees LF RF RB LB) and how many feet had to be clamped."""
    n = int(round(g['frames']))
    offsets = {'LF': 0., 'RF': g['phase_rf'], 'RB': g['phase_rb'], 'LB': g['phase_lb']}
    rows, clamps = [], 0
    for f in range(n):
        row = [0] * 8
        for i, leg in enumerate(LEGS):
            step = g['stride'] * g['scale_' + leg.lower()]
            y0, z0 = neutral(g, leg)
            ph = (f / n - offsets[leg]) % 1.
            if ph < g['duty']:                                   # on the floor, sliding back
                u = ph / g['duty']
                y, z = y0 + direction * step * (.5 - u), z0
            else:                                                # in the air, swinging forward
                u = (ph - g['duty']) / (1 - g['duty'])
                y = y0 + direction * step * (-.5 + (1 - math.cos(math.pi * u)) / 2)
                z = z0 + g['lift'] * math.sin(math.pi * u)
            s, k, c = ik(y, z, FRONT[leg])
            clamps += c
            lo, hi = SHOULDER_LIMIT[FRONT[leg]]
            row[i], row[4 + i] = round(min(hi, max(lo, s))), round(min(KNEE_LIMIT[1], max(KNEE_LIMIT[0], k)))
        rows.append(row)
    return rows, clamps


def quantise(rows):
    """Firmware stores int8: above 125 degrees every value is stored halved (angle ratio 2)."""
    ratio = 2 if max(abs(v) for r in rows for v in r) > 125 else 1
    return [[int(round(v / ratio)) for v in r] for r in rows], ratio


def params_of(task, x=None):
    g = dict(BASE, **SEEDS[task])
    if x is not None:
        g.update({n: float(v) for n, v in zip(NAMES, LOW + np.clip(x, 0, 1) * (HIGH - LOW))})
    if TASKS[task]['mode'] not in ('curve', 'spin'):
        g.update({k: 1. for k in SCALES})
    g['frames'] = int(round(g['frames']))
    return g


def to_unit(g):
    return np.array([(g[n] - lo) / (hi - lo) for n, lo, hi in PARAMS], float)


# ============================================================================================ simulator
class Lab:
    """Headless copy of the Studio simulation used to score gaits."""

    def __init__(self):
        import engine
        import skills
        self.claw = CLAW
        model = model_path(CLAW)
        self.sim = engine.Simulation(xml=model.read_text(encoding='utf-8'), directory=model.parent)
        self.c = self.sim.client
        self.servo_map = skills._servo_map(self.sim.mapping)
        self.links = {p.getJointInfo(self.sim.robot, i, physicsClientId=self.c)[12].decode(): i
                      for i in range(p.getNumJoints(self.sim.robot, physicsClientId=self.c))}
        self.claw_links = [self.links[n] for n in ('claw-forearm-link', 'claw-pincer-link') if n in self.links]
        self.claw_mass = {i: p.getDynamicsInfo(self.sim.robot, i, physicsClientId=self.c)[0] for i in self.claw_links}
        self.mass = sum(p.getDynamicsInfo(self.sim.robot, i, physicsClientId=self.c)[0]
                        for i in range(-1, p.getNumJoints(self.sim.robot, physicsClientId=self.c)))
        self.leg_ids = [j['id'] for j in self.sim.joints if self.servo_map and
                        any(self.servo_map.get(s, (None,))[0] == j['name'] for s in range(8, 16))]
        self.evaluations = 0

    def joint_pose(self, servo_angles):
        return {self.servo_map[s][0]: (a - self.servo_map[s][2]) / self.servo_map[s][1]
                for s, a in servo_angles.items() if s in self.servo_map}

    def condition(self, cond):
        p.changeDynamics(self.sim.ground, -1, lateralFriction=cond['friction'], physicsClientId=self.c)
        for i in range(-1, p.getNumJoints(self.sim.robot, physicsClientId=self.c)):
            p.changeDynamics(self.sim.robot, i, lateralFriction=cond['friction'], physicsClientId=self.c)
        for i, m in self.claw_mass.items():
            p.changeDynamics(self.sim.robot, i, mass=m * cond['claw_mass'], physicsClientId=self.c)

    def run(self, rows, ratio, cond, cycles=None, seconds=2.6, settle=.5, record=False):
        """Play gait frames from a standstill; returns what happened (and, with record, the motion for a replay)."""
        s, c = self.sim, self.c
        self.evaluations += 1
        self.condition(cond)
        frames = [{8 + i: v * ratio for i, v in enumerate(r)} for r in rows]
        hold = {0: 0, 1: 0, 2: 0}
        s.running = False
        s.pose(self.joint_pose({**hold, **frames[0]}))
        p.resetBasePositionAndOrientation(s.robot, [0, 0, .2], [0, 0, 0, 1], physicsClientId=c)
        p.resetBaseVelocity(s.robot, [0, 0, 0], [0, 0, 0], physicsClientId=c)
        s.place_on_ground()
        s.running = True
        dt = 1 / s.physics_hz
        for _ in range(int(settle / dt)):
            s.tick()
        n = len(frames)
        total = cycles * n if cycles else max(int(math.ceil(seconds / FRAME_S)), 2 * n)
        warm = n                                         # first cycle is a warm-up, not measured
        energy = tilt_sum = tilt_max = 0.
        samples = 0
        start = None
        fell = False
        t = 0.
        trace = []
        origin = p.getBasePositionAndOrientation(s.robot, physicsClientId=c)[0]
        joint_ids = [j['id'] for j in s.joints]
        for k in range(total):
            s.pose(self.joint_pose(frames[k % n]))
            if k == warm:
                pos, q = p.getBasePositionAndOrientation(s.robot, physicsClientId=c)
                start = (np.array(pos), p.getEulerFromQuaternion(q)[2], t)
            t_end = (k + 1) * FRAME_S
            while t < t_end - 1e-9:
                s.tick()
                t += dt
                if k >= warm:
                    for st in p.getJointStates(s.robot, self.leg_ids, physicsClientId=c):
                        energy += abs(st[1] * st[3]) * dt
                    pos, q = p.getBasePositionAndOrientation(s.robot, physicsClientId=c)
                    r, pi, _ = p.getEulerFromQuaternion(q)
                    tilt = math.degrees(math.hypot(r, pi))
                    tilt_sum += tilt * tilt
                    tilt_max = max(tilt_max, tilt)
                    samples += 1
                    if tilt > 50 or pos[2] < .035:
                        fell = True
            if record:
                pos, q = p.getBasePositionAndOrientation(s.robot, physicsClientId=c)
                trace.append([round(pos[0] - origin[0], 4), round(pos[1] - origin[1], 4), round(pos[2], 4)]
                             + [round(v, 4) for v in q]
                             + [round(math.degrees(st[0]), 1) for st in p.getJointStates(s.robot, joint_ids, physicsClientId=c)])
            if fell:
                break
        pos, q = p.getBasePositionAndOrientation(s.robot, physicsClientId=c)
        yaw = p.getEulerFromQuaternion(q)[2]
        p0, yaw0, t0 = start if start else (np.array(pos), yaw, t)
        T = max(t - t0, 1e-3)
        d = np.array(pos) - p0
        # body-frame displacement at the start of the measurement
        fwd = np.array([-math.sin(yaw0), math.cos(yaw0)])      # body +y (toward the head)
        side = np.array([math.cos(yaw0), math.sin(yaw0)])      # body +x
        dyaw = math.degrees(math.atan2(math.sin(yaw - yaw0), math.cos(yaw - yaw0)))
        return {'v_fwd': float(d[:2] @ fwd / T), 'v_side': float(d[:2] @ side / T), 'v': float(np.linalg.norm(d[:2]) / T),
                'yaw_rate': dyaw / T, 'power': energy / T, 'tilt_rms': math.sqrt(tilt_sum / max(samples, 1)),
                'tilt_max': tilt_max, 'fell': fell, 'seconds': T, **({'trace': trace} if record else {})}


def score(task, m, clamps):
    """Higher is better. Efficient = little energy per metre (or per turn) at a useful speed, straight and steady."""
    spec = TASKS[task]
    mode = spec['mode']
    penalty = 1000 * m['fell'] + 2 * clamps + .4 * max(0., m['tilt_rms'] - 5) + .2 * max(0., m['tilt_max'] - 15)
    v = spec['direction'] * m['v_fwd']
    j_per_m = m['power'] / max(v, .01)
    if mode in ('efficient', 'fast'):
        shortfall = max(0., spec['vmin'] - v) / spec['vmin']
        straight = abs(m['v_side']) / max(abs(v), .02) * 20 + abs(m['yaw_rate']) * .3
        if mode == 'efficient':
            return -j_per_m - 60 * shortfall - straight - penalty
        return 100 * v - .3 * j_per_m - 60 * shortfall - straight - penalty
    if mode == 'curve':
        shortfall = max(0., spec['vmin'] - v) / spec['vmin']
        yaw_error = abs(m['yaw_rate'] - spec['yaw'])
        return -j_per_m - 60 * shortfall - 3 * yaw_error - penalty
    # spin: turn as fast as the target, for little energy, without wandering off
    rate = math.copysign(1, spec['yaw']) * m['yaw_rate']
    shortfall = max(0., abs(spec['yaw']) - rate)
    return -3 * shortfall - 10 * m['power'] - 300 * m['v'] - penalty


def evaluate(lab, task, g, conditions=(0, 1), record=False):
    """Score one gait on the given floors; record keeps the motion on the first floor for the 3D preview."""
    rows, clamps = gait_rows(g, TASKS[task]['direction'])
    rows, ratio = quantise(rows)
    results = [lab.run(rows, ratio, CONDITIONS[i], record=record and k == 0) for k, i in enumerate(conditions)]
    trace = results[0].pop('trace', None)
    scores = [score(task, m, clamps) for m in results]
    worst = int(np.argmin(scores))
    return {'score': float(min(scores)), 'metrics': results[worst], 'all': results, 'rows': rows, 'ratio': ratio,
            'clamps': clamps, 'trace': trace}


# ============================================================================================ optimiser
class CMAES:
    """Small CMA-ES in the unit cube (Hansen's standard settings)."""

    def __init__(self, mean, sigma=.12, popsize=10, seed=None):
        self.rng = np.random.default_rng(seed)
        self.n = n = len(mean)
        self.mean = np.array(mean, float)
        self.sigma = sigma
        self.lam = popsize
        self.mu = popsize // 2
        w = np.log(self.mu + .5) - np.log(np.arange(1, self.mu + 1))
        self.w = w / w.sum()
        self.mueff = 1 / np.sum(self.w ** 2)
        self.cc = (4 + self.mueff / n) / (n + 4 + 2 * self.mueff / n)
        self.cs = (self.mueff + 2) / (n + self.mueff + 5)
        self.c1 = 2 / ((n + 1.3) ** 2 + self.mueff)
        self.cmu = min(1 - self.c1, 2 * (self.mueff - 2 + 1 / self.mueff) / ((n + 2) ** 2 + self.mueff))
        self.damps = 1 + 2 * max(0, math.sqrt((self.mueff - 1) / (n + 1)) - 1) + self.cs
        self.pc = np.zeros(n)
        self.ps = np.zeros(n)
        self.C = np.eye(n)
        self.chiN = math.sqrt(n) * (1 - 1 / (4 * n) + 1 / (21 * n * n))
        self.gen = 0

    def ask(self):
        D, B = np.linalg.eigh(self.C)
        D = np.sqrt(np.maximum(D, 1e-20))
        self._BD = B * D
        self._B, self._D = B, D
        z = self.rng.standard_normal((self.lam, self.n))
        return np.clip(self.mean + self.sigma * z @ self._BD.T, 0, 1)

    def tell(self, xs, fitness):
        """fitness: higher is better."""
        idx = np.argsort(-np.asarray(fitness))[:self.mu]
        old = self.mean
        sel = np.asarray(xs)[idx]
        self.mean = self.w @ sel
        y = (self.mean - old) / self.sigma
        invsqrt = self._B @ np.diag(1 / self._D) @ self._B.T
        self.ps = (1 - self.cs) * self.ps + math.sqrt(self.cs * (2 - self.cs) * self.mueff) * invsqrt @ y
        self.gen += 1
        hsig = np.linalg.norm(self.ps) / math.sqrt(1 - (1 - self.cs) ** (2 * self.gen)) / self.chiN < 1.4 + 2 / (self.n + 1)
        self.pc = (1 - self.cc) * self.pc + hsig * math.sqrt(self.cc * (2 - self.cc) * self.mueff) * y
        ys = (sel - old) / self.sigma
        self.C = ((1 - self.c1 - self.cmu) * self.C + self.c1 * (np.outer(self.pc, self.pc) + (1 - hsig) * self.cc * (2 - self.cc) * self.C)
                  + self.cmu * (ys.T * self.w) @ ys)
        self.sigma *= math.exp((self.cs / self.damps) * (np.linalg.norm(self.ps) / self.chiN - 1))
        self.sigma = float(min(max(self.sigma, .01), .4))


def free_mask(task):
    """Which numbers a task may change: straight gaits keep both sides stepping the same way."""
    turning = TASKS[task]['mode'] in ('curve', 'spin')
    return np.array([turning or n not in SCALES for n in NAMES])


def learn(evaluate_many, task, generations=14, popsize=10, start=None, report=print, on_round=None, seed=None, record=False,
          show=None, show_every=4):
    """Optimise one command. Returns the best result (never worse than the starting gait).

    evaluate_many(task, list_of_params, conditions, record) scores a whole round at once: local_evaluator(Lab()) does
    it one by one, CrabPool does it with several crabs at the same time. on_round(task, round, results, best) is called
    after every round (round 0 = the starting gait)."""
    if isinstance(evaluate_many, Lab):              # older train_crab.py: one crab, best gait shown every few rounds
        evaluate_many = local_evaluator(evaluate_many)
    if show:
        shown_round = on_round

        def on_round(task, gen, results, best):
            if shown_round:
                shown_round(task, gen, results, best)
            if gen and gen % show_every == 0:
                show(task, best, gen)
    g0 = start or params_of(task)
    mask = free_mask(task)
    x0 = to_unit(g0)
    best = dict(evaluate_many(task, [g0], (0, 1), record)[0], params=g0, generation=0)
    report(f'{task} ({TASKS[task]["label"]}): start {describe(task, best)}')
    if on_round:
        on_round(task, 0, [best], best)
    es = CMAES(x0[mask], popsize=popsize, seed=seed)
    for gen in range(1, generations + 1):
        xs = es.ask()
        params = []
        for x in xs:
            full = x0.copy()
            full[mask] = x
            params.append(params_of(task, full))
        results = evaluate_many(task, params, (0, 1), record)
        for r, g in zip(results, params):
            r['params'] = g
            if r['score'] > best['score']:
                best = dict(r, generation=gen)
        es.tell(xs, [r['score'] for r in results])
        report(f'  generation {gen:2d}/{generations}: best {describe(task, best)}  (spread {es.sigma:.2f})')
        if on_round:
            on_round(task, gen, results, best)
    # final check on all three floors
    final = evaluate_many(task, [best['params']], (0, 1, 2), False)[0]
    best.update(check=final['all'], check_score=final['score'])
    return best


def local_evaluator(lab):
    """Score gaits one after another in this process (one crab)."""
    def evaluate_many(task, params, conditions=(0, 1), record=False):
        return [evaluate(lab, task, g, conditions, record) for g in params]
    return evaluate_many


# ============================================================================================ skills for the real robot
NOT_SENT = {'calib', 'zero'}      # these stay firmware commands (calibration)


def skill_packet(entry):
    """A skill of the Studio table as the OpenCat skill array the firmware takes in a K packet (the same layout as in
    InstinctBittleESP_crab.h). Crab Studio sends this to the robot instead of k<name>, so the robot plays exactly the
    crab skill shown in the simulator, whatever skill table its firmware has."""
    import hashlib
    ratio = int(entry.get('ratio', 1))
    roll, pitch = int(entry.get('roll', 0)), int(entry.get('pitch', 0))
    servos = list(entry['servos'])

    def row(frame, wanted):
        values = dict(zip(servos, frame))
        return [int(values.get(s, 0)) for s in wanted]
    frames = entry['frames']
    if entry['type'] == 'gait':
        data = [len(frames), roll, pitch, ratio] + [v for f in frames for v in row(f, range(8, 16))]
    elif entry['type'] == 'posture':
        data = [1, roll, pitch, ratio] + row(frames[0], range(16))
    else:
        timing = entry.get('timing') or [[8, 0]] * len(frames)
        data = [-len(frames), roll, pitch, ratio] + list(entry.get('loop') or [0, 0, 0])
        for f, (speed, hold) in zip(frames, timing):
            data += row(f, range(16)) + [int(speed), int(hold), 0, 0]
    if not all(-128 <= v <= 127 for v in data):
        raise ValueError('skill value outside the signed-byte range')
    signature = hashlib.sha256(bytes(v & 255 for v in data)).hexdigest()[:16]
    return {'skill': data, 'signature': signature, 'type': entry['type'], 'hz': 50}


def robot_skills(speed=1.):
    """All crab skills ready to send to the robot, by name (wkF, bk, vtL, up, balance, pickup, ...); gaits played at
    the speed dial's speed, exactly as the simulator plays them."""
    import skills
    table = json.loads(SKILLS_JSON.read_text(encoding='utf-8'))['skills']
    return {name: dict(skill_packet(skills.resample(entry, speed)), name=name)
            for name, entry in table.items() if name not in NOT_SENT}


# ============================================================================================ several crabs at once
WORKER_TAG = '@@CRAB@@'


def _plain(value):
    return value.item() if hasattr(value, 'item') else str(value)


def worker_main():
    """One crab in its own physics world: reads gaits to try (one JSON line each) and answers with the scores."""
    import sys
    lab = Lab()
    sys.stdout.write(WORKER_TAG + json.dumps({'ready': True}) + '\n')
    sys.stdout.flush()
    for line in sys.stdin:
        job = json.loads(line)
        if job.get('quit'):
            break
        try:
            r = evaluate(lab, job['task'], job['params'], tuple(job['conditions']), job['record'])
            r['evaluations'] = lab.evaluations
        except Exception as error:   # report it instead of hanging the trainer
            r = {'error': f'{type(error).__name__}: {error}'}
        r['id'] = job['id']
        sys.stdout.write(WORKER_TAG + json.dumps(r, default=_plain) + '\n')
        sys.stdout.flush()


class CrabPool:
    """Several crabs, each in its own physics world (a separate Python process), trying gaits at the same time."""

    def __init__(self, workers, idle=None, log_dir='.'):
        import subprocess
        import sys
        import threading
        from queue import SimpleQueue
        self.idle = idle                       # called while waiting (the trainer plays the 3D preview there)
        self.inbox = SimpleQueue()
        self.arrived = threading.Event()
        self.log_path = Path(log_dir) / 'crab_workers.log'
        log = open(self.log_path, 'w', encoding='utf-8')
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        self.procs = [subprocess.Popen([sys.executable, '-u', str(Path(__file__).resolve()), '--worker'],
                                       cwd=str(APP), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log,
                                       text=True, encoding='utf-8', bufsize=1, creationflags=flags)
                      for _ in range(max(1, int(workers)))]
        for i, proc in enumerate(self.procs):
            threading.Thread(target=self._read, args=(i, proc), daemon=True).start()
        self.evaluations = 0
        ready = 0
        while ready < len(self.procs):           # wait until every crab has loaded its world
            i, msg = self._next()
            ready += 1

    def _read(self, i, proc):
        for line in proc.stdout:
            if line.startswith(WORKER_TAG):
                self.inbox.put((i, json.loads(line[len(WORKER_TAG):])))
                self.arrived.set()
        self.inbox.put((i, None))
        self.arrived.set()

    def _next(self):
        while True:
            if not self.inbox.empty():
                i, msg = self.inbox.get()
                if msg is None:
                    tail = self.log_path.read_text(encoding='utf-8', errors='replace')[-1500:] if self.log_path.is_file() else ''
                    raise RuntimeError('A training crab stopped unexpectedly.\n' + tail)
                return i, msg
            self.arrived.clear()
            if self.inbox.empty():
                if self.idle:
                    self.idle()
                else:
                    self.arrived.wait(.05)

    def _send(self, i, job):
        self.procs[i].stdin.write(json.dumps(job, default=_plain) + '\n')
        self.procs[i].stdin.flush()

    def evaluate_many(self, task, params, conditions=(0, 1), record=False):
        jobs = [{'id': k, 'task': task, 'params': g, 'conditions': list(conditions), 'record': bool(record)}
                for k, g in enumerate(params)]
        results = [None] * len(jobs)
        waiting = list(reversed(jobs))
        for i in range(min(len(self.procs), len(jobs))):
            self._send(i, waiting.pop())
        done = 0
        while done < len(jobs):
            i, r = self._next()
            if 'error' in r:
                raise RuntimeError('Training crab error: ' + r['error'])
            results[r.pop('id')] = r
            r.pop('evaluations', None)
            done += 1
            self.evaluations += len(conditions)
            if waiting:
                self._send(i, waiting.pop())
        return results

    def close(self):
        for proc in self.procs:
            try:
                proc.stdin.write(json.dumps({'quit': True}) + '\n')
                proc.stdin.close()
            except OSError:
                pass
        for proc in self.procs:
            try:
                proc.kill()
            except OSError:
                pass


def describe(task, r):
    m = r['metrics']
    mode = TASKS[task]['mode']
    if mode == 'spin':
        return f'score {r["score"]:7.1f} | turns {m["yaw_rate"]:5.0f} deg/s, {m["power"]:.2f} W, drifts {100 * m["v"]:.1f} cm/s'
    v = TASKS[task]['direction'] * m['v_fwd']
    extra = f', turns {m["yaw_rate"]:4.0f} deg/s' if mode == 'curve' else ''
    return (f'score {r["score"]:7.1f} | {100 * v:4.1f} cm/s, {m["power"]:.2f} W, '
            f'{m["power"] / max(v, .01):5.1f} J/m{extra}, tilt {m["tilt_rms"]:.0f} deg' + (' FELL' if m['fell'] else ''))


# ============================================================================================ pick up trash
# kpickup: reach down, grab, lift the claw beside the body, swing it over the head and open it above the shell.
# Servo 0 swings the arm, servo 1 is the elbow (+ lifts the claw), servo 2 the pincer (0 closed, -40 open).
# Checked against the 3D parts (dev-tools/check_pickup.py), all along the movement:
#   eyes stay at least 2 cm clear, the yoke never comes closer to the shell than at rest,
#   the claw stays clear of the legs, and the open jaws stop about 1-2 mm above the floor.
# More than about 55 degrees of arm swing would push the yoke into the front of the shell.
PICKUP_LOW = .084           # body height while grabbing (normal stance about .095)
PICKUP = [  # stance, arm swing, elbow, pincer, speed, hold (x 50 ms), what happens
    ('up', 0, 0, 0, 8, 0, 'stand, claw at rest'),
    ('up', 0, -60, -40, 8, 0, 'open the claw and lower it'),
    ('low', 0, -90, -40, 4, 4, 'crouch: open jaws just above the floor'),
    ('low', 0, -90, 0, 6, 6, 'close: grab the trash'),
    ('up', 0, -30, 0, 6, 0, 'stand up'),
    ('up', 0, 75, 0, 8, 0, 'lift the claw up beside the body'),
    ('up', 50, 75, 0, 4, 2, 'swing it over the head'),
    ('up', 50, 75, -40, 8, 10, 'open above the shell: the trash drops'),
    ('up', 50, 75, 0, 8, 4, 'close'),
    ('up', 0, 75, 0, 6, 0, 'swing back'),
    ('up', 0, 0, 0, 6, 0, 'claw back to rest'),
]


def pickup_rows(results=None):
    """kpickup frames (16 servo angles each, full degrees) and their [speed, hold] timing."""
    results = results or load_results()
    walk = dict(params_of('wkF'), **((results.get('gaits') or {}).get('wkF') or {}).get('params', {}))
    stances = {'up': results.get('up') or stance_row(walk), 'low': stance_row(dict(walk, height=PICKUP_LOW, pitch=0.))}
    rows, timing = [], []
    for stance, swing, elbow, pincer, speed, hold, _ in PICKUP:
        row = list(stances[stance])
        row[0], row[1], row[2] = swing, elbow, pincer
        rows.append(row)
        timing.append([speed, hold])
    return rows, timing


def pickup_skill(results=None):
    """kpickup as an OpenCat behaviour (Studio skill table format)."""
    rows, timing = pickup_rows(results)
    ratio = 2 if max(abs(v) for r in rows for v in r) > 125 else 1
    return {'servos': list(range(16)), 'frames': [[int(round(v / ratio)) for v in r] for r in rows], 'ratio': ratio,
            'type': 'behavior', 'loop': [0, 0, 0], 'timing': timing, 'roll': 0, 'pitch': 0}


def behavior_motion(name, skill, mapping):
    """An OpenCat behaviour as a Studio function, timed like the firmware plays it."""
    import skills
    smap = skills._servo_map(mapping)
    frames, t, previous = [], 0., None
    for row, (speed, hold) in zip(skill['frames'], skill['timing']):
        angles = dict(zip(skill['servos'], row))
        if previous is not None:
            delta = max(abs(a - previous[s]) for s, a in angles.items()) * skill['ratio']
            t += skills.behavior_seconds(delta, speed)
        pose = skills._pose(angles, smap, skill['ratio'])
        frames.append({'time': round(t, 3), 'pose': pose, 'easing': 'linear'})
        if hold:
            t += hold * .05
            frames.append({'time': round(t, 3), 'pose': dict(pose), 'easing': 'linear'})
        previous = angles
    return {'name': name, 'motion_type': 'behavior', 'hz': 50, 'speed': 1, 'direction': 'forward', 'frames': frames}


# ============================================================================================ results
def load_results(claw=None):
    path = results_path(CLAW if claw is None else claw)
    if path.is_file():
        return json.loads(path.read_text(encoding='utf-8'))
    return {'gaits': {}}


def save_results(results, claw=None):
    path = results_path(CLAW if claw is None else claw)
    path.parent.mkdir(parents=True, exist_ok=True)
    results['claw'] = CLAW if claw is None else bool(claw)
    path.write_text(json.dumps(results, indent=1), encoding='utf-8')


def result_entry(task, best):
    return {'label': TASKS[task]['label'], 'params': best['params'], 'frames': best['rows'], 'ratio': best['ratio'],
            'score': best['score'], 'metrics': best['metrics'], 'check': best.get('check'),
            'learned': time.strftime('%Y-%m-%d %H:%M')}


def seed_results():
    """Results file with the hand-made starting gaits (used before any learning)."""
    out = {'gaits': {}, 'up': None}
    for task in ORDER:
        g = params_of(task)
        rows, _ = gait_rows(g, TASKS[task]['direction'])
        rows, ratio = quantise(rows)
        out['gaits'][task] = {'label': TASKS[task]['label'], 'params': g, 'frames': rows, 'ratio': ratio, 'learned': None}
    out['up'] = stance_row(params_of('wkF'))
    return out


# ---- skill table for the Studio (petoi_skills.json) --------------------------------------------------------
def write_skill_table(results):
    data = json.loads(SKILLS_JSON.read_text(encoding='utf-8'))
    skills = data['skills']
    up = results.get('up') or stance_row(params_of('wkF'))
    ratio = 2 if max(abs(v) for v in up) > 125 else 1
    posture = {'servos': list(range(16)), 'frames': [[int(round(v / ratio)) for v in up]], 'ratio': ratio,
               'type': 'posture', 'loop': None, 'roll': 0, 'pitch': 0}
    skills['up'] = dict(posture)
    skills['balance'] = dict(posture)
    for task, entry in results['gaits'].items():
        skills[task] = {'servos': list(range(8, 16)), 'frames': entry['frames'], 'ratio': entry['ratio'],
                        'type': 'gait', 'loop': None, 'roll': 0, 'pitch': 0}
    if 'bk' in results['gaits']:
        skills['bkF'] = dict(skills['bk'])
    skills['pickup'] = pickup_skill(results)
    data['claw'] = bool(results.get('claw', CLAW))
    SKILLS_JSON.write_text(json.dumps(data, separators=(',', ':')), encoding='utf-8')


# ---- firmware (OpenCat instinct file) ---------------------------------------------------------------------
FIRMWARE_NOTE = {
    'wkF': 'crab walk right (toward the head side)', 'bk': 'crab walk left (toward the tail side)',
    'bkF': 'same as bk', 'trF': 'crab run right', 'wkL': 'walk right, turning left', 'wkR': 'walk right, turning right',
    'bkL': 'walk left, turning left', 'bkR': 'walk left, turning right', 'vtL': 'spin left on the spot',
    'vtR': 'spin right on the spot',
}


def firmware_text(results, template=None):
    """InstinctBittleESP_crab.h: the dog skill file with the crab stance and the learned crab gaits."""
    import re
    text = (template or FIRMWARE / 'InstinctBittleESP.h').read_text(encoding='utf-8')
    gaits = dict(results['gaits'])
    if 'bk' in gaits:
        gaits['bkF'] = gaits['bk']

    def block(name, header, rows, note):
        body = '\n'.join('  ' + ', '.join(f'{v:4d}' for v in r) + ',' for r in rows)
        return f'// CRAB: {note}\nconst int8_t {name}[] PROGMEM = {{ \n{header},\n{body}\n}};'

    new = {}
    for name, e in gaits.items():
        when = e.get('learned') or 'hand-made start gait'
        new[name] = block(name, f'{len(e["frames"])}, 0, 0, {e["ratio"]}', e['frames'],
                          f'{FIRMWARE_NOTE.get(name, name)}. Learned in Crab Studio: {when}. Angle ratio {e["ratio"]}.')
    up = results.get('up') or stance_row(params_of('wkF'))
    ratio = 2 if max(abs(v) for v in up) > 125 else 1
    up_q = [int(round(v / ratio)) for v in up]
    for name in ('balance', 'up'):
        new[name] = (f'// CRAB: crab stance. Angle ratio {ratio}.\nconst int8_t {name}[] PROGMEM = {{ \n1, 0, 0, {ratio},\n  '
                     + ', '.join(f'{v:3d}' for v in up_q) + ',};')
    pick = pickup_skill(results)
    lines = '\n'.join('  ' + ', '.join(f'{v:4d}' for v in r) + f',\t{sp:2d}, {hold:2d}, 0, 0,'
                      for r, (sp, hold) in zip(pick['frames'], pick['timing']))
    new['pickup'] = (f'// CRAB: kpickup, pick up trash: grab from the floor, lift the claw over the head, open it above the\n'
                     f'// shell. Servo 0 swings the arm, 1 is the elbow (+ lifts), 2 the pincer (0 closed, -40 open). '
                     f'Angle ratio {pick["ratio"]}.\nconst int8_t pickup[] PROGMEM = {{ \n'
                     f'-{len(pick["frames"])}, 0, 0, {pick["ratio"]},\n 0, 0, 0,\n{lines}\n}};')
    added = []
    for name, b in new.items():
        pattern = re.compile(r'const int8_t ' + re.escape(name) + r'\[\] PROGMEM = \{.*?\};', re.S)
        text, count = pattern.subn(lambda m: b, text, count=1)
        if not count:                                    # a skill the dog does not have (wkR, bkR, vtR)
            anchor = text.index('const char* skillNameWithType')
            anchor = text.rfind('\n', 0, anchor)
            text = text[:anchor] + '\n' + b + '\n' + text[anchor:]
            added.append(name)
    if added:
        text = re.sub(r'(const char\* skillNameWithType\[\]=\{)', lambda m: m.group(1) + ''.join(f'"{n}I",' for n in added), text)
        text = re.sub(r'(const int8_t\* progmemPointer\[\] = \{)(?!zero, \})', lambda m: m.group(1) + ''.join(f'{n}, ' for n in added), text)
        count = len(re.search(r'const char\* skillNameWithType\[\]=\{(.*?)\};', text, re.S).group(1).split('","'))
        text = re.sub(r'//number of skills: \d+', f'//number of skills: {count}', text, count=1)
    header = ('// =====================================================================================\n'
              '// Bittle X CRAB instincts, written by Crab Studio (crab/app/crab_lab.py). Selected by #define CRAB.\n'
              '// The dog skill file with the crab stance (up, balance) and the crab gaits learned in the simulator:\n'
              '// ' + ', '.join(sorted(gaits)) + ', and the claw behaviour pickup (kpickup).\n'
              '// Gaits: 8 values per frame, shoulders LF RF RB LB (servo 8-11), knees LF RF RB LB (servo 12-15).\n'
              '// Angle ratio 2: every stored value is half the real angle.\n'
              '// =====================================================================================\n')
    return header + text


def write_firmware(results, claw=None):
    """Write the instinct file to crab/firmware and, when found, into the OpenCat source folder (old one kept as .bak).
    A copy for this claw setting stays in crab/firmware (InstinctBittleESP_crab_claw.h / _noclaw.h)."""
    claw = CLAW if claw is None else bool(claw)
    text = (f'// Crab Studio: crab gaits for the crab with {claw_word(claw).upper()} '
            f'(switch: Robot control -> Move -> Claw)\n') + firmware_text(results)
    written = []
    target = FIRMWARE / 'InstinctBittleESP_crab.h'
    target.write_text(text, encoding='utf-8')
    written.append(target)
    (FIRMWARE / ('InstinctBittleESP_crab_claw.h' if claw else 'InstinctBittleESP_crab_noclaw.h')).write_text(text, encoding='utf-8')
    opencat = CRAB.parent / 'OpenCatEsp32-Quadruped-Robot-main' / 'OpenCatEsp32-Quadruped-Robot-main'
    for src in (opencat / 'src', opencat / 'OpenCatEsp32' / 'src'):
        if src.is_dir():
            dest = src / 'InstinctBittleESP_crab.h'
            if dest.is_file():
                dest.with_suffix('.h.bak').write_text(dest.read_text(encoding='utf-8'), encoding='utf-8')
            dest.write_text(text, encoding='utf-8')
            written.append(dest)
    return written


# ---- Studio functions (My skills) -------------------------------------------------------------------------
def gait_motion(name, entry, mapping):
    """A learned gait as a Studio function (motion_type gait), one keyframe per firmware frame."""
    servo_map = {}
    for joint, m in mapping.items():
        if isinstance(m.get('servo'), int):
            servo_map[m['servo']] = (joint, m.get('sign', 1), float(m.get('offset', 0)))
    frames = []
    for k, row in enumerate(entry['frames']):
        pose = {}
        for i, v in enumerate(row):
            joint, sign, offset = servo_map[8 + i]
            pose[joint] = (v * entry['ratio'] - offset) / sign
        frames.append({'time': round(k * FRAME_S, 4), 'pose': pose, 'easing': 'linear'})
    n = len(frames)
    frames.append({'time': round(n * FRAME_S, 4), 'pose': dict(frames[0]['pose']), 'easing': 'linear'})
    return {'name': name, 'motion_type': 'gait', 'hz': 50, 'speed': 1, 'direction': 'forward', 'frames': frames}


def notify_studio(port=8775):
    """Ask the running Crab Studio to load the new results (skill table and My skills)."""
    import urllib.request
    try:
        req = urllib.request.Request(f'http://127.0.0.1:{port}/api/crab/reload', data=b'{}', method='POST',
                                     headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode())
    except Exception as error:   # the Studio may not be running when this is used from a terminal
        return {'error': str(error)}


if __name__ == '__main__':
    import sys
    if '--worker' in sys.argv:
        worker_main()
