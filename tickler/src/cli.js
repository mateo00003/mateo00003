#!/usr/bin/env node
// cli.js — drive the same list from a terminal or a cron job, no MCP needed.
//
//   tickler add "Send Todd: repos still empty" --pri 1 --block --step "draft" --step "send"
//   tickler list [open|due|overdue|blocks|all]
//   tickler tick                 # resurface due items (what cron runs)
//   tickler done <id>
//   tickler snooze <id> <YYYY-MM-DD>
//   tickler block <id> | tickler unblock <id>
//   tickler step <id> <index> [--undone]
//   tickler blocks               # power-block cards
//   tickler behind               # the how-far-behind number
//   tickler rollup               # top three + behind + changelog
//
// Add --json to any read command for machine-readable output.

import { load, save } from './store.js';
import * as T from './tickler.js';

const argv = process.argv.slice(2);
const cmd = argv[0];
const rest = argv.slice(1);

// Split flags (--foo, --foo bar, repeatable --step) from positionals.
const flags = {};
const steps = [];
const positional = [];
for (let i = 0; i < rest.length; i++) {
  const a = rest[i];
  if (a === '--step') { steps.push(rest[++i]); continue; }
  if (a.startsWith('--')) {
    const key = a.slice(2);
    const next = rest[i + 1];
    if (next === undefined || next.startsWith('--')) flags[key] = true;
    else { flags[key] = next; i++; }
    continue;
  }
  positional.push(a);
}

const out = (v) =>
  console.log(flags.json ? JSON.stringify(v, null, 2) : typeof v === 'string' ? v : render(v));

function render(v) {
  if (Array.isArray(v)) return v.map(renderItem).join('\n') || '(none)';
  return renderItem(v);
}
function renderItem(i) {
  if (i.progress !== undefined) return `[${i.progress}] ${i.title} — ${i.subtitle}  (${i.id})`;
  const flag = i.block ? '★' : ' ';
  const when = i.status === 'waiting' ? `↦${i.tickleDate}` : i.status === 'done' ? '✓' : `P${i.priority}`;
  return `${flag} ${when}  ${i.title}  (${i.id})`;
}

function main() {
  const board = load();
  switch (cmd) {
    case 'add': {
      const item = T.add(board, {
        title: positional.join(' '),
        note: flags.note || '',
        tickleDate: flags.date,
        priority: flags.pri,
        surface: flags.surface,
        block: Boolean(flags.block),
        steps,
        tags: flags.tag ? [flags.tag] : [],
      });
      save(board);
      return out(item);
    }
    case 'list': return out(T.list(board, { filter: positional[0] || 'open' }));
    case 'tick': { const woke = T.tick(board); save(board); return out({ surfaced: woke.length, items: woke }); }
    case 'done': { const i = T.complete(board, positional[0]); save(board); return out(i); }
    case 'snooze': { const i = T.snooze(board, positional[0], positional[1]); save(board); return out(i); }
    case 'block': { const i = T.setBlock(board, positional[0], true); save(board); return out(i); }
    case 'unblock': { const i = T.setBlock(board, positional[0], false); save(board); return out(i); }
    case 'step': {
      const i = T.setStep(board, positional[0], Number(positional[1]), !flags.undone);
      save(board);
      return out(i);
    }
    case 'blocks': return out(T.powerBlocks(board));
    case 'behind': return out(T.metrics(board));
    case 'rollup': {
      const r = T.rollup(board);
      return out(flags.json ? r : T.formatRollup(r));
    }
    default:
      console.error('commands: add list tick done snooze block unblock step blocks behind rollup');
      process.exit(cmd ? 1 : 0);
  }
}

try {
  main();
} catch (err) {
  console.error(`error: ${err.message}`);
  process.exit(1);
}
