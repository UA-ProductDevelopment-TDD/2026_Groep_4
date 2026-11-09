// Joint sliders laid out like Petoi's Skill Composer: head pan on top, a top view of Bittle in the middle and each
// leg's sliders at its own corner (knee outside, shoulder inside). Used by the robot console (servo degrees) and by
// Studio's pose inspector (simulation joint degrees). Positions are keyed by OpenCat servo index.
//   controls: [{servo, title, min, max, step, value, sliderId, numberId, mirrored, onInput(value), onCommit(value)}]
//   mirrored: a horizontal slider whose largest angle is on the left, for a joint that turns left as its angle grows.

export const LEGS = [['Left front', 'lf', [12, 8]], ['Right front', 'rf', [9, 13]], ['Left back', 'lb', [15, 11]], ['Right back', 'rb', [10, 14]]];
const LEFT_SERVOS = new Set([8, 12, 11, 15]);
const DIAGRAM = {0: [60, 18], 8: [38, 52], 12: [18, 52], 9: [82, 52], 13: [102, 52], 11: [38, 128], 15: [18, 128], 10: [82, 128], 14: [102, 128]};

// Default OpenCat servo index for a Bittle joint name; -1 if it is not one of Bittle's joints.
export function bittleServo(name) {
  if (/neck|head/.test(name)) return 0;
  if (/elbow/.test(name)) return 1;   // crab claw elbow (OpenCat head-tilt channel)
  if (/pincer/.test(name)) return 2;  // crab pincer (placeholder channel for the wire-pull servo)
  for (const [leg, index] of [['left-front', 8], ['right-front', 9], ['right-back', 10], ['left-back', 11]])
    if (name.includes(leg)) return /knee/.test(name) ? index + 4 : /shoulder/.test(name) ? index : -1;
  return -1;
}

export function renderJointLayout(container, controls) {
  const byServo = new Map(controls.map(control => [control.servo, control]));
  const wrapper = document.createElement('div'); wrapper.className = 'joint-layout';
  const grid = document.createElement('div'); grid.className = 'joint-sliders';
  const highlight = (servo, on) => wrapper.querySelectorAll(`[data-servo="${servo}"]`).forEach(el => el.classList.toggle('active', on));

  function control(spec, vertical) {
    // Left legs' sliders run top-to-bottom so both sides of the body feel mirrored, as the legs themselves are.
    const box = document.createElement('div'); box.className = `joint-control${vertical ? ' vertical' : ''}${vertical && LEFT_SERVOS.has(spec.servo) ? ' inverted' : ''}${!vertical && spec.mirrored ? ' mirrored' : ''}`; box.dataset.servo = spec.servo;
    const head = document.createElement('div'); head.className = 'joint-label';
    head.append(Object.assign(document.createElement('b'), {textContent: `(${spec.servo})`}), ` ${spec.title}`);
    const attrs = {min: spec.min, max: spec.max, step: spec.step ?? 1, value: spec.value};
    const slider = Object.assign(document.createElement('input'), {type: 'range', ...attrs});
    const number = Object.assign(document.createElement('input'), {type: 'number', ...attrs});
    if (spec.sliderId) slider.id = spec.sliderId;
    if (spec.numberId) number.id = spec.numberId;
    slider.setAttribute('aria-label', `${spec.label || spec.title} (servo ${spec.servo}) angle`);
    number.setAttribute('aria-label', `${spec.label || spec.title} (servo ${spec.servo}) degrees`);
    slider.oninput = () => spec.onInput?.(slider.value, slider, number);
    slider.onchange = () => spec.onCommit?.(slider.value, slider, number);
    number.onchange = () => spec.onCommit?.(number.value, slider, number);
    for (const el of [box, slider, number]) {
      el.addEventListener(el === box ? 'pointerenter' : 'focus', () => highlight(spec.servo, true));
      el.addEventListener(el === box ? 'pointerleave' : 'blur', () => highlight(spec.servo, false));
    }
    const limits = document.createElement('div'); limits.className = 'joint-range';
    if (vertical) limits.textContent = `${Math.round(spec.min)}…${Math.round(spec.max)}°`;
    else {
      const ends = [`${Math.round(spec.min)}°`, `${Math.round(spec.max)}°`];
      if (spec.mirrored) ends.reverse();
      limits.append(...ends.map(text => Object.assign(document.createElement('span'), {textContent: text})));
    }
    box.append(head, slider, limits, number);
    return box;
  }

  function diagram() {
    const ns = 'http://www.w3.org/2000/svg', svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('viewBox', '0 0 120 150'); svg.setAttribute('class', 'joint-diagram'); svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label', 'Bittle seen from above, head at the top, with servo numbers');
    const add = (parent, tag, attrs, text) => { const node = document.createElementNS(ns, tag); Object.entries(attrs).forEach(([k, v]) => node.setAttribute(k, v)); if (text !== undefined) node.textContent = text; parent.append(node); return node; };
    add(svg, 'rect', {x: 30, y: 36, width: 60, height: 108, rx: 10, class: 'body'});
    add(svg, 'path', {d: 'M48 34 L52 4 L58 18 L62 18 L68 4 L72 34 Z', class: 'head'});
    add(svg, 'text', {x: 60, y: 92, class: 'caption'}, 'BITTLE'); add(svg, 'text', {x: 60, y: 102, class: 'caption small'}, 'top view');
    add(svg, 'text', {x: 6, y: 92, class: 'side'}, 'L'); add(svg, 'text', {x: 114, y: 92, class: 'side'}, 'R');
    for (const [servo, [x, y]] of Object.entries(DIAGRAM)) {
      const spec = byServo.get(Number(servo));
      if (!spec) continue;
      const dot = add(svg, 'g', {class: 'servo-dot', 'data-servo': servo, tabindex: 0, role: 'button', 'aria-label': `${spec.label || spec.title}, servo ${servo}`});
      add(dot, 'title', {}, `${spec.label || spec.title} · servo ${servo}`);
      add(dot, 'circle', {cx: x, cy: y, r: 9});
      add(dot, 'text', {x, y: y + 3.5}, servo);
      const focus = () => wrapper.querySelector(`.joint-control[data-servo="${servo}"] input[type=range]`)?.focus();
      dot.addEventListener('click', focus);
      dot.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); focus(); } });
      dot.addEventListener('pointerenter', () => highlight(servo, true)); dot.addEventListener('pointerleave', () => highlight(servo, false));
    }
    return svg;
  }

  const parts = [];
  if (byServo.has(0)) {
    const head = control(byServo.get(0), false);
    const claw = [1, 2].filter(servo => byServo.has(servo)).map(servo => control(byServo.get(servo), false));
    if (claw.length) {   // crab: claw elbow and pincer sit next to the arm swing (servo 0)
      const group = document.createElement('div'); group.className = 'joint-claw area-head';
      group.append(head, ...claw); parts.push(group);
    } else { head.classList.add('area-head'); parts.push(head); }
  }
  const centre = document.createElement('div'); centre.className = 'joint-centre area-body'; centre.append(diagram()); parts.push(centre);
  for (const [title, area, servos] of LEGS) {
    const specs = servos.map(servo => byServo.get(servo)).filter(Boolean);
    if (!specs.length) continue;
    const group = document.createElement('div'); group.className = `joint-leg area-${area}`;
    group.append(Object.assign(document.createElement('div'), {className: 'joint-leg-title', textContent: title}));
    const sliders = document.createElement('div'); sliders.className = 'joint-leg-sliders';
    sliders.append(...specs.map(spec => control(spec, true)));
    group.append(sliders); parts.push(group);
  }
  grid.append(...parts); wrapper.append(grid); container.replaceChildren(wrapper);
  return wrapper;
}
