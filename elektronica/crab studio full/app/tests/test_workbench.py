"""Integration checks for physical motion, file handling and export safeguards."""
import ast
import io
import math
import unittest
import zipfile

import pybullet as p
from fastapi.testclient import TestClient

import server


class WorkbenchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.context = TestClient(server.app)
        cls.client = cls.context.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.context.__exit__(None, None, None)

    def test_complete_experiment(self):
        c = self.client
        model = c.get('/api/model').json()
        self.assertEqual(len(model['joints']), 11)   # 8 legs + crab claw: arm swing (neck), elbow, pincer
        neck = next(j for j in model['joints'] if j['name'] == 'neck-joint')
        self.assertAlmostEqual(neck['lower'], -90, places=2)
        self.assertAlmostEqual(neck['upper'], 90, places=2)
        self.assertEqual(model['mapping']['neck-joint']['servo'], 0)
        self.assertEqual(model['mapping']['claw-elbow-joint']['servo'], 1)
        self.assertEqual(model['mapping']['claw-pincer-joint']['servo'], 2)
        import crab_lab   # the claw arm is only drawn with Claw fitted (Robot control → Move → Claw)
        self.assertEqual(any(v.get('url', '').endswith('crab/neck_yoke.stl') for v in model['visuals']), crab_lab.claw_fitted())
        self.assertTrue(any(v.get('url', '').endswith('crab/crab_shell.stl') for v in model['visuals']))
        self.assertGreater(len(model['visuals']), 8)
        self.assertTrue(any('not physically valid' in w for w in model['warnings']))
        initial = c.get('/api/project').json()
        s = server.sim
        with s.lock:
            s.running = True
            for _ in range(480):
                s.tick()
            s.running = False
            fallen = s.state()
        self.assertLess(fallen['height'], .18)
        self.assertGreater(fallen['height'], -.02)
        self.assertGreater(fallen['contacts'], 0)
        self.assertTrue(all(math.isfinite(v) for v in fallen['angles'].values()))
        c.post('/api/command', json={'action': 'reset'})
        first = model['joints'][0]
        c.post('/api/command', json={'action': 'pose', 'pose': {first['name']: 999}})
        self.assertAlmostEqual(c.get('/api/state').json()['targets'][first['name']], first['upper'])
        zero = {j['name']: 0 for j in model['joints']}
        pose = {**zero, first['name']: 30}
        frames = [{'time': 0, 'pose': zero}, {'time': 2, 'pose': pose, 'easing': 'smooth'}]
        self.assertEqual(c.post('/api/frames', json={'frames': frames}).status_code, 200)
        c.post('/api/command', json={'action': 'seek', 'time': 1})
        self.assertAlmostEqual(c.get('/api/state').json()['targets'][first['name']], 15)
        main_frames = c.get('/api/model').json()['frames']
        actor = c.post('/api/actors', json={'duplicate_main': True}).json()
        actor_zero = {j['name']: 0 for j in actor['joints']}
        actor_pose = {**actor_zero, actor['joints'][0]['name']: 20}
        actor_frames = [{'time': 0, 'pose': actor_zero}, {'time': 1, 'pose': actor_pose}]
        result = c.post('/api/frames', json={'target': actor['id'], 'frames': actor_frames})
        self.assertEqual(result.status_code, 200)
        actor_frames = result.json()['frames']
        self.assertEqual(len(c.get('/api/model').json()['frames']), 2)
        c.post('/api/command', json={'action': 'seek', 'target': actor['id'], 'time': .5})
        actor_state = c.get('/api/state').json()['actors'][actor['id']]
        self.assertAlmostEqual(actor_state['targets'][actor['joints'][0]['name']], 10)
        c.post('/api/command', json={'action': 'play', 'target': actor['id'], 'value': True,
                                     'from_start': True, 'loop': False, 'hz': 100})
        with server.sim.lock:
            for _ in range(30):
                server.sim.tick()
        actor_state = c.get('/api/state').json()['actors'][actor['id']]
        self.assertGreater(actor_state['playhead'], 0)
        self.assertEqual(c.get('/api/model').json()['frames'], main_frames)
        obj = c.post('/api/objects', json={'type': 'box', 'position': [.3, 0, .1], 'size': [.1, .1, .1], 'mass': .1}).json()
        self.assertIn('id', obj)
        project = c.get('/api/project').json()
        self.assertEqual(c.post('/api/project', json=project).status_code, 200)
        restored = c.get('/api/project').json()
        self.assertEqual(project['frames'], restored['frames'])
        self.assertEqual(project['objects'], restored['objects'])
        self.assertEqual(project['targets'], restored['targets'])
        restored_actor = next(a for a in restored['actors'] if a['id'] == actor['id'])
        self.assertEqual(restored_actor['frames'], actor_frames)
        bad_mapping = c.post('/api/export', json={'mapping': model['mapping']})
        self.assertEqual(bad_mapping.status_code, 400)
        mapping = model['mapping']
        for m in mapping.values():
            m['verified'] = True
        result = c.post('/api/export', json={'mapping': mapping, 'speed': .5})
        self.assertEqual(result.status_code, 200)
        ast.parse(result.text)
        self.assertIn('--execute', result.text)
        self.assertIn("rotateJoints('I'", result.text)
        export_file = server.ROOT / 'test-results/exported_motion.py'
        export_file.parent.mkdir(exist_ok=True)
        export_file.write_text(result.text)
        mapping[first['name']]['offset'] = 999
        self.assertEqual(c.post('/api/export', json={'mapping': mapping}).status_code, 400)
        self.assertEqual(c.post('/api/project', json=initial).status_code, 200)

    def test_console_buttons(self):
        c = self.client
        button = {'name': 'Greet', 'color': 'blue', 'repeat': False,
                  'steps': [{'kind': 'command', 'command': 'khi', 'wait_ms': 1800}, {'kind': 'motion', 'motion_id': 'abc', 'wait_ms': 0}]}
        saved = c.put('/api/controls', json={'controls': [button]})
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertTrue(saved.json()['controls'][0]['id'])
        self.assertEqual(c.get('/api/controls').json()['controls'][0]['steps'][0]['command'], 'khi')
        self.assertEqual(c.get('/api/project').json()['controls'][0]['name'], 'Greet')
        for bad in ({**button, 'steps': []}, {**button, 'color': 'pink'}, {**button, 'steps': [{'kind': 'command', 'command': 'ké', 'wait_ms': 0}]},
                    {**button, 'steps': [{'kind': 'command', 'command': 'ksit', 'wait_ms': 70000}]}):
            self.assertEqual(c.put('/api/controls', json={'controls': [bad]}).status_code, 400)
        c.put('/api/controls', json={'controls': []})

    def test_import_and_rejection(self):
        c = self.client
        result = c.post('/api/import', files={'file': ('triangle.obj', b'v 0 0 0\nv .1 0 0\nv 0 .1 0\nf 1 2 3\n')})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['kind'], 'mesh')
        self.assertEqual(c.get(result.json()['url']).status_code, 200)
        data = io.BytesIO()
        with zipfile.ZipFile(data, 'w') as z:
            z.writestr('../escape.urdf', 'invalid')
        self.assertEqual(c.post('/api/import', files={'file': ('unsafe.zip', data.getvalue())}).status_code, 400)
        xacro_zip = io.BytesIO()
        xacro_source = '''<robot name="xacro_bot" xmlns:xacro="http://www.ros.org/wiki/xacro">
          <xacro:macro name="wheel" params="side y">
            <link name="${side}_wheel"><visual><geometry><mesh filename="package://test_pkg/meshes/wheel.obj"/></geometry></visual><collision><geometry><box size=".02 .01 .02"/></geometry></collision><inertial><mass value=".01"/><inertia ixx=".00001" iyy=".00001" izz=".00001" ixy="0" ixz="0" iyz="0"/></inertial></link>
            <joint name="${side}_joint" type="revolute"><parent link="base"/><child link="${side}_wheel"/><origin xyz="0 ${y} 0"/><axis xyz="0 1 0"/><limit lower="-3.14" upper="3.14" effort="1" velocity="10"/></joint>
          </xacro:macro>
          <link name="base"><collision><geometry><box size=".05 .05 .02"/></geometry></collision><inertial><mass value=".1"/><inertia ixx=".0001" iyy=".0001" izz=".0001" ixy="0" ixz="0" iyz="0"/></inertial></link>
          <xacro:wheel side="left" y=".03"/><xacro:wheel side="right" y="-.03"/>
          <gazebo><plugin name="ignored" filename="libignored.so"><path>$(find test_pkg)</path></plugin></gazebo>
        </robot>'''
        with zipfile.ZipFile(xacro_zip, 'w') as z:
            z.writestr('test_pkg/package.xml', '<package><name>test_pkg</name></package>')
            z.writestr('test_pkg/urdf/robot.urdf.xacro', xacro_source)
            z.writestr('test_pkg/meshes/wheel.obj', 'v 0 0 0\nv .01 0 0\nv 0 .01 0\nf 1 2 3\n')
        imported = c.post('/api/import?mode=add', files={'file': ('xacro_bot.zip', xacro_zip.getvalue())})
        self.assertEqual(imported.status_code, 200, imported.text)
        actor = next(a for a in c.get('/api/model').json()['actors'] if a['id'] == imported.json()['id'])
        self.assertEqual([j['name'] for j in actor['joints']], ['left_joint', 'right_joint'])
        self.assertNotIn('xacro:', actor['xml'])
        self.assertTrue(actor['visuals'][0]['url'].endswith('/test_pkg/meshes/wheel.obj'))
        original = c.get('/api/model').json()['xml']
        self.assertEqual(c.post('/api/urdf', json={'xml': '<robot broken'}).status_code, 400)
        missing = original.replace('obj/base_frame.obj', '../../outside.obj')
        self.assertEqual(c.post('/api/urdf', json={'xml': missing}).status_code, 400)
        self.assertEqual(c.get('/api/model').json()['xml'], original)
        self.assertEqual(c.post('/api/command', json={'action': 'run', 'value': True}, headers={'Origin':'https://example.com'}).status_code, 403)


if __name__ == '__main__':
    unittest.main()
