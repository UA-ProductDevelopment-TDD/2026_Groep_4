import {catalog} from './catalog.js';
import {COMMAND_NAMES} from './console.js';

// Serial monitor: every command sent to the robot (with the control that sent it and a plain-language meaning)
// and the robot's replies, grouped under the command that caused them. Works with any createLink() object.

const SKILL_NAMES = Object.fromEntries(catalog.map(item => [item.code, item.label]));
const WALKING = new Set(catalog.filter(item => item.walking).map(item => item.code));
const MAX_ROWS = 600;

const pairsText = list => {
  const values = list.trim().split(/\s+/).map(Number);
  const pairs = [];
  for (let i = 0; i + 1 < values.length; i += 2) pairs.push(`servo ${values[i]} → ${values[i + 1]}°`);
  return pairs.join(', ');
};

// Plain-language meaning of an OpenCat command (OpenCatEsp32 OpenCat.h token list).
export function explainCommand(text) {
  const t = String(text).trim();
  if (t.startsWith('K upload')) return 'upload a custom skill (binary K packet); the robot runs it and keeps it for T';
  if (/^I · \d+ joints$/.test(t)) return `move ${t.match(/\d+/)[0]} joints at once (binary I packet)`;
  if (/^k\w+/.test(t)) {
    const code = t.split(/\s+/)[0];
    const name = COMMAND_NAMES[code] || SKILL_NAMES[code] || code.slice(1);
    return `skill: ${name}${WALKING.has(code) || /^k\w+[FLR]$/.test(code) && WALKING.has(code.slice(0, -1) + 'F') ? ' (keeps going until stopped)' : ''}`;
  }
  if (t === 'i') return 'hand the head back to the firmware';
  if (/^i\s*-?\d/.test(t)) return `move joints together: ${pairsText(t.slice(1))}`;
  if (/^m\s*-?\d/.test(t)) return `move joints one after another: ${pairsText(t.slice(1))}`;
  if (t === 'd') return 'rest pose, then every servo off';
  if (/^d\s*\d+$/.test(t)) return `servo ${t.slice(1).trim()} off`;
  if (t === 'f') return 'measure the real servo positions (servos go soft)';
  if (t === 'fp') return 'measure and print the servo positions once (USB only)';
  if (t === 'fP') return 'measure and print the servo positions continuously (USB only)';
  if (t === 'j') return 'report the joint angle list (reply: =, servo numbers, angles)';
  if (t === ':') return 'servos stiff: full holding force';
  if (t === ';') return 'servos soft';
  if (t === 'gb') return 'gyro / balance assistance off';
  if (t === 'gB') return 'gyro / balance assistance on';
  if (t === 'g') return 'toggle gyro / balance assistance';
  if (t === 'XAc') return 'voice module on (reply tone and reactions)';
  if (t === 'XAd') return 'voice module off';
  if (t === 'T') return 'replay the last uploaded skill';
  if (t === 'p') return 'pause';
  if (t === 'u') return 'meow';
  if (t === '.') return 'play skills faster (switched off in current OpenCat firmware)';
  if (t === ',') return 'play skills slower (switched off in current OpenCat firmware)';
  if (t === 'P') return 'report the battery voltage';
  if (t === 'z') return 'toggle random behaviours';
  if (t === '?') return 'ask the firmware version';
  if (t === '!') return 'reset the robot';
  if (/^n\S/.test(t)) return `rename the Bluetooth device to “${t.slice(1)}”`;
  if (/^b/.test(t)) return 'beep / melody';
  if (/^t\s*\d/.test(t)) return 'tilt the body';
  return '';
}

function explainReply(text) {
  if (/Undefined token/.test(text)) return 'the robot did not recognise a command';
  if (text === '=') return 'start of a joint report';
  if (/^\d+(\t\d+){15}\t?$/.test(text)) return 'servo numbers 0–15';
  if (/^-?\d+,\t?(-?\d+,\t?){15}$/.test(text)) return 'joint angles for servos 0–15 (degrees)';
  if (text === 'G' || text === 'g') return text === 'G' ? 'gyro on' : 'gyro off';
  return '';
}

// preview links (attachPreview) are simulator-only test-mode links: their rows show what a robot would receive.
// offlineSend(command) handles typed commands while no robot is connected (Studio: play them in the simulator).
export function initSerialMonitor(root, {link, sendOrigin = 'Serial monitor', offlineSend = null}) {
  root.classList.add('serial-monitor');
  root.innerHTML = `
<div class="sm-toolbar"><select data-sm="filter" aria-label="Show"><option value="all">All</option><option value="tx">Sent</option><option value="rx">Received</option></select>
<label class="check"><input data-sm="scroll" type="checkbox" checked> Auto-scroll</label><span class="sm-spacer"></span>
<button type="button" data-sm="copy" title="Copy the log as text">Copy</button><button type="button" data-sm="clear">Clear</button></div>
<div data-sm="list" class="sm-list" role="log" aria-live="off"><p class="sm-empty">Commands sent to the robot and its replies appear here.</p></div>
<form data-sm="form" class="sm-input"><input data-sm="command" maxlength="64" autocomplete="off" placeholder="Type a Petoi command, e.g. ksit · m 0 30 · j" aria-label="Petoi command"><button>Send</button></form>`;
  const part = name => root.querySelector(`[data-sm="${name}"]`);
  const list = part('list');
  let rxBuffer = '', rxTimer = null, history = [], historyIndex = -1;
  const stamp = () => { const d = new Date(); return d.toLocaleTimeString([], {hour12: false}) + '.' + String(d.getMilliseconds()).padStart(3, '0'); };

  function add(row) {
    list.querySelector('.sm-empty')?.remove();
    row.classList.toggle('sm-hidden', !visible(row));
    list.append(row);
    while (list.children.length > MAX_ROWS) list.firstElementChild.remove();
    if (part('scroll').checked) list.scrollTop = list.scrollHeight;
  }
  const visible = row => part('filter').value === 'all' || row.classList.contains(`sm-${part('filter').value}`);
  function row(kind, text, origin, meaning, preview = false) {
    const element = document.createElement('div'); element.className = `sm-row sm-${kind}${preview ? ' sm-preview' : ''}`;
    const time = Object.assign(document.createElement('time'), {textContent: stamp()});
    const arrow = Object.assign(document.createElement('span'), {className: 'sm-arrow', textContent: kind === 'tx' ? '→' : '←'});
    const code = Object.assign(document.createElement('code'), {textContent: text});
    element.append(time, arrow, code);
    if (origin) element.append(Object.assign(document.createElement('span'), {className: 'sm-origin', textContent: origin}));
    if (preview) element.append(Object.assign(document.createElement('span'), {className: 'sm-badge', textContent: kind === 'tx' ? 'simulator only · not sent' : 'simulated reply'}));
    if (meaning) element.append(Object.assign(document.createElement('div'), {className: 'sm-meaning', textContent: meaning}));
    return element;
  }
  function flushRx(force, preview = false) {
    const lines = rxBuffer.split(/\r?\n/);
    rxBuffer = force ? '' : lines.pop();
    for (const line of lines.map(l => l.replace(/\s+$/, '')).filter(Boolean)) add(row('rx', line.replace(/,?\t/g, m => m === ',\t' ? ', ' : '  '), '', explainReply(line), preview));
  }

  link.addEventListener('tx', ({detail}) => add(row('tx', detail.text, detail.origin || 'Command', explainCommand(detail.text))));
  link.addEventListener('rx', ({detail}) => {
    rxBuffer += detail.text; flushRx(false);
    clearTimeout(rxTimer); rxTimer = setTimeout(() => flushRx(true), 250);  // replies without a trailing newline
  });
  // What the link says the user should see, such as how a streamed function kept time.
  link.addEventListener('note', ({detail}) => {
    const note = document.createElement('div'); note.className = 'sm-row sm-note';
    note.textContent = `${stamp()}  ${detail.message}`;
    add(note);
  });
  root.dataset.connected = String(link.connected);
  link.addEventListener('state', ({detail}) => {
    const now = String(detail.connected);
    if (root.dataset.connected === now) return;
    root.dataset.connected = now;
    const note = document.createElement('div'); note.className = 'sm-row sm-note';
    note.textContent = `${stamp()}  ${detail.connected ? `connected (${detail.testMode ? 'test mode: nothing is sent' : detail.transport})` : 'disconnected'}`;
    add(note);
  });

  part('filter').onchange = () => [...list.querySelectorAll('.sm-row')].forEach(r => r.classList.toggle('sm-hidden', !visible(r)));
  part('clear').onclick = () => { list.replaceChildren(); };
  part('copy').onclick = async () => {
    const text = [...list.querySelectorAll('.sm-row:not(.sm-hidden)')].map(r => r.innerText.replace(/\n+/g, '   ')).join('\n');
    try { await navigator.clipboard.writeText(text); part('copy').textContent = 'Copied'; setTimeout(() => part('copy').textContent = 'Copy', 1200); } catch { /* clipboard blocked */ }
  };
  part('form').onsubmit = event => {
    event.preventDefault();
    const input = part('command'), value = input.value.trim();
    if (!value) return;
    if (!link.connected) {
      if (offlineSend) Promise.resolve(offlineSend(value)).catch(error => add(row('rx', error.message, '', 'not sent')));
      else add(row('tx', value, sendOrigin, 'not sent: connect a robot first'));
      history = [value, ...history.filter(item => item !== value)].slice(0, 30); historyIndex = -1; part('command').value = '';
      return;
    }
    Promise.resolve(link.sendCommand(value, sendOrigin)).catch(error => add(row('rx', error.message, '', 'not sent')));
    history = [value, ...history.filter(item => item !== value)].slice(0, 30); historyIndex = -1; input.value = '';
  };
  part('command').onkeydown = event => {
    if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return;
    event.preventDefault();
    historyIndex = Math.max(-1, Math.min(history.length - 1, historyIndex + (event.key === 'ArrowUp' ? 1 : -1)));
    part('command').value = historyIndex < 0 ? '' : history[historyIndex];
  };
  function attachPreview(source) {
    let buffer = '', timer = null;
    source.addEventListener('tx', ({detail}) => add(row('tx', detail.text, detail.origin || 'Command', explainCommand(detail.text), true)));
    source.addEventListener('rx', ({detail}) => {
      // Keep simulated replies apart from the real robot's buffer.
      const own = rxBuffer; rxBuffer = buffer + detail.text; flushRx(false, true); buffer = rxBuffer; rxBuffer = own;
      clearTimeout(timer); timer = setTimeout(() => { const keep = rxBuffer; rxBuffer = buffer; flushRx(true, true); buffer = ''; rxBuffer = keep; }, 250);
    });
    list.querySelector('.sm-empty')?.replaceChildren('No robot connected: the commands your actions would send appear here, marked “simulator only”. Typed commands play in the simulator.');
  }
  return {clear: () => list.replaceChildren(), attachPreview};
}
