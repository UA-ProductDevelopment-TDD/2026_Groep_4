// Bittle Link: transport and OpenCat protocol for a Petoi Bittle, with no DOM or Studio dependencies.
// Transports: 'ble' (Nordic UART over Web Bluetooth), 'serial' (Web Serial, 115200 baud) and 'test' (logs only).
// Events: 'log' {kind, message}, 'note' {message} (what the user should see, such as how a stream kept time),
// 'state' {connected, transport, testMode, busy, developerMode, message}, 'rx' {text} (raw replies),
// 'joints' {angles: [16 servo angles]} whenever the robot reports its joint list (the j command),
// 'tx' {text, origin, binary, size} for every write that reached the robot (origin = the control that sent it).
// Console macros handled by press(): 'fp' read real positions once, 'fP' keep reading, '#on' switch the servos back on,
// '#random-on' / '#random-off' set the random behaviours, which the firmware only toggles.

const SERVICE = '6e400001-b5a3-f393-e0a9-e50e24dcca9e';
const RX = '6e400002-b5a3-f393-e0a9-e50e24dcca9e';
const TX = '6e400003-b5a3-f393-e0a9-e50e24dcca9e';
const BLE_PACKET = 20;
const STOPPED = 'Motion stopped.';
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

// Pack [servo, angle] pairs into as few "i servo angle ..." text commands as fit in one BLE packet each.
export function packJointCommands(entries, limit = BLE_PACKET - 1) {
  const commands = []; let current = 'i';
  for (const [servo, angle] of entries) {
    const pair = ` ${servo} ${Math.round(angle)}`;
    if (current !== 'i' && (current + pair).length > limit) { commands.push(current); current = 'i'; }
    current += pair;
  }
  if (current !== 'i') commands.push(current);
  return commands;
}

export function createLink() {
  const events = new EventTarget();
  let connected = false, mode = '', device, characteristic, notifications;
  let port, writer, reader, readTask, closing = false, busy = false, epoch = 0, message = '';
  let writeQueue = Promise.resolve(), uploadedSignature = '', developerMode = true;
  let rxLine = '', lastJoints = null;  // last joint list reported by the robot (servo degrees, index = servo number)
  let randomMind = null;  // whether the robot's random behaviours are on, as its last answer to z said (Z on, z off)
  // Custom skills play either streamed (frames sent as binary I packets, starts at once) or uploaded (one K packet).
  let playbackMode = 'stream', streamId = 0, lastStreamed = null;
  try { playbackMode = localStorage.getItem('bittle-playback') === 'upload' ? 'upload' : 'stream'; } catch {}

  const emit = (type, detail) => events.dispatchEvent(new CustomEvent(type, {detail}));
  const log = (kind, text) => emit('log', {kind, message: text});
  const note = text => { log('INFO', text); emit('note', {message: text}); };
  const status = () => ({connected, transport: mode, testMode: mode === 'test', busy, developerMode, message});
  const setState = (text = '') => { message = text; emit('state', status()); };
  const guard = () => { if (!connected) throw new Error('Connect Bittle first.'); };

  function receivedText(text) {
    if (!text) return;
    emit('rx', {text}); log('RX', text.replace(/[\r\n]+/g, ' ').trim());
    // The j report ends with a line of 16 angles separated by ',<tab>' (tools.h list2String).
    rxLine = (rxLine + text).slice(-2000);
    const lines = rxLine.split(/\r?\n/); rxLine = lines.pop();
    for (const line of lines) {
      if (line.trim() === 'Z' || line.trim() === 'z') randomMind = line.trim() === 'Z';
      const values = line.split(',').map(v => v.trim()).filter(Boolean);
      if (values.length === 16 && values.every(v => /^-?\d+$/.test(v))) { lastJoints = values.map(Number); emit('joints', {angles: lastJoints}); }
    }
  }
  function received(event) { receivedText(new TextDecoder().decode(event.target.value)); }
  function lost() { epoch++; connected = busy = false; log('INFO', 'Bluetooth connection lost'); setState('Connection lost'); }
  async function readSerial() {
    const decoder = new TextDecoder();
    try { while (reader) { const {value, done} = await reader.read(); if (done) break; receivedText(decoder.decode(value, {stream: true})); } }
    catch (error) { if (!closing) log('ERROR', error.message); }
  }
  async function cleanup() {
    if (device) { device.removeEventListener('gattserverdisconnected', lost); notifications?.removeEventListener('characteristicvaluechanged', received); if (device.gatt.connected) device.gatt.disconnect(); device = characteristic = notifications = null; }
    if (reader) { try { await reader.cancel(); } catch {} try { reader.releaseLock(); } catch {} reader = null; }
    if (readTask) { try { await readTask; } catch {} readTask = null; }
    if (writer) { try { writer.releaseLock(); } catch {} writer = null; }
    if (port) { try { await port.close(); } catch {} port = null; }
  }

  // Connection. The browser device/port picker needs a user gesture, so call connect() from a click handler.
  async function connect(transport = 'ble') {
    await cleanup(); uploadedSignature = ''; mode = transport;
    if (mode === 'test') { connected = true; log('TEST', 'Connection simulation started'); }
    else if (mode === 'serial') {
      if (!navigator.serial) throw new Error('Web Serial is unavailable. Use Chrome or Edge on http://127.0.0.1 or https.');
      port = await navigator.serial.requestPort(); await port.open({baudRate: 115200}); writer = port.writable.getWriter(); reader = port.readable.getReader(); connected = true; readTask = readSerial(); log('INFO', 'Serial connection opened');
    } else if (mode === 'ble') {
      if (!navigator.bluetooth) throw new Error('Web Bluetooth is unavailable. Use Chrome or Edge on http://127.0.0.1 or https.');
      device = await navigator.bluetooth.requestDevice({filters: [{services: [SERVICE]}, {namePrefix: 'Bittle'}, {namePrefix: 'Petoi'}], optionalServices: [SERVICE]});
      device.addEventListener('gattserverdisconnected', lost); const server = await device.gatt.connect(); const service = await server.getPrimaryService(SERVICE);
      characteristic = await service.getCharacteristic(RX); notifications = await service.getCharacteristic(TX); notifications.addEventListener('characteristicvaluechanged', received); await notifications.startNotifications(); connected = true; log('INFO', `BLE connected: ${device.name || 'Bittle'}`);
    } else throw new Error(`Unknown transport “${transport}”.`);
    setState(mode === 'test' ? 'Test mode · nothing is sent to hardware' : `Connected · ${mode}`);
    if (developerMode) await sendCommand('gb', 'Developer mode (on connect)');
  }
  async function disconnect() { epoch++; busy = false; uploadedSignature = ''; closing = true; try { await cleanup(); } finally { closing = false; connected = false; log('INFO', 'Disconnected'); setState('Not connected'); } }

  // Raw writes are queued so commands, poses and skill uploads never interleave.
  function writeBytes(bytes, description, origin = '', binary = false) {
    const task = writeQueue.then(async () => {
      guard();
      if (mode === 'test') { log('TEST', description); emit('tx', {text: description, origin, binary, size: bytes.length}); testReply(bytes); return; }
      if (characteristic) {
        for (let offset = 0; offset < bytes.length; offset += BLE_PACKET) {
          const chunk = bytes.slice(offset, offset + BLE_PACKET);
          if (characteristic.properties.write) await characteristic.writeValueWithResponse(chunk); else await characteristic.writeValueWithoutResponse(chunk);
          if (bytes.length > BLE_PACKET) await sleep(3);
        }
      } else await writer.write(bytes);
      log('TX', description);
      emit('tx', {text: description, origin, binary, size: bytes.length});
    });
    writeQueue = task.catch(() => {}); return task;
  }
  // Test mode answers j like the firmware (=, servo numbers, 16 angles), using the angles it was sent with i/m.
  const testAngles = new Array(16).fill(0);
  let testRandom = true;  // OpenCat starts with its random behaviours on
  function testReply(bytes) {
    const text = new TextDecoder().decode(bytes).trim();
    const joints = text.match(/^[im]\s*(-?\d+(?:\s+-?\d+)*)$/);
    if (joints) { const v = joints[1].split(/\s+/).map(Number); for (let i = 0; i + 1 < v.length; i += 2) if (v[i] >= 0 && v[i] < 16) testAngles[v[i]] = v[i + 1]; }
    if (text === 'z') { testRandom = !testRandom; setTimeout(() => receivedText(testRandom ? 'Z\r\n' : 'z\r\n'), 30); }
    if (text === 'j') setTimeout(() => receivedText(`=\r\n${[...Array(16).keys()].join('\t')}\t\r\n${testAngles.join(',\t')},\t\r\n`), 30);
  }
  function sendCommand(command, origin = '') {
    const value = String(command).trim();
    if (!value || value.length > 64 || /[^\x20-\x7e]/.test(value)) throw new Error('Enter a printable Petoi command up to 64 characters.');
    const bytes = new TextEncoder().encode(value + '\n');
    if (characteristic && bytes.length > BLE_PACKET) throw new Error('BLE text commands may contain at most 19 characters.');
    return writeBytes(bytes, value, origin);
  }
  function sendPose(values, origin = '') {
    if (!Array.isArray(values) || !values.length || values.length % 2) throw new Error('Invalid servo pose.');
    const packet = new Uint8Array(values.length + 2); packet[0] = 'I'.charCodeAt(0);
    values.forEach((value, index) => { if (!Number.isInteger(value) || value < -128 || value > 127) throw new Error('Servo values must fit signed bytes.'); packet[index + 1] = value & 255; });
    packet[packet.length - 1] = '~'.charCodeAt(0);
    if (characteristic && packet.length > BLE_PACKET) throw new Error('A BLE pose packet may contain at most 9 joints.');
    return writeBytes(packet, `I · ${values.length / 2} joints`, origin, true);
  }
  function uploadSkill(values, description, origin = description) {
    const packet = new Uint8Array(values.length + 2); packet[0] = 'K'.charCodeAt(0);
    values.forEach((value, index) => packet[index + 1] = value & 255); packet[packet.length - 1] = '~'.charCodeAt(0);
    return writeBytes(packet, `K upload · ${description} · ${packet.length} bytes`, origin, true);
  }

  // Streaming: decode the skill's frames (16 servo angles each, as in the K packet built by motion_export.py) and send
  // the walking servos of every frame as one binary I packet on a fixed time grid. Gaits loop until another command.
  const STREAM_SERVOS = [0, 8, 9, 10, 11, 12, 13, 14, 15];  // head + 8 legs: 9 pairs fill one 20-byte BLE packet
  function skillFrames(skill) {
    if (skill[0] === 1) return [skill.slice(4, 20)];
    const frames = [];
    for (let i = 0; i < -skill[0]; i++) frames.push(skill.slice(7 + i * 20, 7 + i * 20 + 16));
    return frames;
  }
  function cancelStream() { streamId++; }
  function streamSkill(entry, name, origin) {
    const frames = skillFrames(entry.skill), hz = Math.max(1, Math.min(50, Number(entry.hz) || 20));
    const id = ++streamId, token = epoch, interval = 1000 / hz, live = () => id === streamId && token === epoch && connected;
    lastStreamed = {entry, name};
    log('INFO', `${name} streamed: ${frames.length} frame${frames.length === 1 ? '' : 's'} at ${hz} Hz${entry.type === 'gait' ? ', looping' : ''}`);
    // Real time, as the simulator plays it: each send is the frame due now, and the frames a slow link could not carry
    // in time are skipped rather than queued, so the robot never falls behind; a motion ends on its last frame.
    const send = frame => sendPose(STREAM_SERVOS.flatMap(servo => [servo, frame[servo]]), origin);
    const loop = entry.type === 'gait', start = performance.now();
    let sent = 0, next = 0, skipped = 0;
    const report = () => {
      const seconds = (performance.now() - start) / 1000, carried = seconds > 0 ? sent / seconds : hz;
      if (!skipped) return note(`${name}: ${sent} frames at ${hz} Hz, on time`);
      note(`${name}: asked ${hz} Hz, the link carried ${carried.toFixed(0)} Hz; ${skipped} of ${skipped + sent} frames skipped to stay on time. `
        + 'Lower the function\'s Hz, or use USB, to send every frame.');
    };
    const run = async () => {
      while (live()) {
        // Always through a timer, even when late, so that Stop and everything else still get their turn.
        await sleep(Math.max(0, start + next * interval - performance.now()));
        if (!live()) break;
        // The frame for now: those whose time passed while the last one was being sent are skipped.
        const now = Math.max(next, Math.floor((performance.now() - start) / interval));
        if (!loop && now >= frames.length) {
          if (next < frames.length) { skipped += frames.length - 1 - next; await send(frames[frames.length - 1]); sent++; }
          break;
        }
        skipped += now - next;
        await send(frames[now % frames.length]); sent++;
        next = now + 1;
      }
      report();
    };
    return run().catch(error => log('ERROR', error.message));
  }

  // Firmware skills: {skill: int[], signature, type, hz}. In upload mode the last uploaded skill lives in the firmware's
  // T slot, so an unchanged skill is recalled with a single 'T' instead of a full upload.
  async function sendSkill(entry, name = 'Custom skill', origin = name) {
    guard();
    if (playbackMode === 'stream') { streamSkill(entry, name, origin); return; }
    const {skill, signature} = entry;
    if (signature && uploadedSignature === signature) { await sendCommand('T', origin); log('INFO', `${name} recalled instantly with T`); }
    else { await uploadSkill(skill, name, origin); uploadedSignature = signature || ''; log('INFO', `${name} stored as the firmware's T skill`); }
  }
  async function runSkill(entry, name) {
    busy = true; setState(`Starting ${name}…`);
    try { await sendSkill(entry, name); busy = entry.type === 'gait'; setState(busy ? `${name} running in firmware · press Stop` : `${name} started in firmware`); }
    catch (error) { busy = false; setState(); throw error; }
  }

  // Cancellation: every new action bumps the epoch; long-running loops abort when their token is stale.
  function wait(ms, token) { return new Promise((resolve, reject) => { const started = performance.now(); const tick = () => { if (!connected || token !== epoch) return reject(new Error(STOPPED)); if (performance.now() - started >= ms) return resolve(); setTimeout(tick, Math.min(20, ms)); }; tick(); }); }
  // Servo feedback is printed to USB only, but it also updates the joint list that j reports on every port.
  // Plain f (not fp) marks a measurement as running, so the firmware re-attaches the servos (which reading detaches)
  // on the next command: here the j that reports the result (OpenCatEsp32 reaction.h / espServo.h).
  async function readPositions(token, origin = 'Read positions') { await sendCommand('f', origin); await wait(350, token); await sendCommand('j', origin); }
  async function livePositions(token) {
    try { while (token === epoch && connected) { await readPositions(token, 'Live positions'); await wait(250, token); } }
    catch (error) { if (error.message !== STOPPED) log('ERROR', error.message); }
  }
  async function motorsOn(origin = 'Motors on') {
    // The firmware has no 'servos on' command: ':' restores full servo stiffness (reading leaves them soft), and
    // commanding the last read positions powers them on where the legs are.
    await sendCommand(':', origin);
    if (!lastJoints) { log('INFO', 'No positions read yet: motors on via kbalance'); return sendCommand('kbalance', origin); }
    const pairs = [0, 8, 9, 10, 11, 12, 13, 14, 15].map(servo => [servo, lastJoints[servo]]);
    for (const command of packJointCommands(pairs)) await sendCommand(command, origin);
    log('INFO', 'Motors on, holding the last read positions');
  }
  async function setRandom(on, token, origin) {
    // OpenCat has no 'on' or 'off' for its random behaviours, only z, which toggles them and answers Z when they are now
    // on, z when off: send z, and once more when the answer is the other way.
    for (let attempt = 0; attempt < 2; attempt++) {
      randomMind = null;
      await sendCommand('z', origin);
      const started = performance.now();
      while (randomMind === null && performance.now() - started < 1000) await wait(20, token);
      if (randomMind === null) { note('No answer to z: this firmware may have no random behaviours (RANDOM_MIND)'); return; }
      if (randomMind === on) { log('INFO', on ? 'Random behaviours on' : 'Random behaviours off'); return; }
    }
  }
  async function press(command, origin = '') {
    guard(); epoch++; busy = false;
    const token = epoch;
    if (command === 'fp') { await readPositions(token, origin || 'Read positions').catch(error => { if (error.message !== STOPPED) throw error; }); setState(); return; }
    if (command === 'fP') { livePositions(token); setState('Reading positions live · press any command to stop'); return; }
    if (command === '#on') { await motorsOn(origin || 'Motors on'); setState(); return; }
    if (command === '#random-on' || command === '#random-off') {
      await setRandom(command === '#random-on', token, origin || 'Random behaviours').catch(error => { if (error.message !== STOPPED) throw error; });
      setState(); return;
    }
    // In stream mode nothing was uploaded, so 'play last skill' streams the last custom skill again.
    if (command === 'T' && playbackMode === 'stream' && lastStreamed) { streamSkill(lastStreamed.entry, lastStreamed.name, origin || 'Play last skill'); setState(); return; }
    await sendCommand(command, origin);
    // gb/gB are developer mode's own commands, so keep its state truthful when they are sent directly.
    if (command === 'gb' || command === 'gB') { developerMode = command === 'gb'; log('INFO', developerMode ? 'Gyro off · developer mode on' : 'Gyro on · developer mode off'); }
    setState();
  }
  // CRAB: play a skill array on the robot from its own memory (K upload; an unchanged skill is recalled with T).
  // Crab Studio uses this for the crab commands, so the robot plays the crab skill even when its firmware still has
  // the dog's skill table. Gaits keep looping on the robot until the next command.
  async function playFirmware(entry, name, origin = name) {
    guard(); epoch++; cancelStream(); busy = false;
    const {skill, signature} = entry;
    if (signature && uploadedSignature === signature) await sendCommand('T', origin);
    else { await uploadSkill(skill, name, origin); uploadedSignature = signature || ''; }
    busy = entry.type === 'gait';
    setState(busy ? `${name} running on the robot · press Stop` : `${name} sent to the robot`);
  }
  async function stopWith(entry) {
    epoch++; cancelStream(); busy = false; setState('Stopping…');
    if (connected) await playFirmware(entry, entry.name || 'Stop', 'Stop');
    setState(connected ? 'Connected · motion stopped' : 'Not connected');
  }
  async function stop() { epoch++; const active = busy; busy = false; setState('Stopping…'); if (connected) await sendCommand('kbalance', 'Stop'); setState(connected ? 'Connected · motion stopped' : 'Not connected'); if (active) log('INFO', 'Motion stopped; balance pose requested'); }
  async function setDeveloperMode(enabled) { if (busy) await stop(); developerMode = !!enabled; if (connected) await sendCommand(enabled ? 'gb' : 'gB', 'Developer mode'); log('INFO', enabled ? 'Developer mode enabled' : 'Developer mode disabled'); setState(); }

  // Composed buttons: {name, repeat, steps: [{kind: 'command', command, wait_ms} | {kind: 'motion', motion_id, wait_ms}]}.
  // resolveMotion(motion_id) must return a firmware skill entry {skill, signature, type, hz, name}.
  async function runSequence(item, {resolveMotion, onStep} = {}) {
    guard();
    const token = ++epoch; busy = false; setState(`Running ${item.name}`); log('INFO', `Composed skill “${item.name}” started`);
    try {
      do {
        for (const [index, step] of item.steps.entries()) {
          if (token !== epoch || !connected) throw new Error(STOPPED);
          onStep?.(index);
          if (step.kind === 'command') { cancelStream(); await sendCommand(step.command, `${item.name} · step ${index + 1}`); }
          else {
            if (!resolveMotion) throw new Error('This console cannot run Studio functions.');
            const entry = await resolveMotion(step.motion_id);
            if (token !== epoch) throw new Error(STOPPED);
            await sendSkill(entry, entry.name || 'Studio function');
          }
          if (step.wait_ms) await wait(step.wait_ms, token);
        }
      } while (item.repeat && token === epoch && connected);
      log('INFO', `Composed skill “${item.name}” finished`);
    } catch (error) { if (error.message !== STOPPED) throw error; log('INFO', `Composed skill “${item.name}” interrupted`); }
    finally { onStep?.(-1); if (token === epoch) setState(connected ? 'Connected · ready' : 'Not connected'); }
  }

  Object.assign(events, {
    status, connect, disconnect, cleanup, sendCommand, sendPose, uploadSkill, sendSkill, runSkill,
    press, stop, stopWith, playFirmware, setDeveloperMode, runSequence,
    markBusy(value, text) { busy = !!value; setState(text); },
    cancelStream,
  });
  const setPlaybackMode = mode => { playbackMode = mode === 'upload' ? 'upload' : 'stream'; try { localStorage.setItem('bittle-playback', playbackMode); } catch {} log('INFO', `Custom skills will be ${playbackMode === 'stream' ? 'streamed frame by frame' : 'uploaded as one skill'}`); };
  // Live getters (Object.assign would copy their current values instead).
  return Object.defineProperties(events, {connected: {get: () => connected}, developerMode: {get: () => developerMode}, lastJoints: {get: () => lastJoints},
    playbackMode: {get: () => playbackMode, set: setPlaybackMode}});
}
