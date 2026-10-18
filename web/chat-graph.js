(function (root) {
  'use strict';

  const SPEAKERS = [
    ['Claude Code', 'agent'], ['Gemini CLI', 'agent'], ['Antigravity', 'agent'],
    ['Assistant', 'agent'], ['Claude', 'agent'], ['Codex', 'agent'], ['Gemini', 'agent'],
    ['User', 'user'], ['Human', 'user']
  ];
  const memoryKinds = { Finding: 'Finding', Decision: 'Decision', Fact: 'Fact', Procedure: 'Procedure' };

  function graph(source, sourceLabel) {
    return { nodes: [], links: [], source: source || 'workspace', sourceLabel: sourceLabel || 'Workspace sample/live' };
  }
  function add(g, node) { if (!g.nodes.some(n => n.id === node.id)) g.nodes.push(node); return node.id; }
  function link(g, source, target, type) {
    if (source && target && g.nodes.some(n => n.id === source) && g.nodes.some(n => n.id === target) && !g.links.some(l => l.source === source && l.target === target && l.type === type)) g.links.push({ source, target, type });
  }
  function safe(s) { return String(s == null ? '' : s); }
  function base(id, type, label, text, extra) { return Object.assign({ id, type, label: label || '', text: text || '' }, extra || {}); }

  function fromState(state) {
    if (!state || typeof state !== 'object') throw new Error('Workspace state is required.');
    const source = root.HM_SOURCE;
    const fallbackLabel = source === 'sample' ? 'Workspace sample' : source === 'offline' ? 'Workspace offline · sample data' : source === 'empty' ? 'Workspace loaded · empty' : source === 'live' ? 'Workspace live · loaded (not streaming)' : 'Workspace sample';
    const g = graph('workspace', state.sourceLabel || fallbackLabel);
    const agents = state.AG && typeof state.AG === 'object' ? state.AG : {};
    const memories = state.MEMS && typeof state.MEMS === 'object' ? state.MEMS : {};
    const logs = state.LOGS && typeof state.LOGS === 'object' ? state.LOGS : {};
    Object.entries(agents).forEach(([agent, info]) => add(g, base('agent:' + agent, 'Agent', safe(info && info.name || agent), '', { agent })));
    Object.entries(logs).forEach(([agent, sessions]) => {
      if (!g.nodes.some(n => n.id === 'agent:' + agent)) add(g, base('agent:' + agent, 'Agent', agent, '', { agent }));
      (Array.isArray(sessions) ? sessions : []).forEach((session, si) => {
        const sid = safe(session && session.id || 'session-' + (si + 1));
        const sessionId = 'session:' + agent + ':' + sid;
        add(g, base(sessionId, 'Session', safe(session && session.title || sid), '', { agent, session: sid }));
        link(g, 'agent:' + agent, sessionId, 'HAS');
        let previous = null;
        (Array.isArray(session && session.turns) ? session.turns : []).forEach((turn, ti) => {
          const turnId = agent + ':' + sid + ':' + (ti + 1);
          const userId = 'message:' + turnId + ':user';
          const replyId = 'message:' + turnId + ':assistant';
          add(g, base(userId, 'Message', 'User message', safe(turn && turn.user), { agent, session: sid, turn: ti + 1, role: 'user' }));
          add(g, base(replyId, 'Message', safe(infoName(agents[agent], agent) + ' reply'), safe(turn && turn.reply), { agent, session: sid, turn: ti + 1, role: 'assistant' }));
          link(g, sessionId, userId, 'HAS'); link(g, sessionId, replyId, 'HAS');
          link(g, 'agent:' + agent, replyId, 'WROTE');
          if (previous) link(g, previous, userId, 'NEXT');
          previous = replyId;
          let priorThought = null;
          (Array.isArray(turn && turn.think) ? turn.think : []).forEach((thought, hi) => {
            const thoughtId = 'thought:' + turnId + ':' + (hi + 1);
            add(g, base(thoughtId, 'Thought', 'Thought ' + (hi + 1), safe(thought && thought.t), { agent, session: sid, turn: ti + 1, fidelity: state.FID && state.FID[agent] || undefined }));
            link(g, priorThought || userId, thoughtId, priorThought ? 'NEXT' : 'THOUGHT');
            if (priorThought) link(g, priorThought, thoughtId, 'NEXT');
            priorThought = thoughtId;
            (Array.isArray(thought && thought.recall) ? thought.recall : []).forEach(mid => {
              if (memories[mid] && safe(memories[mid].by) !== agent) linkMemory(g, memories, mid, agent, thoughtId, 'RECALLED');
            });
          });
          link(g, priorThought || userId, replyId, priorThought ? 'LED_TO' : 'NEXT');
          (Array.isArray(turn && turn.save) ? turn.save : []).forEach(mid => {
            if (memories[mid]) linkMemory(g, memories, mid, agent, replyId, 'SAVED');
          });
        });
      });
    });
    Object.entries(memories).forEach(([mid, mem]) => ensureMemory(g, memories, mid, mem));
    Object.entries(memories).forEach(([mid, mem]) => { if (mem && mem.supersedes && memories[mem.supersedes]) link(g, memoryId(mid), memoryId(mem.supersedes), 'SUPERSEDES'); });
    validate(g);
    g.counts = { nodes: g.nodes.length, links: g.links.length };
    return g;
  }
  function infoName(info, fallback) { return safe(info && info.name || fallback); }
  function memoryId(mid) { return 'memory:' + mid; }
  function ensureMemory(g, memories, mid, mem) {
    if (!mem) return null;
    const id = memoryId(mid);
    if (!g.nodes.some(n => n.id === id)) add(g, base(id, 'Memory', safe(mem.short || mem.kind || mid), safe(mem.text), { agent: safe(mem.by), memory: mid }));
    const writer = safe(mem.by);
    if (writer) { if (!g.nodes.some(n => n.id === 'agent:' + writer)) add(g, base('agent:' + writer, 'Agent', writer, '', { agent: writer })); link(g, 'agent:' + writer, id, 'WROTE'); }
    (Array.isArray(mem.topics) ? mem.topics : []).forEach(topic => { const tid = 'topic:' + safe(topic); add(g, base(tid, 'Topic', safe(topic), safe(topic))); link(g, id, tid, 'ABOUT'); });
    if (mem.to) { const target = 'agent:' + safe(mem.to); if (!g.nodes.some(n => n.id === target)) add(g, base(target, 'Agent', safe(mem.to), '', { agent: safe(mem.to) })); link(g, id, target, 'TO'); }
    return id;
  }
  function linkMemory(g, memories, mid, agent, from, type) { const id = ensureMemory(g, memories, mid, memories[mid]); link(g, from, id, type); }
  function validate(g) { const ids = new Set(g.nodes.map(n => n.id)); if (ids.size !== g.nodes.length) throw new Error('Graph contains duplicate node IDs.'); g.links = g.links.filter(l => ids.has(l.source) && ids.has(l.target)); }

  function parseTranscript(input) {
    const text = safe(input);
    if (!text.trim()) throw new Error('Paste a chat transcript first.');
    if (text.length > 100000) throw new Error('Transcript is limited to 100,000 characters.');
    const lines = text.replace(/\r\n?/g, '\n').split('\n');
    const rx = /^\s*(?:(?:\*\*)?([^:*]{1,40})(?:\*\*)?:|\*\*([^:*]{1,40}):\*\*)(?:\s?)(.*)$/;
    let current = null, messages = [], fenced = false, preamble = [];
    lines.forEach(line => {
      if (/^\s*(```|~~~)/.test(line)) { fenced = !fenced; if (current) current.text += '\n' + line; return; }
      const match = line.match(rx);
      if (match && !fenced) {
        const label = (match[1] || match[2]).trim();
        const known = SPEAKERS.find(s => s[0].toLowerCase() === label.toLowerCase());
        if (!known) { if (current) current.text += '\n' + line; return; }
        current = { label: label, role: known[1], text: match[3] || '' }; fenced = /^\s*(```|~~~)/.test(match[3] || '') && !fenced; messages.push(current);
      } else if (current) current.text += '\n' + line;
      else if (line.trim()) preamble.push(line);
    });
    if (preamble.length) throw new Error('Transcript has unsupported content before the first speaker label.');
    if (!messages.length) throw new Error('Transcript needs supported line-start speaker labels such as User: and Assistant:.');
    if (messages.length > 200) throw new Error('Transcript is limited to about 200 messages.');
    const g = graph('imported-chat', 'Imported chat · local only');
    const sessions = new Map(), agentIds = new Map();
    messages.forEach((m, i) => {
      const agent = m.role === 'user' ? 'user' : m.label.toLowerCase().replace(/\s+/g, '-');
      const agentId = 'agent:' + agent; agentIds.set(agent, agentId);
      if (!g.nodes.some(n => n.id === agentId)) add(g, base(agentId, 'Agent', m.role === 'user' ? 'User' : m.label, '', { agent }));
      const sid = 'session:imported';
      if (!sessions.has(sid)) { sessions.set(sid, true); add(g, base(sid, 'Session', 'Pasted chat', '', { session: 'imported' })); }
      link(g, agentId, sid, 'HAS');
      const mid = 'message:imported:' + (i + 1);
      add(g, base(mid, 'Message', m.label, m.text, { agent, session: 'imported', turn: i + 1, role: m.role })); link(g, sid, mid, 'HAS');
      if (i) link(g, 'message:imported:' + i, mid, 'NEXT');
      if (m.role === 'agent') link(g, agentId, mid, 'WROTE');
      if (m.role === 'agent') {
        let inFence = false; m.text.split('\n').forEach((line, j) => { if (/^\s*(```|~~~)/.test(line)) { inFence = !inFence; return; } const mm = !inFence && line.match(/^\s*(Finding|Decision|Fact|Procedure):\s*(.+)$/i); if (!mm) return; const kind = Object.keys(memoryKinds).find(k => k.toLowerCase() === mm[1].toLowerCase()); const memId = 'memory:imported-' + i + '-' + j; add(g, base(memId, 'Memory', kind, mm[2], { agent, session: 'imported', memory: memId })); link(g, mid, memId, 'SAVED'); link(g, agentIds.get(agent), memId, 'WROTE'); });
      }
    });
    validate(g); g.counts = { nodes: g.nodes.length, links: g.links.length, messages: messages.length }; return g;
  }
  const api = { fromState, parseTranscript };
  if (root) root.HMChatGraph = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this));
