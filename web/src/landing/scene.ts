import * as THREE from "three";

/** Hero background: a live candlestick chart drifting across the scene (new candles form at the right edge, with a
 *  moving average riding them), over a faint field of points and drifting dust. Candles are dim behind the copy on the
 *  left and bright on the right. Follows the pointer a little; pauses when off screen or the tab is hidden. */
export function mountScene(canvas: HTMLCanvasElement, { reduced }: { reduced: boolean }) {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: "high-performance" });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setClearColor(0x000000, 0);

  const scene = new THREE.Scene();
  scene.fog = new THREE.Fog(0x0a0d12, 10, 30);
  const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 100);
  camera.position.set(0, 0.6, 12); // level with the chart, so candles face the viewer

  const BG = new THREE.Color(0x0a0d12);
  const UP = new THREE.Color(0x22c55e);
  const DOWN = new THREE.Color(0xf43f5e);

  // ---- candles ----
  const COUNT = 46; // visible candles
  const Z = -2;
  const LOOK_Y = 0.6;
  // Laid out from the visible frustum in resize(): the chart spans the width and sits in the lower part of the hero,
  // below the headline, whatever the screen's shape.
  let SPACING = 0.46;
  let BODY_W = 0.28;
  let SCALE = 2.4; // price units -> world units
  let LEFT = -((COUNT - 1) * SPACING) / 2;
  let BASE_Y = -0.55;

  let seed = 11;
  const rand = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  let last = 0;
  let drift = 0;
  const next = () => {
    drift = drift * 0.9 + (rand() - 0.5) * 0.08; // trends that come and go
    const o = last;
    const c = o + drift + (rand() - 0.5) * 0.3;
    const h = Math.max(o, c) + rand() * 0.14;
    const l = Math.min(o, c) - rand() * 0.14;
    last = THREE.MathUtils.clamp(c * 0.9, -0.5, 0.5); // pulled back to the middle of the band
    return { o, h, l, c };
  };
  type C = { o: number; h: number; l: number; c: number };
  const candles: C[] = Array.from({ length: COUNT + 1 }, next);
  let forming = { ...next(), target: 0 };
  forming.target = forming.c;
  forming.c = forming.o;
  forming.h = forming.o;
  forming.l = forming.o;

  const box = new THREE.BoxGeometry(1, 1, 1);
  // Opaque on purpose: transparent instances are re-sorted every frame and flicker where bodies and wicks overlap.
  const mat = new THREE.MeshBasicMaterial();
  const bodies = new THREE.InstancedMesh(box, mat, COUNT + 2);
  const wicks = new THREE.InstancedMesh(box, mat, COUNT + 2);
  bodies.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
  wicks.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
  scene.add(bodies, wicks);

  const ma = new THREE.Line(
    new THREE.BufferGeometry().setAttribute("position", new THREE.BufferAttribute(new Float32Array((COUNT + 2) * 3), 3)),
    new THREE.LineBasicMaterial({ color: 0xc99504 }),
  );
  scene.add(ma);
  const pulse = new THREE.Mesh(new THREE.SphereGeometry(0.06, 16, 16), new THREE.MeshBasicMaterial({ color: 0xffffff }));
  scene.add(pulse);

  const m4 = new THREE.Matrix4();
  const col = new THREE.Color();
  const draw = (shift: number) => {
    const all: C[] = [...candles, forming];
    const maPos = ma.geometry.attributes.position as THREE.BufferAttribute;
    let ema = all[0].c;
    for (let i = 0; i < all.length; i++) {
      const k = all[i];
      const x = LEFT + (i - shift) * SPACING;
      const up = k.c >= k.o;
      const bodyH = Math.max(Math.abs(k.c - k.o) * SCALE, 0.015);
      const mid = BASE_Y + ((k.o + k.c) / 2) * SCALE;
      m4.makeScale(BODY_W, bodyH, 0.02).setPosition(x, mid, Z); // flat: a chart, not blocks
      bodies.setMatrixAt(i, m4);
      m4.makeScale(BODY_W * 0.14, Math.max((k.h - k.l) * SCALE, 0.02), 0.02).setPosition(x, BASE_Y + ((k.h + k.l) / 2) * SCALE, Z - 0.01);
      wicks.setMatrixAt(i, m4);
      // brighter to the right: behind the headline the chart stays a texture, not a distraction
      const fade = THREE.MathUtils.smoothstep(x, LEFT + COUNT * SPACING * 0.35, LEFT + COUNT * SPACING * 0.85);
      col.copy(BG).lerp(up ? UP : DOWN, 0.22 + 0.78 * fade);
      bodies.setColorAt(i, col);
      wicks.setColorAt(i, col);
      ema = ema + (k.c - ema) * (2 / 10);
      maPos.setXYZ(i, x, BASE_Y + ema * SCALE, Z + 0.12);
    }
    bodies.count = wicks.count = all.length;
    maPos.needsUpdate = true;
    ma.geometry.setDrawRange(0, all.length);
    bodies.instanceMatrix.needsUpdate = wicks.instanceMatrix.needsUpdate = true;
    if (bodies.instanceColor) bodies.instanceColor.needsUpdate = true;
    if (wicks.instanceColor) wicks.instanceColor.needsUpdate = true;
    const lx = LEFT + (all.length - 1 - shift) * SPACING;
    pulse.position.set(lx, BASE_Y + forming.c * SCALE, Z + 0.15);
  };

  // ---- a faint field of points under the chart, for depth ----
  const COLS = 120;
  const ROWS = 40;
  const pts = new Float32Array(COLS * ROWS * 3);
  for (let r = 0; r < ROWS; r++)
    for (let c = 0; c < COLS; c++) {
      const i = (r * COLS + c) * 3;
      pts[i] = (c / (COLS - 1) - 0.5) * 30;
      pts[i + 1] = -1.6;
      pts[i + 2] = (r / (ROWS - 1) - 0.5) * 16 - 2;
    }
  const fieldGeo = new THREE.BufferGeometry().setAttribute("position", new THREE.BufferAttribute(pts, 3));
  const fieldMat = new THREE.ShaderMaterial({
    uniforms: { uTime: { value: 0 } },
    transparent: true,
    depthWrite: false,
    vertexShader: /* glsl */ `
      uniform float uTime;
      varying float vA;
      void main() {
        vec3 p = position;
        p.y += 0.25 * sin(p.x * 0.35 + uTime * 0.5) * cos(p.z * 0.4 + uTime * 0.3);
        vec4 mv = modelViewMatrix * vec4(p, 1.0);
        gl_Position = projectionMatrix * mv;
        gl_PointSize = 2.0 * (12.0 / -mv.z);
        vA = smoothstep(30.0, 8.0, -mv.z) * 0.5;
      }`,
    fragmentShader: /* glsl */ `
      varying float vA;
      void main() {
        if (length(gl_PointCoord - 0.5) > 0.5) discard;
        gl_FragColor = vec4(0.36, 0.45, 0.6, vA);
      }`,
  });
  scene.add(new THREE.Points(fieldGeo, fieldMat));

  // ---- dust ----
  const DUST = 360;
  const dustPos = new Float32Array(DUST * 3);
  for (let i = 0; i < DUST; i++) {
    dustPos[i * 3] = (Math.random() - 0.5) * 30;
    dustPos[i * 3 + 1] = Math.random() * 8 - 1.5;
    dustPos[i * 3 + 2] = (Math.random() - 0.5) * 18;
  }
  const dust = new THREE.Points(
    new THREE.BufferGeometry().setAttribute("position", new THREE.BufferAttribute(dustPos, 3)),
    new THREE.PointsMaterial({ color: 0x9aa7b8, size: 0.03, transparent: true, opacity: 0.4, depthWrite: false }),
  );
  scene.add(dust);

  // ---- sizing ----
  const resize = () => {
    const w = canvas.clientWidth;
    const h = canvas.clientHeight;
    if (!w || !h) return;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.position.z = w / h < 0.8 ? 19 : 12;
    camera.updateProjectionMatrix();
    // visible size of the plane the candles live on
    const dist = camera.position.z - Z;
    const halfH = Math.tan(THREE.MathUtils.degToRad(camera.fov / 2)) * dist;
    const halfW = halfH * camera.aspect;
    SPACING = (2 * halfW * 1.04) / COUNT;
    BODY_W = SPACING * 0.6;
    LEFT = -((COUNT - 1) * SPACING) / 2;
    SCALE = halfH * 0.4; // a move of 1 price unit = 20% of the screen height (prices stay within about ±0.6)
    BASE_Y = LOOK_Y - halfH * 0.36; // about two thirds of the way down, under the headline
  };
  resize();
  window.addEventListener("resize", resize);

  // ---- pointer parallax ----
  const target = { x: 0, y: 0 };
  const look = { x: 0, y: 0 };
  window.addEventListener(
    "pointermove",
    (e) => {
      target.x = (e.clientX / window.innerWidth - 0.5) * 2;
      target.y = (e.clientY / window.innerHeight - 0.5) * 2;
    },
    { passive: true },
  );

  // ---- loop, paused when nobody can see it ----
  let visible = true;
  let raf = 0;
  new IntersectionObserver(([e]) => {
    visible = e.isIntersecting;
    if (visible && !document.hidden) loop();
  }).observe(canvas);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && visible) loop();
  });

  const clock = new THREE.Clock();
  const CANDLE_SECONDS = 1.1;
  let age = 0;
  const frame = () => {
    const dt = Math.min(clock.getDelta(), 0.05);
    const t = clock.elapsedTime;
    fieldMat.uniforms.uTime.value = t;
    // the forming candle ticks toward its close, then a new one starts and the chart slides left
    age += dt;
    const p = Math.min(age / CANDLE_SECONDS, 1);
    const noise = Math.sin(t * 13) * 0.05 * (1 - p);
    forming.c = forming.o + (forming.target - forming.o) * p + noise;
    forming.h = Math.max(forming.h, forming.c);
    forming.l = Math.min(forming.l, forming.c);
    if (age >= CANDLE_SECONDS) {
      age = 0;
      candles.shift();
      candles.push({ o: forming.o, h: forming.h, l: forming.l, c: forming.target });
      const n = next();
      const o = forming.target;
      forming = { o, h: o, l: o, c: o, target: THREE.MathUtils.clamp(o * 0.9 + (n.c - n.o), -0.6, 0.6) }; // stays in its band
    }
    draw(p);
    dust.rotation.y = t * 0.012;
    look.x += (target.x - look.x) * 0.04;
    look.y += (target.y - look.y) * 0.04;
    camera.position.x = look.x * 0.5;
    camera.position.y = LOOK_Y - look.y * 0.25;
    camera.lookAt(0, LOOK_Y, Z);
    renderer.render(scene, camera);
  };
  function loop() {
    cancelAnimationFrame(raf);
    const tick = () => {
      if (!visible || document.hidden) return;
      frame();
      if (!reduced) raf = requestAnimationFrame(tick);
    };
    tick();
  }
  loop();
}
