"""Play Petoi console commands in the simulator.

Turns OpenCat commands (k<skill>, m/i joint commands) into timed joint-space segments using Petoi's own skill
data (petoi_skills.json) and the robot's Servo setup, inverted: joint angle = (servo angle - offset) / direction.
A plan is {'name', 'segments': [(seconds, {joint: degrees})], 'loop_from': index or None}.
"""
import json
import re
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parent / 'petoi_skills.json'
GAIT_FRAME_S = .02          # one gait frame per 20 ms, matching the walking speed checked in physics
POSTURE_S = .4              # transition into a posture
JOINT_COMMAND_S = .25       # transition for m / i joint commands
MIRROR = {8: 9, 9: 8, 10: 11, 11: 10, 12: 13, 13: 12, 14: 15, 15: 14}
STEP_S = .008               # firmware transform(): one interpolation step per ~8 ms (delay((DOF - offset) / 2))
SERVO_DEG_PER_S = 350       # realistic loaded speed of Bittle's servos (about 0.17 s per 60 degrees)
FIRST_FRAME_DEG = 45       # assumed travel into a behaviour's first frame (the current pose is not known here)


@lru_cache(maxsize=1)
def table():
    return json.loads(DATA.read_text(encoding='utf-8'))['skills']


def skill_names():
    return sorted(table())


def _skill(name):
    """Skill entry by name; right-turn gaits are mirrored from their left versions, as the firmware does."""
    skills = table()
    if name in skills:
        return skills[name]
    if name.endswith('R') and name[:-1] + 'L' in skills:
        left = skills[name[:-1] + 'L']
        servos = left['servos']
        mirrored = [[row[servos.index(MIRROR.get(s, s))] if MIRROR.get(s, s) in servos else row[i] for i, s in enumerate(servos)] for row in left['frames']]
        return {**left, 'frames': mirrored, 'roll': -left['roll']}
    return None


def _servo_map(mapping):
    out = {}
    for joint, m in mapping.items():
        servo, sign = m.get('servo'), m.get('sign', 1)
        if isinstance(servo, int) and 0 <= servo <= 15 and sign in (1, -1):
            out[servo] = (joint, sign, float(m.get('offset', 0)))
    return out


def _pose(servo_angles, servo_map, ratio=1):
    return {servo_map[s][0]: (a * ratio - servo_map[s][2]) / servo_map[s][1] for s, a in servo_angles.items() if s in servo_map}


def _skill_plan(name, mapping, speed=1.):
    entry = _skill(name)
    if entry is None:
        return None
    entry = resample(entry, speed)
    smap, ratio, servos = _servo_map(mapping), entry['ratio'], entry['servos']
    rows = [dict(zip(servos, row)) for row in entry['frames']]
    if entry['type'] == 'posture':
        return {'name': name, 'segments': [(POSTURE_S, _pose(rows[0], smap, ratio))], 'loop_from': None, 'ease': True}
    if entry['type'] == 'gait':
        # Ease into the first frame, then loop all frames until another command arrives.
        segments = [(POSTURE_S, _pose(rows[0], smap, ratio))] + [(GAIT_FRAME_S, _pose(row, smap, ratio)) for row in rows]
        return {'name': name, 'segments': segments, 'loop_from': 1}
    first, last, repeats = entry['loop']
    order = list(range(first)) + list(range(first, last + 1)) * max(1, repeats) + list(range(last + 1, len(rows)))
    segments, previous = [], None
    for i in order:
        row, (speed, delay) = rows[i], entry['timing'][i]
        delta = max((abs(a - previous[s]) for s, a in row.items() if s in previous), default=0) * ratio if previous else FIRST_FRAME_DEG
        segments.append((behavior_seconds(delta, speed), _pose(row, smap, ratio)))
        if delay:  # the firmware then holds the frame for |delay| x 50 ms
            segments.append((abs(delay) * .05, segments[-1][1]))
        previous = row
    return {'name': name, 'segments': segments, 'loop_from': None, 'ease': True}


def behavior_seconds(max_degrees, speed):
    """OpenCat transform(): round(maxDiff / (speed / 8)) steps of about 8 ms each (motion.h, ESP32 firmware)."""
    steps = round(max_degrees / (max(1, abs(speed)) / 8))
    # Fast frames are limited by the servos themselves, not the firmware's step count.
    return max(.02, steps * STEP_S, max_degrees / SERVO_DEG_PER_S)


SPEEDS = (.5, .75, 1., 1.25)   # Crab Studio speed dial: 1 slow, 2, 3 normal (as learned), 4 fast


def resample(entry, speed):
    """A gait played faster or slower with the same steps: its loop resampled to fewer (faster) or more (slower)
    frames, as the firmware plays one frame per step. Other skills are returned unchanged."""
    speed = float(speed or 1)
    if entry['type'] != 'gait' or abs(speed - 1) < 1e-6:
        return entry
    rows, n = entry['frames'], len(entry['frames'])
    m = max(4, round(n / speed))
    frames = []
    for j in range(m):
        t = j * n / m
        i, f = int(t), t - int(t)
        a, b = rows[i % n], rows[(i + 1) % n]
        frames.append([int(round(x + (y - x) * f)) for x, y in zip(a, b)])
    return dict(entry, frames=frames)


def plan(command, mapping, speed=1.):
    """Simulator plan for one console command, or None when it has no effect on the joints (gB, XAc, j...)."""
    text = str(command).strip()
    if text.startswith('k') and len(text) > 1:
        return _skill_plan(text[1:].split()[0], mapping, speed)
    if text == 'd':
        return _skill_plan('rest', mapping)
    match = re.fullmatch(r'([mi])\s*(-?\d+(?:\s+-?\d+)*)', text)
    if match:
        values = [int(v) for v in match.group(2).split()]
        if len(values) % 2:
            return None
        angles = dict(zip(values[::2], values[1::2]))
        pose = _pose(angles, _servo_map(mapping))
        return {'name': text, 'segments': [(JOINT_COMMAND_S, pose)], 'loop_from': None} if pose else None
    return None


def motion_plan(name, poses, step, loop):
    """Plan for a saved Studio function, sampled as joint poses every `step` seconds."""
    if not poses:
        return None
    segments = [(POSTURE_S, poses[0])] + [(step, pose) for pose in poses[1:]]
    return {'name': name, 'segments': segments, 'loop_from': 1 if loop and len(segments) > 1 else None}
