export function initCodePanel({api, toast, getModel, refresh}) {
  const $=id=>document.getElementById(id);
  let files=structuredClone(getModel().scripts||[]), current=null, timer, saving=Promise.resolve(), dirty=false, active=false, lastLog=0;
  $('codePanel').innerHTML=`<div class="code-head"><h2>Python workspace</h2><span id="codeStatus" class="code-status">Ready</span><button id="newScript">＋ File</button><button id="importScript">Import .py</button><button id="timelineCode">Timeline → Python</button><button id="codeHelp">API help</button><button id="closeCode" aria-label="Close Python panel">×</button></div>
  <div class="code-layout"><div id="scriptFiles" class="code-files"></div><div class="code-editor"><div class="code-toolbar"><input id="scriptName" aria-label="Python filename" placeholder="controller.py"><label>Target <select id="scriptTarget" aria-label="Script target"></select></label><button id="saveScript">Save</button><button id="downloadScript">↓ .py</button><button id="deleteScript">Delete</button></div><textarea id="scriptSource" spellcheck="false" aria-label="Python source" placeholder="Create or import a Python file."></textarea></div></div>
  <div class="code-toolbar"><button class="primary" id="runScript">▶ Run file</button><button id="runAllScripts">▶ Run checked</button><button class="danger" id="stopScripts">■ Stop all</button><label class="check"><input id="codePhysics" type="checkbox" checked> Enable physics</label><label>Limit <input id="codeTimeout" type="number" min="1" max="3600" value="3600"> s</label><span class="code-note">A running physics world stays running. Checked files run; unchecked files remain importable modules.</span></div><pre id="codeConsole" class="code-console" role="log" aria-label="Python console"></pre><div class="code-note">Trusted local Python runs on this PC. PetoiRobot calls use a simulation adapter; this is not a security sandbox.</div><input id="scriptFile" type="file" accept=".py" multiple>`;
  const help=document.createElement('dialog');help.id='codeHelpDialog';help.innerHTML=`<div class="dialog-heading"><h2>Simulation Python API</h2><button id="closeCodeHelp">×</button></div><pre id="scriptHelp"></pre>`;document.body.append(help);
  $('scriptHelp').textContent=`from bittle_sim import ctx
import math

state = ctx.get_state()
print(state["joint_names"])
imu = ctx.get_imu()                              # first configured IMU
print(imu["orientation"], imu["angular_velocity"])
ctx.set_joints({"left-front-shoulder-joint": 20}) # degrees
ctx.set_position([0.3, 0, 0.1], [0, 0, 0])     # metres, Euler radians
ctx.apply_force([0, 0, 1])                      # Newtons, one physics step
rate = ctx.rate(50)
for frame in range(100):
    # Update the assigned target here.
    rate.sleep()

ctx.time() and ctx.sleep(seconds) use simulation time.
time.sleep(), time.monotonic() also use simulation time.
set_position teleports the target; it is useful for kinematic objects.
apply_force needs Physics enabled and a body with nonzero mass.

Every runnable file has ONE target. Run checked starts up to eight
files together, on different targets. Uncheck helper.py and import it
from another file with: from helper import your_function
Each process imports its own copy; module globals are not shared.

PetoiRobot adapter: autoConnect, openPort, closePort, rotateJoints,
absValList, getAngle, getAngleList. Unsupported firmware APIs fail.
Exported scripts run with --execute inside the simulation adapter.
Old files use the target mapping; new exports embed STUDIO_MAPPING.
ctx.get_sensor(name), get_imu(name), and get_sensors() read configured
simulation sensors. IMU angles use radians and gyro rates use rad/s.
The adapter does not emulate firmware timing or physical sensor noise.

Stop terminates running processes and pauses physics. Reset also
stops scripts. A run time limit stops infinite loops. Scripts can use
normal Python modules and filesystem APIs: only run trusted code.`;
  const bind=(id, fn, event='click')=>$(id).addEventListener(event,async e=>{try{await fn(e);}catch(error){toast(error.message,true);$('codeStatus').textContent=error.message;}});
  function capture(){const file=files.find(f=>f.id===current);if(file){file.source=$('scriptSource').value;file.name=$('scriptName').value;file.target=$('scriptTarget').value;}}
  async function save(){clearTimeout(timer);capture();const snapshot=structuredClone(files);saving=saving.catch(()=>{}).then(()=>api('/api/scripts',{files:snapshot},'PUT'));await saving;dirty=false;getModel().scripts=structuredClone(snapshot);}
  function schedule(){dirty=true;clearTimeout(timer);timer=setTimeout(()=>save().catch(e=>toast(e.message,true)),800);}
  function targets(){const selected=$('scriptTarget').value;const model=getModel();$('scriptTarget').replaceChildren();for(const [id,name] of [['main','Bittle · main'],...(model.actors||[]).map(a=>[a.id,a.name+' · URDF']),...model.objects.map(o=>[o.id,o.name+' · object'])]){const option=document.createElement('option');option.value=id;option.textContent=name;$('scriptTarget').append(option);}const wanted=files.find(f=>f.id===current)?.target||selected;if(wanted&&!Array.from($('scriptTarget').options).some(o=>o.value===wanted)){const missing=document.createElement('option');missing.value=wanted;missing.textContent='Missing target — reassign';$('scriptTarget').append(missing);}$('scriptTarget').value=wanted||'main';}
  function list(){ $('scriptFiles').replaceChildren();files.forEach(file=>{const row=document.createElement('div');row.className='code-file'+(file.id===current?' active':'');const check=document.createElement('input');check.type='checkbox';check.checked=file.enabled;check.setAttribute('aria-label','Run '+file.name);check.onchange=()=>{file.enabled=check.checked;schedule();};const button=document.createElement('button');button.textContent=file.name;button.title=file.name;button.onclick=()=>{capture();select(file.id);};row.append(check,button);$('scriptFiles').append(row);});}
  function select(id){current=id;const file=files.find(f=>f.id===id);$('scriptName').value=file?.name||'';$('scriptSource').value=file?.source||'';targets();if(file)$('scriptTarget').value=file.target;list();}
  function show(){document.querySelector('.workspace').classList.add('coding');$('codePanel').classList.remove('hidden');targets();}
  function hide(){document.querySelector('.workspace').classList.remove('coding');$('codePanel').classList.add('hidden');}
  function uniqueName(name){const base=name.replace(/\.py$/,'').replace(/[^A-Za-z_0-9]/g,'_').replace(/^[0-9]/,'_');let n=base+'.py',i=2;while(files.some(f=>f.name===n))n=base+'_'+i+++'.py';return n;}
  async function addSource(source,name='motion.py',target='main'){capture();const file={id:crypto.randomUUID(),name:uniqueName(name),source,target,enabled:true};files.push(file);select(file.id);show();await save();}
  function example(target){const actor=target==='main'?getModel():(getModel().actors||[]).find(a=>a.id===target);if(actor){if(actor.sensors?.length){const ankles=actor.joints.filter(j=>/ankle/i.test(j.name)).map(j=>j.name);return `from bittle_sim import ctx\n\n# Simple simulated balance starting point. Tune signs and gains for this URDF.\nankles = ${JSON.stringify(ankles)}\nrate = ctx.rate(50)\nwhile True:\n    imu = ctx.get_imu(${JSON.stringify(actor.sensors[0].name)})\n    pitch = imu["orientation"][1]              # radians\n    pitch_rate = imu["angular_velocity"][1]   # rad/s\n    correction = max(-30, min(30, -30 * pitch - 3 * pitch_rate))\n    ctx.set_joints({name: correction for name in ankles})\n    rate.sleep()\n`; }const joint=actor.joints[0]?.name;return `from bittle_sim import ctx\nimport math\n\nrate = ctx.rate(50)\nfor frame in range(150):\n    angle = 15 * math.sin(2 * math.pi * frame / 150)\n    ctx.set_joints({${JSON.stringify(joint||'joint')}: angle})\n    rate.sleep()\nprint("Joint sweep complete")\n`;}
    return `from bittle_sim import ctx\nimport math\n\norigin = ctx.get_state()["position"]\nrate = ctx.rate(50)\nfor frame in range(150):\n    x = origin[0] + 0.08 * math.sin(2 * math.pi * frame / 150)\n    ctx.set_position([x, origin[1], origin[2]])\n    rate.sleep()\nprint("Object motion complete")\n`;}
  async function forTarget(target){await addSource(example(target),'controller.py',target);}
  bind('newScript',()=>forTarget($('scriptTarget').value||'main'));
  bind('closeCode',hide);
  bind('saveScript',async()=>{await save();list();toast('Python files saved in the current project.');});
  bind('scriptSource',schedule,'input');bind('scriptName',()=>{schedule();},'input');bind('scriptTarget',schedule,'change');
  $('scriptSource').addEventListener('keydown',e=>{if(e.key==='Tab'){e.preventDefault();const t=e.target,start=t.selectionStart,end=t.selectionEnd;t.setRangeText('    ',start,end,'end');schedule();}});
  bind('deleteScript',async()=>{files=files.filter(f=>f.id!==current);select(files[0]?.id||null);await save();});
  bind('downloadScript',async()=>{await save();const file=files.find(f=>f.id===current);if(!file)throw Error('Select a file');const url=URL.createObjectURL(new Blob([file.source],{type:'text/x-python'}));const a=document.createElement('a');a.href=url;a.download=file.name;a.click();setTimeout(()=>URL.revokeObjectURL(url),30000);});
  bind('importScript',()=>$('scriptFile').click());bind('scriptFile',async()=>{for(const f of $('scriptFile').files)await addSource(await f.text(),f.name,$('scriptTarget').value||'main');$('scriptFile').value='';},'change');
  bind('timelineCode',async()=>{const result=await api('/api/motion-code',{hz:Number($('motionHz').value),speed:Number($('speed').value),direction:$('motionDirection').value});await addSource(result.source,'motion.py');toast('Exact exported Python loaded. Choose Run file to preview it.');});
  async function run(all){await save();if(!all&&!current)throw Error('Create or select a Python file first');await api('/api/scripts/run',{...(all?{}:{ids:[current]}),physics:$('codePhysics').checked,timeout:Number($('codeTimeout').value)});lastLog=0;$('codeConsole').textContent='';active=true;}
  bind('runScript',()=>run(false));bind('runAllScripts',()=>run(true));bind('stopScripts',()=>api('/api/scripts/stop',{}));
  bind('codeHelp',()=>$('codeHelpDialog').showModal());bind('closeCodeHelp',()=>$('codeHelpDialog').close());
  async function poll(){try{const status=await api('/api/scripts');active=status.active;$('codeStatus').textContent=status.jobs.length?status.jobs.map(j=>j.name+': '+j.status).join(' · '):'Ready';$('runScript').disabled=$('runAllScripts').disabled=active;const lines=status.logs.filter(l=>l.seq>lastLog);if(lines.length){lastLog=lines.at(-1).seq;$('codeConsole').textContent=($('codeConsole').textContent+lines.map(l=>'['+l.file+'] '+l.text).join('\n')+'\n').slice(-24000);$('codeConsole').scrollTop=$('codeConsole').scrollHeight;}}catch{}setTimeout(poll,500);}poll();
  if(files.length)select(files[0].id);
  return {show,hide,save,addSource,forTarget, reload(){files=structuredClone(getModel().scripts||[]);dirty=false;select(files.some(f=>f.id===current)?current:files[0]?.id||null);},targets};
}
