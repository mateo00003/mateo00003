# Echo Tickler

A sovereign, local-first tickler system for **Echo** (the assistant at
`localhost:8791`). One JSON file is the source of truth; a zero-dependency MCP
server wraps it so **every surface — glass, chat, cron — reads and writes the
same list.** A daily rollup surfaces the top three, keeps a standing changelog,
and reports a single **"how far behind"** number, all sourced from the board,
never from memory.

This is the architecture Echo's own chat asked for:

> the source of truth, wrap it as an MCP so every surface (glass, chat, cron)
> reads and writes the same list, and schedule a daily rollup that surfaces the
> top three.

## Why not Airtable? (the standing rule)

Echo's whole point is **sovereign, offline, your-store-not-theirs**. Airtable is
proprietary SaaS: your data lives on their servers and the system dies without a
network. That's the opposite of what "glass says offline" is promising.

So this is DIY and open-source, and the guidance holds going forward:

| Concern            | This system                                  |
| ------------------ | -------------------------------------------- |
| Storage            | one local JSON file — you own it             |
| Dependencies       | **zero** runtime deps (Node built-ins only)  |
| Network            | fully offline; nothing phones home           |
| Protocol           | MCP over stdio, hand-rolled (no SDK)          |
| Lock-in            | none — it's a file and ~400 lines of JS      |

**Default to open-source, local-first, minimal-dependency. Always.**

## What was "not working"

A tickler system's job is to make an item **resurface on its date**. Nothing was
doing that flip. `tick()` is the fix: it flips every `waiting` item whose
`tickleDate` has arrived to `active`. Run it daily (`cron/daily-rollup.js`) and
the board comes alive. From glass you couldn't see the board at all — wiring the
MCP into glass closes that gap.

## Layout

```
tickler/
  src/store.js        # the source of truth: atomic load/save of one JSON file
  src/tickler.js      # domain logic: add, tick, complete, snooze, rollup, metrics
  src/mcp-server.js   # zero-dep MCP stdio server — glass/chat/cron share one list
  src/cli.js          # same list from a terminal or cron, no MCP needed
  cron/daily-rollup.js# daily heartbeat: tick + rollup + changelog
  test/               # node:test suite, offline, zero deps
  data/               # your store lives here (gitignored)
```

## Quick start

```bash
cd tickler
npm test                                  # 11 tests, offline

# add the power blocks that show under the chat on glass
node src/cli.js add "Start your sovereign memory" --pri 1 --block \
  --step "pick store" --step "wire vault" --step "recall"

node src/cli.js blocks     # the power-block cards (title, subtitle, N/3)
node src/cli.js behind     # the how-far-behind number
node src/cli.js rollup     # top three + behind + changelog
node cron/daily-rollup.js  # what cron runs each morning
```

The store defaults to `data/tickler.json`. Point it anywhere with `TICKLER_DB`.

## Data model

Each item sleeps until its date, then resurfaces:

```
{ id, title, note,
  status: "waiting" | "active" | "done",
  tickleDate: "YYYY-MM-DD",   // resurfaces on this date
  priority: 1 | 2 | 3,        // 1 = highest
  block: true,                // promoted to a power block
  steps: [{ text, done }],    // drives N/3 progress on the card
  surface, tags, createdAt, updatedAt, completedAt }
```

## Wiring the three surfaces

### 1. Glass (the dashboard)

Glass reads the board through the MCP server (or the CLI with `--json`) and
renders it. The two views glass needs:

- **Power blocks** — `tickler_power_blocks` → up to 4 cards, each
  `{ title, subtitle, progress: "N/3" }`. This is exactly the shape of the four
  action cards under the chat. Tapping a step calls `tickler_step`.
- **How far behind** — `tickler_behind` → `{ behind, behindLabel, open, done }`.
  Put `behindLabel` in every update. It comes from the board, not Echo's memory,
  which is the whole ask ("I want to know how far behind you are").

### 2. Chat (Echo / Claude CLI)

Register the MCP server so chat can add, complete, and snooze items in
conversation. For the Claude CLI, add to your MCP config:

```json
{
  "mcpServers": {
    "tickler": {
      "command": "node",
      "args": ["/absolute/path/to/tickler/src/mcp-server.js"],
      "env": { "TICKLER_DB": "/absolute/path/to/tickler/data/tickler.json" }
    }
  }
}
```

All ten tools then appear to chat: `tickler_add`, `tickler_list`, `tickler_tick`,
`tickler_complete`, `tickler_snooze`, `tickler_block`, `tickler_step`,
`tickler_power_blocks`, `tickler_rollup`, `tickler_behind`.

### 3. Cron (the daily heartbeat)

```cron
0 7 * * * cd /path/to/tickler && node cron/daily-rollup.js >> data/rollup.log 2>&1
```

It resurfaces due items, records the rollup, and prints the top three + behind
number + changelog for Echo to speak or post.

## The MCP protocol, briefly

MCP's stdio transport is newline-delimited JSON-RPC 2.0 — one JSON object per
line each way. `mcp-server.js` speaks it directly (initialize, tools/list,
tools/call), which is why there are no dependencies. stdout carries protocol
only; logs go to stderr.
