// Façade Remastered client: renders the apartment and characters, plays the voices, takes input.
import * as THREE from "three";
import { ConvexGeometry } from "three/addons/geometries/ConvexGeometry.js";

const $ = (id) => document.getElementById(id);
const BODY_SCALE = 2.6;          // original joint units -> room units (walls are 160 tall)
const EYE_HEIGHT = 102;
const FPS = 30;                  // original animation frame rate
const ROOM = { minX: -188, maxX: 188, minZ: -488, maxZ: 125 };

// ---------- renderer, camera, lights ----------
const canvas = $("view");
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.05;
const scene = new THREE.Scene();
scene.background = new THREE.Color("#120e0c");
const camera = new THREE.PerspectiveCamera(62, 1, 1, 4000);
const player = { x: -150, z: 122, yaw: 0, pitch: -0.04 };   // yaw 0 looks toward -z (into the room)

scene.add(new THREE.HemisphereLight("#fff3e0", "#3a2c22", 1.1));
const sun = new THREE.DirectionalLight("#ffe9cf", 1.2);
sun.position.set(-150, 300, -600);
scene.add(sun);
for (const [x, z] of [[-60, -250], [120, -120], [-100, -420], [-150, 60]]) {
  const lamp = new THREE.PointLight("#ffd9a8", 40000, 520, 2);
  lamp.position.set(x, 150, z);
  scene.add(lamp);
}

function resize() {
  renderer.setSize(innerWidth, innerHeight, false);
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
}
addEventListener("resize", resize);
resize();

// ---------- apartment ----------
const texLoader = new THREE.TextureLoader();
const texCache = {};
function tex(name) {
  if (!texCache[name]) {
    const t = texLoader.load(`/api/tex/${name}.png`);
    t.colorSpace = THREE.SRGBColorSpace;
    t.anisotropy = 8;
    texCache[name] = t;
  }
  return texCache[name];
}
const deg = (d) => (d * Math.PI) / 180;
const WALL_COLORS = { LR: "#e9dccb", KI: "#f2efe8", HW: "#ddd1c2", FD: "#d8cab8", FY: "#d8cab8" };

// Face a flat thing toward the living room's middle, on whichever side of its rotation that is.
function faceRoom(mesh, x, z, ry) {
  const nx = Math.sin(deg(ry)), nz = Math.cos(deg(ry));
  const toward = (0 - x) * nx + (-200 - z) * nz;
  mesh.rotation.y = deg(ry) + (toward < 0 ? Math.PI : 0);
}

async function buildApartment() {
  const s = await (await fetch("/api/scene")).json();
  for (const p of s.planes) {
    const m = new THREE.Mesh(new THREE.PlaneGeometry(p.d, p.w),
      new THREE.MeshStandardMaterial({ color: p.ceiling ? "#f4efe7" : "#6b4f3a", roughness: p.ceiling ? 1 : 0.55 }));
    m.rotation.x = p.ceiling ? Math.PI / 2 : -Math.PI / 2;
    m.position.set(p.x, p.y, p.z);
    scene.add(m);
  }
  for (const w of s.walls) {
    const mat = w.tex
      ? new THREE.MeshBasicMaterial({ map: tex(w.tex), side: THREE.DoubleSide })
      : new THREE.MeshStandardMaterial({ color: WALL_COLORS[w.name.slice(0, 2)] || "#ddd1c2", roughness: 0.9, side: THREE.DoubleSide });
    const m = new THREE.Mesh(new THREE.PlaneGeometry(w.w, w.h), mat);
    m.position.set(w.x, w.y, w.z);
    if (w.tex) faceRoom(m, w.x, w.z, w.ry); else m.rotation.y = deg(w.ry);
    scene.add(m);
  }
  for (const f of s.furniture) {
    const m = new THREE.Mesh(new THREE.BoxGeometry(f.w, f.h, f.d),
      new THREE.MeshStandardMaterial({ color: f.color, roughness: 0.7 }));
    m.position.set(f.x, f.y, f.z);
    scene.add(m);
  }
  for (const p of s.props) {
    const mat = { map: tex(p.tex), transparent: true, alphaTest: 0.3 };
    let m;
    if (p.billboard && !p.flat) {
      m = new THREE.Sprite(new THREE.SpriteMaterial(mat));
      m.scale.set(p.w, p.h, 1);
    } else {
      m = new THREE.Mesh(new THREE.PlaneGeometry(p.w, p.h), new THREE.MeshBasicMaterial({ ...mat, side: THREE.DoubleSide }));
      if (p.flat) m.rotation.x = -Math.PI / 2; else faceRoom(m, p.x, p.z, p.ry);
    }
    m.position.set(p.x, p.flat ? p.y + 0.5 : p.y, p.z);
    scene.add(m);
  }
  return s;
}

// ---------- characters ----------
const SKIN = new THREE.Color("#e8c4a8");
const LOOKS = {
  grace: { cloth: "#7d2a3a", hair: "#3b2418", name: "Grace" },
  trip: { cloth: "#2f4a6e", hair: "#5a3a22", name: "Trip" },
};

class Character {
  constructor(who, info, frames) {
    Object.assign(this, { who, info, frames });
    this.group = new THREE.Group();
    this.pos = new THREE.Vector3();
    this.target = null;
    this.yaw = 0;
    this.visible = true;
    const J = info.joints;
    const cloth = new THREE.MeshToonMaterial({ color: LOOKS[who].cloth });
    const skin = new THREE.MeshToonMaterial({ color: SKIN });
    // Fingers (the two big clusters near the hands) and the head are skin; the rest is clothing.
    const fingers = new Set();
    for (let j = 33; j < Math.min(J, 67); j++) fingers.add(j);
    this.joints = [];
    for (let j = 0; j < J; j++) {
      const r = j === info.head ? 3.4 : fingers.has(j) ? 0.35 : 0.9;
      const mesh = new THREE.Mesh(new THREE.SphereGeometry(r, 12, 10), j === info.head || fingers.has(j) ? skin : cloth);
      this.group.add(mesh);
      this.joints.push(mesh);
    }
    const hair = new THREE.Mesh(new THREE.SphereGeometry(3.6, 14, 10, 0, Math.PI * 2, 0, Math.PI * 0.55),
      new THREE.MeshToonMaterial({ color: LOOKS[who].hair }));
    this.joints[info.head].add(hair);
    hair.position.y = 0.4;
    this.bones = info.bones.map(([a, b]) => {
      const thin = fingers.has(a) || fingers.has(b);
      const mesh = new THREE.Mesh(new THREE.CylinderGeometry(thin ? 0.25 : 0.75, thin ? 0.25 : 0.75, 1, 8),
        thin ? skin : cloth);
      this.group.add(mesh);
      return { a, b, mesh };
    });
    // Torso: a hull around the joints near the body's centreline between hips and neck,
    // rebuilt every frame so it bends with the spine.
    const rest = frames.subarray(0, J * 3);
    const headY = rest[info.head * 3 + 1];
    this.torsoJoints = [];
    for (let j = 0; j < J; j++) {
      const x = rest[j * 3], y = rest[j * 3 + 1];
      if (j !== info.head && !fingers.has(j) && Math.abs(x) < 5 && y > -14 && y < headY - 4) this.torsoJoints.push(j);
    }
    this.torso = new THREE.Mesh(new THREE.BufferGeometry(), cloth);
    this.group.add(this.torso);
    this.group.scale.setScalar(BODY_SCALE);
    this.clips = info.clips;
    this.idle = info.clips.find((c) => /neutral/i.test(c.name)) || info.clips[0];
    this.gestures = info.clips.filter((c) => /emphasis|gesture|shrug|point/i.test(c.name) && c.end - c.start > 20);
    this.play(this.idle, true);
    this.blendFrom = null;
  }
  play(clip, loop = false) {
    this.blendFrom = this.current ? this.pose() : null;
    this.blendT = 0;
    Object.assign(this, { clip, loop, t: 0 });
  }
  gesture() {
    if (this.gestures.length && Math.random() < 0.7) {
      this.play(this.gestures[Math.floor(Math.random() * this.gestures.length)]);
    }
  }
  frameIndex() {
    const len = this.clip.end - this.clip.start;
    let f = Math.floor(this.t * FPS);
    if (f >= len) {
      if (this.loop) f %= len;
      else { this.play(this.idle, true); f = 0; }
    }
    return this.clip.start + f;
  }
  pose() {
    return this.current ? this.current.slice() : null;
  }
  update(dt, talking, lookAt) {
    this.t += dt;
    const J = this.info.joints;
    const fi = this.frameIndex();
    let cur = this.frames.subarray(fi * J * 3, (fi + 1) * J * 3);
    if (this.blendFrom && this.blendT < 1) {
      this.blendT = Math.min(1, this.blendT + dt * 4);
      const mixed = new Float32Array(cur.length);
      for (let i = 0; i < cur.length; i++) mixed[i] = this.blendFrom[i] + (cur[i] - this.blendFrom[i]) * this.blendT;
      cur = mixed;
    }
    this.current = cur;
    const minY = this.info.minY ?? this.info.min_y;
    const bob = talking ? Math.sin(performance.now() / 95) * 0.35 : 0;
    for (let j = 0; j < J; j++) {
      this.joints[j].position.set(cur[j * 3], cur[j * 3 + 1] - minY + (j === this.info.head ? bob : 0), cur[j * 3 + 2]);
    }
    if (this.torsoJoints.length >= 4) {
      // Pad each point outward a little so the hull has some volume.
      const pts = [];
      for (const j of this.torsoJoints) {
        const p = this.joints[j].position;
        for (const [dx, dz] of [[1.2, 0], [-1.2, 0], [0, 1.2], [0, -1.2]]) pts.push(new THREE.Vector3(p.x + dx, p.y, p.z + dz));
      }
      this.torso.geometry.dispose();
      this.torso.geometry = new ConvexGeometry(pts);
    }
    const up = new THREE.Vector3(0, 1, 0);
    for (const { a, b, mesh } of this.bones) {
      const pa = this.joints[a].position, pb = this.joints[b].position;
      const d = new THREE.Vector3().subVectors(pb, pa);
      const len = d.length();
      mesh.position.copy(pa).addScaledVector(d, 0.5);
      mesh.scale.set(1, Math.max(len, 0.001), 1);
      mesh.quaternion.setFromUnitVectors(up, d.normalize());
    }
    // Walk toward a target spot, then turn to face whoever matters.
    if (this.target) {
      const to = new THREE.Vector3(this.target.x - this.pos.x, 0, this.target.z - this.pos.z);
      const dist = to.length();
      if (dist < 2) this.target = null;
      else {
        this.pos.addScaledVector(to.normalize(), Math.min(dist, 70 * dt));
        lookAt = this.target;
      }
    }
    if (lookAt) {
      const want = Math.atan2(lookAt.x - this.pos.x, lookAt.z - this.pos.z) + this.facingOffset;
      let diff = ((want - this.yaw + Math.PI * 3) % (Math.PI * 2)) - Math.PI;
      this.yaw += diff * Math.min(1, dt * 3);
    }
    this.group.position.copy(this.pos);
    this.group.rotation.y = this.yaw;
    this.group.visible = this.visible;
  }
}
// The original rig faces +z in its own space.
Character.prototype.facingOffset = 0;

async function loadCharacter(who) {
  const [info, buf] = await Promise.all([
    fetch(`/api/anim/${who}.json`).then((r) => r.json()),
    fetch(`/api/anim/${who}.bin`).then((r) => r.arrayBuffer()),
  ]);
  const c = new Character(who, info, new Float32Array(buf));
  scene.add(c.group);
  return c;
}

// ---------- audio and subtitles ----------
let audio = null;
let nextTime = 0;
const lines = new Map();           // id -> {speaker, text, el, start, end}
let voiceOn = true;
let speakingUntil = { grace: 0, trip: 0 };
let lastSpeaker = null;

function now() { return audio ? audio.currentTime : performance.now() / 1000; }

function addSubtitle(cls, html, delay = 0) {
  const el = document.createElement("div");
  el.className = `line ${cls}`;
  el.innerHTML = html;
  setTimeout(() => {
    $("subtitles").appendChild(el);
    while ($("subtitles").children.length > 3) $("subtitles").firstChild.remove();
  }, delay * 1000);
  return el;
}
function fadeSoon(el, after) {
  setTimeout(() => { el.classList.add("fade"); setTimeout(() => el.remove(), 700); }, after * 1000);
}
const esc = (s) => s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
const stageHtml = (t) => esc(t).replace(/\*([^*]+)\*/g, '<span class="stage">$1</span>').replace(/\[([a-z ]+)\]/g, '<span class="stage">($1)</span>');

let textClock = 0;                 // subtitles-only mode: when the current line finishes "being said"
function onLine(ev) {
  const html = `<b>${LOOKS[ev.speaker].name}</b>${stageHtml(ev.text)}`;
  const line = { ...ev, start: null, end: null };
  lines.set(ev.id, line);
  if (!voiceOn) {
    const start = Math.max(now(), textClock);
    const dur = Math.max(1.8, ev.text.replace(/\*[^*]*\*/g, "").length * 0.062);
    textClock = start + dur;
    line.start = start; line.end = textClock;
    const el = addSubtitle(ev.speaker, html, start - now());
    fadeSoon(el, textClock - now() + 4);
    setTimeout(() => startSpeaking(ev.speaker), (start - now()) * 1000);
    speakingUntil[ev.speaker] = Math.max(speakingUntil[ev.speaker], textClock);
  } else {
    line.html = html;
  }
}
function startSpeaking(who) {
  if (lastSpeaker !== who) chars[who]?.gesture();
  lastSpeaker = who;
}
function onAudio(ev) {
  if (!audio) return;
  const raw = atob(ev.pcm);
  const pcm = new Int16Array(raw.length / 2);
  for (let i = 0; i < pcm.length; i++) pcm[i] = raw.charCodeAt(2 * i) | (raw.charCodeAt(2 * i + 1) << 8);
  const buf = audio.createBuffer(1, pcm.length, 24000);
  const ch = buf.getChannelData(0);
  for (let i = 0; i < pcm.length; i++) ch[i] = pcm[i] / 32768;
  const src = audio.createBufferSource();
  src.buffer = buf;
  src.connect(audio.destination);
  const start = Math.max(audio.currentTime + 0.05, nextTime);
  src.start(start);
  nextTime = start + buf.duration;
  const line = lines.get(ev.id);
  if (!line) return;
  if (line.start === null) {
    line.start = start;
    line.el = addSubtitle(line.speaker, line.html, start - audio.currentTime);
    setTimeout(() => startSpeaking(line.speaker), (start - audio.currentTime) * 1000);
  }
  line.end = nextTime;
  speakingUntil[line.speaker] = Math.max(speakingUntil[line.speaker], nextTime);
}
function onLineEnd(ev) {
  const line = lines.get(ev.id);
  if (!line) return;
  if (voiceOn && line.start === null) {         // nothing audible (stage business only, or no voice)
    const start = Math.max(now(), nextTime);
    line.el = addSubtitle(line.speaker, line.html, start - now());
    fadeSoon(line.el, start - now() + 4);
    return;
  }
  if (line.el) fadeSoon(line.el, line.end - now() + 3);
}

// ---------- stage cues and sound effects ----------
const sfxCache = {};
async function playSfx(names) {
  if (!audio) return;
  let at = Math.max(audio.currentTime + 0.05, nextTime);
  for (const name of names) {
    if (!sfxCache[name]) {
      const data = await (await fetch(`/api/sfx/${name}.wav`)).arrayBuffer();
      sfxCache[name] = await audio.decodeAudioData(data);
    }
    const src = audio.createBufferSource();
    src.buffer = sfxCache[name];
    src.connect(audio.destination);
    src.start(at);
    at += sfxCache[name].duration + 0.15;
  }
  nextTime = Math.max(nextTime, at);   // let the characters react after the sound
}
function onCue(ev) {
  if (ev.text) fadeSoon(addSubtitle("cue", `<span class="stage">${esc(ev.text.replace(/\*/g, ""))}</span>`), 5);
  if (ev.sfx?.length) playSfx(ev.sfx);
}

// ---------- staging ----------
let spots = {};
const chars = {};
function stage(staging, instant = false) {
  for (const [who, spot] of Object.entries(staging)) {
    const c = chars[who];
    const p = spots[spot];
    if (!c || !p) continue;
    const target = new THREE.Vector3(p[0], 0, p[1]);
    if (instant) c.pos.copy(target); else c.target = target;
  }
}
function showBeat(title) {
  $("beat").textContent = title;
  $("beat").classList.add("show");
  setTimeout(() => $("beat").classList.remove("show"), 4000);
}

// ---------- events from the server ----------
function connect() {
  const es = new EventSource("/api/events");
  es.onmessage = (m) => {
    const ev = JSON.parse(m.data);
    switch (ev.type) {
      case "thinking": $("thinking").hidden = false; break;
      case "turn_end": $("thinking").hidden = true; break;
      case "line": $("thinking").hidden = true; onLine(ev); break;
      case "audio": onAudio(ev); break;
      case "line_end": onLineEnd(ev); break;
      case "beat": showBeat(ev.title); stage(ev.staging, ev.index === 0); break;
      case "cue": onCue(ev); break;
      case "exit": if (chars[ev.who]) { chars[ev.who].target = new THREE.Vector3(260, 0, -60); setTimeout(() => (chars[ev.who].visible = false), 3500); } break;
      case "enter": if (chars[ev.who]) { chars[ev.who].visible = true; chars[ev.who].pos.set(260, 0, -60); chars[ev.who].target = new THREE.Vector3(60, 0, -220); } break;
      case "kickout": $("end-title").textContent = "You've been asked to leave"; break;
      case "end": {
        const wait = Math.max(0, Math.max(nextTime, textClock) - now()) + 3;
        setTimeout(() => { $("end").hidden = false; }, wait * 1000);
        $("say").disabled = true;
        break;
      }
      case "error": console.error("server:", ev.message); break;
    }
  };
}

// ---------- input ----------
$("talk").addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = $("say").value.trim();
  if (!text) return;
  $("say").value = "";
  fadeSoon(addSubtitle("player", `${esc($("name").value)}: ${stageHtml(text)}`), 4);
  await fetch("/api/say", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) });
});

const keys = new Set();
addEventListener("keydown", (e) => { if (document.activeElement !== $("say") || e.key.startsWith("Arrow")) keys.add(e.key.toLowerCase()); });
addEventListener("keyup", (e) => keys.delete(e.key.toLowerCase()));
let drag = null;
canvas.addEventListener("pointerdown", (e) => { drag = { x: e.clientX, y: e.clientY }; canvas.setPointerCapture(e.pointerId); });
canvas.addEventListener("pointermove", (e) => {
  if (!drag) return;
  player.yaw -= (e.clientX - drag.x) * 0.004;
  player.pitch = Math.max(-0.6, Math.min(0.5, player.pitch - (e.clientY - drag.y) * 0.003));
  drag = { x: e.clientX, y: e.clientY };
});
canvas.addEventListener("pointerup", () => { drag = null; $("say").focus(); });

function movePlayer(dt) {
  const typing = document.activeElement === $("say");
  const k = (a, b) => keys.has(a) || (!typing && keys.has(b));
  const turn = (k("arrowleft", "a") ? 1 : 0) - (k("arrowright", "d") ? 1 : 0);
  const walk = (k("arrowup", "w") ? 1 : 0) - (k("arrowdown", "s") ? 1 : 0);
  player.yaw += turn * dt * 1.8;
  const nx = player.x - Math.sin(player.yaw) * walk * dt * 120;
  const nz = player.z - Math.cos(player.yaw) * walk * dt * 120;
  // Keep the guest a step away from Grace and Trip.
  const blocked = Object.values(chars).some((c) => c.visible && Math.hypot(nx - c.pos.x, nz - c.pos.z) < 32);
  if (!blocked) {
    player.x = Math.max(ROOM.minX, Math.min(ROOM.maxX, nx));
    player.z = Math.max(ROOM.minZ, Math.min(ROOM.maxZ, nz));
  }
  camera.position.set(player.x, EYE_HEIGHT, player.z);
  camera.rotation.set(player.pitch, player.yaw, 0, "YXZ");
}

// ---------- start ----------
let last = performance.now();
function frame(t) {
  const dt = Math.min(0.05, (t - last) / 1000);
  last = t;
  movePlayer(dt);
  const guest = new THREE.Vector3(player.x, 0, player.z);
  const tnow = now();
  for (const [who, c] of Object.entries(chars)) {
    const talking = tnow < speakingUntil[who];
    const other = chars[who === "grace" ? "trip" : "grace"];
    const otherTalking = other && tnow < speakingUntil[other.who];
    c.update(dt, talking, otherTalking && other.visible ? other.pos : guest);
  }
  renderer.render(scene, camera);
  requestAnimationFrame(frame);
}

async function boot() {
  const s = await buildApartment();
  spots = s.spots;
  voiceOn = s.voice;
  const [grace, trip] = await Promise.all([loadCharacter("grace"), loadCharacter("trip")]);
  Object.assign(chars, { grace, trip });
  stage({ trip: "door", grace: "foyer" }, true);
  requestAnimationFrame(frame);
  window.__game = { scene, chars, player, camera };   // for debugging in the console
}

$("knock").addEventListener("click", async () => {
  $("knock").disabled = true;
  audio = new AudioContext({ sampleRate: 24000 });
  connect();
  await fetch("/api/start", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: $("name").value, pronouns: $("pronouns").value }),
  });
  $("start").hidden = true;
  $("say").disabled = false;
  $("say").focus();
});

boot();
