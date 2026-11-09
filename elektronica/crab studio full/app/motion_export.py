"""One chronological sampler shared by exports and code-panel previews."""
import math
import pprint
import hashlib


def build_motion(sim, body, hardware=True):
    motion_type = body.get('motion_type', 'behavior')
    if motion_type not in ('pose', 'behavior', 'gait'):
        raise ValueError('Motion type must be pose, behavior or gait')
    if motion_type != 'pose' and len(sim.frames) < 2:
        raise ValueError(f'A behavior or gait needs at least two keyframes; the timeline has {len(sim.frames)}. '
                         'Add keyframes, or choose Motion type: Pose to export the current pose.')
    mapping = body.get('mapping', sim.mapping)
    used, problems = set(), []
    for joint in sim.joints:
        m = mapping.get(joint['name'], {})
        servo = m.get('servo', -1)
        name = joint['name'].replace('-joint', '').replace('-', ' ')
        if not isinstance(servo, int) or not 0 <= servo <= 15:
            problems.append(f'{name} (no servo number)')
        elif servo in used:
            problems.append(f'{name} (servo {servo} used twice)')
        elif m.get('sign') not in (-1, 1):
            problems.append(f'{name} (no direction)')
        elif hardware and not m.get('verified'):
            problems.append(f'{name} (not verified)')
        if not math.isfinite(float(m.get('offset', 0))):
            raise ValueError('Servo offsets must be finite')
        used.add(servo)
    if problems:
        # The wording "Verify a unique servo index" is kept so older notes and searches still match.
        raise ValueError('Verify a unique servo index (0–15) and direction for every joint. '
                         'Open Servo setup in the top bar for: ' + ', '.join(problems))
    hz = float(body.get('hz', sim.motion_hz))
    speed = float(body.get('speed', .5))
    direction = body.get('direction', sim.direction)
    if not math.isfinite(hz) or not 1 <= hz <= 240:
        raise ValueError('Motion Hz must be between 1 and 240')
    if not math.isfinite(speed) or not .1 <= speed <= 2:
        raise ValueError('Speed must be between 0.1 and 2')
    if direction not in ('forward', 'reverse'):
        raise ValueError('Choose forward or reverse order')
    duration = 0 if motion_type == 'pose' else sim.frames[-1]['time'] / speed
    if duration * hz > 150000:
        raise ValueError('Motion is too large; lower Hz or shorten the timeline')
    samples = []
    # A pose captures the currently displayed target. Behaviors and gaits include
    # the first and exact last timeline pose once; timestamps stay chronological.
    for i in range(math.ceil(duration * hz) + 1):
        timestamp = min(i / hz, duration)
        t = timestamp * speed
        if motion_type == 'pose':
            pose = sim.targets
        elif direction == 'reverse':
            t = sim.frames[-1]['time'] - t
            pose = sim.sample(t)
        else:
            pose = sim.sample(t)
        pairs = []
        for joint in sim.joints:
            m = mapping[joint['name']]
            angle = round(pose[joint['name']] * m['sign'] + float(m.get('offset', 0)))
            if not -125 <= angle <= 125:
                raise ValueError('Mapped servo angle exceeds ±125°: ' + joint['name'])
            pairs.append((m['servo'], angle))
        values = [v for pair in sorted(pairs) for v in pair]
        samples.append([round(timestamp, 8), values])
    metadata = {'type': motion_type, 'loop': motion_type == 'gait', 'hz': hz, 'speed': speed,
                'direction': direction, 'duration': duration, 'samples': len(samples)}
    return samples, mapping, metadata


def firmware_skill(sim, body):
    """Build an OpenCat K packet payload. Firmware stores it for later T replays."""
    requested_type = body.get('motion_type', 'behavior')
    duration = 0 if requested_type == 'pose' or not sim.frames else sim.frames[-1]['time'] / float(body.get('speed', .5))
    # OpenCat stores the behavior frame count in a signed byte. Twenty Hz is
    # smooth enough for firmware interpolation while keeping typical uploads small.
    skill_hz = min(float(body.get('hz', sim.motion_hz)), 20., 119 / duration if duration > 0 else 20.)
    # duration × (119 / duration) can round to just above 119, which would sample one frame too many (121).
    while duration > 0 and math.ceil(duration * skill_hz) > 119:
        skill_hz = math.nextafter(skill_hz, 0)
    samples, mapping, metadata = build_motion(sim, {**body, 'hz': max(1., skill_hz)}, hardware=True)
    if len(samples) > 120:
        raise ValueError('Motion is too long for one firmware skill; shorten it or increase speed')
    full_frames = []
    for _, pairs in samples:
        angles = [0] * 16
        for index, angle in zip(pairs[::2], pairs[1::2]):
            angles[index] = angle
        full_frames.append(angles)
    if requested_type == 'pose':
        data = [1, 0, 0, 1] + full_frames[0]
    else:
        data = [-len(full_frames), 0, 0, 1, 0, len(full_frames) - 1, -1 if requested_type == 'gait' else 0]
        previous = full_frames[0]
        for frame in full_frames:
            delta = max(abs(a - b) for a, b in zip(frame, previous))
            step = max(1, min(125, math.ceil(delta * .02 * metadata['hz'])))
            data.extend(frame + [step, 0, 0, 0])
            previous = frame
    if not all(isinstance(value, int) and -128 <= value <= 127 for value in data):
        raise ValueError('Firmware skill contains a value outside signed-byte range')
    raw = bytes(value & 255 for value in data)
    metadata = {**metadata, 'firmware_hz': metadata['hz'], 'bytes': len(raw) + 2,
                'signature': hashlib.sha256(raw).hexdigest()[:16], 'recall_command': 'T'}
    return data, mapping, metadata


def python_motion(sim, body, hardware=True):
    samples, mapping, metadata = build_motion(sim, body, hardware)
    return python_script(samples, mapping, metadata), mapping, metadata


SEQUENCE_GAP_S = .3   # pause between functions in a sequence
POSE_HOLD_S = 1.      # how long a pose function is held inside a sequence


def sequence_samples(parts):
    """Join several functions' samples [(name, samples, metadata)] end to end into one timed list."""
    samples, steps, offset = [], [], 0.
    for name, part, metadata in parts:
        start = offset
        for timestamp, values in part:
            samples.append([round(offset + timestamp, 8), values])
        length = part[-1][0] if part else 0.
        if metadata['type'] == 'pose':
            length = POSE_HOLD_S
            samples.append([round(offset + length, 8), part[-1][1]])
        steps.append({'name': name, 'type': metadata['type'], 'start': round(start, 3), 'duration': round(length, 3)})
        offset += length + SEQUENCE_GAP_S
    metadata = {'type': 'sequence', 'loop': False, 'hz': max(m['hz'] for _, _, m in parts),
                'duration': round(samples[-1][0], 3), 'samples': len(samples), 'sequence': steps}
    return samples, metadata


def python_script(samples, mapping, metadata):
    return '''"""Bittle Studio motion. Hardware: --execute. Without flags: dry run.
In Studio's code panel PetoiRobot is replaced by the selected simulation target.
Uses I (simultaneous), not M (sequential). Requested Hz is not guaranteed by serial hardware.
Pose, behavior and sequence exports run once. A gait repeats until Ctrl+C.
"""
import argparse
import time

STUDIO_MAPPING = ''' + pprint.pformat(mapping, width=100, sort_dicts=False) + '''
MOTION = ''' + repr(metadata) + '''
SAMPLES = ''' + pprint.pformat(samples, width=110, compact=True) + '''

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    if not args.execute:
        print('Dry run:', MOTION)
        print('First pose:', SAMPLES[0])
        print('Last pose: ', SAMPLES[-1])
        print('Verify mapping and clearance before --execute.')
        return
    from PetoiRobot import autoConnect, rotateJoints, closePort
    autoConnect()
    try:
        input('Support the robot and press Enter to send the first pose.')
        rotateJoints('I', SAMPLES[0][1], 2)
        if MOTION['type'] == 'pose':
            print('Pose sent.')
            return
        input('Check the first pose, then Enter to play (Ctrl+C cancels).')
        rounds = 0
        include_first = False
        while True:
            start = time.monotonic()
            sequence = SAMPLES if include_first else SAMPLES[1:]
            round_sent = 0
            for timestamp, joints in sequence:
                time.sleep(max(0, start + timestamp - time.monotonic()))
                rotateJoints('I', joints, 0)
                round_sent += 1
            elapsed = time.monotonic() - start
            rounds += 1
            print('Round %d completed in %.3fs; effective rate %.1f Hz (requested %.1f)' %
                  (rounds, elapsed, round_sent / max(elapsed, 1e-9), MOTION['hz']))
            if not MOTION['loop']:
                break
            include_first = True
    except KeyboardInterrupt:
        print('Stopped sending targets.')
    finally:
        closePort()

if __name__ == '__main__':
    main()
'''
