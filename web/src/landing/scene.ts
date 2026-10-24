import * as THREE from "three";

/** Hero background: a field of points shaped like a volatility surface that breathes over time, a glowing price line
 *  running across it, and drifting dust. Follows the pointer a little; pauses when off screen or the tab is hidden. */
export function mountScene(canvas: HTMLCanvasElement, { reduced }: { reduced: boolean }) {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: "high-performance" });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setClearColor(0x000000, 0);

  const scene = new THREE.Scene();
  scene.fog = new THREE.Fog(0x0a0d12, 9, 26);
  const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 100);
  camera.position.set(0, 3.4, 11);
  camera.lookAt(0, 0, 0);

  const uniforms = {
    uTime: { value: 0 },
    uAmber: { value: new THREE.Color(0xf5b301) },
    uTeal: { value: new THREE.Color(0x3987e5) },
    uLow: { value: new THREE.Color(0x344052) },
  };

  // ---- the surface: a grid of points displaced by layered waves (smile across strikes, term structure in depth) ----
  const COLS = 150;
  const ROWS = 70;
  const W = 26;
  const D = 14;
  const pos = new Float32Array(COLS * ROWS * 3);
  for (let r = 0; r < ROWS; r++) {
    for (let c = 0; c < COLS; c++) {
      const i = (r * COLS + c) * 3;
      pos[i] = (c / (COLS - 1) - 0.5) * W;
      pos[i + 1] = 0;
      pos[i + 2] = (r / (ROWS - 1) - 0.5) * D;
    }
  }
  const surfaceGeo = new THREE.BufferGeometry();
  surfaceGeo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  const surfaceMat = new THREE.ShaderMaterial({
    uniforms,
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
    vertexShader: /* glsl */ `
      uniform float uTime;
      varying float vH;
      varying float vFade;
      float surface(vec2 p, float t) {
        float smile = 0.018 * p.x * p.x;                       // volatility smile across strikes
        float term = 0.35 * sin(p.y * 0.45 + t * 0.35);          // term structure in depth
        float w1 = 0.45 * sin(p.x * 0.42 + t * 0.6) * cos(p.y * 0.55 - t * 0.4);
        float w2 = 0.18 * sin(p.x * 1.1 - p.y * 0.8 + t * 0.9);
        return smile + term + w1 + w2 - 1.2;
      }
      void main() {
        vec3 p = position;
        p.y = surface(p.xz, uTime);
        vH = p.y;
        vec4 mv = modelViewMatrix * vec4(p, 1.0);
        gl_Position = projectionMatrix * mv;
        gl_PointSize = (2.6 + 2.2 * smoothstep(-1.2, 1.2, p.y)) * (13.0 / -mv.z);
        vFade = smoothstep(26.0, 6.0, -mv.z);
      }`,
    fragmentShader: /* glsl */ `
      uniform vec3 uAmber;
      uniform vec3 uTeal;
      uniform vec3 uLow;
      varying float vH;
      varying float vFade;
      void main() {
        vec2 c = gl_PointCoord - 0.5;
        float d = length(c);
        if (d > 0.5) discard;
        float k = smoothstep(-1.6, 1.4, vH);
        vec3 col = mix(uLow, uTeal, smoothstep(0.0, 0.55, k));
        col = mix(col, uAmber, smoothstep(0.55, 1.0, k));
        float a = (1.0 - d * 2.0) * (0.35 + 0.65 * k) * vFade;
        gl_FragColor = vec4(col, a);
      }`,
  });
  const surface = new THREE.Points(surfaceGeo, surfaceMat);
  scene.add(surface);

  // ---- a price line: a random walk that rolls across the front of the surface ----
  const N = 160;
  const walk: number[] = [];
  let v = 0;
  let seed = 7;
  const rand = () => ((seed = (seed * 16807) % 2147483647) / 2147483647) - 0.5;
  for (let i = 0; i < N * 2; i++) {
    v += rand() * 0.16 + Math.sin(i / 23) * 0.015;
    v *= 0.98;
    walk.push(v);
  }
  const linePos = new Float32Array(N * 3);
  const lineGeo = new THREE.BufferGeometry();
  lineGeo.setAttribute("position", new THREE.BufferAttribute(linePos, 3));
  const line = new THREE.Line(
    lineGeo,
    new THREE.LineBasicMaterial({ color: 0xf5b301, transparent: true, opacity: 0.8, blending: THREE.AdditiveBlending }),
  );
  line.position.set(5.2, 1.9, 1.2); // the empty right side of the hero, clear of the copy
  scene.add(line);
  const glow = new THREE.Line(
    lineGeo,
    new THREE.LineBasicMaterial({ color: 0xffd76a, transparent: true, opacity: 0.25, blending: THREE.AdditiveBlending }),
  );
  glow.position.copy(line.position).add(new THREE.Vector3(0, 0.02, -0.02));
  glow.scale.set(1, 1.04, 1);
  scene.add(glow);
  const head = new THREE.Mesh(new THREE.SphereGeometry(0.075, 16, 16), new THREE.MeshBasicMaterial({ color: 0xffffff }));
  scene.add(head);

  // ---- dust ----
  const DUST = 420;
  const dustPos = new Float32Array(DUST * 3);
  for (let i = 0; i < DUST; i++) {
    dustPos[i * 3] = (Math.random() - 0.5) * 30;
    dustPos[i * 3 + 1] = Math.random() * 8 - 1;
    dustPos[i * 3 + 2] = (Math.random() - 0.5) * 18;
  }
  const dustGeo = new THREE.BufferGeometry();
  dustGeo.setAttribute("position", new THREE.BufferAttribute(dustPos, 3));
  const dust = new THREE.Points(
    dustGeo,
    new THREE.PointsMaterial({ color: 0x9aa7b8, size: 0.035, transparent: true, opacity: 0.45, depthWrite: false }),
  );
  scene.add(dust);

  // ---- sizing ----
  const resize = () => {
    const w = canvas.clientWidth;
    const h = canvas.clientHeight;
    if (!w || !h) return;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    // keep the surface filling narrow screens
    camera.position.z = w / h < 0.8 ? 15 : 11;
    camera.updateProjectionMatrix();
    // on phones the copy fills the screen: keep only the surface behind it
    line.visible = glow.visible = head.visible = w / h >= 0.8;
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
  const io = new IntersectionObserver(([e]) => {
    visible = e.isIntersecting;
    if (visible && !document.hidden) loop();
  });
  io.observe(canvas);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && visible) loop();
  });

  const clock = new THREE.Clock();
  let offset = 0;
  const frame = () => {
    const dt = Math.min(clock.getDelta(), 0.05);
    const t = clock.elapsedTime;
    uniforms.uTime.value = t;
    offset += dt * 9;
    const startIdx = Math.floor(offset) % N;
    const frac = offset % 1;
    for (let i = 0; i < N; i++) {
      const a = walk[startIdx + i];
      const b = walk[startIdx + i + 1];
      linePos[i * 3] = (i / (N - 1) - 0.5) * 9;
      linePos[i * 3 + 1] = a + (b - a) * frac;
      linePos[i * 3 + 2] = 0;
    }
    lineGeo.attributes.position.needsUpdate = true;
    head.position.set(line.position.x + linePos[(N - 1) * 3], line.position.y + linePos[(N - 1) * 3 + 1], line.position.z);

    dust.rotation.y = t * 0.012;
    look.x += (target.x - look.x) * 0.04;
    look.y += (target.y - look.y) * 0.04;
    camera.position.x = look.x * 1.1;
    camera.position.y = 3.4 - look.y * 0.6;
    camera.lookAt(0, 0.2, 0);
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
