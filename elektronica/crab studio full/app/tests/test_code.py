import ast
import json
import time
import unittest

from fastapi.testclient import TestClient

import server
from motion_export import build_motion, firmware_skill, python_motion


class CodeWorkbenchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.context = TestClient(server.app)
        cls.c = cls.context.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.context.__exit__(None, None, None)

    def tearDown(self):
        self.c.post('/api/scripts/stop')

    def wait_finished(self, seconds=8):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            status = self.c.get('/api/scripts').json()
            if status['jobs'] and all(j['status'] != 'running' for j in status['jobs']):
                return status
            time.sleep(.05)
        self.fail('Scripts did not finish: ' + str(status))

    def save(self, files):
        r = self.c.put('/api/scripts', json={'files': files})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()['files']

    def test_01_rates_and_order(self):
        sim = server.sim
        with sim.lock:
            zero = {j['name']: 0 for j in sim.joints}
            name = sim.joints[0]['name']
            frames = [{'time': 2, 'pose': {**zero, name: 30}}, {'time': 0, 'pose': {**zero, name: -20}}, {'time': 1, 'pose': {**zero, name: 5}}]
            sim.set_frames(frames)
            for hz in (10, 50, 100):
                samples, mapping, meta = build_motion(sim, {'hz': hz, 'speed': 1}, False)
                self.assertEqual(len(samples), 2 * hz + 1)
                index = samples[0][1][::2].index(mapping[name]['servo']) * 2 + 1
                self.assertEqual(samples[0][1][index], -20)
                self.assertEqual(samples[-1][1][index], 30)
                self.assertTrue(all(a[0] < b[0] for a, b in zip(samples, samples[1:])))
            reverse, _, _ = build_motion(sim, {'hz': 50, 'speed': 1, 'direction': 'reverse'}, False)
            self.assertEqual(reverse[0][1][index], 30)
            self.assertEqual(reverse[-1][1][index], -20)
        response = self.c.post('/api/command', json={'action':'settings', 'physics_hz':480, 'motion_hz':100})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['physics_hz'], 480)
        self.assertEqual(self.c.post('/api/command', json={'action':'settings','physics_hz':0}).status_code, 400)

    def test_02_multi_target_and_modules(self):
        actor = self.c.post('/api/actors', json={'duplicate_main': True}).json()
        obj = self.c.post('/api/objects', json={'type':'box','position':[.5,0,.1]}).json()
        name = server.sim.joints[0]['name']
        self.save([
            {'id':'helper','name':'helper.py','source':'ANGLE = 22\n','enabled':False},
            {'id':'one','name':'one.py','target':'main','source':f'from bittle_sim import ctx\nfrom helper import ANGLE\nctx.set_joints({{{name!r}: ANGLE}})\nctx.sleep(.15)\nprint("main done")'},
            {'id':'two','name':'two.py','target':actor['id'],'source':f'from bittle_sim import ctx\nctx.set_joints({{{name!r}: -13}})\nctx.sleep(.15)\nprint("other done")'},
            {'id':'box','name':'box.py','target':obj['id'],'source':'from bittle_sim import ctx\nctx.set_position([.6,.1,.2])\nctx.sleep(.15)\nprint("box done")'}])
        result = self.c.post('/api/scripts/run', json={})
        self.assertEqual(result.status_code, 200, result.text)
        status = self.wait_finished()
        self.assertTrue(all(j['status']=='completed' for j in status['jobs']), status)
        state = self.c.get('/api/state').json()
        self.assertAlmostEqual(state['targets'][name], 22)
        self.assertAlmostEqual(state['actors'][actor['id']]['targets'][name], -13)
        self.assertAlmostEqual(state['objects'][obj['id']][0][0], .6)
        project = self.c.get('/api/project').json()
        self.assertEqual(len(project['scripts']), 4)
        self.assertEqual(len(project['actors']), 1)
        restored = self.c.post('/api/project', json=project)
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual(restored.json()['scripts'], project['scripts'])
        self.assertEqual(restored.json()['actors'][0]['id'], actor['id'])
        # Same files must run again after Bullet body IDs have been recreated.
        self.assertEqual(self.c.post('/api/scripts/run', json={}).status_code, 200)
        self.assertTrue(all(j['status']=='completed' for j in self.wait_finished()['jobs']))

    def test_03_exact_export_execution(self):
        sim = server.sim
        name = sim.joints[0]['name']
        zero = {j['name']:0 for j in sim.joints}
        self.c.post('/api/frames', json={'frames':[{'time':0,'pose':{**zero,name:-10}}, {'time':.3,'pose':{**zero,name:20}}]})
        mapping = json.loads(json.dumps(sim.mapping))
        mapping[name]['sign'] = -1
        mapping[name]['offset'] = 7
        result = self.c.post('/api/motion-code', json={'hz':50,'speed':1,'mapping':mapping})
        self.assertEqual(result.status_code, 200, result.text)
        source = result.json()['source']
        ast.parse(source)
        self.assertIn("rotateJoints('I'", source)
        self.save([{'id':'export','name':'motion.py','target':'main','source':source}])
        self.assertEqual(self.c.post('/api/scripts/run', json={}).status_code, 200)
        status = self.wait_finished()
        self.assertEqual(status['jobs'][0]['status'], 'completed', status)
        self.assertEqual(status['jobs'][0]['commands'], 16)
        self.assertAlmostEqual(self.c.get('/api/state').json()['targets'][name], 20)
        self.assertTrue(any('simulation target' in log['text'] for log in status['logs']))

    def test_04_stop_error_and_conflict(self):
        self.save([{'id':'loop','name':'loop.py','source':'while True:\n    pass\n'}])
        self.assertEqual(self.c.post('/api/scripts/run', json={}).status_code, 200)
        self.assertEqual(self.c.post('/api/urdf', json={'xml':server.sim.xml}).status_code, 400)
        started=time.monotonic()
        self.c.post('/api/scripts/stop')
        self.assertLess(time.monotonic()-started, 3)
        self.assertFalse(self.c.get('/api/scripts').json()['active'])
        self.save([{'id':'bad','name':'bad.py','source':'raise RuntimeError("intentional-test-error")'}])
        self.c.post('/api/scripts/run', json={})
        status=self.wait_finished()
        self.assertEqual(status['jobs'][0]['status'],'failed')
        self.assertTrue(any('intentional-test-error' in l['text'] for l in status['logs']))
        self.save([{'id':'timeout','name':'timeout.py','source':'while True:\n    pass'}])
        self.c.post('/api/scripts/run', json={'timeout':1})
        self.assertEqual(self.wait_finished()['jobs'][0]['status'],'timed out')
        self.assertEqual(self.c.put('/api/scripts',json={'files':[{'name':'../escape.py','source':''}]}).status_code,400)

    def test_05_pose_behavior_and_gait_exports(self):
        sim = server.sim
        with sim.lock:
            mapping = json.loads(json.dumps(sim.mapping))
            for item in mapping.values():
                item['verified'] = True
            first_name = sim.joints[0]['name']
            zero = {joint['name']: 0 for joint in sim.joints}
            sim.set_frames([
                {'time': 0, 'pose': zero, 'easing': 'linear'},
                {'time': .2, 'pose': {**zero, first_name: 17}, 'easing': 'linear'}
            ])
            sim.targets[first_name] = 17
            pose, _, pose_meta = build_motion(sim, {'mapping': mapping, 'motion_type': 'pose'}, True)
            self.assertEqual(len(pose), 1)
            self.assertEqual(pose_meta['type'], 'pose')
            self.assertFalse(pose_meta['loop'])
            behavior_source, _, behavior_meta = python_motion(
                sim, {'mapping': mapping, 'motion_type': 'behavior', 'hz': 10, 'speed': 1})
            gait_source, _, gait_meta = python_motion(
                sim, {'mapping': mapping, 'motion_type': 'gait', 'hz': 10, 'speed': 1})
        ast.parse(behavior_source)
        ast.parse(gait_source)
        self.assertFalse(behavior_meta['loop'])
        self.assertTrue(gait_meta['loop'])
        self.assertIn("while True:", gait_source)
        self.assertIn("if not MOTION['loop']", behavior_source)
        samples = self.c.post('/api/motion-samples', json={
            'mapping': mapping, 'motion_type': 'pose', 'hz': 50
        })
        self.assertEqual(samples.status_code, 200, samples.text)
        self.assertEqual(samples.json()['metadata']['samples'], 1)

    def test_06_saved_motion_library_round_trip(self):
        sim = server.sim
        name = sim.joints[0]['name']
        zero = {joint['name']: 0 for joint in sim.joints}
        frames = [
            {'time': 0, 'pose': zero, 'easing': 'linear'},
            {'time': .2, 'pose': {**zero, name: 12}, 'easing': 'smooth'},
        ]
        self.assertEqual(self.c.post('/api/frames', json={'frames': frames}).status_code, 200)
        saved = self.c.post('/api/motions', json={'name': 'Test wave', 'motion_type': 'behavior', 'hz': 25, 'speed': 1, 'direction': 'forward'})
        self.assertEqual(saved.status_code, 200, saved.text)
        item = saved.json()
        self.assertEqual(item['frames'], frames)
        self.assertTrue(any(m['id'] == item['id'] for m in self.c.get('/api/motions').json()['motions']))
        mapping = json.loads(json.dumps(sim.mapping))
        for value in mapping.values():
            value['verified'] = True
        samples = self.c.post(f"/api/motions/{item['id']}/samples", json={'mapping': mapping})
        self.assertEqual(samples.status_code, 200, samples.text)
        self.assertEqual(samples.json()['metadata']['hz'], 25)
        project = self.c.get('/api/project').json()
        self.assertTrue(any(m['id'] == item['id'] for m in project['motions']))
        # A Bittle Link pack carries buttons plus each saved motion compiled to the same firmware skill.
        button = {'name': 'Wave twice', 'steps': [{'kind': 'motion', 'motion_id': item['id'], 'wait_ms': 500}, {'kind': 'command', 'command': 'kbalance', 'wait_ms': 0}]}
        self.assertEqual(self.c.put('/api/controls', json={'controls': [button]}).status_code, 200)
        pack = self.c.post('/api/controls/pack', json={'mapping': mapping})
        self.assertEqual(pack.status_code, 200, pack.text)
        pack = pack.json()
        self.assertEqual((pack['format'], pack['version']), ('bittle-link-pack', 1))
        self.assertEqual(pack['controls'][0]['steps'][0]['motion_id'], item['id'])
        compiled = next(s for s in pack['skills'] if s['id'] == item['id'])
        direct = self.c.post(f"/api/motions/{item['id']}/skill", json={'mapping': mapping}).json()
        self.assertEqual((compiled['skill'], compiled['signature']), (direct['skill'], direct['metadata']['signature']))
        self.c.put('/api/controls', json={'controls': []})
        # A saved function exports straight to Python with its own settings; the timeline is left untouched.
        self.c.post('/api/frames', json={'frames': []})
        exported = self.c.post(f"/api/motions/{item['id']}/export", json={'mapping': mapping})
        self.assertEqual(exported.status_code, 200, exported.text)
        self.assertIn('PetoiRobot', exported.text)
        self.assertIn("'type': 'behavior'", exported.text)
        self.assertEqual(self.c.get('/api/model').json()['frames'], [])
        # Several functions export as one script that plays them in the chosen order.
        second = self.c.post('/api/motions', json={'name': 'Hold', 'motion_type': 'pose', 'hz': 25, 'speed': 1, 'direction': 'forward'}).json()
        combined = self.c.post('/api/motion-sequence/export', json={'ids': [second['id'], item['id']], 'mapping': mapping})
        self.assertEqual(combined.status_code, 200, combined.text)
        self.assertIn("'type': 'sequence'", combined.text)
        self.assertLess(combined.text.index("'name': 'Hold'"), combined.text.index("'name': 'Test wave'"))
        self.assertEqual(self.c.post('/api/motion-sequence/export', json={'ids': [], 'mapping': mapping}).status_code, 400)
        self.assertEqual(self.c.post('/api/motion-sequence/export', json={'ids': ['missing'], 'mapping': mapping}).status_code, 400)
        self.c.delete(f"/api/motions/{second['id']}")
        unverified = json.loads(json.dumps(mapping)); unverified[name]['verified'] = False
        self.assertEqual(self.c.post(f"/api/motions/{item['id']}/export", json={'mapping': unverified}).status_code, 400)
        self.assertEqual(self.c.delete(f"/api/motions/{item['id']}").status_code, 200)
        self.assertFalse(any(m['id'] == item['id'] for m in self.c.get('/api/motions').json()['motions']))

    def test_07_physics_persists_and_firmware_skill_is_compact(self):
        sim = server.sim
        name = sim.joints[0]['name']
        zero = {joint['name']: 0 for joint in sim.joints}
        self.c.post('/api/frames', json={'frames': [
            {'time': 0, 'pose': zero, 'easing': 'linear'},
            {'time': 10, 'pose': {**zero, name: 20}, 'easing': 'smooth'},
        ]})
        mapping = json.loads(json.dumps(sim.mapping))
        for value in mapping.values():
            value['verified'] = True
        skill, _, metadata = firmware_skill(sim, {'mapping': mapping, 'motion_type': 'gait', 'hz': 100, 'speed': 1})
        self.assertGreater(len(skill), 20)
        self.assertGreaterEqual(skill[0], -120)
        self.assertLess(skill[0], 0)
        self.assertEqual(skill[6], -1)
        self.assertEqual(metadata['recall_command'], 'T')
        response = self.c.post('/api/motion-skill', json={'mapping': mapping, 'motion_type': 'behavior', 'hz': 100, 'speed': 1})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertLessEqual(abs(response.json()['skill'][0]), 120)
        # Lengths whose 119 / duration rounds up (7.2 s, 14.4 s) still fit in the firmware's 120 frames.
        for end in (7.2, 14.4):
            self.c.post('/api/frames', json={'frames': [
                {'time': 0, 'pose': zero, 'easing': 'smooth'}, {'time': end, 'pose': {**zero, name: 20}, 'easing': 'smooth'}]})
            skill, _, _ = firmware_skill(sim, {'mapping': mapping, 'motion_type': 'behavior', 'hz': 20, 'speed': 1})
            self.assertEqual(skill[0], -120, end)

        self.save([{'id': 'short', 'name': 'short.py', 'source': 'from bittle_sim import ctx\nctx.sleep(.05)'}])
        self.c.post('/api/command', json={'action': 'run', 'value': True})
        self.assertEqual(self.c.post('/api/scripts/run', json={'physics': False}).status_code, 200)
        self.assertTrue(self.c.get('/api/state').json()['running'])
        self.wait_finished()

    def test_08_main_robot_transform_round_trip(self):
        target = {'position': [.12, -.08, .31], 'rotation': [.1, -.2, .3]}
        result = self.c.post('/api/robot-transform', json=target)
        self.assertEqual(result.status_code, 200, result.text)
        model = self.c.get('/api/model').json()
        self.assertEqual(model['robot_position'], target['position'])
        for actual, expected in zip(model['robot_rotation'], target['rotation']):
            self.assertAlmostEqual(actual, expected)
        project = self.c.get('/api/project').json()
        self.assertEqual(project['robot_position'], target['position'])
        self.c.post('/api/command', json={'action': 'reset'})
        position = server.p.getBasePositionAndOrientation(server.sim.robot, physicsClientId=server.sim.client)[0]
        self.assertAlmostEqual(position[0], target['position'][0], places=4)
        self.c.post('/api/robot-transform', json={'position': [0, 0, .2], 'rotation': [0, 0, 0]})

    def test_09_imu_sensor_and_python_api(self):
        model = self.c.get('/api/model').json()
        sensor = self.c.post('/api/sensors', json={
            'target': 'main', 'type': 'imu', 'name': 'Test IMU', 'link': model['links'][0]
        })
        self.assertEqual(sensor.status_code, 200, sensor.text)
        sensor = sensor.json()
        self.c.post('/api/robot-transform', json={'position': [0, 0, .2], 'rotation': [.1, -.2, .3]})
        reading = self.c.get('/api/state').json()['sensors'][sensor['id']]
        self.assertAlmostEqual(reading['orientation'][0], .1, places=4)
        self.assertAlmostEqual(reading['orientation'][1], -.2, places=4)
        self.assertEqual(len(reading['angular_velocity']), 3)
        self.assertEqual(len(reading['linear_acceleration']), 3)
        reapplied = self.c.post('/api/urdf', json={'xml': model['xml']})
        self.assertEqual(reapplied.status_code, 200, reapplied.text)
        self.assertTrue(any(item['id'] == sensor['id'] for item in reapplied.json()['sensors']))
        duplicate = self.c.post('/api/sensors', json={
            'target': 'main', 'type': 'imu', 'name': 'Test IMU', 'link': model['links'][0]
        })
        self.assertEqual(duplicate.status_code, 400)
        self.save([{'id': 'imu', 'name': 'imu.py', 'target': 'main',
                    'source': 'from bittle_sim import ctx\nimu=ctx.get_imu("Test IMU")\nassert len(imu["orientation"]) == 3\nassert "Test IMU" in [v["name"] for v in ctx.get_sensors().values()]\nprint("imu ok")'}])
        self.assertEqual(self.c.post('/api/scripts/run', json={}).status_code, 200)
        status = self.wait_finished()
        self.assertEqual(status['jobs'][0]['status'], 'completed', status)
        self.assertTrue(any('imu ok' in item['text'] for item in status['logs']))
        project = self.c.get('/api/project').json()
        self.assertTrue(any(item['id'] == sensor['id'] for item in project['sensors']))
        self.assertEqual(self.c.post('/api/project', json=project).status_code, 200)
        self.assertEqual(self.c.delete('/api/sensors/main/' + sensor['id']).status_code, 200)
        self.c.post('/api/robot-transform', json={'position': [0, 0, .2], 'rotation': [0, 0, 0]})


if __name__=='__main__':
    unittest.main()
