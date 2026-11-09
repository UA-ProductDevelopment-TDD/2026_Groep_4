# Developer guide

How Bittle Studio is organised, and how to change it safely. For using the app, see the [README](../README.md).

## Big picture

```
browser (app/web)  ──HTTP──▶  app/server.py (FastAPI)  ──▶  app/engine.py (PyBullet world)
      │                              │
      │                              └──▶ motion_export.py, skills.py, scripting.py, packs.py
      │
      └── app/web/bittle-link  ──Web Bluetooth / Web Serial──▶  Petoi Bittle (OpenCat firmware)
```

- The **Studio** is a local web app. `app/launcher.py` starts `server.py` on http://127.0.0.1:8765 and opens the browser. The server owns the simulation and the project; the browser draws it with three.js and sends edits back.
- **Bittle Link** (`app/web/bittle-link/`) talks to the real robot straight from the browser. It has no dependency on the Studio, so it also runs on its own: through `app/bittle_link.py` (port 8770) or as static files on GitHub Pages.
- In the Studio every robot control drives the simulator, and also the robot when one is connected (`app/web/hardware.js`). With no robot, the same commands go through a test-mode link so the serial monitor can show them (marked "simulator only").

## Repository layout

| Folder | Contents | In git? |
|---|---|---|
| `launchers/` | Double-click launchers per system: `windows/` (`.cmd`), `mac/` (`.command`), `linux/` (`.sh`). They stay thin and call `app/install.py`, `app/launcher.py` or `app/bittle_link.py`. | yes |
| `app/` | All source code: the Python server and simulation, the web front end, tests and dependency lists. | yes |
| `app/web/` | Studio front end. Plain ES modules and CSS, no build step. | yes |
| `app/web/bittle-link/` | **Bittle Link**: robot connection, console, serial monitor and its standalone page. | yes |
| `app/tests/` | Automated tests (`unittest`) and their fixtures. | yes |
| `app/tools/` | One-off maintenance scripts (see [Petoi skill data](#petoi-skill-data)). | yes |
| `robot-models/` | Bundled robot models (URDF + meshes). Served as `/assets/...`. | yes |
| `saved-motions/` | Bittle Link packs (`*.json`). Served as `/saved-motions/...` by both servers. | yes |
| `dev-tools/` | For developers: `run-tests.cmd` / `run-tests.sh` and `build_pages.py` (the GitHub Pages site). | yes |
| `docs/` | User and developer documentation. | yes |
| `.github/workflows/` | `pages.yml`: builds and deploys the GitHub Pages site on every push to `master`. | yes |
| `user-data/` | Runtime data: imported meshes and robots, `studio-session.json` autosave, Python run folders. Served as `/data/...`. Created automatically, and an older `data/` folder is renamed to it on first start. | no |
| `.venv/`, `app/node_modules/` | Installed Python packages and the three.js library, created by the Setup launchers (`app/install.py`). | no |

The URL prefixes `/assets` and `/data` are stored inside saved projects, so they stay fixed even if the folders move on disk. `app/engine.py` defines the mapping in `URL_ROOTS`, and `url_path()` converts such a URL back to a disk path.

## Python modules (`app/`)

| File | Role |
|---|---|
| `server.py` | The FastAPI app: import pipeline, project serialisation, saved functions, console buttons, pack export, simulator skills, Python export, scripts, voice session. Started by `launcher.py`. |
| `engine.py` | Bullet world: URDF/Xacro loading and diagnostics, collision objects, motor control, interpolation, skill playback and state. Defines the folder constants `ROOT`, `PROJECT`, `ASSETS`, `DATA` and `SAVED_MOTIONS`. |
| `skills.py` | Turns console commands (`ksit`, `kwkF`, `m 0 30`, `i 8 20`, `d`) into simulator plans, from `petoi_skills.json` and the Servo setup mapping. Commands without a joint effect return `None`. |
| `motion_export.py` | Timeline sampling, `PetoiRobot` Python script export and firmware skill (`K`) compilation. |
| `scripting.py`, `script_worker.py` | The Python code panel runner (one child process per target). |
| `packs.py` | Bittle Link pack validation, listing and saving. **Standard library only**, so `bittle_link.py` can use it too. |
| `voice_tools.py` | Tool definitions for the optional voice companion. |
| `launcher.py` | Starts `server.py` (or reuses a running one) and opens the browser. |
| `bittle_link.py` | Stdlib-only server for the standalone console (port 8770) and the `saved-motions/` folder. |
| `install.py` | Stdlib-only installer behind every Setup launcher (run again after a new version): creates `.venv`, installs `requirements.txt`, and downloads three.js from the npm registry, verified against `package-lock.json`. Node.js is not needed. |
| `requirements.txt` | Pinned Python packages. |
| `package.json`, `package-lock.json` | Pin the three.js version. `install.py` reads only the lock file; developers with Node.js can still run `npm ci --prefix app`. |

## Front end (`app/web/`)

| File | Role |
|---|---|
| `index.html` | The Studio page: top bar, Stage, viewport, timeline, inspector and dialogs. |
| `app.js` | Studio core: three.js viewport, scene editing, timeline, inspector, panel layout and the server API helper. |
| `hardware.js` | Studio glue for Bittle Link: the unified `control` object (simulator + robot), the function library, sequence exports, Servo setup feedback and the serial monitor window. |
| `code-panel.js` | The Python code panel (loaded on demand). |
| `voice.js` | The optional voice companion (WebRTC session through `/api/voice/session`). |
| `style.css`, `studio.css`, `workspace.css`, `placement.css`, `code.css`, `hardware.css` | Styles: base theme, compact workspace, window/panel layout, robot placement, code panel and robot control. |

## Bittle Link module (`app/web/bittle-link/`)

It imports nothing from the rest of the Studio. The Studio's robot control (`hardware.js`) is built on it, and `index.html` with `app.js` host it standalone.

| File | Role |
|---|---|
| `link.js` | `createLink()` returns the transport and protocol object (no DOM code). `connect('ble' \| 'serial' \| 'test')`, `sendCommand`, `sendPose` (binary `I`), `uploadSkill` (binary `K`), `sendSkill`, `runSkill`, `runSequence`, `press` (with the `fp`/`fP`/`#on` macros), `stop`, `setDeveloperMode`, `cancelStream`, and the `playbackMode` property (`'stream'` or `'upload'`). Events: `log`, `state`, `tx`, `rx`, `joints`. |
| `console.js` | `initConsole(element, {link, store, library, tools, ...})` draws the controller (Move, Postures, Skills, My skills, Buttons, Robot settings) into any element. The host decides where buttons are stored (`store`) and how Studio functions become firmware skills (`library`). |
| `serial-monitor.js` | `initSerialMonitor(element, {link, offlineSend})`: every command sent, with its source and a plain-language meaning, plus the robot's replies. `attachPreview(link)` adds a simulator-only link whose rows are marked "not sent". |
| `joint-layout.js` | Joint sliders laid out like Petoi's Skill Composer, shared by the console (servo degrees) and the Studio's pose inspector (simulation degrees). |
| `pack.js` | The pack format, browser-side validation (the same rules as `packs.py`), local storage for the standalone page and the `saved-motions/` folder helpers (relative URLs, so it also works below a GitHub Pages sub-path). |
| `catalog.js` | The Petoi skill catalog (codes, Dutch labels, which ones loop). Used for suggestions in the button editor and names in the serial monitor. |
| `app.js`, `index.html`, `base.css` | The standalone page. |
| `console.css`, `serial-monitor.css` | Styles shared by both hosts. They use the host's `--line`, `--accent`, `--muted` and `--text` tokens. |

A pack is `{format: 'bittle-link-pack', version: 1, name, controls: [...], skills: [{id, name, type, signature, hz, skill}]}`. Buttons refer to skills by id through `{kind: 'motion', motion_id}` steps, exactly as they do inside the Studio.

### Talking to the robot

- Text commands (`ksit`, `m 0 30`, `d`, `j`...) end with a newline. Binary `I` (joint angles) and `K` (a whole skill) start with their letter and end with `~`. Over Bluetooth every write is split into 20-byte packets, so `I` with Bittle's 9 joints fits exactly one packet.
- Saved functions are **streamed** by default: each frame is one `I` packet on the function's own timing (`hz`). **Upload** mode sends one `K` packet instead, replayed later with `T`.
- The firmware prints servo feedback to USB only, so reading positions sends `f` and then `j`, whose reply reaches every port.
- **Test mode** runs the whole protocol without a robot: writes are logged, `j` is answered with the last angles sent, and `z` as the firmware answers it.
- **Upload** limits: a firmware skill holds at most 120 interpolated frames at 20 Hz, so long timelines are resampled to fit. The robot keeps one uploaded skill, in its last-skill slot: the next upload replaces it, and an unchanged motion is replayed with `T` alone.
- **Motors off** is `d` (rest pose, then every servo off). **Motors on** has no command of its own: `:` restores full servo stiffness, then the last read positions are sent as `I`, or `kbalance` when nothing was read.
- **Random behaviours** has only `z`, which toggles them and answers `Z` (now on) or `z` (now off): **On** and **Off** send `z`, and again when the answer is the other way. A firmware built without `RANDOM_MIND` does not answer.
- **Developer mode** sends `gb` (balance/gyro assistance off; voice actions blocked) and `gB` when it ends; **Gyro on / off** are the same two commands.
- There is no speed command: OpenCat has `T_ACCELERATE` (`.`) and `T_DECELERATE` (`,`) commented out, and a skill's speed is in its own frames.
- The head's servo 0 turns left as its angle grows (`m 0 30` looks left); its sliders are mirrored so that dragging right turns it right. The simulated skills go through Servo setup, so a skill that looks mirrored in the simulator means that joint is set up wrong for the robot too.
- Web Bluetooth and Web Serial need Chrome or Edge and a secure context (`http://127.0.0.1` counts). The device picker always needs a click: nothing connects or moves on startup.

## HTTP API (`server.py`)

Angles are degrees at the API boundary; transforms use metres and xyzw quaternions. Request and response bodies are JSON.

| Area | Endpoints |
|---|---|
| Simulation | `GET /api/model`, `GET /api/state`, `POST /api/command` (e.g. `{"action":"pose","pose":{"left-front-shoulder-joint":20}}`; start dynamics with `{"action":"run","value":true}`), `POST /api/skill` (play a console command or saved function in the simulator; `T` replays the last one), `POST /api/robot-transform` |
| Scene | `POST /api/urdf`, `POST /api/import` (file upload), `POST /api/objects`, `DELETE /api/objects/{id}`, `POST /api/sensors`, `DELETE /api/sensors/{target}/{id}`, `POST /api/actors`, `POST /api/actors/{id}`, `POST /api/actors/{id}/ground`, `DELETE /api/actors/{id}` |
| Project | `GET /api/project`, `POST /api/project`, `POST /api/frames` (timeline), `POST /api/mapping` (Servo setup) |
| Saved functions | `GET /api/motions`, `POST /api/motions`, `DELETE /api/motions/{id}`, `POST /api/motions/{id}/load`, `/skill` (firmware skill with `hz`), `/samples`, `/export` (Python script) |
| Timeline exports | `POST /api/motion-code`, `POST /api/motion-samples`, `POST /api/motion-skill`, `POST /api/export`, `POST /api/motion-sequence/export` |
| Console buttons | `GET /api/controls`, `PUT /api/controls`, `POST /api/controls/pack` with `{mapping}` (buttons plus every saved function as a pack) |
| Packs (both servers) | `GET /saved-motions/index.json`, `GET /saved-motions/<file>.json`, `POST /saved-motions/save` with `{name, pack}` (local pages only) |
| Python scripts | `GET /api/scripts`, `PUT /api/scripts`, `POST /api/scripts/run`, `POST /api/scripts/stop` |
| Voice | `GET /api/voice/config`, `POST /api/voice/key`, `DELETE /api/voice/key`, `POST /api/voice/session` |

Stop timeline playback before sending external controller targets.

## Petoi skill data

`app/petoi_skills.json` holds Petoi's built-in skills, so the simulator can play them. It is generated from OpenCatEsp32's `src/InstinctBittleESP.h` (MIT, see `app/petoi_skills.LICENSE`) with:

```bash
python app/tools/import_petoi_skills.py path/to/InstinctBittleESP.h [commit]
```

## Importing robots and meshes

- Environment meshes (OBJ, STL, GLB) are flattened to OBJ; their textures and materials give way to the inspector's colour. Static meshes collide as triangle meshes, dynamic ones as Bullet's convex hull, so cavities do not collide exactly; boxes and spheres are exact.
- A robot is a ZIP holding one URDF or Xacro file and its meshes. Xacro is expanded on import; Gazebo, transmission and `ros2_control` blocks are ignored, as Bullet runs no plugins. `package://name/...` paths resolve through the bundle's `package.xml`. Primitive-only URDF or Xacro files can be uploaded alone.
- The viewport draws mesh, box, sphere and cylinder visuals (OBJ, STL, DAE, GLB, GLTF); Bullet's own URDF mesh support is narrower, so OBJ or STL is safest. An unsupported file is an error, never a substitute model. Only revolute joints get pose controls; other joints are listed in diagnostics.
- Applying an edited URDF resets that robot's timeline; its sensors are kept where their links still exist.
- Imported files stay in `user-data/`, and a saved project names them rather than holding them: keep `user-data/` and `robot-models/` with your projects.

## Python runner

- Each entry point runs in a child process on its target (the main Bittle, another robot or an object); at most eight run together, one per target. `time.sleep`, `time.monotonic` and `time.perf_counter` follow the simulation clock, so a slow simulation does not make a script skip ahead. Runs are limited to 1 to 3600 wall-clock seconds. Changing the world or a timeline while scripts run is refused until Stop.
- Saving a Python file checkpoints the whole project to `user-data/studio-session.json`, restored at the next launch; no code runs on startup.
- This is trusted local Python, **not a sandbox**: code can use the file system, the network and any installed package. Stop kills the runner, not processes it spawned.
- **Timeline → Python** puts the exported source in the editor; export has its own Hz, speed and order, and the generated script reports the throughput it reached. Angles are whole degrees.

### Controller API

```python
from bittle_sim import ctx
import math

state = ctx.get_state()
print(state['joint_names'])
rate = ctx.rate(50)
for frame in range(150):
    angle = 15 * math.sin(2 * math.pi * frame / 150)
    ctx.set_joints({'left-front-shoulder-joint': angle})
    rate.sleep()
```

`ctx.get_state()` returns position, rotation, time, joint names, measured and target joint angles, and the sensors. `ctx.get_imu()` returns the first IMU, `ctx.get_sensor(name)` one by name or ID, `ctx.get_sensors()` all: roll, pitch and yaw in radians, angular velocity in rad/s, body-frame acceleration in m/s², without bias or noise (a resting accelerometer reports support against gravity). `ctx.set_joints()` takes named angles in degrees. `ctx.set_position([x,y,z], [roll,pitch,yaw])` teleports the target (metres, radians). `ctx.apply_force([fx,fy,fz])` pushes the base for one physics step (a dynamic body, physics on). `ctx.sleep(seconds)`, `ctx.time()` and `ctx.rate(hz).sleep()` keep simulation time.

### Petoi compatibility

In the code panel, `import PetoiRobot` gets a simulation adapter with `autoConnect`, `openPort`, `closePort`, `rotateJoints`, `absValList`, `getAngle` and `getAngleList`; it makes no serial connection. Exported files run with `--execute`, their prompts answered in the console, and their `STUDIO_MAPPING` turns servo angles back into URDF angles (older exports use the target's saved mapping). Exports use `I`, which moves joints together; `M`, which moves them one after another, is accepted with a diagnostic but its timing is not emulated: regenerate such files. See [OpenCat's command definitions](https://github.com/PetoiCamp/OpenCat-Quadruped-Robot/blob/main/src/OpenCat.h).

## Physics model and limits

- Physics Hz 60–1000 (default 240), 80 solver iterations; Motion Hz 1–240 (default 50) sets timeline targets and the export rate. The browser shows Bullet's own link transforms.
- Position motors use each joint's effort and velocity limits. In pose mode joints are placed directly: a preview, not a physical result.
- Link masses are kept; a link without inertial data gets the median mass and inertia, reported in diagnostics, as are the supplied model's nonphysical inertia tensors.
- New robots start with their lowest collision shape 2 mm above the floor (**Place lowest collision on ground** does it again). Collision pairs that already overlap in the zero pose are disabled and reported. Self-collision is on except between parent and child links.
- Not modelled: spring compliance, servo backlash, motor electronics, battery limits, a balance controller, sensor noise. Simulated motion does not guarantee balance on the robot; the crouch example is a pose study, not a validated gait.
- The body and legs are the community Bittle model; the head, jaw, neck mount and neck servo come from Petoi's official model, fitted through the four shoulder hinges (within 4 mm). The neck turns ±90° about the real robot's tilted head axis. Projects saved before the head was added are upgraded if their model was never edited.
- One shared simulation per server: several tabs operate the same world. The server listens on loopback only; do not expose it to a network.
- Not there yet: inverse-kinematics foot handles, a curve editor, texture authoring.

## Sources and licences

- The robot model was copied from a Bittle URDF directory; its README and GPL licence are kept as `robot-models/bittle/SOURCE.md` and `robot-models/bittle/LICENSE`. That README attributes the meshes to a reverse-engineered GrabCAD design: check those terms before redistributing.
- The head and neck meshes are from Petoi's [ros_opencat](https://github.com/PetoiCamp/ros_opencat) model, MIT (`robot-models/bittle/head/LICENSE`).
- Petoi's skill data is from OpenCatEsp32, MIT (`app/petoi_skills.LICENSE`).
- The Petoi function catalogue and connection code are adapted from [Bittle AI Voice](https://github.com/UA-ProductDevelopment-TDD/Bittle_AI_voice); check its licence and Petoi's firmware terms before redistributing.
- Three.js and the other dependencies keep their own licences.
- References: [Petoi Python API](https://docs.petoi.com/apis/python-api), [serial protocol](https://docs.petoi.com/apis/serial-protocol), [skill data format](https://docs.petoi.com/applications/skill-creation). Inspiration: [Petoi Bittle X simulator](https://bittlex-sim.petoi.com/).

## GitHub Pages site

`dev-tools/build_pages.py` writes `_site/`: a landing page, a copy of Bittle Link and a read-only copy of `saved-motions/`. The landing page's **How to use it** card links to the README, the one place users start from. `.github/workflows/pages.yml` runs it and deploys on every push to `master`. Build it locally with `python dev-tools/build_pages.py`.

## Testing changes

Double-click `dev-tools/run-tests.cmd` (Windows) or run `dev-tools/run-tests.sh` (Mac/Linux). Or run the Python tests directly:

```bash
.venv/Scripts/python.exe -m unittest discover -s app/tests -t app -v     # Windows
.venv/bin/python -m unittest discover -s app/tests -t app -v             # Mac/Linux
```

The scripts also run `node --check` on every file in `app/web` when Node.js is installed. For anything touching the robot connection, use **Test mode** in the Studio or in Bittle Link: it logs every command that would have been sent, without a robot.
