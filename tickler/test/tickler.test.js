// Zero-dependency tests: node --test. Runs fully offline.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

import * as T from '../src/tickler.js';
import { load, save } from '../src/store.js';

const NOW = new Date('2026-07-22T12:00:00');
const fresh = () => ({ version: 1, items: [], changelog: [], rollups: [] });

test('add: due today is active, future is waiting', () => {
  const b = fresh();
  const now = T.add(b, { title: 'now thing' }, NOW);
  const later = T.add(b, { title: 'later thing', tickleDate: '2026-08-01' }, NOW);
  assert.equal(now.status, 'active');
  assert.equal(later.status, 'waiting');
  assert.equal(now.tickleDate, '2026-07-22');
});

test('add requires a title', () => {
  assert.throws(() => T.add(fresh(), { title: '   ' }, NOW), /title is required/);
});

test('tick resurfaces waiting items whose date has arrived', () => {
  const b = fresh();
  T.add(b, { title: 'past', id: 'a', tickleDate: '2026-07-20' }, NOW); // active already
  const item = T.add(b, { title: 'wakes', id: 'b', tickleDate: '2026-08-01' }, NOW);
  item.status = 'waiting';
  item.tickleDate = '2026-07-22'; // now due, but still marked waiting
  const woke = T.tick(b, NOW);
  assert.equal(woke.length, 1);
  assert.equal(woke[0].id, 'b');
  assert.equal(b.items.find((i) => i.id === 'b').status, 'active');
  assert.ok(b.changelog.some((c) => c.action === 'surfaced' && c.id === 'b'));
});

test('complete marks done and writes the changelog', () => {
  const b = fresh();
  const i = T.add(b, { title: 'finish me', id: 'x' }, NOW);
  T.complete(b, i.id, NOW);
  assert.equal(b.items[0].status, 'done');
  assert.ok(b.items[0].completedAt);
  assert.ok(b.changelog.some((c) => c.action === 'done' && c.id === 'x'));
});

test('behind number counts open items past their date', () => {
  const b = fresh();
  T.add(b, { title: 'late-1', tickleDate: '2026-07-18' }, NOW); // 4d overdue
  T.add(b, { title: 'late-2', tickleDate: '2026-07-21' }, NOW); // 1d overdue
  T.add(b, { title: 'today', tickleDate: '2026-07-22' }, NOW); // not overdue
  const m = T.metrics(b, NOW);
  assert.equal(m.behind, 2);
  assert.equal(m.oldestOverdueDays, 4);
  assert.match(m.behindLabel, /2 overdue, oldest 4d/);
});

test('behind is "on track" when nothing is overdue', () => {
  const b = fresh();
  T.add(b, { title: 'today', tickleDate: '2026-07-22' }, NOW);
  assert.equal(T.metrics(b, NOW).behindLabel, 'on track');
});

test('rollup surfaces at most the top three, highest priority first', () => {
  const b = fresh();
  T.add(b, { title: 'p3', priority: 3, tickleDate: '2026-07-20' }, NOW);
  T.add(b, { title: 'p1', priority: 1, tickleDate: '2026-07-20' }, NOW);
  T.add(b, { title: 'p2', priority: 2, tickleDate: '2026-07-20' }, NOW);
  T.add(b, { title: 'p2b', priority: 2, tickleDate: '2026-07-20' }, NOW);
  const r = T.rollup(b, NOW);
  assert.equal(r.topThree.length, 3);
  assert.equal(r.topThree[0].title, 'p1');
});

test('power blocks: promoted items with N/total step progress, max 4', () => {
  const b = fresh();
  const i = T.add(b, { title: 'block A', block: true, steps: ['one', 'two', 'three'] }, NOW);
  T.setStep(b, i.id, 0, true, NOW);
  for (let n = 0; n < 5; n++) T.add(b, { title: `filler ${n}`, block: true }, NOW);
  const blocks = T.powerBlocks(b, NOW);
  assert.equal(blocks.length, 4);
  const a = blocks.find((x) => x.title === 'block A');
  assert.equal(a.progress, '1/3');
});

test('snooze to the future sends an item back to waiting', () => {
  const b = fresh();
  const i = T.add(b, { title: 'snooze me' }, NOW);
  T.snooze(b, i.id, '2026-08-15', NOW);
  assert.equal(b.items[0].status, 'waiting');
  assert.equal(b.items[0].tickleDate, '2026-08-15');
});

test('store save/load round-trips through a real file', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'tickler-'));
  const file = path.join(dir, 'tickler.json');
  const b = fresh();
  T.add(b, { title: 'persist me', id: 'keep' }, NOW);
  save(b, file);
  const loaded = load(file);
  assert.equal(loaded.items.length, 1);
  assert.equal(loaded.items[0].id, 'keep');
  fs.rmSync(dir, { recursive: true, force: true });
});

test('load of a missing file returns an empty board', () => {
  const loaded = load(path.join(os.tmpdir(), 'does-not-exist-xyz.json'));
  assert.deepEqual(loaded.items, []);
});
