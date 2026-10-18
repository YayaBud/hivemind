import React from 'react';
import {
  AbsoluteFill, Sequence, OffthreadVideo, Audio, staticFile, useCurrentFrame, useVideoConfig,
  interpolate, spring, Easing, random,
} from 'remotion';
import { loadFont } from '@remotion/google-fonts/Geist';
import { loadFont as loadMono } from '@remotion/google-fonts/GeistMono';

const { fontFamily: SANS } = loadFont('normal', { weights: ['400', '500', '600', '700', '800'], subsets: ['latin'] });
const { fontFamily: MONO } = loadMono('normal', { weights: ['400', '500'], subsets: ['latin'] });

const C = {
  bg: '#050B08', bg2: '#0A1712', ink: '#F2F7F4', muted: '#8FA59A', faint: '#4D6157',
  green: '#22C55E', greenDeep: '#16A34A', glow: 'rgba(34,197,94,.55)', line: 'rgba(143,165,154,.18)',
};
const AGENTS = [
  { k: 'C', name: 'Claude Code', hue: '#E8916B' },
  { k: 'X', name: 'Codex', hue: '#E8EDEA' },
  { k: 'G', name: 'Gemini CLI', hue: '#7AA7FF' },
  { k: 'A', name: 'Antigravity', hue: '#C08BFF' },
];

// scene table (frames @30fps); every cut sits on a 2 s bar of the 120 bpm soundtrack
const S = {
  hook: [0, 120], problem: [120, 120], reveal: [240, 120], live: [360, 240], explore: [600, 240],
  convo: [840, 180], rooms: [1020, 360], memory: [1380, 120], compare: [1500, 300], arch: [1800, 240], results: [2040, 240], outro: [2280, 180],
};
export const TOTAL = 2460;

const clamp = { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' };
const ease = Easing.bezier(0.22, 1, 0.36, 1);
const lerp = (f, a, b, x, y, e = ease) => interpolate(f, [a, b], [x, y], { ...clamp, easing: e });

/* ---------- atmosphere ---------- */
const Backdrop = () => {
  const f = useCurrentFrame();
  const dots = React.useMemo(() => Array.from({ length: 70 }, (_, i) => ({
    x: random(`x${i}`) * 1920, y: random(`y${i}`) * 1080, r: 1 + random(`r${i}`) * 2.2, s: 0.2 + random(`s${i}`) * 0.6, o: 0.15 + random(`o${i}`) * 0.4,
  })), []);
  const gx = (f * 0.4) % 80;
  return (
    <AbsoluteFill style={{ background: `radial-gradient(1200px 800px at 70% 20%, #0F2A1C 0%, ${C.bg} 60%)` }}>
      <AbsoluteFill style={{
        backgroundImage: `linear-gradient(${C.line} 1px, transparent 1px), linear-gradient(90deg, ${C.line} 1px, transparent 1px)`,
        backgroundSize: '80px 80px', backgroundPosition: `${gx}px ${gx}px`, opacity: 0.35,
        maskImage: 'radial-gradient(900px 600px at 50% 50%, black, transparent)',
        WebkitMaskImage: 'radial-gradient(900px 600px at 50% 50%, black, transparent)',
      }} />
      <svg width="1920" height="1080" style={{ position: 'absolute' }}>
        {dots.map((d, i) => (
          <circle key={i} cx={(d.x + f * d.s * 0.6) % 1920} cy={(d.y - f * d.s + 1080) % 1080} r={d.r} fill={C.green} opacity={d.o * (0.6 + 0.4 * Math.sin(f / 20 + i))} />
        ))}
      </svg>
    </AbsoluteFill>
  );
};

const Grain = () => {
  const f = useCurrentFrame();
  return (
    <AbsoluteFill style={{ pointerEvents: 'none' }}>
      <svg width="1920" height="1080" style={{ position: 'absolute', opacity: 0.07, mixBlendMode: 'overlay' }}>
        <filter id="g"><feTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves="2" seed={f % 30} /></filter>
        <rect width="1920" height="1080" filter="url(#g)" />
      </svg>
      <AbsoluteFill style={{ background: 'radial-gradient(ellipse at center, transparent 55%, rgba(0,0,0,.55) 100%)' }} />
    </AbsoluteFill>
  );
};

// white flash + chromatic kick at each cut
const CutFlash = () => {
  const f = useCurrentFrame();
  const cuts = Object.values(S).map(([s]) => s).filter(s => s > 0);
  const d = Math.min(...cuts.map(c => Math.abs(f - c)));
  const o = d < 6 ? interpolate(d, [0, 6], [0.22, 0]) : 0;
  return <AbsoluteFill style={{ background: '#fff', opacity: o, pointerEvents: 'none' }} />;
};

/* ---------- type ---------- */
const Words = ({ text, at = 0, size = 96, weight = 700, color = C.ink, stagger = 3, style, highlight = [] }) => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: `0 ${size * 0.26}px`, fontFamily: SANS, fontSize: size, fontWeight: weight, letterSpacing: '-0.045em', lineHeight: 1.02, color, ...style }}>
      {text.split(' ').map((w, i) => {
        const p = spring({ frame: f - at - i * stagger, fps, config: { damping: 18, stiffness: 140 } });
        const hl = highlight.includes(i);
        return (
          <span key={i} style={{
            display: 'inline-block', opacity: p, transform: `translateY(${(1 - p) * size * 0.5}px) rotate(${(1 - p) * 4}deg)`,
            filter: `blur(${(1 - p) * 12}px)`, color: hl ? C.green : undefined, textShadow: hl ? `0 0 40px ${C.glow}` : undefined,
          }}>{w}</span>
        );
      })}
    </div>
  );
};

const Chip = ({ children, at = 0, style }) => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  const p = spring({ frame: f - at, fps, config: { damping: 16 } });
  return (
    <div style={{
      display: 'inline-flex', alignItems: 'center', gap: 10, padding: '10px 18px', borderRadius: 999,
      background: 'rgba(34,197,94,.12)', border: `1px solid rgba(34,197,94,.45)`, color: C.green,
      fontFamily: MONO, fontSize: 20, letterSpacing: '.06em', textTransform: 'uppercase',
      opacity: p, transform: `translateY(${(1 - p) * 20}px)`, ...style,
    }}>
      <span style={{ width: 9, height: 9, borderRadius: 9, background: C.green, boxShadow: `0 0 12px ${C.green}` }} />
      {children}
    </div>
  );
};

/* ---------- agents ---------- */
const Orb = ({ a, x, y, scale = 1, opacity = 1, ring = 0 }) => (
  <div style={{ position: 'absolute', left: x, top: y, transform: `translate(-50%,-50%) scale(${scale})`, opacity, textAlign: 'center' }}>
    <div style={{
      width: 120, height: 120, borderRadius: 120, display: 'grid', placeItems: 'center', margin: '0 auto',
      background: `radial-gradient(circle at 35% 30%, ${a.hue}, #1a2620 75%)`, color: '#08100C',
      fontFamily: SANS, fontWeight: 800, fontSize: 52, boxShadow: `0 0 ${40 + ring * 60}px ${a.hue}66, inset 0 0 0 2px rgba(255,255,255,.25)`,
    }}>{a.k}</div>
    <div style={{ marginTop: 16, fontFamily: SANS, fontSize: 26, fontWeight: 600, color: C.ink, letterSpacing: '-0.02em' }}>{a.name}</div>
  </div>
);

const Hook = () => {
  const f = useCurrentFrame();
  const spread = lerp(f, 30, 115, 0, 1, Easing.inOut(Easing.cubic));
  const pos = [[-1, -1], [1, -1], [-1, 1], [1, 1]];
  return (
    <AbsoluteFill>
      {AGENTS.map((a, i) => {
        const [dx, dy] = pos[i];
        const appear = lerp(f, 10 + i * 6, 40 + i * 6, 0, 1);
        return (
          <React.Fragment key={a.k}>
            <Orb a={a} x={960 + dx * (230 + spread * 470)} y={560 + dy * (150 + spread * 230)} scale={0.6 + 0.4 * appear} opacity={appear} />
            {/* each agent's private memory, scattering away */}
            {Array.from({ length: 9 }).map((_, j) => {
              const ang = random(`a${i}${j}`) * Math.PI * 2, rr = 110 + random(`r${i}${j}`) * 70 + spread * 40;
              return <div key={j} style={{
                position: 'absolute', left: 960 + dx * (230 + spread * 470) + Math.cos(ang + f / 40) * rr, top: 560 + dy * (150 + spread * 230) + Math.sin(ang + f / 40) * rr - 20,
                width: 10, height: 10, borderRadius: 10, background: a.hue, opacity: appear * 0.6,
              }} />;
            })}
          </React.Fragment>
        );
      })}
      <AbsoluteFill style={{ alignItems: 'center', justifyContent: 'center', flexDirection: 'column', gap: 10 }}>
        <Words text="Every AI agent you use" at={8} size={84} weight={600} color={C.muted} />
        <Words text="works alone." at={24} size={150} weight={800} />
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

const Problem = () => {
  const f = useCurrentFrame();
  const lines = [
    ['Claude Code', 'forgets what Codex learned.'],
    ['Gemini', 'repeats Antigravity’s dead ends.'],
    ['You', 'paste the same context. Again.'],
  ];
  const i = Math.min(2, Math.floor(f / 40));
  const lf = f - i * 40;
  const glitch = lf < 5 ? (random(`gl${f}`) - 0.5) * 30 : 0;
  return (
    <AbsoluteFill style={{ alignItems: 'center', justifyContent: 'center' }}>
      <div key={i} style={{ textAlign: 'center', transform: `translateX(${glitch}px)` }}>
        <div style={{ fontFamily: MONO, fontSize: 26, color: C.green, letterSpacing: '.18em', textTransform: 'uppercase', marginBottom: 20, opacity: lerp(lf, 0, 8, 0, 1) }}>
          {String(i + 1).padStart(2, '0')} / 03
        </div>
        <Words text={`${lines[i][0]} ${lines[i][1]}`} at={0} size={104} stagger={2} highlight={[0, ...(lines[i][0].includes(' ') ? [1] : [])]} style={{ justifyContent: 'center', maxWidth: 1500 }} />
      </div>
      {lf < 5 && <AbsoluteFill style={{ background: `linear-gradient(transparent ${random(`b${f}`) * 80}%, rgba(34,197,94,.25) 0, transparent ${random(`b${f}`) * 80 + 6}%)` }} />}
    </AbsoluteFill>
  );
};

const Logo = ({ size = 160, at = 0 }) => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  const p = spring({ frame: f - at, fps, config: { damping: 14, stiffness: 90 } });
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: size * 0.22, transform: `scale(${0.85 + 0.15 * p})`, opacity: p }}>
      <div style={{ width: size * 0.42, height: size * 0.42, borderRadius: size, background: C.green, boxShadow: `0 0 0 ${size * 0.1}px rgba(34,197,94,.18), 0 0 ${size * 0.6}px ${C.glow}` }} />
      <div style={{ fontFamily: SANS, fontWeight: 800, fontSize: size, letterSpacing: '-0.06em', color: C.ink }}>Hivemind</div>
    </div>
  );
};

const Reveal = () => {
  const f = useCurrentFrame();
  const conv = lerp(f, 0, 34, 0, 1, Easing.in(Easing.cubic));
  const pos = [[-1, -1], [1, -1], [-1, 1], [1, 1]];
  return (
    <AbsoluteFill>
      {f < 36 && AGENTS.map((a, i) => (
        <Orb key={a.k} a={a} x={960 + pos[i][0] * 700 * (1 - conv)} y={540 + pos[i][1] * 380 * (1 - conv)} scale={1 - conv * 0.7} opacity={1 - conv * 0.5} />
      ))}
      {f >= 34 && [0, 8, 16].map(d => {
        const r = lerp(f, 34 + d, 90 + d, 0, 1400, Easing.out(Easing.cubic));
        return <div key={d} style={{ position: 'absolute', left: 960 - r / 2, top: 540 - r / 2, width: r, height: r, borderRadius: r, border: `2px solid ${C.green}`, opacity: lerp(f, 34 + d, 90 + d, 0.8, 0) }} />;
      })}
      <AbsoluteFill style={{ alignItems: 'center', justifyContent: 'center', flexDirection: 'column', gap: 36 }}>
        {f >= 34 && <Logo at={34} size={170} />}
        {f >= 34 && <Words text="One shared memory for every agent." at={52} size={52} weight={500} color={C.muted} stagger={2} />}
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

/* ---------- screen footage in a 3D browser ---------- */
// keys: [{f, s, x, y}] — zoom scale and the clip pixel to centre, in scene frames
const zoomAt = (f, keys) => {
  const fs = keys.map(k => k.f);
  const g = p => interpolate(f, fs, keys.map(k => k[p]), { ...clamp, easing: Easing.inOut(Easing.cubic) });
  const s = g('s');
  const hw = 960 / s, hh = 540 / s;
  const x = Math.min(1920 - hw, Math.max(hw, g('x'))), y = Math.min(1080 - hh, Math.max(hh, g('y')));
  return `translate(${960 - x * s}px, ${540 - y * s}px) scale(${s})`;
};

const Device = ({ src, rate = 2, trim = 0, keys = [{ f: 0, s: 1, x: 960, y: 540 }], url, dur, tilt = 1 }) => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  const enter = spring({ frame: f, fps, config: { damping: 20, stiffness: 70 } });
  const exit = lerp(f, dur - 14, dur, 0, 1, Easing.in(Easing.cubic));
  const W = 1500, H = 844;
  const rx = (1 - enter) * 22 * tilt + Math.sin(f / 60) * 1.2;
  const ry = (1 - enter) * -28 * tilt + Math.cos(f / 70) * 1.5;
  const sc = 0.82 + 0.18 * enter + f * 0.00012 - exit * 0.08;
  return (
    <AbsoluteFill style={{ perspective: 2200, alignItems: 'center', justifyContent: 'center' }}>
      <div style={{
        width: W, transform: `translateY(${-30 + (1 - enter) * 160}px) rotateX(${rx}deg) rotateY(${ry}deg) scale(${sc})`,
        opacity: enter * (1 - exit), filter: `blur(${exit * 14}px)`, transformStyle: 'preserve-3d',
      }}>
        <div style={{ borderRadius: 18, overflow: 'hidden', background: '#fff', boxShadow: `0 60px 140px rgba(0,0,0,.6), 0 0 0 1px rgba(255,255,255,.08), 0 0 120px rgba(34,197,94,.18)` }}>
          <div style={{ height: 44, background: '#EEF2F0', display: 'flex', alignItems: 'center', gap: 9, padding: '0 18px', borderBottom: '1px solid #DDE3E0' }}>
            {['#FF5F57', '#FEBC2E', '#28C840'].map(c => <span key={c} style={{ width: 13, height: 13, borderRadius: 13, background: c }} />)}
            <div style={{ marginLeft: 22, flex: '0 1 560px', height: 28, borderRadius: 8, background: '#fff', border: '1px solid #DDE3E0', display: 'flex', alignItems: 'center', padding: '0 12px', fontFamily: MONO, fontSize: 15, color: '#5B6B63' }}>
              <span style={{ color: C.greenDeep, marginRight: 8 }}>●</span>{url}
            </div>
          </div>
          <div style={{ width: W, height: H, overflow: 'hidden', position: 'relative' }}>
            <div style={{ width: 1920, height: 1080, transform: `scale(${W / 1920})`, transformOrigin: '0 0' }}>
              <div style={{ width: 1920, height: 1080, transformOrigin: '0 0', transform: zoomAt(f, keys) }}>
                <OffthreadVideo src={staticFile(`clips/${src}.mp4`)} playbackRate={rate} trimBefore={trim} muted style={{ width: 1920, height: 1080 }} />
              </div>
            </div>
            {/* glass sheen sweep */}
            <div style={{ position: 'absolute', inset: 0, background: 'linear-gradient(105deg, transparent 40%, rgba(255,255,255,.18) 50%, transparent 60%)', transform: `translateX(${lerp(f, 6, 40, -1600, 1600)}px)` }} />
          </div>
        </div>
      </div>
    </AbsoluteFill>
  );
};

const Caption = ({ tag, title, at = 10, hl = [] }) => (
  <div style={{ position: 'absolute', left: 120, bottom: 64, display: 'flex', flexDirection: 'column', gap: 16, alignItems: 'flex-start' }}>
    <Chip at={at}>{tag}</Chip>
    <div style={{ padding: '18px 28px', borderRadius: 18, background: 'rgba(5,11,8,.78)', backdropFilter: 'blur(14px)', border: '1px solid rgba(255,255,255,.08)', boxShadow: '0 20px 60px rgba(0,0,0,.45)' }}>
      <Words text={title} at={at + 4} size={52} weight={700} stagger={2} highlight={hl} style={{ maxWidth: 1100 }} />
    </div>
  </div>
);

const Stat = ({ n, label, at, x, y }) => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  const p = spring({ frame: f - at, fps, config: { damping: 15 } });
  const v = Math.round(n * lerp(f, at, at + 36, 0, 1, Easing.out(Easing.cubic)));
  return (
    <div style={{
      position: 'absolute', left: x, top: y, padding: '18px 26px', borderRadius: 18, minWidth: 250,
      background: 'rgba(5,11,8,.82)', border: '1px solid rgba(34,197,94,.35)', boxShadow: `0 20px 60px rgba(0,0,0,.5), 0 0 40px rgba(34,197,94,.15)`,
      transform: `translateX(${(1 - p) * 120}px) scale(${0.9 + 0.1 * p})`, opacity: p, backdropFilter: 'blur(12px)',
    }}>
      <div style={{ fontFamily: SANS, fontSize: 60, fontWeight: 800, color: C.green, letterSpacing: '-0.04em', lineHeight: 1 }}>{v.toLocaleString('en-US')}</div>
      <div style={{ fontFamily: MONO, fontSize: 18, color: C.muted, letterSpacing: '.1em', textTransform: 'uppercase', marginTop: 8 }}>{label}</div>
    </div>
  );
};

const Live = () => (
  <AbsoluteFill>
    <Device src="live" url="127.0.0.1:5199 · workspace new-game · FalkorDB" rate={2.2} trim={30} dur={S.live[1]}
      keys={[{ f: 0, s: 1, x: 960, y: 540 }, { f: 60, s: 1.0, x: 960, y: 540 }, { f: 130, s: 1.6, x: 840, y: 430 }, { f: 200, s: 1.35, x: 900, y: 450 }, { f: 240, s: 1.15, x: 960, y: 500 }]} />
    <Stat n={1595} label="real messages" at={24} x={1500} y={150} />
    <Stat n={445} label="memory notes" at={34} x={1500} y={300} />
    <Stat n={4} label="agents · 1 graph" at={44} x={1500} y={450} />
    <Caption tag="Live · FalkorDB" title="Every chat, thought and memory from every agent. One graph." hl={[8, 9]} at={14} />
  </AbsoluteFill>
);

const Explore = () => (
  <AbsoluteFill>
    <Device src="explore" url="127.0.0.1:5199/index.html · 3D explorer" rate={1.75} trim={180} dur={S.explore[1]}
      keys={[{ f: 0, s: 1.1, x: 900, y: 500 }, { f: 56, s: 1.2, x: 1100, y: 420 }, { f: 74, s: 2.1, x: 1385, y: 200 }, { f: 108, s: 2.1, x: 1385, y: 200 }, { f: 128, s: 1.35, x: 1000, y: 520 }, { f: 240, s: 1.15, x: 960, y: 520 }]} />
    <Caption tag="Search" title="Search across every agent’s mind at once." hl={[3, 4]} at={12} />
  </AbsoluteFill>
);

const Convo = () => (
  <AbsoluteFill>
    <Device src="convo" url="127.0.0.1:5199/index.html · conversations" rate={1.6} trim={45} dur={S.convo[1]}
      keys={[{ f: 0, s: 1.1, x: 960, y: 400 }, { f: 30, s: 1.35, x: 800, y: 520 }, { f: 130, s: 1.35, x: 900, y: 520 }, { f: 180, s: 1.15, x: 960, y: 540 }]} />
    <Caption tag="Trace" title="Prompt → thinking → reply. See why an agent did what it did." hl={[2]} at={12} />
  </AbsoluteFill>
);

const Rooms = () => (
  <AbsoluteFill>
    <Device src="rooms" url="127.0.0.1:5199/rooms.html · debate room" rate={1} trim={15} dur={S.rooms[1]}
      keys={[{ f: 0, s: 1, x: 960, y: 540 }, { f: 20, s: 1, x: 960, y: 540 }, { f: 60, s: 1.45, x: 1150, y: 430 }, { f: 200, s: 1.45, x: 1150, y: 560 }, { f: 260, s: 1.15, x: 1100, y: 540 }, { f: 360, s: 1.3, x: 1150, y: 600 }]} />
    <Sequence from={0} durationInFrames={180}><Caption tag="Rooms" title="Agents take turns, recall shared memory, think out loud." hl={[4, 5]} at={12} /></Sequence>
    <Sequence from={180} durationInFrames={180}><Caption tag="Debate" title="They argue. The graph keeps score. You make the call." hl={[3]} at={4} /></Sequence>
  </AbsoluteFill>
);

const Memory = () => (
  <AbsoluteFill>
    <Device src="memory" url="127.0.0.1:5199/index.html#agents" rate={2} trim={40} dur={S.memory[1]}
      keys={[{ f: 0, s: 1.25, x: 960, y: 700 }, { f: 120, s: 1.6, x: 1000, y: 720 }]} />
    <Caption tag="Per-agent graphs" title="Four private minds. One memory they all share." hl={[3, 4]} at={10} />
  </AbsoluteFill>
);

/* ---------- what's different ---------- */
const ROWS = [
  ['Runs on', 'Paid API keys, billed per token', 'The CLIs you already subscribe to'],
  ['While they argue', 'Hidden API calls. Read logs afterwards', 'Watch every turn and thought, live'],
  ['Memory', 'Per agent or per run, then gone', 'One common graph + one per agent'],
  ['Provenance', 'No trail of who learned what', 'Who wrote it, and who recalled it'],
  ['Control', 'Write orchestration code', 'Pick agents and models. You decide'],
];
const Compare = () => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  const X0 = 120, LW = 300, CW = 690;
  return (
    <AbsoluteFill>
      <div style={{ position: 'absolute', left: X0, top: 64 }}>
        <Chip at={0}>What's different</Chip>
        <Words text="Other frameworks make agents argue. You never see it." at={6} size={60} stagger={2} highlight={[6, 7, 8]} style={{ marginTop: 18, maxWidth: 1650 }} />
      </div>
      <div style={{ position: 'absolute', left: X0 + LW, top: 300, width: CW, fontFamily: MONO, fontSize: 20, color: C.muted, letterSpacing: '.1em', textTransform: 'uppercase', opacity: lerp(f, 20, 34, 0, 1) }}>Typical agent-debate frameworks</div>
      <div style={{ position: 'absolute', left: X0 + LW + CW + 34, top: 300, width: CW, fontFamily: MONO, fontSize: 20, color: C.green, letterSpacing: '.1em', textTransform: 'uppercase', opacity: lerp(f, 20, 34, 0, 1) }}>Hivemind</div>
      <div style={{ position: 'absolute', left: X0 + LW + CW + 4, top: 280, width: CW + 30, height: 5 * 128 + 40, borderRadius: 22, border: '1px solid rgba(34,197,94,.5)', background: 'linear-gradient(180deg, rgba(34,197,94,.14), rgba(34,197,94,.03))', boxShadow: '0 0 80px rgba(34,197,94,.18)', opacity: lerp(f, 18, 34, 0, 1) }} />
      {ROWS.map(([k, bad, good], i) => {
        const at = 34 + i * 26;
        const p = spring({ frame: f - at, fps, config: { damping: 18 } });
        const strike = lerp(f, at + 10, at + 22, 0, 1);
        const q = spring({ frame: f - at - 12, fps, config: { damping: 14 } });
        const y = 360 + i * 128;
        return (
          <React.Fragment key={k}>
            <div style={{ position: 'absolute', left: X0, top: y, width: LW, fontFamily: SANS, fontSize: 26, fontWeight: 600, color: C.muted, opacity: p }}>{k}</div>
            <div style={{ position: 'absolute', left: X0 + LW, top: y - 4, width: CW - 40, display: 'flex', gap: 16, alignItems: 'flex-start', opacity: p * (1 - 0.45 * strike), transform: `translateX(${(1 - p) * -40}px)` }}>
              <span style={{ fontSize: 30, color: '#E5484D', lineHeight: 1.1 }}>✕</span>
              <span style={{ position: 'relative', fontFamily: SANS, fontSize: 32, fontWeight: 500, color: C.ink, lineHeight: 1.2 }}>
                {bad}
                <span style={{ position: 'absolute', left: 0, top: '55%', height: 3, width: `${strike * 100}%`, background: '#E5484D', borderRadius: 3 }} />
              </span>
            </div>
            <div style={{ position: 'absolute', left: X0 + LW + CW + 34, top: y - 4, width: CW - 30, display: 'flex', gap: 16, alignItems: 'flex-start', opacity: q, transform: `translateX(${(1 - q) * 40}px) scale(${0.96 + 0.04 * q})` }}>
              <span style={{ width: 34, height: 34, flex: 'none', borderRadius: 34, background: C.green, color: '#04120A', display: 'grid', placeItems: 'center', fontSize: 22, fontWeight: 800, boxShadow: `0 0 24px ${C.glow}` }}>✓</span>
              <span style={{ fontFamily: SANS, fontSize: 34, fontWeight: 700, color: C.ink, lineHeight: 1.2, letterSpacing: '-0.02em' }}>{good}</span>
            </div>
            {i < 4 && <div style={{ position: 'absolute', left: X0, top: y + 92, width: LW + 2 * CW + 64, height: 1, background: C.line, opacity: p }} />}
          </React.Fragment>
        );
      })}
    </AbsoluteFill>
  );
};

/* ---------- architecture ---------- */
const Box = ({ x, y, w, h, at, title, sub, accent }) => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  const p = spring({ frame: f - at, fps, config: { damping: 16 } });
  return (
    <div style={{
      position: 'absolute', left: x, top: y, width: w, height: h, borderRadius: 20, padding: '20px 24px',
      background: accent ? 'linear-gradient(160deg, rgba(34,197,94,.22), rgba(5,11,8,.9))' : 'rgba(10,23,18,.9)',
      border: `1px solid ${accent ? 'rgba(34,197,94,.7)' : 'rgba(255,255,255,.1)'}`, boxShadow: accent ? `0 0 60px rgba(34,197,94,.3)` : '0 20px 40px rgba(0,0,0,.4)',
      opacity: p, transform: `translateY(${(1 - p) * 40}px) scale(${0.9 + 0.1 * p})`,
    }}>
      <div style={{ fontFamily: SANS, fontWeight: 700, fontSize: 30, color: C.ink, letterSpacing: '-0.02em' }}>{title}</div>
      <div style={{ fontFamily: MONO, fontSize: 18, color: accent ? C.green : C.muted, marginTop: 8, whiteSpace: 'pre-line', lineHeight: 1.5 }}>{sub}</div>
    </div>
  );
};

const Arch = () => {
  const f = useCurrentFrame();
  const flow = (d, at) => ({ strokeDasharray: '10 12', strokeDashoffset: -f * 2, opacity: lerp(f, at, at + 15, 0, 1), d });
  const cli = [['claude -p', 170], ['codex exec', 330], ['gemini -p', 490], ['antigravity', 650]];
  return (
    <AbsoluteFill>
      <div style={{ position: 'absolute', left: 120, top: 70 }}>
        <Chip at={0}>How it works</Chip>
        <Words text="Local daemon. One FalkorDB. Five graphs." at={6} size={64} stagger={2} highlight={[3]} style={{ marginTop: 18 }} />
      </div>
      <svg width="1920" height="1080" style={{ position: 'absolute' }}>
        {cli.map(([, y], i) => <path key={i} {...flow(`M 470 ${y + 230} C 560 ${y + 230}, 560 610, 660 610`, 22 + i * 4)} stroke={C.green} strokeWidth="3" fill="none" />)}
        <path {...flow('M 1020 610 L 1180 610', 50)} stroke={C.green} strokeWidth="4" fill="none" />
        <path {...flow('M 1180 560 C 1100 400, 900 380, 860 460', 80)} stroke={C.muted} strokeWidth="2" fill="none" />
      </svg>
      {cli.map(([t, y], i) => <Box key={t} x={120} y={y + 190} w={350} h={84} at={10 + i * 4} title={t} sub="" />)}
      <Box x={660} y={520} w={360} h={180} at={36} title="Hivemind daemon" sub={'reads agent logs\nredacts · extracts memory'} />
      <Box x={1180} y={420} w={600} h={300} at={56} accent title="FalkorDB" sub={'4 agent graphs  + 1 shared graph\n\nMemory ─ WROTE ─ Agent\nThought ─ RECALLED ─ Memory\nMemory ─ SUPERSEDES ─ Memory'} />
      <Box x={660} y={300} w={360} h={130} at={80} title="MCP tools" sub={'graph_search · graph_open\nany agent can ask'} />
      <div style={{ position: 'absolute', left: 120, bottom: 60, fontFamily: MONO, fontSize: 20, color: C.muted, opacity: lerp(f, 96, 116, 0, 1) }}>
        Uses the CLIs you already pay for. No API key required. Runs on your laptop.
      </div>
    </AbsoluteFill>
  );
};

/* ---------- results ---------- */
const Bar = ({ label, v, max, at, y, good }) => {
  const f = useCurrentFrame();
  const w = lerp(f, at, at + 40, 0, v / max, Easing.out(Easing.cubic));
  return (
    <div style={{ position: 'absolute', left: 120, top: y, width: 1100 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontFamily: SANS, fontSize: 30, fontWeight: 600, color: good ? C.ink : C.muted, marginBottom: 12, opacity: lerp(f, at - 6, at + 6, 0, 1) }}>
        <span>{label}</span><span style={{ fontFamily: MONO, color: good ? C.green : C.muted }}>{Math.round(w * max)} / {max}</span>
      </div>
      <div style={{ height: 34, borderRadius: 34, background: 'rgba(255,255,255,.06)', overflow: 'hidden' }}>
        <div style={{ height: '100%', width: `${w * 100}%`, borderRadius: 34, background: good ? `linear-gradient(90deg, ${C.greenDeep}, ${C.green})` : 'rgba(143,165,154,.45)', boxShadow: good ? `0 0 30px ${C.glow}` : 'none' }} />
      </div>
    </div>
  );
};

const Results = () => {
  const f = useCurrentFrame();
  const pct = lerp(f, 100, 140, 0, 34, Easing.out(Easing.cubic));
  return (
    <AbsoluteFill>
      <div style={{ position: 'absolute', left: 120, top: 80 }}>
        <Chip at={0}>Held-out benchmark · 18 questions</Chip>
        <Words text="Graph memory finds the evidence flat search misses." at={6} size={64} stagger={2} highlight={[0, 1]} style={{ marginTop: 18, maxWidth: 1500 }} />
      </div>
      <Bar label="Hybrid graph retrieval · complete cases" v={10} max={18} at={24} y={370} good />
      <Bar label="Flat search · complete cases" v={5} max={18} at={32} y={490} />
      <Bar label="Hybrid graph retrieval · evidence groups" v={21} max={33} at={52} y={640} good />
      <Bar label="Flat search · evidence groups" v={12} max={33} at={60} y={760} />
      <div style={{ position: 'absolute', right: 120, top: 380, width: 480, textAlign: 'right', opacity: lerp(f, 92, 108, 0, 1) }}>
        <div style={{ fontFamily: SANS, fontSize: 200, fontWeight: 800, color: C.green, letterSpacing: '-0.06em', lineHeight: 0.9, textShadow: `0 0 60px ${C.glow}` }}>{Math.round(pct)}%</div>
        <div style={{ fontFamily: SANS, fontSize: 34, fontWeight: 600, color: C.ink, marginTop: 14 }}>fewer context tokens</div>
        <div style={{ fontFamily: MONO, fontSize: 18, color: C.muted, marginTop: 10, lineHeight: 1.5 }}>hybrid @800 vs flat @1,200 —<br />and still more evidence found</div>
      </div>
      <div style={{ position: 'absolute', left: 120, bottom: 54, fontFamily: MONO, fontSize: 18, color: C.muted, opacity: lerp(f, 60, 80, 0, 1) }}>
        800-token budget, local FalkorDB, 1,595 redacted messages. Measures evidence retrieval, not answer accuracy.
      </div>
    </AbsoluteFill>
  );
};

const Outro = () => {
  const f = useCurrentFrame();
  return (
    <AbsoluteFill style={{ alignItems: 'center', justifyContent: 'center', flexDirection: 'column', gap: 34, opacity: lerp(f, 150, 178, 1, 0) }}>
      {[0, 10].map(d => {
        const r = lerp(f, d, 70 + d, 0, 1600, Easing.out(Easing.cubic));
        return <div key={d} style={{ position: 'absolute', left: 960 - r / 2, top: 540 - r / 2, width: r, height: r, borderRadius: r, border: `2px solid ${C.green}`, opacity: lerp(f, d, 70 + d, 0.7, 0) }} />;
      })}
      <Logo at={4} size={190} />
      <Words text="Let your agents think together." at={20} size={54} weight={500} color={C.muted} stagger={2} />
      <div style={{ display: 'flex', gap: 14, marginTop: 20 }}>
        <Chip at={44}>Graph Hacks · Track 02</Chip>
        <Chip at={52}>Built on FalkorDB</Chip>
        <Chip at={60}>Team Handsome Squidward</Chip>
      </div>
    </AbsoluteFill>
  );
};

const SCENES = { hook: Hook, problem: Problem, reveal: Reveal, live: Live, explore: Explore, convo: Convo, rooms: Rooms, memory: Memory, compare: Compare, arch: Arch, results: Results, outro: Outro };

export const Video = () => (
  <AbsoluteFill style={{ background: C.bg, fontFamily: SANS }}>
    <Backdrop />
    {Object.entries(SCENES).map(([k, Comp]) => (
      <Sequence key={k} from={S[k][0]} durationInFrames={S[k][1]} name={k}><Comp /></Sequence>
    ))}
    <CutFlash />
    <Grain />
    <Audio src={staticFile('music.wav')} />
  </AbsoluteFill>
);
