const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const { fromState, parseTranscript } = require('../web/chat-graph.js');

class FakeElement {
  constructor(tag = 'div') {
    this.tagName = tag.toUpperCase();
    this.listeners = {};
    this.dataset = {};
    this.style = {};
    this.classList = { add() {} };
    this.hidden = false;
    this.value = '';
    this.textContent = '';
    this.options = [];
    this._innerHTML = '';
  }
  set innerHTML(value) {
    this._innerHTML = String(value);
    this.options = [...this._innerHTML.matchAll(/<option value="([^"]*)">([^<]*)<\/option>/g)]
      .map(match => ({ value: match[1], textContent: match[2] }));
  }
  get innerHTML() { return this._innerHTML; }
  addEventListener(type, listener) {
    (this.listeners[type] ||= []).push(listener);
  }
  dispatch(type, extra = {}) {
    const event = { target: this, preventDefault() {}, ...extra };
    for (const listener of this.listeners[type] || []) listener(event);
  }
  append() {}
  getBoundingClientRect() { return { width: 960, height: 640, left: 0, top: 0 }; }
}

class FakeCanvas extends FakeElement {
  constructor(width = 850, height = 600) {
    super('canvas');
    this.viewport = { width, height };
    this.width = 0;
    this.height = 0;
    this.drawnText = [];
    this.drawCalls = [];
    this.roundedRects = 0;
    this.roundedRectArgs = [];
    this.context = {
      measureText: text => ({ width: String(text).length * 6 }),
      fillText: (text, x, y) => {
        const value = String(text);
        this.drawnText.push(value);
        this.drawCalls.push({ text: value, x, y, font: this.context.font });
      },
      roundRect: (...args) => { this.roundedRects += 1; this.roundedRectArgs.push(args); },
      beginPath() {}, closePath() {}, fill() {}, stroke() {}, moveTo() {}, lineTo() {}, rect() {}, arc() {},
      setTransform() {}, clearRect() {}, fillRect() {}
    };
  }
  getContext() { return this.context; }
  getBoundingClientRect() { return { ...this.viewport, left: 0, top: 0 }; }
  setPointerCapture() {}
  hasPointerCapture() { return false; }
  releasePointerCapture() {}
}

class FakeRoot extends FakeElement {
  constructor(width = 850, height = 600) {
    super('main');
    this.controls = {
      '[data-canvas]': new FakeCanvas(width, height),
      '.hmx-stats': new FakeElement(),
      '[data-detail]': new FakeElement(),
      '[data-list]': new FakeElement(),
      '[data-session-help]': new FakeElement(),
      '[data-empty]': new FakeElement(),
      '[data-tooltip]': new FakeElement(),
      '[data-view]': new FakeElement('select'),
      '.hmx-instructions': new FakeElement(),
      '[data-agents]': new FakeElement('select'),
      '[data-sessions]': new FakeElement('select'),
      '[data-layer]': new FakeElement('select'),
      '[data-search]': new FakeElement('input'),
      '[data-paste]': new FakeElement('textarea'),
      '[data-error]': new FakeElement()
    };
    this.controls['[data-card-pages]'] = new FakeElement();
    this.controls['[data-card-page]'] = new FakeElement();
  }
  querySelector(selector) { return this.controls[selector] || null; }
  dispatch(type, extra = {}) {
    const target = extra.target || new FakeElement('button');
    if (!target.closest) target.closest = () => null;
    super.dispatch(type, { ...extra, target });
  }
}

const rafQueue = [];
const window = {};
const document = { createElement: tag => new FakeElement(tag) };
global.window = window;
global.document = document;
global.devicePixelRatio = 1;
global.requestAnimationFrame = callback => { rafQueue.push(callback); return rafQueue.length; };
global.cancelAnimationFrame = () => {};
global.ResizeObserver = class { observe() {} disconnect() {} };

const explorer = require('../web/explorer.js');
const dataContext = { window: {} };
vm.runInNewContext(fs.readFileSync(require.resolve('../web/data.js'), 'utf8'), dataContext);
const sample = fromState(dataContext.window.HM);

function drainRaf() {
  while (rafQueue.length) rafQueue.shift()();
}

function select(root, selector, value) {
  const control = root.querySelector(selector);
  control.value = value;
  control.dispatch('change');
  drainRaf();
}

function largeRects(canvas, start) {
  return canvas.roundedRectArgs.slice(start).filter(args => args[2] > 100 && args[3] > 60);
}

function overlapCount(rects) {
  let count = 0;
  for (let i = 0; i < rects.length; i += 1) {
    for (let j = i + 1; j < rects.length; j += 1) {
      const [ax, ay, aw, ah] = rects[i];
      const [bx, by, bw, bh] = rects[j];
      if (ax < bx + bw && ax + aw > bx && ay < by + bh && ay + ah > by) count += 1;
    }
  }
  return count;
}

function assertFiniteInBounds(rects, width, height, label) {
  for (const [x, y, w, h] of rects) {
    assert([x, y, w, h].every(Number.isFinite), `${label} card rectangle is finite`);
    assert(x >= 0 && y >= 0 && x + w <= width && y + h <= height, `${label} card rectangle stays within stage`);
  }
}

assert.strictEqual(
  explorer.summarizeSession({ session: 'reply-check' }, [
    { type: 'Message', session: 'reply-check', role: 'user', turn: 1, text: 'Hi' },
    { type: 'Message', session: 'reply-check', role: 'assistant', turn: 1, text: 'Answer' },
    { type: 'Message', session: 'reply-check', role: 'user', turn: 2, text: 'Thanks' }
  ]).reply,
  'Answer',
  'summary reply ignores trailing user turn'
);

const root = new FakeRoot();
const mounted = explorer.mount(root, sample);
drainRaf();
select(root, '[data-view]', 'conversations');
const canvas = root.querySelector('[data-canvas]');
assert(canvas.roundedRects > 0, 'default draw paints nonzero rounded cards');

const overviewRectStart = canvas.roundedRectArgs.length;
select(root, '[data-layer]', 'conversation');
const overviewRects = largeRects(canvas, overviewRectStart);
console.log(`conversation overview cards=${overviewRects.length} overlaps=${overlapCount(overviewRects)}`);
assert.strictEqual(overlapCount(overviewRects), 0, 'conversation overview cards do not overlap');
assertFiniteInBounds(overviewRects, 850, 600, 'conversation overview');
const sessionSummaryText = canvas.drawnText.join(' ');
assert(sessionSummaryText.includes('Users get logged out at random. Can you check auth?'), 'conversation layer paints prompt summary');
assert(sessionSummaryText.includes('Added a failing boundary test and handed the fix to Claude Code.'), 'conversation layer paints latest reply summary');
assert(canvas.roundedRects > 0, 'conversation layer paints nonzero session cards');

const focusedRectStart = canvas.roundedRectArgs.length;
const focusedDrawStart = canvas.drawCalls.length;
select(root, '[data-sessions]', 'session:codex:cx_s1');
const focusedRects = largeRects(canvas, focusedRectStart);
const focusedTitles = canvas.drawCalls.slice(focusedDrawStart)
  .filter(call => call.font === '600 14px system-ui')
  .map(call => call.text);
const focusedPageTitles = focusedTitles.slice();
const focusedSecondStart = canvas.drawCalls.length;
if (!root.querySelector('[data-card-pages]').hidden) { root.dispatch('click', { target: { closest: selector => selector === '[data-act]' ? { dataset: { act: 'card-next' } } : null } }); drainRaf(); }
const focusedSecondTitles = canvas.drawCalls.slice(focusedSecondStart)
  .filter(call => call.font === '600 14px system-ui').map(call => call.text);
const focusedAllTitles = focusedPageTitles.concat(focusedSecondTitles);
assert.deepStrictEqual(focusedAllTitles, [
  'Prompt', 'Thought · summary', 'Thought · summary', 'Thought · summary', 'Reply',
  'Prompt', 'Thought · summary', 'Thought · summary', 'Reply'
], 'focused canvas preserves prompt/thought/reply order per turn');
const focusedText = canvas.drawnText.join(' ');
assert(focusedText.includes('Users get logged out at random. Can you check auth?'), 'focused graph paints prompt text');
assert(focusedText.includes('Found it: the expiry check uses < instead of <='), 'focused graph paints reply text');
assert(focusedText.includes('Random logouts usually mean a boundary bug in the token check.'), 'focused graph paints thought text');
const detail = root.querySelector('[data-detail]').innerHTML;
assert(detail.includes('Users get logged out at random. Can you check auth?'), 'session inspector contains full prompt');
assert(detail.includes('Found it: the expiry check uses &lt; instead of &lt;='), 'session inspector contains full reply');
assert(detail.includes('Random logouts usually mean a boundary bug in the token check.'), 'session inspector contains full thought');
assert(!detail.includes('No text recorded.'), 'session inspector does not show placeholder for actual transcript');
const visible = root.querySelector('[data-list]').innerHTML.toLowerCase();
assert(!visible.includes('gemini') && !visible.includes('antigravity'), 'focused visible list excludes unrelated agents');
assert(!visible.includes('cx_s2'), 'focused visible list excludes unrelated sessions');
console.log(`focused Codex cards=${focusedRects.length} overlaps=${overlapCount(focusedRects)}`);
assert.strictEqual(overlapCount(focusedRects), 0, 'focused Codex cards do not overlap');
assertFiniteInBounds(focusedRects, 850, 600, 'focused Codex');

const mobileRoot = new FakeRoot(326, 360);
const mobileMounted = explorer.mount(mobileRoot, sample);
drainRaf();
const mobileCanvas = mobileRoot.querySelector('[data-canvas]');
select(mobileRoot, '[data-view]', 'conversations');
const mobileRectStart = mobileCanvas.roundedRectArgs.length;
select(mobileRoot, '[data-layer]', 'conversation');
const mobileCards = largeRects(mobileCanvas, mobileRectStart);
assert(mobileCards.length >= 1, 'mobile overview paints at least one content card');
assert.strictEqual(overlapCount(mobileCards), 0, 'mobile overview cards do not overlap');
assertFiniteInBounds(mobileCards, 326, 360, 'mobile overview');
const mobilePages = mobileRoot.querySelector('[data-card-pages]');
assert(!mobilePages.hidden, 'mobile overview exposes explicit pagination when all sessions cannot fit');
assert(mobileRoot.querySelector('[data-card-page]').textContent, 'mobile pagination exposes current page');
mobileMounted.destroy();

const narrowRoot = new FakeRoot(490, 490);
const narrowMounted = explorer.mount(narrowRoot, sample);
drainRaf();
const narrowCanvas = narrowRoot.querySelector('[data-canvas]');
assert(narrowCanvas.drawCalls.length > 0, 'default view renders the network hero');
select(narrowRoot, '[data-view]', 'conversations');
const narrowCards = largeRects(narrowCanvas, 0);
assert(narrowCards.length > 0, '490px default view paints session cards');
assert.strictEqual(overlapCount(narrowCards), 0, '490px default cards do not overlap');
assertFiniteInBounds(narrowCards, 490, 490, '490px default');
narrowMounted.destroy();

const longUrlGraph = fromState({
  AG: { codex: { name: 'Codex' } }, FID: {}, MEMS: {},
  LOGS: { codex: [{ id: 'long-url', title: 'Long URL', turns: [{ user: `https://example.test/${'a'.repeat(400)}`, think: [], reply: 'short reply' }] }] }
});
const longUrlRoot = new FakeRoot(326, 360);
const longUrlMounted = explorer.mount(longUrlRoot, longUrlGraph);
drainRaf();
select(longUrlRoot, '[data-view]', 'conversations');
const longUrlCanvas = longUrlRoot.querySelector('[data-canvas]');
for (const call of longUrlCanvas.drawCalls.filter(call => call.font === '14px system-ui')) {
  assert(call.x + String(call.text).length * 6 <= 306, 'long unbroken URL lines stay inside narrow card');
}
longUrlMounted.destroy();

const synthetic = { nodes: [], links: [], source: 'synthetic', sourceLabel: 'Synthetic graph' };
for (let i = 0; i < 2; i += 1) synthetic.nodes.push({ id: `agent:${i}`, type: 'Agent', agent: `Agent ${i}`, label: `Agent ${i}` });
for (let i = 0; i < 14; i += 1) {
  const agent = `Agent ${i % 2}`, session = `session:${i}`;
  synthetic.nodes.push({ id: session, type: 'Session', session, agent, label: `Run ${i}` });
  synthetic.links.push({ source: `agent:${i % 2}`, target: session, type: 'owns' });
  for (let j = 0; j < 42; j += 1) {
    const id = `${session}:message:${j}`;
    synthetic.nodes.push({ id, type: 'Message', session, agent, role: j % 2 ? 'assistant' : 'user', turn: j, text: `${j % 2 ? 'Reply' : 'Prompt'} evidence ${j}` });
    synthetic.links.push({ source: session, target: id, type: 'contains' });
  }
}
synthetic.nodes.push({ id: 'memory:artifact', type: 'Memory', label: 'Artifact evidence', text: 'Artifact-only memory evidence' });
synthetic.links.push({ source: 'agent:0', target: 'memory:artifact', type: 'recalls' });
synthetic.nodes.push({ id: 'session:0:empty', type: 'Message', session: 'session:0', agent: 'Agent 0', role: 'assistant', turn: 99, text: '' });
const syntheticRoot = new FakeRoot(490, 490);
const syntheticMounted = explorer.mount(syntheticRoot, synthetic);
drainRaf();
select(syntheticRoot, '[data-view]', 'conversations');
const syntheticCanvas = syntheticRoot.querySelector('[data-canvas]');
assert(syntheticRoot.querySelector('[data-list]').innerHTML.includes('Showing'), 'large graph list reports bounded visible subset');
select(syntheticRoot, '[data-sessions]', 'session:0');
select(syntheticRoot, '[data-layer]', 'all');
assert(syntheticCanvas.roundedRects > 0, 'focused all layer paints message cards');
const focusedTextEnd = syntheticCanvas.drawnText.join(' ');
assert(!focusedTextEnd.includes('No text recorded.'), 'focused cards omit empty transcript stubs');
const searchStart = syntheticCanvas.roundedRects;
const searchControl = syntheticRoot.querySelector('[data-search]');
searchControl.value = 'evidence 7';
searchControl.dispatch('input');
drainRaf();
assert(syntheticCanvas.roundedRects > searchStart, 'search matching content remains readable without a session card');
searchControl.value = '';
searchControl.dispatch('input');
drainRaf();
select(syntheticRoot, '[data-layer]', 'memory');
assert(syntheticCanvas.drawnText.join(' ').includes('Artifact-only memory evidence'), 'memory-only filter paints artifact cards');
const networkTextStart = syntheticCanvas.drawnText.length;
select(syntheticRoot, '[data-view]', 'network');
const networkLabels = syntheticCanvas.drawnText.slice(networkTextStart);
assert(networkLabels.length <= 8, 'narrow network caps labels');
const networkCalls = syntheticCanvas.drawCalls.slice(-networkLabels.length);
assert(networkCalls.every(call => call.x >= 0 && call.y >= 0 && call.x + String(call.text).length * 6 <= 490 && call.y <= 490), 'network labels stay within narrow stage');
syntheticCanvas.dispatch('pointerdown', { pointerId: 1, clientX: 245, clientY: 245, button: 0 });
syntheticCanvas.dispatch('pointerup', { pointerId: 1, clientX: 245, clientY: 245, button: 0 });
assert(syntheticRoot.querySelector('[data-detail]').innerHTML, 'network hit regions keep inspector usable');
syntheticMounted.destroy();

const sameRawSessionId = fromState({
  AG: { codex: { name: 'Codex' }, claude: { name: 'Claude' } }, FID: {}, MEMS: {},
  LOGS: {
    codex: [{ id: 'same', title: 'Codex same', turns: [{ user: 'Codex prompt', think: [], reply: 'Codex reply' }] }],
    claude: [{ id: 'same', title: 'Claude same', turns: [{ user: 'Claude prompt', think: [], reply: 'Claude reply' }] }]
  }
});
mounted.setGraph(sameRawSessionId);
select(root, '[data-layer]', 'conversation');
select(root, '[data-sessions]', 'session:codex:same');
assert(!root.querySelector('[data-detail]').innerHTML.includes('Claude prompt'), 'same raw session ID keeps Codex transcript isolated');
select(root, '[data-sessions]', 'session:claude:same');
assert(!root.querySelector('[data-detail]').innerHTML.includes('Codex prompt'), 'same raw session ID keeps Claude transcript isolated');

const imported = parseTranscript('User: Imported prompt\nCodex: Codex imported message\nClaude: Claude imported message');
assert(!imported.nodes.some(node => node.type === 'Thought'), 'imported transcript creates no invented thoughts');
mounted.setGraph(imported);
select(root, '[data-sessions]', 'session:imported');
const importedDetail = root.querySelector('[data-detail]').innerHTML;
assert(importedDetail.includes('Codex imported message'), 'imported session inspector includes Codex message');
assert(importedDetail.includes('Claude imported message'), 'imported session inspector includes Claude message');

mounted.destroy();
console.log('explorer content tests passed');
