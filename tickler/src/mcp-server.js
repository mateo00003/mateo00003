#!/usr/bin/env node
// mcp-server.js — a zero-dependency MCP server over stdio.
//
// No SDK. MCP's stdio transport is just newline-delimited JSON-RPC 2.0, so we
// speak it directly: read a JSON object per line on stdin, write one per line
// on stdout. That keeps the whole system sovereign and offline — nothing to
// npm install, nothing phoning home.
//
// This is the wrapper Echo's chat asked for: "wrap it as an MCP so every
// surface (glass, chat, cron) reads and writes the same list." Point glass,
// the Claude CLI, and any cron job at this one server.
//
// IMPORTANT: stdout carries protocol only. All logging goes to stderr.

import readline from 'node:readline';
import { load, save } from './store.js';
import * as T from './tickler.js';

const PROTOCOL_VERSION = '2024-11-05';
const SERVER_INFO = { name: 'echo-tickler', version: '0.1.0' };

const log = (...a) => process.stderr.write(a.join(' ') + '\n');

// Each tool maps to a tickler operation. mutates:true tools save the board.
const TOOLS = {
  tickler_add: {
    description: 'Add an item to the tickler. Sleeps until tickleDate, then resurfaces.',
    inputSchema: {
      type: 'object',
      properties: {
        title: { type: 'string' },
        note: { type: 'string' },
        tickleDate: { type: 'string', description: 'YYYY-MM-DD; defaults to today' },
        priority: { type: 'number', description: '1 highest .. 3 lowest' },
        surface: { type: 'string', description: 'glass | chat | cron' },
        block: { type: 'boolean', description: 'promote to a power block' },
        steps: { type: 'array', items: { type: 'string' } },
        tags: { type: 'array', items: { type: 'string' } },
      },
      required: ['title'],
    },
    mutates: true,
    run: (b, a) => T.add(b, a),
  },
  tickler_list: {
    description: 'List items. filter: all|open|active|waiting|due|overdue|done|blocks',
    inputSchema: { type: 'object', properties: { filter: { type: 'string' } } },
    run: (b, a) => T.list(b, { filter: a.filter || 'open' }),
  },
  tickler_tick: {
    description: 'Resurface every waiting item whose date has arrived. Run daily.',
    inputSchema: { type: 'object', properties: {} },
    mutates: true,
    run: (b) => T.tick(b),
  },
  tickler_complete: {
    description: 'Mark an item done and record it in the changelog.',
    inputSchema: { type: 'object', properties: { id: { type: 'string' } }, required: ['id'] },
    mutates: true,
    run: (b, a) => T.complete(b, a.id),
  },
  tickler_snooze: {
    description: 'Push an item to a new tickle date (YYYY-MM-DD).',
    inputSchema: {
      type: 'object',
      properties: { id: { type: 'string' }, tickleDate: { type: 'string' } },
      required: ['id', 'tickleDate'],
    },
    mutates: true,
    run: (b, a) => T.snooze(b, a.id, a.tickleDate),
  },
  tickler_block: {
    description: 'Promote (block:true) or demote an item as a power block.',
    inputSchema: {
      type: 'object',
      properties: { id: { type: 'string' }, block: { type: 'boolean' } },
      required: ['id'],
    },
    mutates: true,
    run: (b, a) => T.setBlock(b, a.id, a.block !== false),
  },
  tickler_step: {
    description: 'Set a step done/undone on a power block (drives N/3 progress).',
    inputSchema: {
      type: 'object',
      properties: { id: { type: 'string' }, index: { type: 'number' }, done: { type: 'boolean' } },
      required: ['id', 'index'],
    },
    mutates: true,
    run: (b, a) => T.setStep(b, a.id, a.index, a.done !== false),
  },
  tickler_power_blocks: {
    description: 'The up-to-4 power-block cards for glass (title, subtitle, N/3 progress).',
    inputSchema: { type: 'object', properties: {} },
    run: (b) => T.powerBlocks(b),
  },
  tickler_rollup: {
    description: 'Daily rollup: top three, the how-far-behind number, and the changelog.',
    inputSchema: { type: 'object', properties: {} },
    run: (b) => T.rollup(b),
  },
  tickler_behind: {
    description: 'Just the how-far-behind number and open/done counts, from the board.',
    inputSchema: { type: 'object', properties: {} },
    run: (b) => T.metrics(b),
  },
};

function callTool(name, args) {
  const tool = TOOLS[name];
  if (!tool) throw { code: -32602, message: `unknown tool: ${name}` };
  const board = load();
  const result = tool.run(board, args || {});
  if (tool.mutates) save(board);
  return { content: [{ type: 'text', text: JSON.stringify(result, null, 2) }] };
}

function handle(method, params) {
  switch (method) {
    case 'initialize':
      return { protocolVersion: PROTOCOL_VERSION, capabilities: { tools: {} }, serverInfo: SERVER_INFO };
    case 'tools/list':
      return {
        tools: Object.entries(TOOLS).map(([name, t]) => ({
          name,
          description: t.description,
          inputSchema: t.inputSchema,
        })),
      };
    case 'tools/call':
      return callTool(params?.name, params?.arguments);
    case 'ping':
      return {};
    default:
      throw { code: -32601, message: `method not found: ${method}` };
  }
}

function send(msg) {
  process.stdout.write(JSON.stringify(msg) + '\n');
}

const rl = readline.createInterface({ input: process.stdin });
rl.on('line', (line) => {
  const text = line.trim();
  if (!text) return;
  let req;
  try {
    req = JSON.parse(text);
  } catch {
    log('skip non-JSON line');
    return;
  }
  // Notifications have no id and expect no reply (e.g. notifications/initialized).
  if (req.id === undefined || req.id === null) return;
  try {
    send({ jsonrpc: '2.0', id: req.id, result: handle(req.method, req.params) });
  } catch (err) {
    const code = typeof err?.code === 'number' ? err.code : -32603;
    send({ jsonrpc: '2.0', id: req.id, error: { code, message: err?.message || String(err) } });
  }
});

log(`echo-tickler MCP server ready on stdio (${Object.keys(TOOLS).length} tools)`);
