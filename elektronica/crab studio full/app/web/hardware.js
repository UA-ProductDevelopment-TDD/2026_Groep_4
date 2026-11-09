import {catalog} from './bittle-link/catalog.js';
import {createLink, packJointCommands} from './bittle-link/link.js';
import {initConsole, crabSpeed} from './bittle-link/console.js';
import {saveFolderPack} from './bittle-link/pack.js';
import {initSerialMonitor} from './bittle-link/serial-monitor.js';

// Studio glue for the Bittle Link module: timeline playback, the function library and the developer-mode panel.
// The connection, protocol and console live in web/bittle-link and also run standalone. In Studio every console
// action moves the simulator; when a robot is connected it is sent over Bluetooth as well.
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

export function initHardware({api, toast, getModel, getMapping, refresh}) {
  const $ = id => document.getElementById(id);
  const link = createLink();
  // Learning aid: with no robot connected, actions still go through a second link in test mode, so the serial monitor
  // shows the exact commands a real Bittle would receive (marked as simulator only; nothing leaves the computer).
  const shadow = createLink();
  let shadowReady = false;
  const robot = () => link.connected ? link : shadowReady ? shadow : null;
  let motions = [], sequence = [];  // sequence: function ids ticked for a combined export, in click order

  link.addEventListener('state', render);
  function render() {
    const {connected, busy, developerMode, message, transport} = link.status();
    $('hardwareStatus').textContent = message || (connected ? `Connected · ${transport}` : 'Not connected');
    $('hardwareConnect').disabled = connected; $('hardwareDisconnect').disabled = !connected;
    $('hardwareTransport').disabled = connected;
    $('developerMode').checked = developerMode;
    $('developerStatus').textContent = developerMode ? (connected ? 'Active · background voice, autonomous actions and gyro/balance assistance are off.' : 'Will activate when connected.') : (connected ? 'Off · balance assistance and voice actions are available.' : 'Off.');
    $('developerStatus').classList.toggle('active', developerMode && connected);
    window.dispatchEvent(new CustomEvent('bittle-hardware-state', {detail: link.status()}));
  }

  const skillEntry = (result, name) => ({skill: result.skill, signature: result.metadata.signature, type: result.metadata.type, hz: result.metadata.firmware_hz, name});
  async function resolveMotion(id) { const result = await api(`/api/motions/${id}/skill`, {mapping: getMapping()}); return skillEntry(result, motions.find(item => item.id === id)?.name || 'Studio function'); }

  function functionRow(title, detail, actions) { const row = document.createElement('div'); row.className = 'function-row'; const copy = document.createElement('div'); const b = document.createElement('b'), small = document.createElement('small'); b.textContent = title; small.textContent = detail; copy.append(b, small); row.append(copy, actions); return row; }
  function renderLibrary() {
    const {connected, busy} = link.status(), q = $('functionSearch').value.trim().toLowerCase();
    $('customFunctions').replaceChildren(); if (!motions.length) { const p = document.createElement('p'); p.className = 'note'; p.textContent = 'No functions yet. Make a motion, then press ★ Save as function.'; $('customFunctions').append(p); }
    for (const item of motions.filter(item => !q || item.name.toLowerCase().includes(q))) { const actions = document.createElement('div'); actions.className = 'row-actions'; for (const [label, action] of [['Load', loadSaved], ['Run', playSaved], ['Python', exportSaved], ['×', deleteSaved]]) { const button = document.createElement('button'); button.textContent = label; button.title = {Load: 'Put this function back on the timeline', Run: 'Play in the simulator, and on the robot when connected', Python: 'Download this function as a PetoiRobot Python script', '×': 'Delete this function'}[label]; if (label === 'Run') button.dataset.motionPlay = item.id; button.onclick = () => action(item).catch(error => toast(error.message, true)); actions.append(button); } const row = functionRow(item.name, `${item.motion_type} · ${item.hz} Hz · ${item.frames.length} keyframes`, actions); const position = sequence.indexOf(item.id); const pick = Object.assign(document.createElement('button'), {type: 'button', className: 'sequence-pick' + (position >= 0 ? ' picked' : ''), textContent: position >= 0 ? String(position + 1) : ''}); pick.title = position >= 0 ? `Number ${position + 1} in the export · click to remove` : 'Add to the combined Python export'; pick.setAttribute('aria-pressed', String(position >= 0)); pick.onclick = () => { sequence = position >= 0 ? sequence.filter(id => id !== item.id) : [...sequence, item.id]; renderLibrary(); }; row.prepend(pick); $('customFunctions').append(row); }
    $('exportSequence').disabled = !sequence.length; $('clearSequence').disabled = !sequence.length; $('runSequence').disabled = !sequence.length;
    $('exportSequence').textContent = sequence.length > 1 ? `Export ${sequence.length} ↗` : 'Export selected ↗';
    $('sequenceHint').textContent = sequence.length ? sequence.map(id => motions.find(m => m.id === id)?.name).join(' → ') : 'Tick functions in the order they should play.';
  }
  async function refreshMotions() { motions = (await api('/api/motions')).motions; sequence = sequence.filter(id => motions.some(m => m.id === id)); renderLibrary(); consolePanel.refreshLibrary(); }
  async function loadSaved(item) { await api(`/api/motions/${item.id}/load`, {}); await refresh?.(); toast(`Loaded “${item.name}” into the timeline.`); }
  async function playSaved(item) { control.cancelSequence(); await Promise.all([simulate({motion_id: item.id}), robot() ? resolveMotion(item.id).then(entry => robot()?.runSkill(entry, item.name)) : null]); }
  async function exportSaved(item) {
    // The function's own type, Hz, speed and order are used; the timeline is left untouched.
    const file = `bittle_${safeName(item.name)}.py`;
    await downloadPython(`/api/motions/${item.id}/export`, {mapping: getMapping()}, file);
    toast(`Downloaded “${item.name}” as ${file} (${item.motion_type}). Run it without flags for a dry run.`);
  }
  async function downloadPython(url, body, file) {
    let response;
    try { response = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)}); }
    catch { throw new Error('The Bittle Studio server is not running. Start it with the “Start Bittle Studio” launcher in the launchers folder (keep its window open), then reload this page.'); }
    if (!response.ok) { let detail = response.statusText; try { detail = (await response.json()).detail; } catch {} throw new Error(typeof detail === 'string' ? detail : 'Export failed'); }
    const href = URL.createObjectURL(new Blob([await response.text()], {type: 'text/x-python'}));
    Object.assign(document.createElement('a'), {href, download: file}).click(); setTimeout(() => URL.revokeObjectURL(href), 1000);
  }
  const safeName = name => name.replace(/[^A-Za-z0-9_-]+/g, '_').replace(/^_+|_+$/g, '') || 'motion';
  // Preview the ticked functions with the exported script's pacing (pose held 1 s, gait one cycle, 0.3 s between).
  const SEQUENCE_GAP_MS = 300, POSE_HOLD_MS = 1000;
  const functionMs = item => item.motion_type === 'pose' ? POSE_HOLD_MS : Math.round((item.frames.at(-1)?.time || 0) / (item.speed || 1) * 1000);
  async function runSelected() {
    const items = sequence.map(id => motions.find(m => m.id === id)).filter(Boolean);
    const steps = items.map(item => ({kind: 'motion', motion_id: item.id, wait_ms: functionMs(item) + SEQUENCE_GAP_MS}));
    toast(`Playing ${items.map(item => item.name).join(' → ')}${link.connected ? ' on the simulator and the robot' : ' in the simulator'}. Stop or Esc interrupts.`);
    await control.runSequence({name: 'Selected functions', repeat: false, steps});
    // Like the exported script, end after the last function: a gait would otherwise keep looping.
    if (items.at(-1)?.motion_type === 'gait') await control.stop();
    else await api('/api/command', {action: 'pose', pose: {}});
  }
  async function exportSequence() {
    const names = sequence.map(id => motions.find(m => m.id === id)?.name);
    const file = `bittle_${safeName(names.join('_then_')).slice(0, 60)}.py`;
    await downloadPython('/api/motion-sequence/export', {ids: sequence, mapping: getMapping()}, file);
    toast(`Downloaded ${file}: ${names.join(' → ')} (gaits play one cycle). Run it without flags for a dry run.`);
  }
  async function deleteSaved(item) { await api(`/api/motions/${item.id}`, {}, 'DELETE'); await refreshMotions(); toast(`Deleted “${item.name}”.`); }
  async function voiceAct(code, duration = 800) { if (link.developerMode) throw new Error('Voice actions are blocked by developer mode.'); const item = catalog.find(entry => entry.code === code); if (!item) throw new Error('Unknown Petoi function code.'); await link.sendCommand(item.code, 'Voice'); if (item.walking) { await sleep(Math.max(200, Math.min(3000, duration))); await link.sendCommand('kbalance', 'Voice'); } return {status: link.status().testMode ? 'simulated' : 'sent', command: item.code}; }
  async function setDeveloperMode(enabled) { await link.setDeveloperMode(enabled); window.dispatchEvent(new CustomEvent('bittle-developer-mode', {detail: {enabled}})); }

  // Unified control: the console, library and sliders drive the simulator, and the robot too when connected.
  let sequenceEpoch = 0;
  const simulate = body => api('/api/skill', body).catch(error => { toast(error.message, true); return {simulated: false}; });
  const controlStatus = () => ({...link.status(), connected: true, robot: link.connected});
  // CRAB: k<skill> commands go to the robot as the crab skill itself (K upload), not as k<name>: the robot then plays
  // exactly what the simulator shows, also when its firmware still has the dog's skills. Fetched fresh (cached 3 s),
  // so newly trained gaits are used straight away.
  let crabSkills = null, crabSkillsAt = 0, crabSkillsSpeed = 1;
  async function crabSkill(command) {
    const match = /^k([A-Za-z]+)$/.exec(String(command).trim());
    if (!match) return null;
    const speed = crabSpeed();
    if (!crabSkills || speed !== crabSkillsSpeed || performance.now() - crabSkillsAt > 3000) {
      try { crabSkills = await api(`/api/crab/robot-skills?speed=${speed}`); crabSkillsAt = performance.now(); crabSkillsSpeed = speed; }
      catch { crabSkills = crabSkills || {}; }
    }
    return crabSkills[match[1]] || null;
  }
  // The claw switch changed the model and the gaits: reload the 3D model and fetch the new gaits on the next press.
  window.addEventListener('crab-claw', () => { crabSkills = null; Promise.resolve(refresh?.()).catch(() => {}); });
  async function robotPress(command, origin = '') {
    const target = robot();
    if (!target) return;
    const entry = target.playFirmware ? await crabSkill(command) : null;
    return entry ? target.playFirmware(entry, `Crab ${entry.name}`, origin) : target.press(command, origin);
  }
  async function robotStop() {
    const target = robot();
    if (!target) return;
    const entry = target.stopWith ? await crabSkill('kbalance') : null;
    return entry ? target.stopWith(entry) : target.stop();
  }
  const both = (command, origin = '') => Promise.all([simulate({command, speed: crabSpeed()}), robotPress(command, origin)]);
  const control = Object.assign(new EventTarget(), {
    status: controlStatus,
    cancelSequence() { sequenceEpoch++; },
    async press(command, origin = '') { sequenceEpoch++; await both(command, origin); },
    async stop() { sequenceEpoch++; await Promise.all([simulate({command: 'kbalance'}), robotStop()]); },
    async runSequence(item, {onStep} = {}) {
      const token = ++sequenceEpoch, live = () => token === sequenceEpoch;
      const wait = ms => new Promise(resolve => { const end = performance.now() + ms; const tick = () => !live() || performance.now() >= end ? resolve() : setTimeout(tick, Math.min(20, ms)); tick(); });
      try {
        do {
          for (const [index, step] of item.steps.entries()) {
            if (!live()) return;
            onStep?.(index);
            if (step.kind === 'command') { robot()?.cancelStream(); await both(step.command, `${item.name} · step ${index + 1}`); }
            else await Promise.all([simulate({motion_id: step.motion_id}), robot() ? resolveMotion(step.motion_id).then(entry => live() && robot()?.sendSkill(entry, entry.name, `${item.name} · step ${index + 1}`)) : null]);
            if (step.wait_ms) await wait(step.wait_ms);
          }
        } while (item.repeat && live());
      } finally { onStep?.(-1); }
    },
  });
  Object.defineProperties(control, {connected: {get: () => true}, developerMode: {get: () => link.developerMode},
    playbackMode: {get: () => link.playbackMode, set: mode => { link.playbackMode = mode; shadow.playbackMode = mode; }}});
  link.addEventListener('state', () => control.dispatchEvent(new CustomEvent('state', {detail: controlStatus()})));

  // Real joint positions reported by the robot (Read / Live positions) pose the simulated Bittle through Servo setup.
  let lastFeedbackAt = 0;
  link.addEventListener('joints', ({detail}) => {
    const now = performance.now();
    if (now - lastFeedbackAt < 90) return;
    lastFeedbackAt = now;
    const map = getMapping(), pose = {};
    for (const [joint, m] of Object.entries(map))
      if (Number.isInteger(m.servo) && m.servo >= 0 && m.servo < 16) pose[joint] = (detail.angles[m.servo] - Number(m.offset || 0)) / (m.sign || 1);
    if (Object.keys(pose).length) api('/api/command', {action: 'pose', pose}).catch(() => {});
  });

  // Joint sliders in the inspector mirror to the robot's servos when connected (rate-limited, packed per BLE packet).
  const pendingServos = new Map(); let servoTimer = null;
  function mirrorServos(entries) {
    if (!robot()) return;
    entries.forEach(([servo, angle]) => pendingServos.set(servo, angle));
    if (servoTimer) return;
    servoTimer = setTimeout(async () => {
      servoTimer = null;
      const batch = [...pendingServos]; pendingServos.clear();
      for (const command of packJointCommands(batch)) await robot()?.sendCommand(command, 'Joint sliders').catch(error => toast(error.message, true));
    }, 80);
  }

  const consolePanel = initConsole($('hardwareConsole'), {
    link: control, toast, joints: false, tabs: true, extraTabs: [{label: 'Joints', element: $('jointsSection')}],
    statusText: detail => detail.robot ? `Simulator + robot (${detail.testMode ? 'test mode' : detail.transport === 'ble' ? 'BLE' : 'serial'})` : 'Simulator only · connect via Bluetooth for the robot',
    store: {load: async () => (await api('/api/controls')).controls, save: async controls => (await api('/api/controls', {controls}, 'PUT')).controls},
    library: {list: () => motions, resolve: resolveMotion},
    tools: [
      {label: 'Save pack', title: 'Save these buttons and your Studio functions to the saved-motions folder for Bittle Link', onClick: async () => {
        const name = prompt('Name for this pack (saved in the saved-motions folder; the same name replaces the older file):', 'my-bittle-moves');
        if (!name) return;
        const saved = await saveFolderPack(name, await api('/api/controls/pack', {mapping: getMapping()}));
        toast(`Saved saved-motions/${saved.file} · ${saved.controls} buttons, ${saved.skills} Studio functions.`);
      }},
      {label: 'Open standalone ↗', title: 'Open the Bittle Link console in its own tab (no simulator needed)', onClick: () => window.open('/web/bittle-link/index.html','_blank', 'noopener')},
    ],
  });

  $('openHardware').onclick = () => { $('hardwareDialog').open ? $('hardwareDialog').close() : $('hardwareDialog').show(); };
  $('hardwareConnect').onclick = () => link.connect($('hardwareTransport').value)
    .then(() => { if (link.developerMode) window.dispatchEvent(new CustomEvent('bittle-developer-mode', {detail: {enabled: true}})); })
    .catch(error => { $('hardwareStatus').textContent = 'Connection failed'; toast(error.message, true); });
  $('hardwareDisconnect').onclick = () => link.disconnect().catch(error => toast(error.message, true));
  $('developerMode').onchange = () => setDeveloperMode($('developerMode').checked).catch(error => { render(); toast(error.message, true); });
  $('functionSearch').oninput = renderLibrary;
  $('exportSequence').onclick = () => exportSequence().catch(error => toast(error.message, true));
  $('clearSequence').onclick = () => { sequence = []; renderLibrary(); };
  $('runSequence').onclick = () => runSelected().catch(error => toast(error.message, true));
  const stopIfBusy = () => { if (link.status().busy) link.stop().catch(error => toast(error.message, true)); };
  $('hardwareDialog').addEventListener('close', stopIfBusy); document.addEventListener('visibilitychange', () => { if (document.hidden) stopIfBusy(); }); window.addEventListener('pagehide', () => link.cleanup());

  // Serial monitor: a floating window that opens when a robot connects (Window → Serial monitor reopens it).
  const monitor = initSerialMonitor($('serialMonitor'), {link, offlineSend: command => control.press(command, 'Serial monitor')});
  shadow.connect('test').then(() => { shadowReady = true; monitor.attachPreview(shadow); }).catch(() => {});
  const serialWindow = $('serialWindow');
  const showSerial = visible => serialWindow.classList.toggle('hidden', !visible);
  let wasConnected = false;
  link.addEventListener('state', ({detail}) => {
    $('serialStatus').textContent = detail.connected ? (detail.testMode ? 'test mode · nothing is sent' : `connected · ${detail.transport === 'ble' ? 'Bluetooth BLE' : 'serial'}`) : 'simulator only · commands shown, not sent';
    if (detail.connected && !wasConnected) showSerial(true);
    wasConnected = detail.connected;
  });
  $('serialStatus').textContent = 'simulator only · commands shown, not sent';
  $('closeSerial').onclick = () => showSerial(false);
  $('openSerial').onclick = () => showSerial(serialWindow.classList.contains('hidden'));
  const head = serialWindow.querySelector('.serial-head');
  head.addEventListener('pointerdown', event => {
    if (event.target.closest('button')) return;
    const box = serialWindow.getBoundingClientRect(), dx = event.clientX - box.left, dy = event.clientY - box.top;
    head.setPointerCapture(event.pointerId);
    const move = e => { serialWindow.style.left = `${Math.max(0, Math.min(innerWidth - 120, e.clientX - dx))}px`; serialWindow.style.top = `${Math.max(0, Math.min(innerHeight - 40, e.clientY - dy))}px`; serialWindow.style.right = serialWindow.style.bottom = 'auto'; };
    const up = () => { head.removeEventListener('pointermove', move); head.removeEventListener('pointerup', up); };
    head.addEventListener('pointermove', move); head.addEventListener('pointerup', up);
  });
  window.addEventListener('bittle-toggle-serial', () => showSerial(serialWindow.classList.contains('hidden')));

  const publicApi = {status: link.status, sendCommand: link.sendCommand, cancel: control.stop, voiceAct, disconnect: link.disconnect, refreshMotions, mirrorServos, link};
  window.bittleHardware = publicApi; render(); renderLibrary(); refreshMotions().catch(error => toast(error.message, true)); return publicApi;
}
