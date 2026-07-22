#!/usr/bin/env node
// daily-rollup.js — the scheduled heartbeat.
//
// Run this once a day (cron, launchd, or Echo's own scheduler). It:
//   1. tick()s the board so items due today resurface (this is the fix for
//      "the tickler doesn't seem to be working" — nothing was resurfacing).
//   2. builds the rollup: top three, the how-far-behind number, the changelog.
//   3. records the rollup on the board and prints it for Echo to speak/post.
//
// Example crontab (07:00 daily):
//   0 7 * * * cd /path/to/tickler && node cron/daily-rollup.js >> data/rollup.log 2>&1

import { load, save } from '../src/store.js';
import * as T from '../src/tickler.js';

const board = load();
const woke = T.tick(board);
const r = T.rollup(board);
T.recordRollup(board, r);
save(board);

if (process.argv.includes('--json')) {
  console.log(JSON.stringify({ surfaced: woke.length, ...r }, null, 2));
} else {
  if (woke.length) console.log(`Surfaced ${woke.length} item(s) due today.\n`);
  console.log(T.formatRollup(r));
}
