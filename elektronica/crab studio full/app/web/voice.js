const $ = id => document.getElementById(id);
let pc, dc, mic, active = false, starting = false, responseActive = false, muted = false;

function setStatus(text) { $('voiceStatus').textContent = text; }
function fail(error) { $('voiceError').textContent = error?.message || String(error); }
function hardware() { return window.bittleHardware; }
function controls() {
  const ready = !!hardware(), blocked = ready && hardware().status().developerMode;
  $('voiceStart').disabled = !ready || active || starting || blocked;
  $('voiceEnd').disabled = !active && !starting; $('voiceMute').disabled = !active || !mic;
  $('voiceText').disabled = !active; $('voiceTextSend').disabled = !active;
  if (blocked) setStatus('Blocked by developer mode');
}
function line(role, text) { const p = document.createElement('p'); p.className = role; p.textContent = `${role === 'you' ? 'You' : role === 'bobby' ? 'Bobby' : 'Robot'}: ${text}`; $('voiceTranscript').append(p); $('voiceTranscript').scrollTop = $('voiceTranscript').scrollHeight; }
function emit(value) { if (dc?.readyState === 'open') dc.send(JSON.stringify(value)); }
function context() { return hardware()?.status() || {connected: false, developerMode: false}; }
function response() { if (!responseActive) { responseActive = true; emit({type: 'response.create'}); } }
function message(text) { if (!active || !text.trim()) return; line('you', text); emit({type: 'conversation.item.create', item: {type: 'message', role: 'user', content: [{type: 'input_text', text: `${text}\nCurrent app status: ${JSON.stringify(context())}`}]}}); response(); }

async function callApi(path, options = {}) {
  const result = await fetch(path, options); if (!result.ok) { let data = {}; try { data = await result.json(); } catch {} throw new Error(data.detail || 'Voice service request failed.'); } return result;
}
async function config() { const data = await (await callApi('/api/voice/config')).json(); $('voiceSettings').open = !data.configured; setStatus(data.configured ? `Ready · ${data.model}` : 'Add an OpenAI API key to enable voice'); return data; }

async function handleEvent(event) {
  if (event.type === 'error') { fail(new Error(event.error?.message || 'Voice session error')); return; }
  if (event.type === 'response.created') responseActive = true;
  if (event.type === 'conversation.item.input_audio_transcription.completed') line('you', event.transcript);
  if (event.type === 'response.output_audio_transcript.done' || event.type === 'response.output_text.done') line('bobby', event.transcript || event.text || '');
  if (event.type !== 'response.done') return;
  responseActive = false;
  const calls = (event.response?.output || []).filter(item => item.type === 'function_call');
  for (const call of calls) {
    let output;
    try {
      const args = JSON.parse(call.arguments || '{}');
      if (call.name === 'robot_status') output = context();
      else if (call.name === 'robot_action') { output = await hardware().voiceAct(args.code, args.duration_ms); line('action', `${args.code} · ${output.status}`); }
      else throw new Error('Unknown robot tool');
    } catch (error) { output = {error: error.message}; }
    emit({type: 'conversation.item.create', item: {type: 'function_call_output', call_id: call.call_id, output: JSON.stringify(output)}});
  }
  if (calls.length) response(); else setStatus('Bobby is listening');
}

function close(reason = 'Conversation stopped') {
  active = starting = responseActive = false;
  if (dc) { dc.onclose = null; dc.close(); dc = null; }
  if (pc) { pc.onconnectionstatechange = null; pc.close(); pc = null; }
  mic?.getTracks().forEach(track => track.stop()); mic = null; $('voiceAudio').srcObject = null;
  setStatus(reason); controls();
}
async function start() {
  if (starting || active) return; $('voiceError').textContent = '';
  if (!hardware()) throw new Error('The robot connection panel is still loading.');
  if (hardware().status().developerMode) throw new Error('Turn off developer mode before starting voice.');
  if (!(await config()).configured) throw new Error('Add an OpenAI API key first.');
  starting = true; controls(); setStatus('Starting voice connection…');
  try {
    pc = new RTCPeerConnection(); mic = await navigator.mediaDevices.getUserMedia({audio: {echoCancellation: true, noiseSuppression: true, autoGainControl: true}}); mic.getTracks().forEach(track => pc.addTrack(track, mic));
    pc.ontrack = event => { $('voiceAudio').srcObject = event.streams[0]; $('voiceAudio').play().catch(() => {}); };
    dc = pc.createDataChannel('oai-events'); dc.onmessage = event => { try { handleEvent(JSON.parse(event.data)).catch(fail); } catch { fail(new Error('Unreadable voice event.')); } };
    dc.onopen = () => { starting = false; active = true; controls(); setStatus('Bobby is listening'); message('Introduce yourself briefly as Bobby.'); };
    dc.onclose = () => close('Voice connection closed');
    pc.onconnectionstatechange = () => { if (['failed', 'disconnected'].includes(pc?.connectionState)) close('Voice connection lost'); };
    const offer = await pc.createOffer(); await pc.setLocalDescription(offer);
    const answer = await (await callApi('/api/voice/session', {method: 'POST', headers: {'Content-Type': 'application/sdp'}, body: offer.sdp})).text();
    await pc.setRemoteDescription({type: 'answer', sdp: answer});
  } catch (error) { close('Voice could not start'); throw error; }
}

$('voiceStart').onclick = () => start().catch(fail); $('voiceEnd').onclick = () => close();
$('voiceMute').onclick = () => { if (!mic) return; muted = !muted; mic.getAudioTracks().forEach(track => track.enabled = !muted); $('voiceMute').textContent = muted ? 'Unmute microphone' : 'Mute microphone'; };
$('voiceTextForm').onsubmit = event => { event.preventDefault(); const value = $('voiceText').value; $('voiceText').value = ''; message(value); };
$('voiceKeyForm').onsubmit = async event => { event.preventDefault(); try { await callApi('/api/voice/key', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({key: $('voiceKey').value})}); $('voiceKey').value = ''; await config(); } catch (error) { fail(error); } };
$('voiceKeyRemove').onclick = async () => { close(); try { await callApi('/api/voice/key', {method: 'DELETE'}); await config(); } catch (error) { fail(error); } };
window.addEventListener('bittle-hardware-ready', controls); window.addEventListener('bittle-hardware-state', controls);
window.addEventListener('bittle-developer-mode', event => { if (event.detail.enabled && (active || starting)) close('Stopped because developer mode was enabled'); controls(); });
window.addEventListener('pagehide', () => close()); controls(); config().catch(fail);
