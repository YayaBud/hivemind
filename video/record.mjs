// Scripted real-app screen recordings via CDP screencast -> CFR 30fps mp4 in public/clips/.
import { chromium } from 'playwright-core';
import ffmpeg from 'ffmpeg-static';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const BASE = 'http://127.0.0.1:5199';
const W = 1920, H = 1080;
const only = process.argv[2];

// Visible cursor: screencast frames have no OS cursor.
const CURSOR = `
addEventListener('DOMContentLoaded', () => {
  const c = document.createElement('div');
  c.id = '__cur';
  c.innerHTML = '<svg width="34" height="34" viewBox="0 0 24 24"><path d="M4 2 L4 19 L8.5 15 L11.5 22 L14.5 20.8 L11.6 14 L18 14 Z" fill="#111816" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/></svg><i></i>';
  const s = document.createElement('style');
  s.textContent = '#__cur{position:fixed;left:0;top:0;z-index:2147483647;pointer-events:none;transform:translate(-200px,-200px);filter:drop-shadow(0 3px 6px rgba(0,0,0,.25))}#__cur svg{position:absolute;left:-5px;top:-3px}#__cur i{position:absolute;left:-22px;top:-22px;width:44px;height:44px;border-radius:50%;background:rgba(22,163,74,.35);transform:scale(0);opacity:0;transition:transform .35s,opacity .5s}#__cur.down i{transform:scale(1);opacity:1;transition:transform .12s,opacity .12s}html{scrollbar-width:none}*{user-select:none!important}';
  document.head.appendChild(s); document.body.appendChild(c);
  addEventListener('mousemove', e => c.style.transform = 'translate(' + e.clientX + 'px,' + e.clientY + 'px)', true);
  addEventListener('mousedown', () => c.classList.add('down'), true);
  addEventListener('mouseup', () => setTimeout(() => c.classList.remove('down'), 120), true);
});`;

let mx = W / 2, my = H / 2;
async function glide(p, x, y, ms = 700) {
  const steps = Math.max(8, Math.round(ms / 16));
  const sx = mx, sy = my;
  for (let i = 1; i <= steps; i++) {
    const t = i / steps, e = t < .5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
    await p.mouse.move(sx + (x - sx) * e, sy + (y - sy) * e);
    await p.waitForTimeout(ms / steps);
  }
  mx = x; my = y;
}
async function click(p, x, y, ms) { await glide(p, x, y, ms); await p.mouse.down(); await p.waitForTimeout(90); await p.mouse.up(); }
async function clickSel(p, sel, ms) {
  const b = await p.locator(sel).first().boundingBox();
  await click(p, b.x + b.width / 2, b.y + b.height / 2, ms);
}
async function drag(p, dx, dy, ms = 2500) {
  await p.mouse.down();
  const steps = Math.round(ms / 16), sx = mx, sy = my;
  for (let i = 1; i <= steps; i++) {
    const t = i / steps, e = (1 - Math.cos(Math.PI * t)) / 2;
    await p.mouse.move(sx + dx * e, sy + dy * e);
    await p.waitForTimeout(16);
  }
  await p.mouse.up(); mx = sx + dx; my = sy + dy;
}
async function typeSlow(p, text) { for (const ch of text) { await p.keyboard.type(ch); await p.waitForTimeout(70 + Math.random() * 60); } }
async function smoothScroll(p, y, ms = 900) {
  await p.evaluate(([y, ms]) => new Promise(r => {
    const s = scrollY, t0 = performance.now();
    const f = t => { const k = Math.min(1, (t - t0) / ms), e = (1 - Math.cos(Math.PI * k)) / 2; scrollTo(0, s + (y - s) * e); k < 1 ? requestAnimationFrame(f) : r(); };
    requestAnimationFrame(f);
  }), [y, ms]);
}

async function record(browser, name, url, script) {
  const ctx = await browser.newContext({ viewport: { width: W, height: H }, deviceScaleFactor: 1 });
  await ctx.addInitScript(CURSOR);
  const p = await ctx.newPage();
  await p.goto(url); await p.waitForTimeout(2500);
  mx = W / 2; my = H / 2; await p.mouse.move(mx, my);
  const dir = path.resolve('frames', name);
  fs.rmSync(dir, { recursive: true, force: true }); fs.mkdirSync(dir, { recursive: true });
  const cdp = await ctx.newCDPSession(p);
  const frames = [];
  cdp.on('Page.screencastFrame', async f => {
    const file = path.join(dir, `${String(frames.length).padStart(5, '0')}.jpg`);
    fs.writeFileSync(file, Buffer.from(f.data, 'base64'));
    frames.push({ file, t: performance.now() / 1000 });
    cdp.send('Page.screencastFrameAck', { sessionId: f.sessionId }).catch(() => {});
  });
  await cdp.send('Page.startScreencast', { format: 'jpeg', quality: 92, maxWidth: W, maxHeight: H, everyNthFrame: 1 });
  // keep compositor painting so static moments still emit frames
  await p.evaluate(() => { const d = document.createElement('div'); d.style.cssText = 'position:fixed;left:0;bottom:0;width:1px;height:1px;opacity:.01;z-index:-1'; document.body.appendChild(d); let k = 0; (function f() { d.style.transform = `translateX(${k++ % 2}px)`; requestAnimationFrame(f); })(); });
  const t0 = Date.now();
  await script(p);
  const tEnd = Date.now() / 1000;
  await cdp.send('Page.stopScreencast');
  await p.waitForTimeout(300);
  await ctx.close();
  // concat list with real durations -> constant 30fps
  let list = '';
  frames.forEach((f, i) => {
    const next = i + 1 < frames.length ? frames[i + 1].t : f.t + 1 / 30;
    list += `file '${f.file.replace(/\\/g, '/')}'\nduration ${Math.max(0.001, next - f.t).toFixed(4)}\n`;
  });
  list += `file '${frames.at(-1).file.replace(/\\/g, '/')}'\n`;
  fs.writeFileSync(path.join(dir, 'list.txt'), list);
  fs.mkdirSync('public/clips', { recursive: true });
  execFileSync(ffmpeg, ['-y', '-loglevel', 'error', '-f', 'concat', '-safe', '0', '-i', path.join(dir, 'list.txt'),
    '-vf', `fps=30,scale=${W}:${H}:flags=lanczos,format=yuv420p`, '-c:v', 'libx264', '-crf', '14', '-preset', 'slow',
    `public/clips/${name}.mp4`]);
  console.log(name, frames.length, 'frames', ((Date.now() - t0) / 1000).toFixed(1) + 's');
}

const SHOTS = {
  // real FalkorDB-backed workspace: scale shot
  live: [`${BASE}/index.html`, async p => {
    await p.evaluate(() => scrollTo(0, 300));
    await p.waitForTimeout(600);
    await glide(p, 760, 420, 900);
    await drag(p, 520, 140, 3200);
    await p.waitForTimeout(300);
    await glide(p, 960, 470, 500);
    for (let i = 0; i < 6; i++) { await p.mouse.wheel(0, -120); await p.waitForTimeout(160); }
    await drag(p, -380, -60, 2600);
    await p.waitForTimeout(800);
  }],
  // sample workspace: click a node, search, orbit
  explore: [`${BASE}/index.html?demo`, async p => {
    await smoothScroll(p, 170, 800);
    await p.waitForTimeout(400);
    await glide(p, 1300, 520, 900);
    await drag(p, -260, 80, 2200);
    await p.waitForTimeout(500);
    await clickSel(p, 'input[placeholder="Search full text"]', 900);
    await typeSlow(p, 'token expiry');
    await p.waitForTimeout(1800);
    await glide(p, 1000, 600, 800);
    await drag(p, 300, -40, 2200);
    await p.waitForTimeout(700);
  }],
  // conversation cards view
  convo: [`${BASE}/index.html?demo`, async p => {
    await smoothScroll(p, 170, 700);
    await p.waitForTimeout(300);
    await clickSel(p, 'select >> nth=0', 900);
    await p.selectOption('select >> nth=0', 'conversations');
    await p.keyboard.press('Escape');
    await p.waitForTimeout(1600);
    await glide(p, 700, 600, 900);
    await p.waitForTimeout(800);
    await click(p, 700, 600, 300);
    await p.waitForTimeout(2200);
    await smoothScroll(p, 700, 1600);
    await p.waitForTimeout(1500);
  }],
  // 2D shared memory overview + agent graphs, click memory
  memory: [`${BASE}/index.html?demo`, async p => {
    await smoothScroll(p, 1680, 1800);
    await p.waitForTimeout(900);
    const nodes = p.locator('#grid [id^="n-"]');
    const n = await nodes.count();
    for (let i = 0; i < n; i++) { const b = await nodes.nth(i).boundingBox(); if (b && b.y > 300 && b.y < 900 && b.x > 700) { await click(p, b.x + b.width / 2, b.y + b.height / 2, 1100); break; } }
    await p.waitForTimeout(3000);
    await glide(p, 900, 800, 900);
    await p.waitForTimeout(1500);
  }],
  // debate room at 4x
  rooms: [`${BASE}/rooms.html`, async p => {
    await p.waitForTimeout(400);
    await clickSel(p, '#mode button[data-v="debate"]', 900);
    await p.waitForTimeout(500);
    await p.selectOption('#speed', '4');
    await clickSel(p, '#start', 1000);
    await p.waitForTimeout(800);
    await glide(p, 1200, 700, 1200);
    await p.waitForTimeout(24000);
  }],
};

const browser = await chromium.launch({ executablePath: CHROME, args: ['--hide-scrollbars'] });
for (const [name, [url, fn]] of Object.entries(SHOTS)) if (!only || only === name) await record(browser, name, url, fn);
await browser.close();
