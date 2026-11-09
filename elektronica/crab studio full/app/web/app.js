import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { TransformControls } from 'three/addons/controls/TransformControls.js';
import { OBJLoader } from 'three/addons/loaders/OBJLoader.js';
import { STLLoader } from 'three/addons/loaders/STLLoader.js';
import { ColladaLoader } from 'three/addons/loaders/ColladaLoader.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { bittleServo, renderJointLayout } from './bittle-link/joint-layout.js';

const $ = id => document.getElementById(id);
let model, state, selected = null, selectedFrame = null, frameList = [], mapping = {}, loading = false, timelineTarget = 'main';
let toastTimer, pendingPose = {}, poseTimer;
let codePanel, hardwarePanel, selectedActor=null, mainSelected=true, gizmoTarget='robot', robotDrag=null, editorTarget='main';
const robotLinks = new Map(), objectMeshes = new Map(), meshCache = new Map();
function toast(message, error = false) {
  const dialog=document.querySelector('dialog[open]:modal');
  if(dialog){let status=dialog.querySelector('.dialog-status');if(!status){status=document.createElement('p');status.className='dialog-status';status.setAttribute('role','status');dialog.append(status);}status.textContent=message;status.style.color=error?'#ffc6bc':'#c0e581';status.scrollIntoView({block:'nearest'});}
  $('toast').textContent = message; $('toast').className = 'show' + (error ? ' error' : '');
  clearTimeout(toastTimer); toastTimer = setTimeout(() => $('toast').className = '', error ? 11000 : 5000);
}
// A network failure means the local server is gone (window closed or stopped), not a bad request.
const SERVER_OFFLINE = 'The Bittle Studio server is not running. Start it with the “Start Bittle Studio” launcher in the launchers folder (keep its window open), then reload this page.';
async function api(url, data, method = 'POST') {
  let response;
  try { response = await fetch(url, data === undefined ? {} : {method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)}); }
  catch { throw new Error(SERVER_OFFLINE); }
  if (!response.ok) { let message; try { message = (await response.json()).detail; } catch { message = response.statusText; } throw new Error(typeof message === 'string' ? message : JSON.stringify(message)); }
  return response.headers.get('content-type')?.includes('application/json') ? response.json() : response.text();
}
const command = (action, rest = {}) => api('/api/command', {action, ...rest});
const robotCommand = (action, rest = {}) => command(action, {target: timelineTarget, ...rest});
function timelineModel() {return timelineTarget === 'main' ? model : model?.actors?.find(actor => actor.id === timelineTarget);}
function timelineState(value=state) {return timelineTarget === 'main' ? value : value?.actors?.[timelineTarget];}
function setTimelineTarget(target) {
  if(target !== 'main' && !model?.actors?.some(actor => actor.id === target)) target='main';
  timelineTarget=target;const robot=timelineModel();if(!robot)return;
  frameList=robot.frames||[];selectedFrame=null;pendingPose={};
  $('timelineRobot').value=target;$('motionHz').value=robot.motion_hz||model.motion_hz;$('motionDirection').value=robot.direction||'forward';
  $('mainPlacement').classList.toggle('hidden',target!=='main');$('jointTitle').textContent=(target==='main'?'Bittle':robot.name)+' joint angles';
  buildJoints();timeline();
}
function on(id, action, event = 'click') { $(id).addEventListener(event, async e => {try {await action(e);} catch(error) {toast(error.message, true);} }); }
function download(name, value, type = 'application/json') {
  const url = URL.createObjectURL(new Blob([typeof value === 'string' ? value : JSON.stringify(value, null, 2)], {type}));
  const a = document.createElement('a'); a.href = url; a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(url), 30000);
}
function label(name) {return name.replace(/-joint$/, '').replaceAll('-', ' ').replaceAll('_', ' ');}
function tab(name) {
  // The inspector always shows Control; object and extra-robot properties appear under the Stage list.
  ['object', 'actor'].forEach(t => $(t + 'Panel').classList.toggle('hidden', name !== t));
  if (name === 'object' || name === 'actor') document.querySelector('.objects-section').open = true;  // show what was just selected
  $('mainPlacement').classList.toggle('hidden', name !== 'pose' || timelineTarget !== 'main');
}
document.querySelectorAll('[data-tab]').forEach(el => el.addEventListener('click', () => tab(el.dataset.tab)));
document.querySelectorAll('[data-close]').forEach(el => el.addEventListener('click', () => $(el.dataset.close).close()));

const panelDefaults={scene:true,inspector:true,timeline:true};
function savedPanelLayout(){try{return JSON.parse(localStorage.getItem('bittle-workspace-panels')||'{}');}catch{return {};}}
let panelVisibility={...panelDefaults,...savedPanelLayout()};
function applyPanelLayout(){
  const main=document.querySelector('main');
  for(const name of Object.keys(panelDefaults)){
    document.querySelector(`[data-workspace-panel="${name}"]`)?.classList.toggle('hidden',!panelVisibility[name]);
    main.classList.toggle('hide-'+name,!panelVisibility[name]);
    const item=document.querySelector(`[data-window="${name}"]`);if(item)item.textContent=(panelVisibility[name]?'✓ ':'')+(name==='scene'?'Stage':name[0].toUpperCase()+name.slice(1));
  }
  // Left column: Stage. Centre: viewport and timeline (with saved functions). Right: Robot control at full height.
  const left=[panelVisibility.scene&&'scene'].filter(Boolean),columns=[],rows=['',''];
  if(left.length){columns.push('clamp(210px,16vw,270px)');rows[0]+=left[0]+' ';rows[1]+=(left[1]||left[0])+' ';}
  columns.push('minmax(0,1fr)');rows[0]+='work ';rows[1]+='work ';
  if(panelVisibility.inspector){columns.push('clamp(300px,26vw,420px)');rows[0]+='insp';rows[1]+='insp';}
  main.style.gridTemplateColumns=columns.join(' ');main.style.gridTemplateAreas=rows.map(r=>`"${r.trim()}"`).join(' ');
  try{localStorage.setItem('bittle-workspace-panels',JSON.stringify(panelVisibility));}catch{/* Restricted browser storage must not prevent startup. */}
  requestAnimationFrame(()=>window.dispatchEvent(new Event('resize')));
}
function setPanel(name,visible){panelVisibility[name]=visible;applyPanelLayout();}
document.querySelectorAll('[data-hide-panel]').forEach(button=>button.addEventListener('click',()=>setPanel(button.dataset.hidePanel,false)));
document.querySelectorAll('[data-window]').forEach(button=>button.addEventListener('click',()=>{
  const name=button.dataset.window;
  if(name==='serial')window.dispatchEvent(new Event('bittle-toggle-serial'));else if(name==='voice')$('voiceDialog').open?$('voiceDialog').close():$('voiceDialog').show();else if(name==='code')codePanel?.show();else if(name==='robot')openRobotEditor();else if(name==='reset'){panelVisibility={...panelDefaults};applyPanelLayout();codePanel?.hide();}else setPanel(name,!panelVisibility[name]);
  button.closest('details')?.removeAttribute('open');
}));
applyPanelLayout();

function filterStage(){const query=$('stageSearch').value.trim().toLowerCase();document.querySelectorAll('.scene-list .scene-item').forEach(item=>item.classList.toggle('hidden',!item.textContent.toLowerCase().includes(query)));}
$('stageSearch').addEventListener('input',filterStage);
new MutationObserver(filterStage).observe(document.querySelector('.scene-list'),{childList:true,subtree:true});

// Render link transforms from Bullet, rather than maintaining a separate visual-only robot.
THREE.Object3D.DEFAULT_UP.set(0, 0, 1);
const scene = new THREE.Scene(); scene.background = new THREE.Color('#55595b');
scene.fog = new THREE.Fog('#55595b', 2.2, 6);
const camera = new THREE.PerspectiveCamera(42, 1, .001, 30);
camera.position.set(.40, .52, .31);
const renderer = new THREE.WebGLRenderer({antialias: true});
renderer.setPixelRatio(Math.min(devicePixelRatio, 2)); renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap; renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.25; $('viewport').append(renderer.domElement);
const controls = new OrbitControls(camera, renderer.domElement); controls.target.set(0, 0, .12);
controls.enableDamping = true; controls.minDistance = .05; controls.maxDistance = 7;
const hemi = new THREE.HemisphereLight(0xe4f3ff, 0x697462, 2.1); scene.add(hemi);
const sun = new THREE.DirectionalLight(0xffefd8, 3.4); sun.position.set(-.8, .7, 1.6); sun.castShadow = true;
sun.shadow.mapSize.set(2048, 2048); Object.assign(sun.shadow.camera, {left:-1.5,right:1.5,top:1.5,bottom:-1.5,near:.01,far:5}); sun.shadow.bias=-.0001; sun.shadow.normalBias=.0004; scene.add(sun);
const rim = new THREE.DirectionalLight(0x90bfff, 1.8); rim.position.set(.5,-.8,.6); scene.add(rim);
const floor = new THREE.Mesh(new THREE.PlaneGeometry(200, 200), new THREE.MeshStandardMaterial({color: '#515557', roughness: .92}));
floor.receiveShadow = true; floor.position.z = -.001; scene.add(floor);
const grid = new THREE.GridHelper(8, 160, 0x80919c, 0x506571); grid.rotation.x = Math.PI / 2; grid.position.z = .0001;
grid.material.transparent = true; grid.material.opacity = .42; scene.add(grid);
const gizmo = new TransformControls(camera, renderer.domElement); gizmo.setSize(.7); scene.add(gizmo.getHelper());
const robotHandle = new THREE.Object3D(); scene.add(robotHandle);
gizmo.addEventListener('dragging-changed', e => controls.enabled = !e.value);
gizmo.addEventListener('mouseDown', () => {
  if (gizmoTarget !== 'robot') return;
  robotHandle.updateMatrixWorld(true);
  robotDrag = {handle: robotHandle.matrixWorld.clone(), groups: new Map()};
  for (const [name, group] of robotLinks) if (!name.includes('/')) {group.updateMatrixWorld(true);robotDrag.groups.set(group,group.matrixWorld.clone());}
});
gizmo.addEventListener('objectChange', () => {
  if (gizmoTarget !== 'robot' || !robotDrag) return;
  robotHandle.updateMatrixWorld(true);
  const delta=robotHandle.matrixWorld.clone().multiply(robotDrag.handle.clone().invert());
  for(const [group,start] of robotDrag.groups)delta.clone().multiply(start).decompose(group.position,group.quaternion,group.scale);
  syncRobotFields();
});
gizmo.addEventListener('mouseUp', async () => {
  if (!gizmo.object) return;
  try {
    if(gizmoTarget==='robot'){robotDrag=null;model.robot_position=robotHandle.position.toArray();model.robot_rotation=robotHandle.rotation.toArray().slice(0,3);await api('/api/robot-transform',{position:model.robot_position,rotation:model.robot_rotation});}
    else if(selected){const obj=model.objects.find(o=>o.id===selected);obj.position=gizmo.object.position.toArray();obj.rotation=gizmo.object.rotation.toArray().slice(0,3);await api('/api/objects',obj);selectObject(selected);}
  } catch(e){toast(e.message,true);await refresh();}
});
const selectionBox = new THREE.BoxHelper(undefined, 0xc0e581); selectionBox.visible = false; scene.add(selectionBox);
new ResizeObserver(() => {const w=$('viewport').clientWidth,h=$('viewport').clientHeight;if(!w||!h)return;renderer.setSize(w,h);camera.aspect=w/h;camera.updateProjectionMatrix();}).observe($('viewport'));
function disposeGroup(group) { scene.remove(group); group.traverse(child => { if (child.isMesh) {child.geometry?.dispose(); (Array.isArray(child.material) ? child.material : [child.material]).forEach(m => m?.dispose());} }); }
async function loadMesh(url) {
  if (!meshCache.has(url)) meshCache.set(url, (async () => {
    const ext = url.split('.').pop().toLowerCase();
    if (ext === 'obj') return new OBJLoader().loadAsync(url);
    if (ext === 'stl') return new THREE.Mesh(await new STLLoader().loadAsync(url));
    if (ext === 'dae') return (await new ColladaLoader().loadAsync(url)).scene;
    if (ext === 'glb' || ext === 'gltf') return (await new GLTFLoader().loadAsync(url)).scene;
    throw new Error('Viewport does not support mesh format: ' + ext);
  })());
  return (await meshCache.get(url)).clone(true);
}
function materialize(group, color) {
  group.traverse(child => {if(child.isMesh) {child.geometry = child.geometry.clone(); child.material = new THREE.MeshStandardMaterial({color, roughness: .42, metalness: .12});child.castShadow=true;child.receiveShadow=true;} });
}
async function buildRobot() {
  for(const group of robotLinks.values()) disposeGroup(group); robotLinks.clear();
  const visuals=[...model.visuals.map(v=>({...v,actor:'main'})),...(model.actors||[]).flatMap(a=>a.visuals.map(v=>({...v,actor:a.id})))];
  await Promise.all(visuals.map(async v => {
    const key=v.actor==='main'?v.link:v.actor+'/'+v.link;
    let group = robotLinks.get(key);
    if(!group) {group=new THREE.Group();group.userData.robot=true;group.userData.actor=v.actor;scene.add(group);robotLinks.set(key,group);}
    let mesh;
    if(v.type === 'mesh') {mesh=await loadMesh(v.url);mesh.scale.fromArray(v.scale);}
    else if(v.type === 'box') {mesh=new THREE.Mesh(new THREE.BoxGeometry(...v.size.split(' ').map(Number)));}
    else if(v.type === 'sphere') {mesh=new THREE.Mesh(new THREE.SphereGeometry(Number(v.radius),32,24));}
    else if(v.type === 'cylinder') {mesh=new THREE.Mesh(new THREE.CylinderGeometry(Number(v.radius),Number(v.radius),Number(v.length),32));mesh.geometry.rotateX(Math.PI/2);}
    else {throw new Error('Unsupported URDF visual: ' + v.type);}
    let color = /knee|cover|head|neck/.test(v.link) ? '#eab94f' : /shoulder/.test(v.link) ? '#333d46' : /board|imu/.test(v.link) ? '#355c43' : '#5d6c76';
    if(v.color) color=new THREE.Color(...v.color.slice(0,3));
    materialize(mesh,color);mesh.position.fromArray(v.position);mesh.quaternion.fromArray(v.quaternion);group.add(mesh);
  }));
  $('bodyCount').textContent=model.visuals.length;$('jointCount').textContent=model.joints.length;
  renderActors();
}
async function buildObjects() {
  gizmo.detach(); selectionBox.visible=false;
  for(const mesh of objectMeshes.values()) disposeGroup(mesh); objectMeshes.clear();
  for(const obj of model.objects) {
    let mesh;
    if(obj.type==='mesh') {mesh=await loadMesh(obj.url);mesh.scale.fromArray(obj.size);}
    else if(obj.type==='sphere') mesh=new THREE.Mesh(new THREE.SphereGeometry(obj.size[0]/2,32,24));
    else mesh=new THREE.Mesh(new THREE.BoxGeometry(...obj.size));
    materialize(mesh,obj.color);mesh.position.fromArray(obj.position);mesh.rotation.set(...obj.rotation,'XYZ');mesh.userData.objectId=obj.id;
    mesh.traverse(c=>c.userData.objectId=obj.id);scene.add(mesh);objectMeshes.set(obj.id,mesh);
  }
  renderObjectList(); if(selected && objectMeshes.has(selected)) selectObject(selected);
}
function renderObjectList() {
  renderActors();
  $('objectList').replaceChildren();
  for(const o of model.objects) {const b=document.createElement('button');b.className='scene-item'+(selected===o.id?' selected':'');const icon=document.createElement('span');icon.textContent=o.type==='sphere'?'○':'◇';const text=document.createElement('div');text.textContent=o.name;const small=document.createElement('small');small.textContent=(o.mass===0?'Static':o.mass+' kg')+' · '+o.type;text.append(small);b.append(icon,text);b.onclick=()=>selectObject(o.id);$('objectList').append(b);}
}
function selectObject(id) {
  selectedActor=null;mainSelected=false;gizmoTarget='object';
  selected=id; const obj=model.objects.find(o=>o.id===id); if(!obj)return;
  tab('object');$('selectRobot').classList.remove('selected');$('inspectorTitle').textContent='Robot control';$('selectionLabel').textContent=obj.type.toUpperCase();
  $('objectEmpty').classList.add('hidden');$('objectForm').classList.remove('hidden');$('objName').value=obj.name;
  ['position','rotation','size'].forEach(kind=>obj[kind].forEach((v,i)=>$(kind+i).value=(kind==='rotation'?THREE.MathUtils.radToDeg(v):v).toFixed(4)));
  $('sizeTitle').textContent=obj.type==='mesh'?'Mesh scale · 0.001 for mm':obj.type==='sphere'?'Diameter · X only':'Size · metres';
  $('objMass').value=obj.mass;$('objFriction').value=obj.friction;$('objColor').value=obj.color;
  const mesh=objectMeshes.get(id);if(!state?.running)gizmo.attach(mesh);selectionBox.setFromObject(mesh);selectionBox.visible=true;renderObjectList();
}
for(const kind of ['position','rotation','size']) for(let i=0;i<3;i++) {const l=document.createElement('label');l.textContent=['X','Y','Z'][i];const input=document.createElement('input');input.id=kind+i;input.type='number';input.step=kind==='rotation'?'1':'.01';input.required=true;if(kind==='size')input.min='.000001';l.append(input);$(kind+'Fields').append(l);}
function syncRobotFields(){const p=robotHandle.position,r=robotHandle.rotation;[p.x,p.y,p.z].forEach((v,i)=>$('robotPosition'+i).value=v.toFixed(4));[r.x,r.y,r.z].forEach((v,i)=>$('robotRotation'+i).value=THREE.MathUtils.radToDeg(v).toFixed(2));}
function selectMainRobot(){selectedActor=null;selected=null;mainSelected=true;gizmoTarget='robot';gizmo.detach();selectionBox.visible=false;setTimelineTarget('main');tab('pose');$('selectRobot').classList.add('selected');$('inspectorTitle').textContent='Robot control';$('selectionLabel').textContent='BITTLE';renderObjectList();if(!state?.running)gizmo.attach(robotHandle);syncRobotFields();}
on('selectRobot',selectMainRobot);
// The robot's handle is shown only while physics is paused (selectMainRobot): say so rather than do nothing.
on('moveRobot',()=>{selectMainRobot();gizmo.setMode('translate');if(state?.running)throw new Error('Pause physics before moving the robot');});
on('rotateRobot',()=>{selectMainRobot();gizmo.setMode('rotate');if(state?.running)throw new Error('Pause physics before rotating the robot');});
async function applyRobotPlacement(position,rotation){if(state?.running)throw new Error('Pause physics before moving the robot');await api('/api/robot-transform',{position,rotation});model.robot_position=position;model.robot_rotation=rotation;robotHandle.position.fromArray(position);robotHandle.rotation.set(...rotation,'XYZ');syncRobotFields();}
on('applyRobotPlacement',()=>applyRobotPlacement([0,1,2].map(i=>Number($('robotPosition'+i).value)),[0,1,2].map(i=>THREE.MathUtils.degToRad(Number($('robotRotation'+i).value)))));
on('resetRobotPlacement',()=>applyRobotPlacement([0,0,.2],[0,0,0]));
const raycaster=new THREE.Raycaster();let down;
renderer.domElement.addEventListener('pointerdown',e=>down=e.button===0?[e.clientX,e.clientY]:null);
renderer.domElement.addEventListener('pointercancel',()=>down=null);
renderer.domElement.addEventListener('pointerup',e=>{
  const start=down;down=null;
  if(!start||e.button!==0||Math.hypot(e.clientX-start[0],e.clientY-start[1])>4||gizmo.dragging||gizmo.axis)return;
  const rect=renderer.domElement.getBoundingClientRect();
  raycaster.setFromCamera(new THREE.Vector2((e.clientX-rect.left)/rect.width*2-1,-(e.clientY-rect.top)/rect.height*2+1),camera);
  const hit=raycaster.intersectObjects([...objectMeshes.values(),...robotLinks.values()],true)[0];
  if(!hit)return;
  if(hit.object.userData.objectId){selectObject(hit.object.userData.objectId);return;}
  let link=hit.object;while(link&&!link.userData.robot)link=link.parent;
  if(link?.userData.actor==='main')selectMainRobot();else if(link)selectActor(link.userData.actor);
});
function focusRobot(view='perspective') {const box=new THREE.Box3();robotLinks.forEach(g=>{if(g.userData.actor===(selectedActor||'main'))box.expandByObject(g);});const center=box.isEmpty()?new THREE.Vector3(0,0,.12):box.getCenter(new THREE.Vector3());controls.target.copy(center);const distance=Math.max(.35,box.getSize(new THREE.Vector3()).length()*1.7);camera.position.copy(center).add(view==='top'?new THREE.Vector3(.0001,0,distance):view==='side'?new THREE.Vector3(distance,0,.02):new THREE.Vector3(distance*.7,distance*.95,distance*.6));controls.update();}
on('focus',()=>focusRobot());on('topView',()=>focusRobot('top'));on('sideView',()=>focusRobot('side'));on('grid',()=>{$('grid').setAttribute('aria-pressed',String(grid.visible=!grid.visible));});
function render() {requestAnimationFrame(render);controls.update();if(selected&&selectionBox.visible)selectionBox.update();renderer.render(scene,camera);} render();

// Mirror simulator joint changes to the connected robot through Servo setup (servo = direction × angle + offset).
function mirrorPose(pose){if(timelineTarget!=='main'||!window.bittleHardware?.status().connected)return;const map=Object.keys(mapping).length?mapping:timelineModel().mapping;const entries=Object.entries(pose).flatMap(([name,value])=>{const m=map[name];return m&&Number.isInteger(m.servo)&&m.servo>=0&&m.servo<=15?[[m.servo,Math.round(m.sign*value+Number(m.offset||0))]]:[];});if(entries.length)window.bittleHardware.mirrorServos(entries);}
function buildJoints() {
  const robot=timelineModel();if(!robot)return;
  const setJoint=(j,value,slider,number)=>{const v=Number(value);if(!Number.isFinite(v))return;const clamped=Math.max(j.lower,Math.min(j.upper,v));number.value=clamped.toFixed(1);slider.value=clamped;pendingPose[j.name]=clamped;clearTimeout(poseTimer);poseTimer=setTimeout(flushPose,40);mirrorPose({[j.name]:clamped});};
  // A Bittle-shaped robot (every joint maps to a distinct OpenCat servo) gets the Skill Composer layout; any other URDF keeps the list.
  const servos=robot.joints.map(j=>bittleServo(j.name));
  if(servos.every(s=>s>=0)&&new Set(servos).size===servos.length){
    $('jointControls').classList.add('composer');
    renderJointLayout($('jointControls'),robot.joints.map((j,i)=>({servo:servos[i],label:label(j.name),title:servos[i]===0?(robot.joints.some(k=>/elbow/.test(k.name))?'Claw swing':'Head pan'):servos[i]===1?'Claw elbow':servos[i]===2?'Pincer':/knee/.test(j.name)?'Knee':'Shoulder',mirrored:servos[i]===0,min:j.lower,max:j.upper,step:.1,value:robot.targets[j.name]??0,sliderId:'joint-'+j.id,numberId:'angle-'+j.id,onInput:(v,slider,number)=>setJoint(j,v,slider,number),onCommit:(v,slider,number)=>setJoint(j,v,slider,number)})));
    return;
  }
  $('jointControls').classList.remove('composer');
  $('jointControls').replaceChildren();
  for(const j of robot.joints) {
    const row=document.createElement('div');row.className='joint';const head=document.createElement('div');head.className='joint-head';const l=document.createElement('label');l.textContent=label(j.name);l.htmlFor='joint-'+j.id;
    const number=document.createElement('input');number.type='number';number.min=j.lower.toFixed(2);number.max=j.upper.toFixed(2);number.step='.1';number.id='angle-'+j.id;number.setAttribute('aria-label',label(j.name)+' degrees');
    const slider=document.createElement('input');slider.type='range';slider.min=j.lower;slider.max=j.upper;slider.step='.1';slider.id='joint-'+j.id;slider.value=number.value=robot.targets[j.name]??0;
    slider.oninput=()=>setJoint(j,slider.value,slider,number);number.onchange=()=>setJoint(j,number.value,slider,number);head.append(l,number);
    const limits=document.createElement('div');limits.className='joint-limits';limits.innerHTML=`<span>${Math.round(j.lower)}°</span><span>${Math.round(j.upper)}°</span>`;row.append(head,slider,limits);$('jointControls').append(row);
  }
}
async function flushPose(){if(!Object.keys(pendingPose).length)return;const pose=pendingPose;pendingPose={};try{await robotCommand('pose',{pose});}catch(e){toast(e.message,true);}}
function updatePoseInputs(pose) {for(const j of timelineModel()?.joints||[]) {if(pendingPose[j.name]!==undefined)continue;const slider=$('joint-'+j.id),num=$('angle-'+j.id);if(slider&&num&&document.activeElement!==slider&&document.activeElement!==num){slider.value=pose[j.name];num.value=Number(pose[j.name]).toFixed(1);}}}
function crouchPose(){return Object.fromEntries((timelineModel()?.joints||[]).map(j=>[j.name,/knee/.test(j.name)?45:-35]));}
on('zeroPose',()=>{const pose=Object.fromEntries(timelineModel().joints.map(j=>[j.name,0]));mirrorPose(pose);return robotCommand('pose',{pose});});
on('run',async()=>{await flushPose();await command('run',{value:!state.running});});on('reset',async()=>{await command('reset');$('frameTime').value=0;});
on('applyPhysics',async()=>{await command('settings',{gravity:Number($('gravity').value),friction:Number($('friction').value),fixed:$('fixed').checked,physics_hz:Number($('physicsHz').value),motion_hz:Number($('motionHz').value),direction:$('motionDirection').value});await refresh();toast('Physics settings applied.');});


async function addObject(obj){const added=await api('/api/objects',obj);model.objects.push(added);await buildObjects();selectObject(added.id);return added;}
on('addBox',()=>addObject({type:'box',name:'Test block',position:[.22,0,.025],size:[.12,.12,.05],color:'#849bab'}));
on('addSphere',()=>addObject({type:'sphere',name:'Ball',position:[.22,0,.12],size:[.07,.07,.07],mass:.1,color:'#d89f66'}));
on('addRamp',()=>addObject({type:'box',name:'Ramp · 15°',position:[.28,0,.045],rotation:[0,-Math.PI/12,0],size:[.32,.22,.018],color:'#799b9a'}));
on('addSteps',async()=>{for(let i=0;i<3;i++)await addObject({type:'box',name:'Step '+(i+1),position:[.23+i*.11,0,(i+1)*.0125],size:[.11,.25,(i+1)*.025],color:['#667d8d','#7a929f','#95aab4'][i]});toast('Three 2.5 cm steps added.');});
on('objectForm',async e=>{e.preventDefault();const old=model.objects.find(o=>o.id===selected);const obj={...old,name:$('objName').value,mass:Number($('objMass').value),friction:Number($('objFriction').value),color:$('objColor').value};for(const kind of ['position','rotation','size'])obj[kind]=[0,1,2].map(i=>Number($(kind+i).value)*(kind==='rotation'?Math.PI/180:1));const updated=await api('/api/objects',obj);model.objects[model.objects.indexOf(old)]=updated;await buildObjects();toast('Object updated.');},'submit');
on('duplicateObject',async()=>{const o=structuredClone(model.objects.find(o=>o.id===selected));delete o.id;o.name+=' copy';o.position[0]+=.12;await addObject(o);});
on('deleteObject',async()=>{await api('/api/objects/'+selected,{},'DELETE');model.objects=model.objects.filter(o=>o.id!==selected);selected=null;await buildObjects();$('objectForm').classList.add('hidden');$('objectEmpty').classList.remove('hidden');});
on('import',()=>$('assetFile').click());
on('assetFile',async()=>{const file=$('assetFile').files[0];if(!file)return;const form=new FormData();form.append('file',file);$('import').disabled=true;try{const response=await fetch('/api/import?mode='+$('robotImportMode').value,{method:'POST',body:form});const result=await response.json();if(!response.ok)throw new Error(result.detail);if(result.kind==='robot'||result.kind==='actor'){await refresh();if(result.kind==='actor')selectActor(result.id);focusRobot();toast('Robot imported.');}else{await addObject({type:'mesh',name:result.name,url:result.url,size:[1,1,1],position:[.25,0,0],color:'#8fa6b2'});toast(result.note);}}finally{$('assetFile').value='';$('import').disabled=false;}},'change');

function timeline(){const end=Math.max(5,frameList.at(-1)?.time??0);$('scrubber').max=end;$('frameCount').textContent=(timelineModel()?.name||'Bittle')+' · '+frameList.length+' keyframes';$('ruler').replaceChildren();for(let i=0;i<=5;i++){const s=document.createElement('span');s.textContent=(i*end/5).toFixed(1)+' s';$('ruler').append(s);}$('keyframes').replaceChildren();if(!frameList.length){const p=document.createElement('p');p.textContent='Pose the joints, choose a time, then add a keyframe.';$('keyframes').append(p);}frameList.forEach(f=>{const b=document.createElement('button');b.className='frame'+(selectedFrame===f.time?' selected':'');b.textContent=f.time.toFixed(2)+' s';b.onclick=async()=>{selectedFrame=f.time;$('frameTime').value=f.time;$('easing').value=f.easing;await robotCommand('seek',{time:f.time});timeline();};$('keyframes').append(b);});}
async function saveFrames(){const result=await api('/api/frames',{target:timelineTarget,frames:frameList});frameList=result.frames;timelineModel().frames=frameList;timeline();}
on('keyframe',async()=>{await flushPose();const s=timelineState(await api('/api/state'));const t=Number($('frameTime').value);const next=frameList.filter(f=>f.time!==t);next.push({time:t,pose:structuredClone(s.targets),easing:$('easing').value});const result=await api('/api/frames',{target:timelineTarget,frames:next});frameList=result.frames;timelineModel().frames=frameList;selectedFrame=t;timeline();toast('Keyframe saved at '+t+' s.');});
on('removeFrame',async()=>{if(selectedFrame===null)throw new Error('Select a keyframe first');frameList=frameList.filter(f=>f.time!==selectedFrame);selectedFrame=null;await saveFrames();});
on('clearFrames',async()=>{if(!frameList.length)return toast('The timeline is already empty.');if(!confirm(`Remove all ${frameList.length} keyframes from this motion? Saved Studio functions are not affected.`))return;frameList=[];selectedFrame=null;await saveFrames();await robotCommand('seek',{time:0});$('frameTime').value='0';toast('Timeline cleared.');});
on('demoMotion',async()=>{const zero=Object.fromEntries(timelineModel().joints.map(j=>[j.name,0]));frameList=[{time:0,pose:zero,easing:'smooth'},{time:1.5,pose:crouchPose(),easing:'smooth'},{time:3,pose:zero,easing:'smooth'}];await saveFrames();await robotCommand('seek',{time:0});toast('Crouch example loaded. This is a pose study, not a validated gait.');});
on('play',async()=>{if(frameList.length<2)throw new Error('Add at least two keyframes to play motion');await flushPose();await robotCommand('play',{value:!timelineState()?.playing,loop:$('loop').checked,speed:Number($('speed').value),hz:Number($('motionHz').value),direction:$('motionDirection').value});});
on('speed',()=>robotCommand('play',{value:timelineState()?.playing,loop:$('loop').checked,speed:Number($('speed').value),hz:Number($('motionHz').value),direction:$('motionDirection').value}),'change');
on('loop',()=>robotCommand('play',{value:timelineState()?.playing,loop:$('loop').checked,speed:Number($('speed').value),hz:Number($('motionHz').value),direction:$('motionDirection').value}),'change');
let seekTimer;on('scrubber',()=>{const t=Number($('scrubber').value),target=timelineTarget;$('frameTime').value=t.toFixed(2);clearTimeout(seekTimer);seekTimer=setTimeout(()=>command('seek',{target,time:t}).catch(e=>toast(e.message,true)),25);},'input');
on('frameTime',()=>robotCommand('seek',{time:Number($('frameTime').value)}),'change');
on('saveProject',async()=>{await codePanel?.save();await flushPose();download('bittle-experiment.json',await api('/api/project'));toast('Project saved. Keep this installation’s data folder with imported assets.');});
on('openProject',()=>$('projectFile').click());on('projectFile',async()=>{const f=$('projectFile').files[0];if(!f)return;await api('/api/project',JSON.parse(await f.text()));await refresh();window.bittleHardware?.refreshMotions?.();codePanel?.reload();focusRobot();$('projectFile').value='';toast('Project restored.');},'change');
on('help',()=>$('helpDialog').showModal());
let mappingSaveTimer;
function saveMapping(){clearTimeout(mappingSaveTimer);mappingSaveTimer=setTimeout(async()=>{try{const result=await api('/api/mapping',{target:timelineTarget,mapping});timelineModel().mapping=structuredClone(result.mapping);}catch(e){toast(e.message,true);}},250);}
async function testJoint(j,m,button){
  // Move the simulated joint and the mapped real servo +10° (or -10° near the upper limit) and back, so both can be compared.
  const robot=timelineModel(),start=Number(robot.targets?.[j.name]??0),delta=start+10<=j.upper?10:-10,hw=window.bittleHardware,live=hw?.status().connected;
  const servoAngle=v=>Math.round(m.sign*v+Number(m.offset||0));
  if(live&&!(Number.isInteger(m.servo)&&m.servo>=0&&m.servo<=15))throw new Error('Give this joint a servo number first.');
  button.disabled=true;
  try{
    await robotCommand('pose',{pose:{[j.name]:start+delta}});if(live)await hw.sendCommand(`i ${m.servo} ${servoAngle(start+delta)}`,`Servo setup · Test ${label(j.name)}`);
    await new Promise(r=>setTimeout(r,900));
    await robotCommand('pose',{pose:{[j.name]:start}});if(live)await hw.sendCommand(`i ${m.servo} ${servoAngle(start)}`,`Servo setup · Test ${label(j.name)}`);
    toast(live?`${label(j.name)}: servo ${m.servo} moved ${delta>0?'+':''}${delta}° and back. Did the real joint match the simulation?`:`${label(j.name)} moved in the simulation. Connect in Bluetooth to move the real servo too.`);
  }finally{button.disabled=false;}
}
// Servo setup: servo number, direction and offset per joint, with Test and Verified. Python export lives on the functions.
function openServoSetup(){const robot=timelineModel();mapping=structuredClone(robot.mapping);$('mapping').replaceChildren();
  for(const j of robot.joints){const m=mapping[j.name];const row=document.createElement('div');row.className='mapping-row';const name=document.createElement('span');name.textContent=label(j.name);
    const servo=document.createElement('input');servo.type='number';servo.min=0;servo.max=15;servo.value=m.servo;servo.setAttribute('aria-label',label(j.name)+' servo index');
    const sign=document.createElement('select');sign.innerHTML='<option value="1">+1</option><option value="-1">−1</option>';sign.value=m.sign;sign.setAttribute('aria-label',label(j.name)+' direction');
    const offset=document.createElement('input');offset.type='number';offset.value=m.offset;offset.setAttribute('aria-label',label(j.name)+' offset');
    const test=document.createElement('button');test.type='button';test.textContent='Test';test.title='Move this joint +10° and back, in the simulation and on the connected robot';
    const verified=document.createElement('input');verified.type='checkbox';verified.checked=m.verified;verified.setAttribute('aria-label',label(j.name)+' mapping verified');
    const unverify=()=>{m.verified=verified.checked=false;row.classList.remove('verified');};
    servo.onchange=()=>{m.servo=Number(servo.value);unverify();saveMapping();};sign.onchange=()=>{m.sign=Number(sign.value);unverify();saveMapping();};offset.onchange=()=>{m.offset=Number(offset.value);unverify();saveMapping();};
    verified.onchange=()=>{m.verified=verified.checked;row.classList.toggle('verified',m.verified);saveMapping();};
    test.onclick=()=>testJoint(j,m,test).catch(e=>toast(e.message,true));
    row.classList.toggle('verified',!!m.verified);row.append(name,servo,sign,offset,test,verified);$('mapping').append(row);}
  $('exportDialog').showModal();}
on('servoSetup',openServoSetup);
document.addEventListener('keydown',e=>{if(e.repeat||e.ctrlKey||e.metaKey||e.altKey||e.target.isContentEditable||e.target.closest('input,textarea,select,button,summary')||document.querySelector('dialog[open]:modal'))return;if(e.key.toLowerCase()==='f')focusRobot();if(e.code==='Space'){e.preventDefault();$('play').click();}});

on('openCode',()=>codePanel?.show());
// Connection status and an always-visible Stop in the top bar (fed by the Bluetooth module's state events).
window.addEventListener('bittle-hardware-state',({detail})=>{const pill=$('connectionPill');pill.dataset.state=detail.connected?(detail.testMode?'test':'live'):'off';
  $('connectionText').textContent=detail.connected?(detail.testMode?'Test mode':`Connected · ${detail.transport==='ble'?'BLE':'serial'}`):'Not connected';$('globalStop').classList.toggle('hidden',!detail.connected);});
on('connectionPill',()=>$('openHardware').click());
on('globalStop',()=>window.bittleHardware?.cancel());
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&window.bittleHardware?.status().connected)window.bittleHardware.cancel().catch(error=>toast(error.message,true));});
// Save the current timeline as a function without opening the Bluetooth panel.
on('saveAsFunction',()=>{if(frameList.length<1)throw new Error('Add at least one keyframe first.');$('saveFunctionType').value=frameList.length<2?'pose':'behavior';$('saveFunctionDialog').showModal();$('saveFunctionName').focus();});
on('saveFunctionForm',async e=>{e.preventDefault();const name=$('saveFunctionName').value.trim();if(!name)return;
  await api('/api/motions',{name,motion_type:$('saveFunctionType').value,hz:Number($('motionHz').value),speed:.5,direction:$('motionDirection').value});
  $('saveFunctionDialog').close();$('saveFunctionName').value='';await window.bittleHardware?.refreshMotions?.();
  toast(`Saved “${name}”. Run it in Bluetooth → Functions, or add it to a console button.`);},'submit');
on('objectCode',()=>codePanel.forTarget(selected));
on('actorCode',()=>codePanel.forTarget(selectedActor));
on('playFromStart',async()=>{if(frameList.length<2)throw Error('Add at least two keyframes');await robotCommand('play',{value:true,from_start:true,loop:$('loop').checked,speed:Number($('speed').value),hz:Number($('motionHz').value),direction:$('motionDirection').value});});
for(const id of ['motionHz','motionDirection'])on(id,async()=>{await robotCommand('settings',{motion_hz:Number($('motionHz').value),direction:$('motionDirection').value});timelineModel().motion_hz=Number($('motionHz').value);timelineModel().direction=$('motionDirection').value;},'change');

function renderActors(){
  $('actorList').replaceChildren();
  for(const actor of model.actors||[]){const button=document.createElement('button');button.className='scene-item'+(selectedActor===actor.id?' selected':'');const icon=document.createElement('span');icon.textContent='▧';const name=document.createElement('div');name.textContent=actor.name;const detail=document.createElement('small');detail.textContent='URDF · '+actor.joints.length+' joints';name.append(detail);button.append(icon,name);button.onclick=()=>selectActor(actor.id);$('actorList').append(button);}
}
function robotFor(target){return target==='main'?model:model?.actors?.find(actor=>actor.id===target);}
function nextSensorName(robot){const used=new Set((robot.sensors||[]).map(sensor=>sensor.name));let index=1,name='Body IMU';while(used.has(name)){index+=1;name='Body IMU '+index;}return name;}
function editorTab(name){document.querySelectorAll('[data-editor-tab]').forEach(button=>button.classList.toggle('selected',button.dataset.editorTab===name));document.querySelectorAll('.editor-section').forEach(section=>section.classList.toggle('hidden',section.id!=='editor-'+name));}
document.querySelectorAll('[data-editor-tab]').forEach(button=>button.addEventListener('click',()=>editorTab(button.dataset.editorTab)));
function buildSensors() {
  const robot=robotFor(editorTarget);if(!robot)return;
  const previous=$('sensorLink').value;
  $('sensorLink').replaceChildren(...(robot.links||[]).map(link=>new Option(label(link),link)));
  if(Array.from($('sensorLink').options).some(option=>option.value===previous))$('sensorLink').value=previous;
  $('sensorList').replaceChildren();
  if(!(robot.sensors||[]).length){const empty=document.createElement('div');empty.className='editor-empty';empty.textContent='No sensors yet. Choose a link above and add an IMU.';$('sensorList').append(empty);}
  for(const sensor of robot.sensors||[]){const card=document.createElement('div');card.className='sensor-card';const head=document.createElement('header');const title=document.createElement('div');const strong=document.createElement('b');strong.textContent=sensor.name;const link=document.createElement('small');link.textContent='IMU · '+label(sensor.link);title.append(strong,link);const remove=document.createElement('button');remove.type='button';remove.textContent='Remove';remove.onclick=async()=>{await api('/api/sensors/'+editorTarget+'/'+sensor.id,{},'DELETE');await refresh();buildRobotEditor();toast('Sensor removed from '+robot.name+'.');};head.append(title,remove);const values=document.createElement('small');values.id='sensor-value-'+sensor.id;values.className='sensor-readout';values.textContent='RPY 0°, 0°, 0° · gyro 0, 0, 0 rad/s';card.append(head,values);$('sensorList').append(card);}
}
function buildRobotEditor(){
  const robot=robotFor(editorTarget);if(!robot)return;
  $('editorRobot').value=editorTarget;$('editorRobotName').textContent=robot.name||'Bittle';$('editorUrdf').value=robot.xml;
  $('sensorName').value=nextSensorName(robot);
  $('robotStats').innerHTML=`<span><b>${(robot.links||[]).length}</b> links</span><span><b>${(robot.joints||[]).length}</b> joints</span><span><b>${(robot.sensors||[]).length}</b> sensors</span>`;
  $('editorLinks').replaceChildren(...(robot.links||[]).map(name=>{const chip=document.createElement('span');chip.textContent=label(name);return chip;}));
  $('editorJoints').replaceChildren(...(robot.joints||[]).map(joint=>{const row=document.createElement('div'),name=document.createElement('span'),limits=document.createElement('small');name.textContent=label(joint.name);limits.textContent=`${joint.lower.toFixed(1)}° … ${joint.upper.toFixed(1)}°`;row.append(name,limits);return row;}));
  $('editorDiagnostics').replaceChildren(...(robot.warnings||[]).map(message=>{const p=document.createElement('p');p.textContent=message;return p;}));
  buildSensors();
}
function syncRobotEditorTargets(){const current=editorTarget;$('editorRobot').replaceChildren(new Option('Bittle · main','main'),...(model?.actors||[]).map(actor=>new Option(actor.name,actor.id)));if(!robotFor(current))editorTarget='main';$('editorRobot').value=editorTarget;}
function openRobotEditor(target=timelineTarget){editorTarget=robotFor(target)?target:'main';syncRobotEditorTargets();buildRobotEditor();$('robotEditorDialog').showModal();}
on('openRobotEditor',()=>openRobotEditor());on('openRobotEditorFromPose',()=>openRobotEditor());
on('editorRobot',()=>{editorTarget=$('editorRobot').value;buildRobotEditor();},'change');
on('addImu',async()=>{const robot=robotFor(editorTarget);const link=$('sensorLink').value;if(!link)throw Error('This robot has no link available for a sensor');const requested=$('sensorName').value.trim();const sensor=await api('/api/sensors',{target:editorTarget,type:'imu',name:requested||nextSensorName(robot),link});await refresh();buildRobotEditor();editorTab('sensors');toast(sensor.name+' added to '+(robot.name||'Bittle')+'.');});
on('editorApplyUrdf',async()=>{const xml=$('editorUrdf').value;if(editorTarget==='main')await api('/api/urdf',{xml});else await api('/api/actors/'+editorTarget,{xml});await refresh();syncRobotEditorTargets();buildRobotEditor();toast('Robot definition validated and applied.');});
on('editorDownloadUrdf',()=>download(label(robotFor(editorTarget)?.name||'robot')+'.urdf',$('editorUrdf').value,'application/xml'));
function updateSensors(readings) {for(const sensor of Object.values(readings||{})){const el=$('sensor-value-'+sensor.id);if(!el)continue;const r=sensor.orientation.map(v=>THREE.MathUtils.radToDeg(v).toFixed(1)+'°').join(', ');const g=sensor.angular_velocity.map(v=>Number(v).toFixed(2)).join(', ');el.textContent='RPY '+r+' · gyro '+g+' rad/s';}}
function selectActor(id){
  const actor=model.actors.find(a=>a.id===id);if(!actor)return;selectedActor=id;selected=null;mainSelected=false;gizmoTarget='actor';gizmo.detach();selectionBox.visible=false;setTimelineTarget(id);$('selectRobot').classList.remove('selected');$('inspectorTitle').textContent='Robot control';$('selectionLabel').textContent='URDF';tab('actor');$('actorEmpty').classList.add('hidden');$('actorForm').classList.remove('hidden');$('actorName').value=actor.name;$('actorUrdf').value=actor.xml;$('actorFixed').checked=actor.fixed;$('actorDiagnostics').replaceChildren(...actor.warnings.map(w=>Object.assign(document.createElement('p'),{textContent:w})));['Position','Rotation'].forEach(kind=>actor[kind.toLowerCase()].forEach((v,i)=>$('actor'+kind+i).value=(kind==='Rotation'?THREE.MathUtils.radToDeg(v):v).toFixed(4)));renderActors();renderObjectList();
}
for(const kind of ['Position','Rotation'])for(let i=0;i<3;i++){const label=document.createElement('label');label.textContent=['X','Y','Z'][i];const input=document.createElement('input');input.type='number';input.step='any';input.id='actor'+kind+i;input.required=true;label.append(input);$('actor'+kind).append(label);}
on('duplicateRobot',async()=>{await codePanel?.save();const actor=await api('/api/actors',{duplicate_main:true});await refresh();selectActor(actor.id);focusRobot();});
on('actorForm',async e=>{e.preventDefault();await codePanel?.save();const body={name:$('actorName').value,xml:$('actorUrdf').value,fixed:$('actorFixed').checked,position:[0,1,2].map(i=>Number($('actorPosition'+i).value)),rotation:[0,1,2].map(i=>THREE.MathUtils.degToRad(Number($('actorRotation'+i).value)))};await api('/api/actors/'+selectedActor,body);await refresh();selectActor(selectedActor);toast('Robot updated.');},'submit');
on('groundActor',async()=>{const actor=await api('/api/actors/'+selectedActor+'/ground',{});const current=model.actors.find(a=>a.id===selectedActor);Object.assign(current,actor);selectActor(selectedActor);toast(actor.name+' placed on the ground.');});
on('deleteActor',async()=>{await api('/api/actors/'+selectedActor,{},'DELETE');selectedActor=null;await refresh();$('actorForm').classList.add('hidden');$('actorEmpty').classList.remove('hidden');});

on('timelineRobot',()=>{const target=$('timelineRobot').value;if(target==='main')selectMainRobot();else {selectActor(target);tab('pose');}},'change');
async function refresh(){loading=true;try{const target=timelineTarget;model=await api('/api/model');$('timelineRobot').replaceChildren(new Option('Bittle · main','main'),...(model.actors||[]).map(a=>new Option(a.name,a.id)));robotHandle.position.fromArray(model.robot_position||[0,0,.2]);robotHandle.rotation.set(...(model.robot_rotation||[0,0,0]),'XYZ');syncRobotFields();setTimelineTarget(target);$('gravity').value=model.gravity;$('friction').value=model.friction;$('fixed').checked=model.fixed;$('physicsHz').value=model.physics_hz;await buildRobot();await buildObjects();codePanel?.targets();syncRobotEditorTargets();if($('robotEditorDialog').open)buildRobotEditor();}finally{loading=false;}}
let actorSync=false;
function applyState(s){state=s;if(!model)return;{const live=Object.keys(s.actors||{}).sort().join(),known=(model.actors||[]).map(a=>a.id).sort().join();if(live!==known&&!actorSync){actorSync=true;refresh().finally(()=>{actorSync=false;});}}for(const [id,a] of Object.entries(s.actors||{})){for(const [name,[pos,quat]] of Object.entries(a.transforms)){const group=robotLinks.get(id+'/'+name);if(group){group.position.fromArray(pos);group.quaternion.fromArray(quat);}}}if(!(gizmo.dragging&&gizmoTarget==='robot'))for(const [name,[pos,quat]] of Object.entries(s.transforms)){const group=robotLinks.get(name);if(group){group.position.fromArray(pos);group.quaternion.fromArray(quat);}}for(const [id,[pos,quat]] of Object.entries(s.objects)){const mesh=objectMeshes.get(id);if(mesh&&!(gizmo.dragging&&id===selected)){mesh.position.fromArray(pos);mesh.quaternion.fromArray(quat);}}const active=timelineState(s)||s;const editorState=editorTarget==='main'?s:s.actors?.[editorTarget];$('run').textContent=s.running?'Ⅱ Pause physics':'▶ Run physics';$('play').textContent=active.playing?'Ⅱ Pause motion':'▶ Play motion';$('mode').textContent=s.running?'PHYSICS LIVE':'POSE MODE';$('simTime').textContent=s.time.toFixed(2)+' s';$('height').textContent=active.height.toFixed(3)+' m';$('contacts').textContent=active.contacts;$('roll').textContent=THREE.MathUtils.radToDeg(active.rpy[0]).toFixed(1)+'°';if(document.activeElement!==$('scrubber'))$('scrubber').value=active.playhead;if(active.playing)$('frameTime').value=active.playhead.toFixed(2);updatePoseInputs(active.targets);updateSensors(editorState?.sensors);if(s.running)gizmo.detach();else if(!gizmo.object){if(mainSelected)gizmo.attach(robotHandle);else if(selected&&objectMeshes.has(selected))gizmo.attach(objectMeshes.get(selected));}}
async function poll(){try{if(!loading){applyState(await api('/api/state'));$('connection').textContent='Bullet · '+state.physics_hz+' Hz';}}catch(e){$('connection').textContent='Disconnected · retrying';}setTimeout(poll,16);}
try {await refresh();applyState(await api('/api/state'));focusRobot();$('loading').remove();codePanel=(await import('./code-panel.js')).initCodePanel({api,toast,getModel:()=>model,refresh});hardwarePanel=(await import('./hardware.js')).initHardware({api,toast,getModel:()=>model,getMapping:()=>Object.keys(mapping).length?mapping:timelineModel().mapping,refresh});window.dispatchEvent(new Event('bittle-hardware-ready'));poll();}catch(e){$('loading').textContent='Could not load Bittle: '+e.message;toast(e.message,true);}
