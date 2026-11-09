import {createLink} from './link.js';
import {initConsole} from './console.js';
import {createLocalStore, downloadJson, listFolderPacks, loadFolderPack, pickJson, saveFolderPack} from './pack.js';
import {initSerialMonitor} from './serial-monitor.js';

// Standalone Bittle Link: the robot console without the simulator. Buttons and imported skills live in this browser;
// packs are shared through the repository's saved-motions/ folder.
const $ = id => document.getElementById(id);
let toastTimer;
function toast(message, error = false) { $('toast').textContent = message; $('toast').className = 'show' + (error ? ' error' : ''); clearTimeout(toastTimer); toastTimer = setTimeout(() => $('toast').className = '', 4500); }

const link = createLink();
const local = createLocalStore();

link.addEventListener('state', ({detail}) => {
  $('connect').disabled = detail.connected; $('disconnect').disabled = !detail.connected;
  $('transport').disabled = detail.connected;
  $('developerMode').checked = detail.developerMode;
});

async function importPack(pack, label) {
  const result = local.importPack(pack); await panel.reload(); $('importDialog').close();
  toast(`Imported ${label}: ${result.controls} buttons and ${result.skills} Studio functions.`);
}
async function openImport() {
  const list = $('packList'); list.replaceChildren(Object.assign(document.createElement('p'), {className: 'note', textContent: 'Loading…'}));
  $('importDialog').showModal();
  try {
    const packs = await listFolderPacks();
    list.replaceChildren(...(packs.length ? packs.map(pack => {
      const row = document.createElement('div'); row.className = 'pack-row';
      const text = document.createElement('div'); text.append(Object.assign(document.createElement('b'), {textContent: pack.name}), Object.assign(document.createElement('small'), {textContent: `${pack.file} · ${pack.controls} buttons · ${pack.skills} Studio functions`}));
      const button = Object.assign(document.createElement('button'), {type: 'button', className: 'primary', textContent: 'Import'});
      button.onclick = () => loadFolderPack(pack.file).then(data => importPack(data, pack.name)).catch(error => toast(error.message, true));
      row.append(text, button); return row;
    }) : [Object.assign(document.createElement('p'), {className: 'note', textContent: 'No packs yet. In Bittle Studio use Console → Save pack.'})]));
  } catch (error) { list.replaceChildren(Object.assign(document.createElement('p'), {className: 'note', textContent: `${error.message} Use “From another file…” instead.`})); }
}
async function savePack() {
  const name = prompt('Name for this pack (saved in the saved-motions folder; the same name replaces the older file):', 'bittle-link-buttons');
  if (!name) return;
  try { const saved = await saveFolderPack(name, local.exportPack()); toast(`Saved saved-motions/${saved.file}.`); }
  catch (error) { downloadJson(local.exportPack(), 'bittle-link-pack.json'); toast(`${error.message} Downloaded the pack instead.`, true); }
}

const panel = initConsole($('console'), {
  link, toast, offlineHint: 'Not connected. Choose a connection above and press Connect Bittle.',
  store: local.store, library: local.library,
  tools: [
    {label: 'Import pack', title: 'Load buttons and Studio functions from the saved-motions folder', onClick: openImport},
    {label: 'Save pack', title: 'Save these buttons and skills to the saved-motions folder', onClick: savePack},
  ],
});

$('importFile').onclick = () => pickJson().then(data => importPack(data, 'file')).catch(error => toast(error.message, true));
document.querySelectorAll('[data-close]').forEach(button => button.onclick = () => button.closest('dialog').close());
$('connect').onclick = () => link.connect($('transport').value).catch(error => toast(error.message, true));
$('disconnect').onclick = () => link.disconnect().catch(error => toast(error.message, true));
$('developerMode').onchange = () => link.setDeveloperMode($('developerMode').checked).catch(error => toast(error.message, true));
initSerialMonitor($('serialMonitor'), {link});
document.addEventListener('visibilitychange', () => { if (document.hidden && link.status().busy) link.stop().catch(() => {}); });
window.addEventListener('pagehide', () => link.cleanup());
