import {catalog} from './catalog.js';
import {renderJointLayout} from './joint-layout.js';
import {packJointCommands} from './link.js';

// Controller modelled on the Petoi app: gait pad, posture and skill grids, and user-composed buttons.
// Host-provided pieces keep this UI independent of where buttons and skills are stored:
//   store:   {load(): Promise<controls[]>, save(controls): Promise<controls[]>}
//   library: {list(): [{id, name, motion_type}], resolve(id): Promise<{skill, signature, type, name}>}  (optional)
//   tools:   [{label, title, onClick}] extra buttons beside “My buttons”, e.g. import/export.
// CRAB: Crab Studio controls. The crab walks along the dog's length: dog forward = the crab scuttles to its right.
// [code, label, directions]: walk has right (F), curving left (L) and right (R); run only goes right.
const GAITS = [['wk', 'Walk'], ['tr', 'Run', 'F']];
const gaitCommand = (code, dirs = 'FLR', d) => dirs === '' ? (d === 'F' ? `k${code}` : null) : dirs.includes(d) ? `k${code}${d}` : null;
// Pad as the crab sees it (its eyes face you): left/right scuttle, curving in both directions, spinning on the spot.
const PAD = [['↰', 'kbkL', 'Scuttle left, turning left'], ['⟲', 'kvtL', 'Spin left on the spot'], ['↱', 'L', 'Scuttle right, turning left'],
  ['←', 'kbk', 'Scuttle left'], ['■', 'kbalance', 'Stop'], ['→', 'F', 'Scuttle right'],
  ['↲', 'kbkR', 'Scuttle left, turning right'], ['⟳', 'kvtR', 'Spin right on the spot'], ['↳', 'R', 'Scuttle right, turning right']];
// Postures and claw moves in groups, each sized to fill rows of three buttons.
const POSTURE_GROUPS = [
  ['Poses', [['kup', 'Crab stance'], ['kbalance', 'Balance'], ['krest', 'Rest']]],
  ['Claw (servo 0 swings the arm)', [['m 0 -10', 'Claw in'], ['m 0 0', 'Claw home'], ['m 0 20', 'Claw out']]],
  ['Claw elbow and pincer (once the claw servos are fitted)', [['m 1 60', 'Raise claw'], ['m 1 0', 'Claw level'], ['m 1 -45', 'Lower claw'],
    ['m 2 -40', 'Open pincer'], ['m 2 0', 'Close pincer'], ['i 1 0 2 0', 'Claw rest']]],
  ['Setup', [['kzero', 'Zero'], ['kcalib', 'Calibrate']]]];
const SKILL_GROUPS = [['Claw', [['kpickup', 'Pick up trash']]]];
// CRAB: speed dial. The gait's loop is resampled (same steps, faster or slower), in the simulator and on the robot.
// The Studio reads the level from localStorage 'crab-speed' when it sends a gait.
const SPEEDS = [[.5, '1', 'Slow · 50 %'], [.75, '2', '75 %'], [1, '3', 'Normal · as learned'], [1.25, '4', 'Fast · 125 %']];
export function crabSpeed() { try { return Number(localStorage.getItem('crab-speed')) || 1; } catch { return 1; } }
const POSTURES = POSTURE_GROUPS.flatMap(([, items]) => items), SKILLS = SKILL_GROUPS.flatMap(([, items]) => items);
// Robot settings. Gyro: gB enables balance/gyro assistance, gb disables it (the same commands developer mode sends).
// Petoi voice command module: XAc enables its reply tone and reactions, XAd silences and disables them.
// Servos: d = rest pose then all servos off; #on = servos back on holding the last read positions (link.js macro);
// fp / fP = soft servos and read the real joint angles once / continuously (link.js sends f then j).
// Random behaviours: the firmware only toggles them (z); #random-on / #random-off read its reply and set them (link.js macro).
// Shown one row per setting, its on and off side by side: [setting, [[code, button label, full name, what it does], ...]].
const SETTINGS = [
  ['Motors', [['#on', 'On', 'Motors on', 'servos back on, holding the last read positions (or balance)'], ['d', 'Off', 'Motors off', 'rest pose, then every servo off (limp)']]],
  ['Gyro', [['gB', 'On', 'Gyro on', 'balance/gyro assistance on'], ['gb', 'Off', 'Gyro off', 'balance/gyro assistance off']]],
  ['Voice module', [['XAc', 'On', 'Voice module on', 'Petoi voice command module on'], ['XAd', 'Off', 'Voice module off', 'Petoi voice command module off']]],
  ['Random behaviours', [['#random-on', 'On', 'Random behaviours on', 'random idle behaviours on (z, checked against the robot\'s reply)'],
    ['#random-off', 'Off', 'Random behaviours off', 'random idle behaviours off (z, checked against the robot\'s reply)']]],
  ['Joint positions', [['fp', 'Read once', 'Read positions', 'servos go soft; read the real joint angles once'],
    ['fP', 'Read live', 'Live positions', 'servos go soft; keep reading the real joint angles until another command']]],
  ['Sounds', [['b14 8', 'Beep', 'Beep', 'short beep (b note duration)'], ['u', 'Meow', 'Meow', 'meow sound']]],
  ['Robot info', [['P', 'Battery voltage', 'Battery voltage', 'print the battery voltage'], ['?', 'Firmware version', 'Firmware version', 'print the firmware version']]]];
// No faster or slower: OpenCat has T_ACCELERATE '.' and T_DECELERATE ',' switched off, and a built-in skill's speed is
// fixed in its own frames.
const MODULES = SETTINGS.flatMap(([, buttons]) => buttons.map(([code, , name, what]) => [code, name, what]));
// Direct servo control with OpenCat's ASCII "i index angle" command, in firmware (servo) degrees.
const SERVOS = [[0, 'Claw swing'], [1, 'Claw elbow'], [2, 'Pincer'], [8, 'Left front shoulder'], [12, 'Left front knee'], [9, 'Right front shoulder'], [13, 'Right front knee'],
  [11, 'Left back shoulder'], [15, 'Left back knee'], [10, 'Right back shoulder'], [14, 'Right back knee']];
const SERVO_LIMIT = 90, BLE_TEXT = 19, SLIDER_INTERVAL_MS = 80;
// English names of console commands (used by the serial monitor to explain what was sent).
const DIRECTIONS = {F: 'right', L: 'right, turning left', R: 'right, turning right'};
export const COMMAND_NAMES = Object.fromEntries([
  ...GAITS.flatMap(([code, label, dirs]) => Object.entries(DIRECTIONS).map(([d, name]) => [gaitCommand(code, dirs, d), `${label} ${name}`]).filter(([command]) => command)),
  ...PAD.filter(([, code]) => code.length > 1).map(([, code, title]) => [code, title]),
  ...POSTURES, ...SKILLS.map(([code, label]) => [code, label]), ...MODULES.map(([code, label]) => [code, label]),
]);
export const COLORS = ['green', 'blue', 'amber', 'red', 'violet', 'grey'];
const WALKING = new Set(catalog.filter(item => item.walking).map(item => item.code));
let datalistCount = 0;

const MARKUP = `
<div class="console-top"><p data-part="status" class="console-status">Not connected</p><button type="button" data-part="stop" class="danger" disabled>■ Stop</button></div>
<div class="console-layout"><div class="console-drive" data-section="move"><h4>Gait</h4><div data-part="gaits" class="gait-chips"></div><div data-part="pad" class="console-pad"></div><h4>Speed</h4><div data-part="speed" class="gait-chips speed-dial"></div><h4>Claw</h4><div data-part="claw" class="gait-chips claw-switch"></div><p data-part="clawNote" class="note"></p><p class="note">Gaits keep running until you press ■ or another command. Changing the speed while walking changes it at once.</p></div>
<div class="console-actions"><div data-section="postures"><h4>Postures</h4><div data-part="postures" class="button-groups"></div></div><div data-section="skills"><div class="section-head"><h4>Skills</h4><button type="button" data-part="replaySkill" class="replay" disabled title="Replay the last built-in skill or posture you pressed">▶ Play last skill</button></div><div data-part="skills" class="button-groups"></div></div><div data-section="settings"><h4>Robot settings</h4><div data-part="modules" class="settings-rows"></div><label class="playback-mode" title="Stream: each frame of a saved function is sent as a joint command on its own timing (starts at once). Upload: the whole skill is sent in one K packet, then played by the robot.">Function playback <select data-part="playback"><option value="stream">Stream</option><option value="upload">Upload</option></select></label></div><div data-section="myskills"><div class="section-head"><h4>My skills</h4><button type="button" data-part="replayCustom" class="replay" title="Replay the last custom skill (Petoi T command)">▶ Play last skill</button></div><div data-part="myskills" class="console-grid custom-grid"></div><p data-part="myskillsHint" class="note"></p></div></div></div>
<details data-part="jointPanel" class="console-joints" open><summary><h4>Joints</h4><span class="note">Move each servo directly (servo degrees, sent as <code>i servo angle</code>).</span></summary>
<div data-part="joints" class="joint-sliders"></div>
<div class="joint-actions"><button type="button" data-part="jointsZero">All to 0°</button><button type="button" data-part="jointsRelease">Release head</button><button type="button" data-part="jointsRead">Read angles</button></div>
<p class="note">Sliders show the last angle sent from here, not the robot's live position. Turn the gyro off (Robot settings) so balance correction does not fight the sliders. Start with small moves and keep the robot lifted.</p></details>
<div data-section="buttons"><div class="console-custom-head"><div><h4>My buttons</h4><p data-part="hint" class="note"></p></div><div class="console-tools"><span data-part="tools"></span><label class="check"><input data-part="editMode" type="checkbox"> Edit buttons</label></div></div><div data-part="custom" class="console-grid custom-grid"></div>
<form data-part="editor" class="console-editor hidden"><div class="editor-head"><h4 data-part="editorTitle">New button</h4><span data-part="summary" class="note"></span></div>
<div class="editor-fields"><label>Name<input data-part="name" maxlength="40" placeholder="For example: Greet and sit"></label><div class="editor-colors"><span>Colour</span><div data-part="colors"></div></div><label class="check"><input data-part="repeat" type="checkbox"> Repeat until stopped</label></div>
<div data-part="steps" class="step-list"></div><div class="editor-step-actions"><button type="button" data-part="addStep">+ Command step</button><button type="button" data-part="addMotion">+ Studio function step</button><label class="check"><input data-part="record" type="checkbox"> Record presses from the pad and grids</label></div>
<div class="console-editor-actions"><button class="primary">Save button</button><button type="button" data-part="test">▶ Test</button><button type="button" data-part="cancel">Cancel</button><button type="button" data-part="delete" class="danger hidden">Delete</button></div></form>
<p class="note">Last command: <code data-part="last">none</code>. The wait after each step is how long the console waits before sending the next one. Walking steps keep going during that wait, so end a walk with <code>kbalance</code>.</p></div><datalist data-part="codes"></datalist>`;

//   joints: false hides the joint sliders (Studio has them in the inspector); statusText(detail) customises the status line.
//   tabs: true shows Move / Postures / Skills / Buttons as tabs under the status line (for narrow hosts);
//   extraTabs: [{label, element}] are added in front of them (Studio's Joints panel).
export function initConsole(root, {link, toast = message => console.warn(message), store, library = null, tools = [], offlineHint = 'Not connected.', joints = true, statusText = null, tabs = false, extraTabs = []}) {
  root.classList.add('bittle-console', 'offline'); root.innerHTML = MARKUP;
  const part = name => root.querySelector(`[data-part="${name}"]`);
  const listId = `petoiCodes${++datalistCount}`; part('codes').id = listId;
  let controls = [], gait = 'wk', editing = null, activeId = null;
  const button = (label, title, onclick, className = '') => { const b = document.createElement('button'); b.type = 'button'; b.textContent = label; if (title) b.title = title; if (className) b.className = className; b.onclick = onclick; return b; };
  const fail = error => toast(error.message, true);
  const recording = () => editing && part('record').checked;
  const editMode = () => part('editMode').checked;
  const functions = () => library?.list() || [];
  const functionName = id => functions().find(item => item.id === id)?.name;

  // A press sends immediately and, while recording, also appends a step to the button being edited.
  let lastSkill = null;  // last built-in skill / posture pressed (Skills tab replay)
  let moving = null;     // the gait running from the Move pad, replayed when the speed changes
  // CRAB: claw fitted or not. Switches the simulator model, the learned gaits used here and sent to the robot, and the
  // Arduino crab file (Crab Studio server, /api/crab/config). Training learns the gaits for the current setting.
  let claw = null, clawLearned = [];
  const clawText = () => claw === null ? '' : `${claw ? 'Claw fitted' : 'No claw'}: the simulator, the Move pad and the robot use the ${claw ? 'claw' : 'no-claw'} gaits`
    + (clawLearned.length ? ` (learned: ${clawLearned.length} of 9)` : ' (not trained yet: run train_crab.py)') + '.';
  async function loadClaw() {
    try { const r = await fetch('/api/crab/config'); if (!r.ok) return; const d = await r.json(); claw = d.claw; clawLearned = d.learned || []; renderStatic(); } catch {}
  }
  async function setClaw(value) {
    try {
      const r = await fetch('/api/crab/config', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({claw: value})});
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.detail || d.error || 'Could not switch the claw setting');
      claw = d.claw; clawLearned = d.learned || []; renderStatic();
      window.dispatchEvent(new CustomEvent('crab-claw', {detail: d}));
      toast(`${claw ? 'Claw fitted' : 'No claw'}: model, gaits and the Arduino crab file switched.`);
      if (moving) press(moving.command, moving.label, 'Move');
    } catch (error) { toast(error.message, true); }
  }
  function press(command, label, section = '') {
    if (/^k\w/.test(command) && (section === 'Skills' || section === 'Postures')) {
      lastSkill = {command, label, section};
      part('replaySkill').disabled = false; part('replaySkill').textContent = `▶ Play last skill (${label})`;
    }
    if (recording()) { editing.steps.push({kind: 'command', command, wait_ms: WALKING.has(command.split(' ')[0]) ? 2000 : 1500}); renderEditor(); }
    if (section === 'Move') moving = WALKING.has(command.split(' ')[0]) || /^k(wk|bk|tr|vt)/.test(command) ? {command, label} : null;
    else if (/^k/.test(command)) moving = null;
    if (link.connected) link.press(command, section ? `${section} · ${label}` : label).catch(fail);
    else if (!recording()) toast(offlineHint, true);
    part('last').textContent = `${label} · ${command}`;
  }
  function renderStatic() {
    const dirsOf = code => GAITS.find(entry => entry[0] === code)?.[2] ?? 'FLR';
    part('gaits').replaceChildren(...GAITS.map(([code, label, dirs = 'FLR']) => { const b = button(label, dirs === '' ? `k${code} (forward only)` : dirs === 'F' ? `k${code}F (forward only)` : `k${code}F / L / R`, () => { gait = code; renderStatic(); }); b.classList.toggle('selected', code === gait); return b; }));
    part('speed').replaceChildren(...SPEEDS.map(([value, glyph, title]) => {
      const b = button(glyph, `Speed ${glyph}: ${title}`, () => {
        try { localStorage.setItem('crab-speed', String(value)); } catch {}
        renderStatic();
        if (moving) press(moving.command, moving.label, 'Move');
      });
      b.classList.toggle('selected', Math.abs(crabSpeed() - value) < 1e-6);
      return b;
    }));
    part('claw').replaceChildren(...[[true, 'Fitted'], [false, 'Not fitted']].map(([value, label]) => {
      const b = button(label, value ? 'Claw arm fitted: its weight is in the physics and the claw gaits are used' : 'No claw arm: lighter model and the no-claw gaits', () => { if (claw !== value) setClaw(value); });
      b.classList.toggle('selected', claw === value);
      return b;
    }));
    part('clawNote').textContent = clawText();
    part('pad').replaceChildren(...PAD.map(([glyph, code, title]) => {
      const command = code.length === 1 ? gaitCommand(gait, dirsOf(gait), code) : code;
      const b = button(glyph, command ? `${title} · ${command}` : 'This gait only goes forward', () => press(command, title, 'Move'), code === 'kbalance' ? 'pad-stop' : '');
      if (!command) b.disabled = true;
      return b;
    }));
    part('postures').replaceChildren(...groups(POSTURE_GROUPS, ([code, label]) => button(label, code, () => press(code, label, 'Postures'))));
    part('skills').replaceChildren(...groups(SKILL_GROUPS, ([code, label, risky]) => button(risky ? `⚠ ${label}` : label, risky ? `${code} · needs free space and a soft floor` : code, () => press(code, label, 'Skills'), risky ? 'risky' : '')));
    if (part('playback')) { part('playback').value = link.playbackMode || 'stream'; part('playback').onchange = event => { link.playbackMode = event.target.value; }; }
  part('modules').replaceChildren(...SETTINGS.map(([setting, buttons]) => {
      const row = document.createElement('div'); row.className = 'settings-row';
      const name = document.createElement('span'); name.className = 'settings-name'; name.textContent = setting;
      const group = document.createElement('div'); group.className = 'settings-buttons';
      group.append(...buttons.map(([code, label, full, what]) => button(label, `${full} · ${code} · ${what}`, () => press(code, full, 'Robot settings'))));
      row.append(name, group);
      return row;
    }));
    part('tools').replaceChildren(...tools.map(tool => button(tool.label, tool.title, () => Promise.resolve(tool.onClick()).catch(fail))));
  }
  // Each group its name, then its buttons: beside them in a wide console, above them in a narrow one.
  function groups(list, make) {
    return list.map(([name, items]) => {
      const group = document.createElement('div'); group.className = 'button-group';
      const caption = document.createElement('span'); caption.className = 'button-group-name'; caption.textContent = name;
      const grid = document.createElement('div'); grid.className = 'console-grid'; grid.append(...items.map(make));
      group.append(caption, grid);
      return group;
    });
  }
  function renderMySkills() {
    const items = functions();
    part('myskills').replaceChildren(...items.map(item => {
      const b = button(item.name, `${item.motion_type} · plays in the simulator and on the robot when connected`, () => runSkill(item), 'custom-button color-violet');
      if (item.motion_type === 'gait') b.append(Object.assign(document.createElement('small'), {textContent: '↻'}));
      return b;
    }));
    part('myskillsHint').textContent = items.length ? 'Your saved functions. Make more on the timeline with ★ Save as function.'
      : library ? 'No custom skills yet: make a motion on the timeline and press ★ Save as function (or import a pack).' : '';
  }
  async function runSkill(item) {
    if (!link.connected) return toast(offlineHint, true);
    part('last').textContent = `My skills · ${item.name}`;
    try { await link.runSequence({name: `My skills · ${item.name}`, repeat: false, steps: [{kind: 'motion', motion_id: item.id, wait_ms: 0}]}, {resolveMotion: library && (id => library.resolve(id))}); }
    catch (error) { fail(error); }
  }
  function renderControls() {
    const list = part('custom'); list.replaceChildren();
    for (const item of controls) {
      const b = button(item.name, `${item.steps.length} step${item.steps.length === 1 ? '' : 's'}${item.repeat ? ' · repeats' : ''}`, () => editMode() ? openEditor(item) : run(item), `custom-button color-${item.color}`);
      if (item.id === activeId) b.classList.add('running');
      if (item.repeat) b.append(Object.assign(document.createElement('small'), {textContent: '↻'}));
      list.append(b);
    }
    list.append(button('+ New button', 'Compose a new button', () => openEditor(null), 'custom-add'));
    part('hint').textContent = controls.length ? (editMode() ? 'Click a button to edit it.' : 'Click to run. Any other press or Stop interrupts it.') : 'Compose buttons from Petoi commands and saved Studio functions.';
  }
  // Joint sliders: changes are coalesced and sent at most every SLIDER_INTERVAL_MS, packing as many
  // "index angle" pairs into one i-command as fit in a BLE text packet.
  const jointValues = new Map(SERVOS.map(([index]) => [index, 0])), pendingJoints = new Map();
  let jointTimer = null;
  async function flushJoints() {
    jointTimer = null;
    if (!pendingJoints.size) return;
    const entries = [...pendingJoints]; pendingJoints.clear();
    if (!link.connected) return toast(offlineHint, true);
    for (const command of packJointCommands(entries, BLE_TEXT)) await link.press(command, 'Joint sliders').catch(fail);
    part('last').textContent = `Joints · ${entries.map(([i, a]) => `${i}:${a}°`).join(' ')}`;
  }
  function queueJoint(index, angle) {
    jointValues.set(index, angle); pendingJoints.set(index, angle);
    if (!jointTimer) jointTimer = setTimeout(flushJoints, SLIDER_INTERVAL_MS);
  }
  function renderJoints() {
    renderJointLayout(part('joints'), SERVOS.map(([servo, label]) => {
      const set = (value, final, slider, number) => {
        const angle = Math.max(-SERVO_LIMIT, Math.min(SERVO_LIMIT, Math.round(Number(value) || 0)));
        slider.value = number.value = angle; queueJoint(servo, angle);
        // While recording, each finished slider move becomes one command step of the button being edited.
        if (final && recording()) { editing.steps.push({kind: 'command', command: `i ${servo} ${angle}`, wait_ms: 500}); renderEditor(); }
      };
      // OpenCat turns the head left as its angle grows (m 0 30 looks left): its slider is mirrored, so right turns it right.
      return {servo, label, title: servo === 0 ? 'Head pan' : label.replace(/^(Left|Right) (front|back) /, '').replace(/^./, c => c.toUpperCase()),
        min: -SERVO_LIMIT, max: SERVO_LIMIT, step: 1, value: jointValues.get(servo), mirrored: servo === 0,
        onInput: (value, slider, number) => set(value, false, slider, number), onCommit: (value, slider, number) => set(value, true, slider, number)};
    }));
  }
  part('jointsZero').onclick = () => { SERVOS.forEach(([index]) => queueJoint(index, 0)); renderJoints(); };
  part('jointsRelease').onclick = () => link.connected ? link.press('i').then(() => { part('last').textContent = 'Release head · i'; }).catch(fail) : toast(offlineHint, true);
  part('jointsRead').onclick = () => link.connected ? link.press('j').then(() => toast('Joint angles requested; the robot replies in the log.')).catch(fail) : toast(offlineHint, true);
  if (joints) renderJoints(); else part('jointPanel').remove();

  async function run(item) {
    if (!link.connected) return toast(offlineHint, true);
    activeId = item.id; renderControls();
    try { await link.runSequence(item, {resolveMotion: library && (id => library.resolve(id))}); }
    catch (error) { fail(error); }
    finally { if (activeId === item.id) { activeId = null; renderControls(); } }
  }
  async function save(next) { controls = await store.save(next); renderControls(); }
  async function reload() { controls = await store.load(); renderControls(); renderEditor(); renderMySkills(); }

  function openEditor(item) {
    editing = item ? structuredClone(item) : {id: '', name: '', color: 'green', repeat: false, steps: []};
    part('editor').classList.remove('hidden'); part('editorTitle').textContent = item ? `Edit “${item.name}”` : 'New button';
    part('name').value = editing.name; part('repeat').checked = editing.repeat; part('delete').classList.toggle('hidden', !item);
    renderEditor(); part('name').focus();
  }
  function closeEditor() { editing = null; part('record').checked = false; part('editor').classList.add('hidden'); }
  function renderEditor() {
    if (!editing) return;
    part('addMotion').classList.toggle('hidden', !library);
    part('colors').replaceChildren(...COLORS.map(color => { const b = button('', color, () => { editing.color = color; renderEditor(); }, `swatch color-${color}`); b.setAttribute('aria-pressed', color === editing.color); b.setAttribute('aria-label', color); return b; }));
    const available = functions(), rows = part('steps'); rows.replaceChildren();
    if (!editing.steps.length) rows.append(Object.assign(document.createElement('p'), {className: 'note', textContent: 'No steps yet. Add one below, or tick “Record presses” and use the pad and skill buttons.'}));
    editing.steps.forEach((step, index) => {
      const row = document.createElement('div'); row.className = 'step-row';
      const number = Object.assign(document.createElement('span'), {className: 'step-number', textContent: index + 1});
      const kind = document.createElement('select'); kind.append(new Option('Command', 'command')); if (library || step.kind === 'motion') kind.append(new Option('Studio function', 'motion')); kind.value = step.kind;
      kind.onchange = () => { editing.steps[index] = kind.value === 'command' ? {kind: 'command', command: 'kbalance', wait_ms: step.wait_ms} : {kind: 'motion', motion_id: available[0]?.id || '', wait_ms: step.wait_ms}; renderEditor(); };
      let target;
      if (step.kind === 'command') { target = document.createElement('input'); target.value = step.command; target.maxLength = 64; target.setAttribute('list', listId); target.placeholder = 'ksit, kwkF, m 0 30…'; target.oninput = () => { step.command = target.value; }; }
      else {
        target = document.createElement('select');
        if (!available.length) target.append(new Option('No saved Studio functions', ''));
        if (step.motion_id && !functionName(step.motion_id)) target.append(new Option('(missing function)', step.motion_id));
        available.forEach(item => target.append(new Option(`${item.name} · ${item.motion_type}`, item.id)));
        target.value = step.motion_id; target.onchange = () => { step.motion_id = target.value; };
      }
      const wait = document.createElement('input'); wait.type = 'number'; wait.min = 0; wait.max = 60000; wait.step = 100; wait.value = step.wait_ms; wait.title = 'Wait after this step (ms)'; wait.oninput = () => { step.wait_ms = Math.max(0, Math.min(60000, Math.round(Number(wait.value) || 0))); };
      const move = delta => () => { const to = index + delta; if (to < 0 || to >= editing.steps.length) return; [editing.steps[index], editing.steps[to]] = [editing.steps[to], editing.steps[index]]; renderEditor(); };
      const actions = document.createElement('div'); actions.className = 'row-actions';
      actions.append(button('↑', 'Move up', move(-1)), button('↓', 'Move down', move(1)), button('×', 'Remove step', () => { editing.steps.splice(index, 1); renderEditor(); }));
      const waitLabel = document.createElement('label'); waitLabel.className = 'step-wait'; waitLabel.append(wait, Object.assign(document.createElement('span'), {textContent: 'ms'}));
      row.append(number, kind, target, waitLabel, actions); rows.append(row);
    });
    part('summary').textContent = editing.steps.length ? `${editing.steps.length} step${editing.steps.length === 1 ? '' : 's'} · about ${(editing.steps.reduce((sum, step) => sum + step.wait_ms, 0) / 1000).toFixed(1)} s${editing.repeat ? ' per round' : ''}` : '';
  }
  function collect() {
    editing.name = part('name').value.trim(); editing.repeat = part('repeat').checked;
    if (!editing.name) throw new Error('Give the button a name.');
    if (!editing.steps.length) throw new Error('Add at least one step.');
    return editing;
  }

  part('editMode').onchange = renderControls;
  part('addStep').onclick = () => { editing.steps.push({kind: 'command', command: 'kbalance', wait_ms: 1500}); renderEditor(); };
  part('addMotion').onclick = () => { const first = functions()[0]; if (!first) return toast('There are no saved Studio functions yet.', true); editing.steps.push({kind: 'motion', motion_id: first.id, wait_ms: 2000}); renderEditor(); };
  part('repeat').onchange = () => { editing.repeat = part('repeat').checked; renderEditor(); };
  part('cancel').onclick = closeEditor;
  part('test').onclick = () => { try { const item = collect(); run({...item, id: 'test', name: `${item.name} (test)`}); } catch (error) { fail(error); } };
  part('editor').onsubmit = async event => {
    event.preventDefault();
    try { const item = collect(); await save(item.id ? controls.map(entry => entry.id === item.id ? item : entry) : [...controls, item]); toast(`Saved “${item.name}”.`); closeEditor(); }
    catch (error) { fail(error); }
  };
  part('delete').onclick = async () => { if (!editing?.id || !confirm(`Delete the button “${editing.name}”?`)) return; try { await save(controls.filter(entry => entry.id !== editing.id)); closeEditor(); } catch (error) { fail(error); } };
  part('stop').onclick = () => link.stop().catch(fail);
  part('replaySkill').onclick = () => lastSkill && press(lastSkill.command, lastSkill.label, lastSkill.section);
  part('replayCustom').onclick = () => { if (link.connected) link.press('T', 'My skills · Play last skill').catch(fail); else toast(offlineHint, true); };
  catalog.forEach(item => part('codes').append(new Option(item.label, item.code)));

  function onState({detail}) {
    root.classList.toggle('offline', !detail.connected); part('stop').disabled = !detail.connected;
    part('status').textContent = statusText ? statusText(detail) : detail.connected ? `Connected · ${detail.transport}${detail.testMode ? ' (test mode, nothing is sent)' : ''}` : offlineHint;
  }
  link.addEventListener('state', onState); onState({detail: link.status()});

  // Tab mode: move each section into its own pane, inside this console so every part keeps working.
  function buildTabs() {
    const bar = document.createElement('div'); bar.className = 'console-tabs'; bar.setAttribute('role', 'tablist');
    const panes = document.createElement('div'); panes.className = 'console-panes';
    const entries = [...extraTabs.map(tab => ({label: tab.label, nodes: [tab.element]})),
      {label: 'Move', nodes: [root.querySelector('[data-section="move"]')]},
      {label: 'Postures', nodes: [root.querySelector('[data-section="postures"]')]},
      {label: 'Skills', nodes: [root.querySelector('[data-section="skills"]')]},
      ...(library ? [{label: 'My skills', nodes: [root.querySelector('[data-section="myskills"]')]}] : []),
      {label: 'Buttons', nodes: [root.querySelector('[data-section="buttons"]')]},
      {label: 'Robot settings', nodes: [root.querySelector('[data-section="settings"]')]}];
    const select = index => {
      [...bar.children].forEach((b, i) => b.setAttribute('aria-selected', String(i === index)));
      [...panes.children].forEach((pane, i) => pane.classList.toggle('hidden', i !== index));
      try { localStorage.setItem('bittle-console-tab', String(index)); } catch {}
    };
    entries.forEach((entry, index) => {
      const button = Object.assign(document.createElement('button'), {type: 'button', textContent: entry.label});
      button.setAttribute('role', 'tab'); button.onclick = () => select(index);
      const pane = document.createElement('div'); pane.className = 'console-pane'; pane.setAttribute('role', 'tabpanel');
      pane.append(...entry.nodes.filter(Boolean));
      bar.append(button); panes.append(pane);
    });
    root.querySelector('.console-layout').remove();
    root.querySelector('.console-top').after(bar, panes);
    root.classList.add('tabbed');
    let saved = 0; try { saved = Number(localStorage.getItem('bittle-console-tab')) || 0; } catch {}
    select(saved < entries.length ? saved : 0);
  }
  if (tabs) buildTabs();
  renderStatic(); reload().catch(fail); loadClaw();
  if (!library) root.querySelector('[data-section="myskills"]').remove();
  return {reload, refreshLibrary: () => { renderEditor(); renderMySkills(); }, get controls() { return controls; }};
}
