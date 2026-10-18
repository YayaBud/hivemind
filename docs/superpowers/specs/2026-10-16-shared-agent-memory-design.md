# Hivemind: Shared Graph Memory for Coding Agents — Design

Working name: **Hivemind** (rename freely). Hackathon: Graph Hacks (WeMakeDevs × FalkorDB),
**Track 02 — Agent Memory and Coordination**. Deadline: 2026-10-18 23:59 IST. Solo build.

## 1. Goal

Claude Code, Codex CLI, Antigravity and any other coding agent share one memory. Each agent's
chat history becomes its own graph; an LLM consolidates all of them into one common graph; every
agent can read every graph and guide any other agent through handoffs. A web UI shows each
agent's graph, the common graph, and the handoffs between them, live.

### Principle: equal peers

No agent outranks another. Same MCP server, same tools, same read access to every graph. A
handoff is a request, not a command — the receiver accepts or declines, and that answer is
recorded. **No code path branches on agent kind except the transcript parser selection.**

### Track 02 focus → feature

| Focus | Feature |
|---|---|
| Episodic memory | Per-agent graphs: Session → Message chains from real transcripts |
| Semantic memory | Common graph: Concept + Memory (fact / decision / finding) |
| Procedural memory | Memory `kind=procedure` ("run tests with …") |
| Persistent history | Collector ingests transcripts continuously; graphs persist in FalkorDB |
| Shared state + handoffs | Common graph readable by all; handoff memories with accept/decline |
| High-concurrency reads/writes | Many agents + collector + consolidator write concurrently (stretch: load test) |
| Separate memory per tenant | One set of graphs per workspace token (FalkorDB multi-graph) |

## 2. Architecture

```
 Claude Code ─┐  MCP (streamable HTTP)   ┌────────── FastAPI (single process) ──────────┐
 Codex CLI  ──┼─────────────────────────▶│ /mcp      MCP tools (§5, §5.1)               │
 Antigravity ─┤                          │ /ingest   transcript events + extractions    │
 other CLIs ──┘                          │ /api/*    graphs, search, trace, handoffs    │
                                         │ /events   SSE: new nodes pushed to the UI    │
 hivemind daemon (each dev machine) ────▶│ /         built React app (static)           │
   collector: tail transcript files      └───────────────────┬──────────────────────────┘
   extractor: LLM via your CLI login or key                  │
   runner:    wakes CLI agents for debates                   │ Cypher (falkordb-py)
                                             FalkorDB (Cloud when hosted, Docker locally)
```

Approach chosen: **single FastAPI process** (rejected: split services — 4 deploys for one person;
local stdio MCP per agent — every machine would need DB credentials, extraction duplicated).

## 3. Graph topology (FalkorDB multi-graph)

FalkorDB cannot traverse across graphs, so cross-graph provenance is stored as properties
(`src_graph`, `src_id`) and resolved by a second query.

```
{ws}__agent__claude-code    per-agent graph  (deterministic, no LLM)
{ws}__agent__codex
{ws}__agent__antigravity
{ws}__agent__<any>
{ws}__common                common graph     (LLM consolidator + agent MCP writes)
```

### 3.1 Agent graph schema

Only what agents do **not** already share: their chat and their thinking. Files and tool calls
are not captured — every agent already sees the same repository, so they connect nothing new
(decision 2026-10-16, Option A in `implementation_plan.md`).

```
(:Session {id, agent, title, started_at})
(:Session)-[:HAS]->(:Message {id, role: user|assistant|checkpoint, text, ts, extracted: bool})
(:Message {role:user})-[:THOUGHT]->(:Thought {id, text, fidelity: full|summary, ts})
(:Thought)-[:NEXT]->(:Thought)
(:Thought)-[:LED_TO]->(:Message {role:assistant})
(:Message)-[:NEXT]->(:Message)                    reply → next user message
(:Thought)-[:RECALLED]->(:MemoryRef {memory_id})  a thought used a shared memory
(:Message)-[:SAVED]->(:MemoryRef {memory_id})     a reply produced a shared memory
```

`fidelity` says how much thinking the agent's logs hold: Claude Code writes full thinking blocks
(when extended thinking is on); Codex writes reasoning items, often only a summary with the full
text encrypted; Gemini CLI unverified. The UI labels every thought with it.

`MemoryRef` exists because FalkorDB cannot link across graphs: the agent graph keeps a reference
node, the common graph keeps the Memory. A recall is tied to a thought because the agent's MCP
`recall` call appears in its own transcript as a tool use; the collector links the thought just
before that call to the memory ids the server returned.

Written only by `/ingest` (from the collector) and by that agent's own `checkpoint` tool calls.
All writes use `MERGE` on `id` → ingest is idempotent.

### 3.2 Common graph schema

```
(:Agent {id, kind, color, last_seen})
(:Concept {key, name, type})                     key = normalized lowercase name; dedupe on key
(:Memory {id, kind: fact|decision|finding|procedure|handoff, text,
          source: agent|extracted, src_graph, src_id, status: active|superseded, ts,
          handoff_status: open|accepted|declined|done, note})   handoff_* only for kind=handoff

(:Agent)-[:WROTE]->(:Memory)
(:Memory)-[:RECALLED {count, last, thought_ids}]->(:Agent)   another agent's thinking used it
(:Memory)-[:ABOUT]->(:Concept)                   shown as "topic" in the UI
(:Memory)-[:SUPERSEDES]->(:Memory)               old memory kept as history, status=superseded
(:Memory {kind:handoff})-[:TO]->(:Agent)         a handoff is a memory addressed to one agent
(:Agent)-[:KNOWS {count}]->(:Concept)            which agent has worked on what
(:Concept)-[:RELATED {weight}]->(:Concept)       co-occurrence in the same memory/message batch
```

`RECALLED` is the edge the product exists for: it shows one agent's knowledge being used inside
another agent's thinking.

Full-text indexes: `Memory.text`, `Concept.name` (common); `Message.text` (each agent graph).
No vector index (add only if full-text recall measurably misses).

## 4. Capture: collector + parsers

`collector.py` runs on each developer machine. Stdlib only (polling, `urllib`), no extra deps.

- Parser registry: `{agent_id: (glob_pattern, parse_fn)}`. `parse_fn(path, new_lines) → [event]`
  where event ∈ `session | message | thought | recall`, normalized to the §3.1 schema. Tool
  calls and tool output are skipped, except Hivemind's own `recall` / `sync` calls, which become
  `recall` events.
- Offsets saved in `~/.hivemind/offsets.json` → restart resumes, nothing re-sent.
- **Project allowlist (`--project PATH`, repeatable):** only sessions whose working directory is
  in the allowlist are sent. Required — real transcripts contain unrelated private work, and the
  hackathon forbids loading personal or restricted data.
- POST batches to `/ingest` with `Authorization: Bearer <workspace token>`.

Parser status (verified on this machine 2026-10-16):

| Agent | Source | Status |
|---|---|---|
| Claude Code | `~/.claude/projects/<slug>/<session>.jsonl`; line `type` ∈ user, assistant, attachment, system, … | format present, parser day 1 |
| Codex CLI | `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`; `type/payload.type` ∈ session_meta, response_item/message, response_item/function_call, response_item/custom_tool_call, event_msg/* | format present, parser day 1 |
| Antigravity | `~/.gemini/antigravity-ide/{conversations,brain}/` | **dirs empty (user cleared history)** — format unknown; parser written after the user runs one sample chat. Until then Antigravity history comes via `checkpoint` |
| Gemini CLI / others | unknown | stretch |

## 5. MCP server

FastMCP (official `mcp` Python SDK), streamable HTTP, mounted at `/mcp` inside FastAPI.

**Identity:** `Authorization: Bearer <workspace token>` + `X-Agent-Id: <agent id>`.
Codex supports `[mcp_servers.<name>.http_headers]` in `~/.codex/config.toml` (verified locally).
Claude Code supports headers on HTTP MCP servers. Antigravity header support: verify in phase 1;
fallback `?agent=<id>` query parameter.

**Tools** (identical for every agent):

| Tool | Behavior |
|---|---|
| `sync()` | My open handoffs + what peers did recently + top recent memories. Call at task start. |
| `remember(text, kind, about[], supersedes?)` | Write Memory to common; link Concepts; mark superseded memory. |
| `recall(query, agent?)` | Full-text over common + every agent graph (or one agent's); returns hits with source graph and 1-hop context. |
| `peers(project?)` | Per other agent: recent sessions, files touched, latest memories. |
| `handoff(to, text, about[])` | Create an open Memory of kind `handoff` from me, `TO` the other agent. |
| `respond(handoff_id, decision, note)` | decision ∈ accept, decline, done. Only the addressee may respond. |
| `checkpoint(summary, decisions[])` | Write a `checkpoint` Message to my own agent graph; decisions become Memories. Guarantees history for agents without a parser. |
| `trace(id)` | Memory → `src_graph/src_id` → source Message with its Session and neighbors. |

**Guidance delivery — server-side, equal for all, no client hooks:**
1. MCP `instructions` (sent at initialize) tell every agent the protocol: `sync` at task start,
   `remember` on decisions, `checkpoint` at the end. Whether each client surfaces `instructions`
   is unverified → channel 2 is the guarantee.
2. **Every tool result ends with an inbox footer** when the caller has unread handoffs, e.g.
   `📬 1 open handoff from codex: "auth middleware drops expired tokens" → respond(h_12, ...)`.

Rejected: Claude Code `UserPromptSubmit` hook (privileges one agent); Codex `notify` (the single
slot is already used by Codex computer-use on this machine).

## 5.1 Debate: agents argue for the best idea

Any agent opens a debate on a question; every agent proposes, supports or attacks proposals and
arguments, may revise its proposal, and votes. The winner becomes a decision Memory; losers stay
as rejected alternatives with their reasons, so `trace` on a decision shows the whole argument.

Common-graph additions:

```
(:Debate {id, question, status: open|running|closed, rounds, ts})
(:Proposal {id, text, ts})-[:FOR]->(:Debate)        (:Agent)-[:PROPOSED]->(:Proposal)
(:Proposal)-[:REVISES]->(:Proposal)
(:Argument {id, text, stance: support|attack, ts})-[:TARGETS]->(:Proposal|:Argument)
(:Agent)-[:ARGUED]->(:Argument)
(:Agent)-[:VOTED]->(:Proposal)                      no self-votes, one vote per agent per debate
(:Debate)-[:DECIDED]->(:Memory {kind: decision})    winner
(:Proposal)-[:REJECTED_IN]->(:Debate)               losers, kept
```

Tools (same for every agent): `debate(question)`, `propose(debate_id, text, revises?)`,
`argue(target_id, stance, text)`, `vote(proposal_id)`. Open debates appear in the tool-result
footer like handoffs.

**Winner = graph score + human final say.** An argument is *standing* if no standing attack
targets it (computed bottom-up over the argument tree). Proposal score =
`2 × votes + standing supports − standing attacks`. The UI shows the ranking with the standing
arguments behind each score; the human confirms or overrides, then the debate closes.

**Rounds:** "Start debate" in the UI sets `status=running`. A local `runner` (collector
subcommand) wakes installed CLI agents headless for N rounds (`claude -p`, `codex exec`,
`gemini -p`) with a fixed prompt: sync, read the debate, then propose/argue/vote. Debate runs are
restricted to Hivemind tools only (Claude `--allowedTools "mcp__hivemind__*"`, Codex
`--sandbox read-only`) so a debate can never edit code. IDE agents (Antigravity) join manually
with the same tools and the same weight. Exact headless flags: verify per CLI.

**UI:** argument map — proposals as large nodes, support edges green, attack edges orange (also
labelled "supports"/"attacks", not color alone), winner highlighted, score panel.

## 5.2 Rooms: agents take turns, watched live from the web page

A **Room** is a turn-based conversation between agents, set up and watched entirely from the web
page. Example order: `codex (gpt) → claude-code → antigravity → gemini → codex …`. Each agent waits
for the previous turn to finish, sees everything said so far, and replies. Debate (§5.1) is a
Room mode.

**Set up on the web page:** topic/task, participants in order (drag to reorder), **model per
participant** (dropdown), number of rounds, mode `relay` (each builds on the last) or `debate`
(propose / attack / vote, scored per §5.1). Start, pause, stop, skip a turn, add your own message.

**Live view:** one column of turns like a group chat. Each turn shows agent + model, the input it
received, its **thinking stream** (whatever the CLI streams: reasoning summaries, tool calls,
partial text — raw chain-of-thought is summarized or hidden by some providers), and its final
answer. Status per turn: waiting / thinking / done / timed out.

**Graph:**

```
(:Room {id, topic, mode: relay|debate, rounds, status: draft|running|paused|done, order})
(:Turn {id, n, agent, model, input, thinking, output, status, started, ended})
(:Room)-[:HAS_TURN]->(:Turn)-[:NEXT]->(:Turn)       (:Agent)-[:TOOK]->(:Turn)
(:Turn)-[:REPLIES_TO]->(:Turn)                      (:Turn)-[:USED]->(:Memory)  context it was given
```

**Each turn's input is built from the graph:** the room's previous turns, plus `recall` on the
topic across all agent graphs, plus rejected alternatives from past debates on that topic. The
`USED` edges record exactly which memories shaped the turn.

**Turn lock:** the server hands the turn to the next agent only when the current one is `done`.
Per-turn timeout (default 5 min) → `timed out`, chain moves on, so one stuck agent never blocks
the room.

**Two ways to take a turn — same protocol, same weight:**

| Agent | How its turn runs | Verified 2026-10-16 |
|---|---|---|
| Claude Code | daemon runs `claude -p --output-format stream-json --include-partial-messages --model <m> --allowedTools "mcp__hivemind__*"` | flags present |
| Codex | daemon runs `codex exec --json -m <m> -s read-only` | flags present |
| Gemini CLI | daemon runs `gemini -p … -m <m> -o stream-json` | flags present |
| Antigravity / any IDE agent | user tells it once "join room X"; it loops `wait_turn(room)` (MCP long-poll, returns when it is this agent's turn or "call again" after ~50 s) → thinks → `post_turn(room, output)` | no Antigravity CLI on this machine (`agy` not found) |

The daemon streams each CLI's output to `/ingest` as `turn_chunk` events → server → SSE → web
page. IDE agents' model is chosen inside the IDE; the web page shows it as "set in Antigravity".

New tools: `wait_turn(room)`, `post_turn(room, output, thinking?)`.

## 6. Consolidator (LLM) — runs in the local daemon, not the server

**No API key required.** All LLM work (extraction here, debate turns in §5.1) runs in the
`hivemind daemon` on the user's machine, through whatever the user is already logged into. The
hosted server holds no LLM key and pays for no LLM calls; each user's own plan does the thinking.

The daemon batches the messages it has just collected (per session), every few minutes or every
N messages (configurable — subscription plans have usage limits, so batch big and rarely):

1. Redact secrets (§8), truncate long text, skip raw tool output.
2. One LLM call → JSON `{concepts:[{name,type}], memories:[{kind,text,about[],src_message_id}]}`.
3. POST the result to `/ingest` as an `extraction` event. Server writes to common: `MERGE`
   Concept on `key`, create Memory with `src_graph/src_id`, bump `KNOWS` and `RELATED`, set the
   source messages `extracted=true`.
4. On LLM error: batch stays in a local pending file, retried next tick; agent graphs unaffected.

Exact-text Memory dedupe only. Supersession only via explicit `remember(supersedes=…)`.

**LLM backends** (user picks one: `hivemind daemon --llm <backend>`):

| Backend | Uses | Cost to user |
|---|---|---|
| `claude-cli` | `claude -p` (Claude Code, logged in with Claude Pro/Max) | counts toward the plan's usage limits |
| `codex-cli` | `codex exec` (Codex CLI, logged in with ChatGPT Plus/Pro) | counts toward the plan's usage limits |
| `gemini-cli` | `gemini -p` (Gemini CLI, Google account) | free tier / plan limits |
| `ollama` | local model over Ollama's OpenAI-compatible HTTP API | free, offline |
| `api` | any OpenAI-compatible endpoint + key (OpenAI, Anthropic, Gemini, Groq, OpenRouter) | pay per token |

CLI backends call the user's own installed CLI on their own machine; Hivemind never sees,
stores or proxies a login. `api` and `ollama` use stdlib HTTP (`urllib`), no SDK dependency.
Each provider's terms apply to automated use of its CLI: check them before the demo.

## 7. Frontend

Static pages in `web/` using d3 (no build step), served by FastAPI. Minimal white + green.
Prototype on sample data: `web/index.html` (run locally with `python -m http.server 5199
--directory web`).

- **Graph page:** combined graph on top = the shared memory (agents, memories, topics; WROTE,
  RECALLED, TO, SUPERSEDES). One card per agent below = that agent's own chat and thinking, with
  saved / recalled memory nodes. Clicking a memory lights up the chat + thinking it came from and
  every other agent's thought that recalled it; a right-hand drawer shows the full story.
  Clicking a session shows its chat with thinking interleaved, each thought labeled full or
  summary. New nodes appear live via SSE `/events`.
- **Search:** across chat, thinking and memory in every graph; hits highlighted.
- **Handoffs:** handoff memories, open / accepted / declined / done, from → to.
- **Rooms page (§5.2, `web/rooms.html`):** create a room (topic, ordered participants, model per
  participant, rounds, mode), then watch the live turn-by-turn chat: each turn shows the memories
  it recalled, its thinking stream (full or summary) and its answer. Debate mode runs propose →
  argue → vote and ends with the score tree and a confirm/override step that saves the decision
  and the rejected ideas. Each turn is written into that agent's own graph; in the prototype the
  two pages talk over `BroadcastChannel('hivemind')`, in the product over the server.
- **Settings:** workspace token, copy-paste MCP config snippets for Claude Code, Codex,
  Antigravity, and the daemon start command with an LLM backend picker (§6). Shows each
  connected daemon and which backend it uses.

REST: `GET /api/graphs`, `GET /api/graph/{name}?limit=`, `GET /api/search?q=`,
`GET /api/trace/{id}`, `GET /api/handoffs`, `GET|PUT /api/settings`, `GET /events`,
`POST /ingest`.

## 8. Error handling and safety

- Ingest idempotent (`MERGE` on ids). Collector offsets → crash-safe resume.
- Consolidator failure never blocks ingest or MCP tools.
- **Secret redaction before storage and before any LLM call:** `sk-…`, `sk-ant-…`, `ghp_…`,
  `github_pat_…`, `AKIA…`, `xox[bp]-…`, `-----BEGIN … PRIVATE KEY-----` blocks,
  `(?i)(api[_-]?key|secret|token|password)\s*[=:]\s*\S+`.
- Project allowlist in the collector (§4).
- Workspace token required on `/ingest`, `/mcp`, `/api/*`. One workspace = one set of graphs.
- `respond` only by the addressee; agents write only their own agent graph.

## 9. Testing

- Parser tests on **synthetic** fixtures (never real transcripts in the repo).
- Graph query tests against FalkorDB in Docker.
- MCP tool tests through the MCP Python client against the running app.
- Redaction test with a fixture full of fake keys.
- One end-to-end script: two simulated agents → ingest → consolidate (stub LLM) → handoff →
  respond → trace back to source message.
- Equal-peers invariant: tool handlers contain no branch on agent kind (test calls every tool
  as two different agents and compares behavior).

## 10. Deployment

- Hosted: Dockerfile (Node build stage → Python runtime), FalkorDB Cloud, one web service
  (Render / Railway / Fly — pick in phase 4).
- Local: `docker compose up` (falkordb + app).

## 11. Submission checklist (from hackathon rules)

- Public repo; README with setup, graph data model (§3), the Cypher queries the product
  depends on; demo video; live deployment + local setup; track = Track 02.
- Disclose AI coding assistant use in README.
- Data: only the user's own transcripts from allowlisted demo projects, redacted.
- Graph does real work: remove FalkorDB and nothing functions (all memory, recall, handoffs,
  trace live in the graph).

## 12. Scope

Current phase: **idea submission (deck)**. Build follows for the showcase, in this order:
FalkorDB + schema → collector (Claude Code, Codex) + `/ingest` → MCP tools → consolidator →
frontend → deploy → Antigravity parser (after sample) → README + demo video.

**Cut:** user accounts (token only), vector search, graph editing from UI, human-sent handoffs.
**Stretch:** concurrency load test, Gemini CLI parser, blog post (side prize).

## 13. Open items to verify in phase 1

- Antigravity: MCP remote-HTTP config + custom header support.
- Antigravity transcript format (needs one sample chat).
- FastMCP: reading request headers inside a tool for the streamable-HTTP transport.
- Which clients surface MCP `instructions` (footer channel covers the rest).
- Headless flags and JSON output for `claude -p`, `codex exec`, `gemini -p` on Windows; each
  provider's terms for scripted use of its CLI.

New dependencies needing approval: `fastapi`, `uvicorn`, `falkordb`, `mcp`, `pytest` (Python); d3 7.9.0 from cdnjs (frontend, no build step); Docker image
`falkordb/falkordb`.
