// Sample data shared by the Graph page (index.html) and the Rooms page (rooms.html).
// Stands in for what the daemon will read from real chat logs and FalkorDB.
window.HM = (() => {
  const AG = {
    'claude-code': { name: 'Claude Code', mono: 'C' },
    'codex':       { name: 'Codex',       mono: 'X' },
    'gemini':      { name: 'Gemini CLI',  mono: 'G' },
    'antigravity': { name: 'Antigravity', mono: 'A' },
  };
  // how much of the thinking each agent's logs hold: full text, or only a summary
  const FID = { 'claude-code': 'full', 'codex': 'summary', 'gemini': 'summary', 'antigravity': 'summary' };
  const KIND = {
    finding: { g: '!', t: 'finding' }, decision: { g: '✓', t: 'decision' }, fact: { g: '·', t: 'fact' },
    howto: { g: '›', t: 'how-to' }, handoff: { g: '→', t: 'handoff' }, rejected: { g: '×', t: 'rejected' },
  };

  /* memories (what the LLM step and the agents' remember() calls saved); `by` = the agent that saves it */
  const MEMS = {
    m1: { by: 'codex', kind: 'finding', short: 'tokens pass at expiry', text: 'Expired tokens pass auth: the check uses < instead of <=, so a token at its exact expiry second still gets in.', topics: ['token expiry'] },
    m2: { by: 'codex', kind: 'howto', short: 'run the auth tests', text: 'Run the auth tests with pytest tests/auth -x before and after any auth change.', topics: ['auth tests'] },
    m3: { by: 'codex', kind: 'handoff', short: 'fix token expiry', text: 'Codex asks Claude Code to fix the token expiry check. The failing test is attached.', topics: ['token expiry'], to: 'claude-code' },
    m4: { by: 'claude-code', kind: 'decision', short: 'reject at expiry, 30 s skew', text: 'Reject tokens at the expiry second, and allow 30 seconds of clock skew between servers.', topics: ['token expiry'] },
    m5: { by: 'codex', kind: 'fact', short: 'in-process LRU, no TTL', text: 'API responses are cached in an in-process LRU with no TTL.', topics: ['API cache'] },
    m6: { by: 'claude-code', kind: 'fact', short: '4 workers, 4 caches', text: 'Uvicorn runs 4 workers and each keeps its own LRU, so the cache is split four ways.', topics: ['API cache'] },
    m7: { by: 'gemini', kind: 'fact', short: 'Redis set up, unused', text: 'Redis is already configured in infra, but nothing uses it.', topics: ['API cache', 'Redis'] },
    m8: { by: 'antigravity', kind: 'fact', short: 'login polls every 30 s', text: 'The login page polls the session endpoint every 30 seconds, even when the user is idle.', topics: ['login page'] },
    m9: { by: 'claude-code', kind: 'handoff', short: 'retry once on 401', text: 'Claude Code asks Antigravity to retry the session once on a 401 before logging the user out.', topics: ['login page'], to: 'antigravity' },
    m10: { by: 'gemini', kind: 'finding', short: 'session OK under load', text: 'The session endpoint holds up under the load test after the auth fix.', topics: ['login page'] },
    m11: { by: 'claude-code', kind: 'decision', short: 'move cache to Redis', text: 'Move the API cache to Redis with a 5-minute TTL, so every worker shares one cache.', topics: ['API cache', 'Redis'], supersedes: 'm5' },
    // saved by a relay room (rooms.html); debate results are created there and sent with the turn
    m12: { by: 'claude-code', kind: 'decision', short: 'Redis + 2 s local LRU', text: 'Cache in Redis with a 5-minute TTL, plus a 2-second LRU in each worker to absorb bursts. Agreed in a room by all four agents.', topics: ['API cache', 'Redis'], supersedes: 'm5' },
  };

  /* sample chat + thinking logs, one list of sessions per agent */
  const LOGS = {
    'codex': [
      { id: 'cx_s1', title: 'debug login 401s', turns: [
        { user: 'Users get logged out at random. Can you check auth?',
          think: [{ t: 'Random logouts usually mean a boundary bug in the token check.' }, { t: 'Compare the expiry check against the token’s exp claim.' }, { t: 'It uses <, so a token at its exact expiry second still passes.' }],
          reply: 'Found it: the expiry check uses < instead of <=.', save: ['m1'] },
        { user: 'Write a test for it and hand the fix off.',
          think: [{ t: 'A boundary test at the exact expiry second fails today.' }, { t: 'Claude Code can take the fix. Attach the test.' }],
          reply: 'Added a failing boundary test and handed the fix to Claude Code.', save: ['m2', 'm3'] },
      ]},
      { id: 'cx_s2', title: 'cache review', turns: [
        { user: 'How do we cache API responses today?',
          think: [{ t: 'Look at the cache module.' }, { t: '512 entries, and nothing ever expires.' }],
          reply: 'An in-process LRU with 512 entries and no TTL.', save: ['m5'] },
      ]},
    ],
    'claude-code': [
      { id: 'cc_s1', title: 'fix token expiry', turns: [
        { user: 'Pick up whatever is waiting for you.',
          think: [{ t: 'sync() → one handoff from Codex: fix token expiry.', recall: ['m3'] }, { t: 'recall("token expiry") → Codex’s finding: < instead of <=.', recall: ['m1'] }, { t: 'Codex left a how-to: run the auth tests before and after.', recall: ['m2'] }, { t: 'Use <= and allow 30 s of skew for clock drift between servers.' }],
          reply: 'Changed < to <= and allowed 30 seconds of clock skew. The auth tests pass.', save: ['m4'] },
        { user: 'Does the frontend need to change?',
          think: [{ t: 'Tokens now fail exactly at expiry, so the login page will see more 401s.' }, { t: 'Antigravity owns the login page. Hand it off.' }],
          reply: 'Asked Antigravity to retry once on a 401 before logging the user out.', save: ['m9'] },
      ]},
      { id: 'cc_s2', title: 'review caching', turns: [
        { user: 'Is our caching good enough?',
          think: [{ t: 'recall("cache") → Codex: in-process LRU, no TTL.', recall: ['m5'] }, { t: 'Gemini found Redis configured but unused.', recall: ['m7'] }, { t: 'Uvicorn runs 4 workers, so that LRU exists four times.' }],
          reply: 'Each of the 4 workers keeps its own LRU, so the cache is split four ways.', save: ['m6'] },
      ]},
    ],
    'gemini': [
      { id: 'gm_s1', title: 'cache options', turns: [
        { user: 'Compare caching options for the API.',
          think: [{ t: 'recall("cache") → Codex: in-process LRU, no TTL.', recall: ['m5'] }, { t: 'Check infra for an existing Redis setup.' }],
          reply: 'Redis is already configured in infra, but nothing uses it yet.', save: ['m7'] },
      ]},
      { id: 'gm_s2', title: 'load test the session API', turns: [
        { user: 'Load-test the session endpoint.',
          think: [{ t: 'recall("session") → Antigravity: login polls every 30 s.', recall: ['m8'] }, { t: 'So idle users alone create steady load. Test well above that.' }],
          reply: 'Running the load test against staging now.' },
      ]},
    ],
    'antigravity': [
      { id: 'ag_s1', title: 'login page polish', turns: [
        { user: 'Why does the login page feel busy?',
          think: [{ t: 'Watch the network calls while the page is idle.' }, { t: 'The session endpoint is called every 30 seconds.' }],
          reply: 'The login page polls the session endpoint every 30 seconds, even when idle.', save: ['m8'] },
      ]},
      { id: 'ag_s2', title: 'retry on 401', turns: [
        { user: 'Pick up whatever is waiting for you.',
          think: [{ t: 'sync() → handoff from Claude Code: retry once on 401.', recall: ['m9'] }, { t: 'recall("expiry") → Claude’s decision: 30 s of skew.', recall: ['m4'] }, { t: 'One retry covers clock drift. A second 401 means the token really expired.' }],
          reply: 'Added a single retry on 401 in the session hook, then log out.' },
      ]},
    ],
  };

  return { AG, FID, KIND, MEMS, LOGS };
})();
