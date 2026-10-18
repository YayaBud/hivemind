// docs/deck/deck.html -> slides/slide-NN.png (3840x2160) + PDF + PPTX (image slides + speaker notes).
const path = require('path');
const fs = require('fs');
const { chromium } = require(path.join(__dirname, '..', 'video', 'node_modules', 'playwright-core'));
const pptxgen = require('pptxgenjs');

const DECK = path.join(__dirname, '..', 'docs', 'deck');
const SLIDES = path.join(DECK, 'slides');
const NAME = 'Hivemind-HandsomeSquidward';
const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';

const NOTES = [
  'Hivemind gives Claude Code, Codex, Gemini CLI and Antigravity one shared memory in FalkorDB. Four agents, five graphs, ten MCP tools, and a live workspace of 1,595 real messages. Team Handsome Squidward, Track 02.',
  'On one real project the agents produced about 250k tokens of decisions and findings, split across separate logs. None of it is visible to the other agents, so the human copies context by hand.',
  'Agent-debate frameworks usually run hidden API calls with per-run memory. Hivemind runs on the CLIs you already pay for, shows every turn live, and keeps one common graph plus one per agent, with provenance on every memory.',
  'A daemon reads agent logs and redacts them, builds a graph per agent, extracts shared memories into the common graph, and agents recall over MCP. Each cross-agent recall becomes a RECALLED edge.',
  'The Graph page on the live FalkorDB workspace: 3D explorer, filters, full-text search, and the source behind every node.',
  'Rooms: agents take turns with the model you pick. Debate: propose, argue, vote; the graph scores it and a human confirms the winner.',
  'Four agent graphs plus the common graph in one FalkorDB. Recall is a full-text query ranked by how often other agents used each memory. The query shown is the one in hivemind/store.py.',
  'Held-out benchmark, 18 questions: hybrid graph retrieval completed 10 versus 5 for flat search at the same 800-token budget, found 21 of 33 evidence groups versus 12, and used 34% fewer context tokens than flat search at 1,200 tokens.',
  'Runtime proof: auth enforced, restart persistence, zero mismatches over 120 context rechecks, 54 Python tests. Limits stay visible: latest-request cases 0 of 2, and this measures retrieval, not answer accuracy.',
  'Next: embeddings and query rewriting on a new sealed holdout, the Antigravity parser and latest-turn recall, and shared team workspaces. Thank you.',
];

(async () => {
  fs.mkdirSync(SLIDES, { recursive: true });
  for (const f of fs.readdirSync(SLIDES)) if (/^slide-\d+\.png$/.test(f)) fs.unlinkSync(path.join(SLIDES, f));
  const browser = await chromium.launch({ executablePath: CHROME });
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 2 });
  await page.goto('file:///' + path.join(DECK, 'deck.html').replace(/\\/g, '/'));
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(800);
  const n = await page.locator('section.s').count();
  for (let i = 0; i < n; i++) {
    await page.locator('section.s').nth(i).screenshot({ path: path.join(SLIDES, `slide-${String(i + 1).padStart(2, '0')}.png`) });
  }
  await browser.close();
  // PDF from the screenshots (one JPEG per page): a vector print of the CSS glows/blurs lags in viewers
  require('child_process').execFileSync('python', ['-c', `
from PIL import Image; import glob, sys
ims = [Image.open(f).convert('RGB').resize((2560, 1440), Image.LANCZOS) for f in sorted(glob.glob(sys.argv[1] + '/slide-*.png'))]
ims[0].save(sys.argv[2], save_all=True, append_images=ims[1:], resolution=192, quality=90)`, SLIDES, path.join(DECK, `${NAME}.pdf`)]);

  const pres = new pptxgen();
  pres.layout = 'LAYOUT_WIDE';
  pres.title = 'Hivemind';
  pres.author = 'Handsome Squidward';
  for (let i = 0; i < n; i++) {
    const s = pres.addSlide();
    s.background = { color: '050B08' };
    s.addImage({ path: path.join(SLIDES, `slide-${String(i + 1).padStart(2, '0')}.png`), x: 0, y: 0, w: 13.333, h: 7.5 });
    if (NOTES[i]) s.addNotes(NOTES[i]);
  }
  await pres.writeFile({ fileName: path.join(DECK, `${NAME}.pptx`) });
  console.log('slides', n);
})().catch(e => { console.error(e); process.exit(1); });
