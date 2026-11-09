import {COLORS} from './console.js';

// A Bittle Link pack is the hand-off format between Bittle Studio and a standalone console:
// {format: 'bittle-link-pack', version: 1, controls: [...], skills: [{id, name, motion_type, type, signature, skill: int[]}]}
// Controls reference skills by id through {kind: 'motion', motion_id} steps, exactly as they do inside Studio.
export const PACK_FORMAT = 'bittle-link-pack';

export function validatePack(pack) {
  if (!pack || pack.format !== PACK_FORMAT || pack.version !== 1) throw new Error('This is not a Bittle Link pack.');
  const skills = (pack.skills || []).map(raw => {
    if (!raw?.id || !raw.name || !Array.isArray(raw.skill) || !raw.skill.length || raw.skill.some(value => !Number.isInteger(value) || value < -128 || value > 255)) throw new Error('The pack contains an invalid skill.');
    return {id: String(raw.id), name: String(raw.name).slice(0, 80), motion_type: raw.motion_type || raw.type || 'behavior', type: raw.type || raw.motion_type || 'behavior', signature: String(raw.signature || ''), hz: Number(raw.hz) || 20, skill: raw.skill};
  });
  const controls = validateControls(pack.controls || []);
  return {controls, skills};
}

// Mirrors server.py validate_controls so both hosts accept the same buttons.
export function validateControls(items) {
  if (!Array.isArray(items) || items.length > 60) throw new Error('The console holds at most 60 custom buttons.');
  const result = items.map(raw => {
    const name = String(raw?.name || '').trim().slice(0, 40);
    if (!name) throw new Error('Every console button needs a name.');
    if (!COLORS.includes(raw.color || 'green')) throw new Error('Unknown console button colour.');
    if (!Array.isArray(raw.steps) || raw.steps.length < 1 || raw.steps.length > 30) throw new Error(`“${name}” needs between 1 and 30 steps.`);
    const steps = raw.steps.map(step => {
      const wait_ms = Math.round(Number(step?.wait_ms) || 0);
      if (wait_ms < 0 || wait_ms > 60000) throw new Error('Step waits must be 0–60000 ms.');
      if (step.kind === 'command') {
        const command = String(step.command || '').trim();
        if (!command || command.length > 64 || /[^\x20-\x7e]/.test(command)) throw new Error(`“${name}” has a command that is not a printable Petoi command.`);
        return {kind: 'command', command, wait_ms};
      }
      if (step.kind === 'motion' && step.motion_id) return {kind: 'motion', motion_id: String(step.motion_id).slice(0, 64), wait_ms};
      throw new Error(`“${name}” contains an invalid step.`);
    });
    return {id: String(raw.id || crypto.randomUUID().replaceAll('-', '')).slice(0, 64), name, color: raw.color || 'green', repeat: !!raw.repeat, steps};
  });
  if (new Set(result.map(item => item.id)).size !== result.length) throw new Error('Console button IDs must be unique.');
  return result;
}

// Browser storage for the standalone console: buttons plus the skill library imported from packs.
export function createLocalStore(key = 'bittle-link') {
  const read = () => { try { return JSON.parse(localStorage.getItem(key)) || {}; } catch { return {}; } };
  const write = data => { try { localStorage.setItem(key, JSON.stringify(data)); } catch { throw new Error('This browser blocked local storage, so buttons cannot be kept.'); } };
  let skills = read().skills || [];
  return {
    store: {
      async load() { return read().controls || []; },
      async save(controls) { const clean = validateControls(controls); write({...read(), controls: clean}); return clean; },
    },
    library: {
      list: () => skills,
      async resolve(id) { const entry = skills.find(item => item.id === id); if (!entry) throw new Error('This button uses a Studio function that is not in the imported pack.'); return entry; },
    },
    importPack(pack, {replace = false} = {}) {
      const incoming = validatePack(pack), current = read();
      const byId = new Map((replace ? [] : current.skills || []).map(item => [item.id, item]));
      incoming.skills.forEach(item => byId.set(item.id, item));
      const controlsById = new Map((replace ? [] : current.controls || []).map(item => [item.id, item]));
      incoming.controls.forEach(item => controlsById.set(item.id, item));
      skills = [...byId.values()];
      write({controls: [...controlsById.values()], skills});
      return {controls: incoming.controls.length, skills: incoming.skills.length};
    },
    exportPack() { const data = read(); return {format: PACK_FORMAT, version: 1, exported: new Date().toISOString(), controls: data.controls || [], skills: data.skills || []}; },
  };
}

// The saved-motions/ folder, served by both Bittle Studio and the Bittle Link launcher (read-only on GitHub Pages).
// Relative URLs so the console also works below a sub-path such as https://<user>.github.io/<repo>/bittle-link/.
async function folderRequest(url, options) {
  const response = await fetch(url, options);
  let data; try { data = await response.json(); } catch { data = {}; }
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'The saved-motions folder is not available.');
  return data;
}
export const listFolderPacks = async () => (await folderRequest('saved-motions/index.json')).packs;
export const loadFolderPack = file => folderRequest(`saved-motions/${encodeURIComponent(file)}`);
export const saveFolderPack = (name, pack) => folderRequest('saved-motions/save', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({name, pack})});

export function downloadJson(data, filename) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 1)], {type: 'application/json'}));
  const a = Object.assign(document.createElement('a'), {href: url, download: filename}); a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export function pickJson() {
  return new Promise((resolve, reject) => {
    const input = Object.assign(document.createElement('input'), {type: 'file', accept: '.json,application/json'});
    input.onchange = async () => { try { resolve(JSON.parse(await input.files[0].text())); } catch { reject(new Error('That file is not valid JSON.')); } };
    input.click();
  });
}
