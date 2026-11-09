"""Convert Petoi's Bittle skill table (OpenCatEsp32 src/InstinctBittleESP.h, MIT) into app/petoi_skills.json.

    python app/tools/import_petoi_skills.py path/to/InstinctBittleESP.h [commit]

OpenCat skill layout: [frame count, roll, pitch, angle ratio, ...]. A count of 1 is a posture (16 angles).
A positive count is a gait: that many frames of leg angles for servos 8..15 (or more columns for arm variants).
A negative count is a behaviour: 3 loop bytes (first frame, last frame, repeats), then frames of 16 angles plus
4 timing bytes (speed, delay, trigger axis, trigger angle).
"""
import json
import re
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / 'petoi_skills.json'


def parse(text):
    skills = {}
    for name, body in re.findall(r'const int8_t (\w+)\[\] PROGMEM = \{(.*?)\};', text, re.S):
        data = [int(v) for v in re.findall(r'-?\d+', body)]
        count, roll, pitch, ratio = data[:4]
        entry = {'roll': roll, 'pitch': pitch, 'ratio': ratio or 1}
        if count == 1:
            entry.update(type='posture', servos=list(range(16)), frames=[data[4:20]])
        elif count > 1:
            columns = (len(data) - 4) // count
            if columns * count != len(data) - 4:
                raise ValueError(f'{name}: gait data does not divide into {count} frames')
            entry.update(type='gait', servos=list(range(16 - columns, 16)),
                         frames=[data[4 + i * columns: 4 + (i + 1) * columns] for i in range(count)])
        else:
            frames = -count
            first, last, repeats = data[4:7]
            rows = [data[7 + i * 20: 7 + (i + 1) * 20] for i in range(frames)]
            if any(len(row) != 20 for row in rows):
                raise ValueError(f'{name}: behaviour rows must have 20 values')
            entry.update(type='behavior', servos=list(range(16)), loop=[first, last, repeats],
                         frames=[row[:16] for row in rows], timing=[row[16:18] for row in rows])
        skills[name] = entry
    return skills


def main():
    source = Path(sys.argv[1])
    commit = sys.argv[2] if len(sys.argv) > 2 else ''
    skills = parse(source.read_text(encoding='utf-8'))
    OUT.write_text(json.dumps({
        'source': 'https://github.com/PetoiCamp/OpenCatEsp32/blob/main/src/InstinctBittleESP.h',
        'license': 'MIT License, Copyright (c) 2021 Rongzhong Li. Full text: petoi_skills.LICENSE', 'commit': commit,
        'servo_order': 'OpenCat servo indices: 0 head pan, 8-11 shoulders LF RF RB LB, 12-15 knees LF RF RB LB',
        'skills': skills}, separators=(',', ':')), encoding='utf-8')
    kinds = {}
    for entry in skills.values():
        kinds[entry['type']] = kinds.get(entry['type'], 0) + 1
    print(f'{len(skills)} skills written to {OUT}: {kinds}')


if __name__ == '__main__':
    main()
