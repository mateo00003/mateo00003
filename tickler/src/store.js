// store.js — the source of truth.
//
// One local JSON file. No server, no cloud, no Airtable. You own this file.
// Every surface (glass, chat, cron) loads and saves through here, so there is
// exactly one list and it can never disagree with itself.

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DEFAULT_DB = path.join(__dirname, '..', 'data', 'tickler.json');

const EMPTY = { version: 1, items: [], changelog: [], rollups: [] };

/** Resolve the store location. Override with TICKLER_DB to point anywhere. */
export function dbPath() {
  return process.env.TICKLER_DB ? path.resolve(process.env.TICKLER_DB) : DEFAULT_DB;
}

/** Load the whole board. Missing file => a fresh, empty board (never throws). */
export function load(file = dbPath()) {
  try {
    const data = JSON.parse(fs.readFileSync(file, 'utf8'));
    return {
      version: data.version ?? 1,
      items: Array.isArray(data.items) ? data.items : [],
      changelog: Array.isArray(data.changelog) ? data.changelog : [],
      rollups: Array.isArray(data.rollups) ? data.rollups : [],
    };
  } catch (err) {
    if (err.code === 'ENOENT') return structuredClone(EMPTY);
    throw err;
  }
}

/** Save atomically: write a temp file then rename, so a crash never truncates the board. */
export function save(data, file = dbPath()) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const tmp = `${file}.${process.pid}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(data, null, 2) + '\n');
  fs.renameSync(tmp, file);
  return data;
}
