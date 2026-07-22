// tickler.js — the domain logic.
//
// A tickler item sleeps ("waiting") until its tickle date arrives, then it
// resurfaces ("active"). That resurfacing is the whole point of a tickler
// system, and it's the part that "wasn't working": nothing was flipping items
// active on their date. tick() does that, and cron runs it daily.
//
// Every function is pure-ish: it mutates the passed-in board object and returns
// a result. Callers (CLI, MCP, cron) are responsible for load()/save().

let COUNTER = 0;

/** Local calendar date as YYYY-MM-DD (sorts and compares as a plain string). */
export function today(now = new Date()) {
  const y = now.getFullYear();
  const m = String(now.getMonth() + 1).padStart(2, '0');
  const d = String(now.getDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
}

function newId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID().slice(0, 8);
  return `t${Date.now().toString(36)}${(COUNTER++).toString(36)}`;
}

function daysBetween(fromISO, toISO) {
  const a = new Date(`${fromISO}T00:00:00`);
  const b = new Date(`${toISO}T00:00:00`);
  return Math.round((b - a) / 86_400_000);
}

const OPEN = new Set(['waiting', 'active']);

/** Add an item. Defaults its tickle date to today, so it's active immediately
 *  unless you schedule it for later. */
export function add(board, input = {}, now = new Date()) {
  const t = today(now);
  const tickleDate = input.tickleDate || t;
  const item = {
    id: input.id || newId(),
    title: String(input.title || '').trim(),
    note: input.note || '',
    status: tickleDate <= t ? 'active' : 'waiting',
    tickleDate,
    priority: clampPriority(input.priority),
    surface: input.surface || 'chat',
    block: Boolean(input.block),
    steps: normalizeSteps(input.steps),
    tags: Array.isArray(input.tags) ? input.tags : [],
    createdAt: now.toISOString(),
    updatedAt: now.toISOString(),
    completedAt: null,
  };
  if (!item.title) throw new Error('title is required');
  board.items.push(item);
  return item;
}

function clampPriority(p) {
  const n = Number(p);
  if (!Number.isFinite(n)) return 2; // default: medium
  return Math.min(3, Math.max(1, Math.round(n))); // 1 = highest, 3 = lowest
}

function normalizeSteps(steps) {
  if (!Array.isArray(steps)) return [];
  return steps.map((s) =>
    typeof s === 'string' ? { text: s, done: false } : { text: String(s.text || ''), done: Boolean(s.done) },
  );
}

const byPriorityThenDate = (a, b) =>
  a.priority - b.priority || a.tickleDate.localeCompare(b.tickleDate) || a.createdAt.localeCompare(b.createdAt);

function find(board, id) {
  const item = board.items.find((i) => i.id === id);
  if (!item) throw new Error(`no item with id ${id}`);
  return item;
}

/** List items by filter, always sorted highest-priority / soonest first. */
export function list(board, { filter = 'open', now = new Date() } = {}) {
  const t = today(now);
  const items = board.items.filter((i) => {
    switch (filter) {
      case 'all': return true;
      case 'open': return OPEN.has(i.status);
      case 'active': return i.status === 'active';
      case 'waiting': return i.status === 'waiting';
      case 'done': return i.status === 'done';
      case 'due': return OPEN.has(i.status) && i.tickleDate <= t;
      case 'overdue': return OPEN.has(i.status) && i.tickleDate < t;
      case 'blocks': return OPEN.has(i.status) && i.block;
      default: throw new Error(`unknown filter: ${filter}`);
    }
  });
  return items.sort(byPriorityThenDate);
}

/** Resurface: flip every waiting item whose date has arrived to active.
 *  Returns the items that just woke up. This is the daily heartbeat. */
export function tick(board, now = new Date()) {
  const t = today(now);
  const woke = [];
  for (const i of board.items) {
    if (i.status === 'waiting' && i.tickleDate <= t) {
      i.status = 'active';
      i.updatedAt = now.toISOString();
      woke.push(i);
      logChange(board, i, 'surfaced', now);
    }
  }
  return woke;
}

/** Mark done and record it in the changelog (the standing record of progress). */
export function complete(board, id, now = new Date()) {
  const item = find(board, id);
  item.status = 'done';
  item.completedAt = now.toISOString();
  item.updatedAt = now.toISOString();
  logChange(board, item, 'done', now);
  return item;
}

/** Push an item's tickle date out. Future date => back to waiting. */
export function snooze(board, id, tickleDate, now = new Date()) {
  const item = find(board, id);
  item.tickleDate = tickleDate;
  item.status = tickleDate <= today(now) ? 'active' : 'waiting';
  item.updatedAt = now.toISOString();
  return item;
}

/** Promote/demote an item to a power block (the action cards under the chat). */
export function setBlock(board, id, block, now = new Date()) {
  const item = find(board, id);
  item.block = Boolean(block);
  item.updatedAt = now.toISOString();
  return item;
}

/** Toggle a step's done state (drives the N/3 progress on a power block). */
export function setStep(board, id, index, done, now = new Date()) {
  const item = find(board, id);
  if (index < 0 || index >= item.steps.length) throw new Error(`no step ${index} on ${id}`);
  item.steps[index].done = Boolean(done);
  item.updatedAt = now.toISOString();
  return item;
}

function logChange(board, item, action, now) {
  board.changelog.push({ ts: now.toISOString(), id: item.id, title: item.title, action });
}

// --- Reporting: everything below reads the board, never memory. ---------------

/** How far behind, sourced from the board. Overdue = open and past its date. */
export function metrics(board, now = new Date()) {
  const t = today(now);
  const open = board.items.filter((i) => OPEN.has(i.status));
  const overdue = open.filter((i) => i.tickleDate < t);
  const oldest = overdue.reduce((max, i) => Math.max(max, daysBetween(i.tickleDate, t)), 0);
  return {
    open: open.length,
    active: open.filter((i) => i.status === 'active').length,
    waiting: open.filter((i) => i.status === 'waiting').length,
    done: board.items.filter((i) => i.status === 'done').length,
    overdue: overdue.length,
    oldestOverdueDays: oldest,
    // The single "how far behind" number Echo puts in every update:
    behind: overdue.length,
    behindLabel: overdue.length === 0 ? 'on track' : `${overdue.length} overdue, oldest ${oldest}d`,
  };
}

/** Power blocks: up to 4 promoted, open items shaped as the glass cards. */
export function powerBlocks(board, now = new Date()) {
  return list(board, { filter: 'blocks', now })
    .slice(0, 4)
    .map((i) => {
      const total = i.steps.length;
      const done = i.steps.filter((s) => s.done).length;
      return {
        id: i.id,
        title: i.title,
        subtitle: i.note || i.tickleDate,
        progress: `${done}/${total || 3}`,
        done,
        total: total || 3,
        priority: i.priority,
      };
    });
}

/** The daily rollup: the top three, the behind number, and what changed. */
export function rollup(board, now = new Date(), sinceTs) {
  const since = sinceTs || new Date(now.getTime() - 24 * 3600 * 1000).toISOString();
  const topThree = list(board, { filter: 'due', now }).slice(0, 3);
  const recent = board.changelog.filter((c) => c.ts >= since);
  return {
    date: today(now),
    behind: metrics(board, now),
    topThree: topThree.map((i) => ({ id: i.id, title: i.title, priority: i.priority, tickleDate: i.tickleDate })),
    changelogSince: recent,
    powerBlocks: powerBlocks(board, now),
  };
}

/** Record a rollup on the board and cap history at 60 entries. */
export function recordRollup(board, r) {
  board.rollups.push({ date: r.date, behind: r.behind.behind, done: r.changelogSince.filter((c) => c.action === 'done').length });
  if (board.rollups.length > 60) board.rollups = board.rollups.slice(-60);
  return r;
}

/** Human-readable rollup for chat / terminal. */
export function formatRollup(r) {
  const lines = [];
  lines.push(`# Echo rollup — ${r.date}`);
  lines.push('');
  lines.push(`How far behind: ${r.behind.behindLabel}  (open ${r.behind.open}, done ${r.behind.done})`);
  lines.push('');
  lines.push('Top three:');
  if (r.topThree.length === 0) lines.push('  (nothing due — clear board)');
  r.topThree.forEach((i, n) => lines.push(`  ${n + 1}. [P${i.priority}] ${i.title}  (${i.id})`));
  lines.push('');
  const dones = r.changelogSince.filter((c) => c.action === 'done');
  lines.push(`Changelog (last window): ${dones.length} done`);
  dones.forEach((c) => lines.push(`  ✓ ${c.title}`));
  return lines.join('\n');
}
