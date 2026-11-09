"""Check kpickup against the 3D parts: eyes, shell, legs and floor, all along the movement.

Needs scipy (pip install scipy). Run:  python dev-tools/check_pickup.py
The firmware moves all servos of a frame together, so the path between two frames is a straight line in servo angles.
"""
import math
import sys
from pathlib import Path

import numpy as np
import pybullet as p
import trimesh
from scipy.spatial import cKDTree

CRAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CRAB / 'app'))
import crab_lab as C  # noqa: E402

MODEL = CRAB / 'robot-models' / 'bittle'
SERVO = {0: ('neck-joint', 1), 1: ('claw-elbow-joint', 1), 2: ('claw-pincer-joint', 1),
         8: ('left-front-shoulder-joint', 1), 9: ('right-front-shoulder-joint', -1), 10: ('right-back-shoulder-joint', -1),
         11: ('left-back-shoulder-joint', 1), 12: ('left-front-knee-joint', 1), 13: ('right-front-knee-joint', -1),
         14: ('right-back-knee-joint', -1), 15: ('left-back-knee-joint', 1)}
MOVING = {'forearm': ('forearm_claw.stl', 'claw-forearm-link'), 'pincer': ('pincer.stl', 'claw-pincer-link'),
          'yoke': ('neck_yoke.stl', 'head-link'), 'elbow servo': ('elbow_servo.stl', 'head-link')}
LEG_SKIP = {'claw-forearm-link', 'claw-pincer-link', 'head-link', 'neck-mount-link', 'crab-shell-link'}

c = p.connect(p.DIRECT)
robot = p.loadURDF(str(MODEL / 'bittle.urdf'), [0, 0, 0], useFixedBase=True, physicsClientId=c)
JOINT = {p.getJointInfo(robot, i, physicsClientId=c)[1].decode(): i for i in range(p.getNumJoints(robot, physicsClientId=c))}
LINK = {p.getJointInfo(robot, i, physicsClientId=c)[12].decode(): i for i in range(p.getNumJoints(robot, physicsClientId=c))}
rng = np.random.default_rng(1)
points = {}
hulls = {}
for name, (f, _) in MOVING.items():
    mesh = trimesh.load(MODEL / 'crab' / f)
    points[name] = np.vstack([mesh.vertices, mesh.sample(6000, seed=rng)])
    shape = p.createCollisionShape(p.GEOM_MESH, fileName=str(MODEL / 'crab' / f), physicsClientId=c)
    hulls[name] = p.createMultiBody(0, shape, physicsClientId=c)
trees = {}
for name in ('eyes', 'shell'):
    mesh = trimesh.load(MODEL / 'crab' / f'crab_{name}.stl')
    trees[name] = cKDTree(np.vstack([mesh.vertices, mesh.sample(60000, seed=rng)]))


def frame(link):
    s = p.getLinkState(robot, LINK[link], computeForwardKinematics=1, physicsClientId=c)
    return np.array(p.getMatrixFromQuaternion(s[5])).reshape(3, 3), np.array(s[4]), s[5]


def measure(row):
    for s, (j, sign) in SERVO.items():
        p.resetJointState(robot, JOINT[j], math.radians(row[s] * sign), physicsClientId=c)
    Rs, ts, _ = frame('crab-shell-link')
    out = {}
    world = {}
    for name, (_, link) in MOVING.items():
        R, t, q = frame(link)
        world[name] = points[name] @ R.T + t
        local = (world[name] - ts) @ Rs
        out[name + ' - eyes'] = trees['eyes'].query(local)[0].min()
        out[name + ' - shell'] = trees['shell'].query(local)[0].min()
        p.resetBasePositionAndOrientation(hulls[name], t, q, physicsClientId=c)
    legs = [pt[8] for name in ('forearm', 'pincer') for link, i in LINK.items() if link not in LEG_SKIP
            for pt in p.getClosestPoints(hulls[name], robot, .1, linkIndexB=i, physicsClientId=c)]
    out['claw - legs/body'] = min(legs, default=.1)
    feet = min(p.getAABB(robot, LINK[n], physicsClientId=c)[0][2] for n in LINK if 'knee' in n)
    out['claw - floor'] = min(world[n][:, 2].min() for n in ('forearm', 'pincer')) - feet
    return out


def main():
    skill = C.pickup_skill()
    rows = [[v * skill['ratio'] for v in r] for r in skill['frames']]     # what the firmware really plays
    rest = measure(rows[0])
    worst = {k: (1., None) for k in rest}
    for i in range(len(rows) - 1):
        a, b = np.array(rows[i], float), np.array(rows[i + 1], float)
        for u in np.linspace(0, 1, 25):
            m = measure(a + (b - a) * u)
            for k, v in m.items():
                if v < worst[k][0]:
                    worst[k] = (v, f'frame {i + 1}->{i + 2} ({C.PICKUP[i + 1][6]}) at {u:.0%}')
    print('closest approach during kpickup (at rest in brackets):')
    ok = True
    for k, (v, where) in worst.items():
        limit = .0 if 'floor' in k else rest[k] - .0005 if k == 'yoke - shell' else .005
        flag = 'ok' if v >= min(limit, rest[k]) else 'TOO CLOSE'
        ok &= flag == 'ok'
        print(f'  {k:22s} {1000 * v:6.1f} mm  ({1000 * rest[k]:5.1f})  {flag}  {where or ""}')
    print('all clear' if ok else 'CHECK THE MOVEMENT')


if __name__ == '__main__':
    main()
