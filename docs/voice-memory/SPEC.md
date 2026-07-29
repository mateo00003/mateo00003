# Vox — Personal Voice Memory Pipeline

**Status:** Draft v0.1 · **Owner:** @mateo00003 · **Target:** implementation-ready

A source-agnostic pipeline that turns any recorded audio — life log, voice memos,
Google Meet, future sources — into structured, searchable, speaker-attributed
memory that agents can query.

> **Open dependency:** Gigaton already runs `Gigaton-Network/gigaton-memory-server`.
> Sections [§6 Data Model](#6-data-model) and [§13 Retrieval](#13-retrieval) are
> specified standalone and **must be reconciled** against that service before
> Phase 1 build starts. See [§20 Open Questions](#20-open-questions).

---

## Table of contents

1. [Goals and non-goals](#1-goals-and-non-goals)
2. [Glossary](#2-glossary)
3. [Design principles](#3-design-principles)
4. [System architecture](#4-system-architecture)
5. [Repository layout](#5-repository-layout)
6. [Data model](#6-data-model)
7. [Core contracts](#7-core-contracts)
8. [Capture adapters](#8-capture-adapters)
9. [Ingest service](#9-ingest-service)
10. [Job queue and lifecycle](#10-job-queue-and-lifecycle)
11. [ASR worker](#11-asr-worker)
12. [Speaker identity service](#12-speaker-identity-service)
13. [Retrieval](#13-retrieval)
14. [Enrichment](#14-enrichment)
15. [MCP server](#15-mcp-server)
16. [Privacy, consent, security](#16-privacy-consent-security)
17. [Configuration](#17-configuration)
18. [Observability](#18-observability)
19. [Testing strategy](#19-testing-strategy)
20. [Open questions](#20-open-questions)
21. [Phased build plan](#21-phased-build-plan)

---

## 1. Goals and non-goals

### Goals

- **G1** — Ingest audio from heterogeneous sources through one contract.
- **G2** — Produce speaker-attributed, timestamped transcripts with stable speaker
  identity *across* recordings, not just within one.
- **G3** — Make every transcript retrievable by semantic meaning, exact phrase, speaker,
  and time range.
- **G4** — Expose the corpus to agents as tools (MCP), not as a UI.
- **G5** — Keep the ASR engine swappable. The pipeline outlives any single model.
- **G6** — Preserve raw audio so the entire archive can be re-transcribed when models improve.
- **G7** — Make consent and privacy class first-class fields enforced at capture.

### Non-goals (v1)

- **NG1** — Real-time / streaming transcription. Everything is post-hoc batch.
  Live captioning is a different engine and a separate design.
- **NG2** — Multi-tenant SaaS. Single operator, single corpus. Schema leaves room
  for `owner_id` but v1 does not implement tenancy.
- **NG3** — A web UI. MCP tools plus SQL are the v1 interface.
- **NG4** — Automatic upload of client audio to third-party APIs. See [§16](#16-privacy-consent-security).

### Success criteria

The system is working when this query returns a correct answer with a citation
that links to an audio timestamp:

> "What did Sarah commit to on the pricing question, and when?"

---

## 2. Glossary

| Term | Meaning |
|---|---|
| **Source** | One captured audio artifact plus its metadata. The unit of ingest. |
| **Episode** | A life-log segment cut from continuous recording, bounded by silence and a duration cap. Life-log sources are always episodes. |
| **Utterance** | One contiguous speech span by one speaker: `(start_s, end_s, speaker_tag, text)`. Engine output unit. |
| **Segment** | A persisted utterance, post speaker-resolution. |
| **Chunk** | A window of consecutive segments embedded as one vector. Retrieval unit. |
| **Speaker tag** | Engine-local, job-scoped label (`SPEAKER_00`). Meaningless across jobs. |
| **Speaker** | A globally resolved person identity, stable across the corpus. |
| **Voiceprint** | A speaker embedding used to map speaker tags → speakers. |
| **Engine** | An `ASREngine` implementation (VibeVoice, WhisperX, hosted API). |
| **Privacy class** | `personal` \| `client` \| `public` — drives routing and retention. |

---

## 3. Design principles

**P1 — One envelope, many adapters.** Sources differ only in capture. Everything
downstream sees one schema. Adding a source is writing an adapter, never touching
the pipeline.

**P2 — The engine is a plugin.** All model-specific logic lives behind
`ASREngine`. VibeVoice-ASR is the v1 implementation, not the architecture.

**P3 — Content-addressed and idempotent.** Every audio blob is keyed by SHA-256.
Job identity is `(audio_sha256, engine_name, engine_version)`. Replays are free
and safe.

**P4 — Raw audio is the source of truth.** Transcripts are derived artifacts and
are expected to be regenerated. Never delete audio to save transcript storage.

**P5 — Diarization ≠ identity.** The engine says "two people spoke." Deciding
*who* is a separate, independently-versioned stage. Never conflate them.

**P6 — Postgres until it hurts.** Relational data, queue, full-text, and vectors
in one system. Adding infra is a decision to be forced, not assumed.

**P7 — Consent is data, not policy.** A recording without a consent record is a
bug that fails closed.

---

## 4. System architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│ CAPTURE (per-source, thin)                                              │
│                                                                         │
│  lifelog        voicememo        gmeet            future                │
│  wearable/      iOS Shortcut /   Drive folder     phone, Slack          │
│  phone rec      iCloud watch     watcher          huddle, YouTube       │
│      │               │               │                 │               │
│  VAD gate +      passthrough    + Calendar          adapter             │
│  episode cut                      enrichment                            │
└──────┼───────────────┼───────────────┼─────────────────┼────────────────┘
       └───────────────┴───────┬───────┴─────────────────┘
                               ▼
                    ┌──────────────────────┐
                    │ INGEST SERVICE       │  POST /v1/sources
                    │ validate envelope    │  → object store (sha256 key)
                    │ dedupe on sha256     │  → sources row
                    │ enqueue job          │  → jobs row
                    └──────────┬───────────┘
                               ▼
                    ┌──────────────────────┐
                    │ JOB QUEUE            │  Postgres SKIP LOCKED
                    │ lease / retry / DLQ  │  no extra infra
                    └──────────┬───────────┘
                               ▼
       ┌───────────────────────────────────────────────┐
       │ ASR WORKER (GPU)                              │
       │  1. ffmpeg normalize → 16 kHz mono WAV        │
       │  2. split if > engine.max_audio_s             │
       │  3. ASREngine.transcribe(audio, hotwords)     │
       │     └─ VibeVoiceASREngine (7B, 60 min pass)   │
       │  4. stitch chunks, normalize → Utterance[]    │
       └───────────────────────┬───────────────────────┘
                               ▼
       ┌───────────────────────────────────────────────┐
       │ SPEAKER IDENTITY (separate stage, versioned)  │
       │  embed each utterance → match voiceprints     │
       │  → resolve SPEAKER_00 → speaker_id            │
       └───────────────────────┬───────────────────────┘
                               ▼
       ┌───────────────────────────────────────────────┐
       │ ENRICHMENT (Claude)                           │
       │  glossary correction · entities · topics      │
       │  action items · decisions · summaries         │
       └───────────────────────┬───────────────────────┘
                               ▼
       ┌───────────────────────────────────────────────┐
       │ MEMORY                                        │
       │  Postgres: sources, transcripts, segments,    │
       │            speakers, chunks, enrichments      │
       │  pgvector (semantic) + tsvector (lexical)     │
       │  Object store: raw audio, forever             │
       └───────────────────────┬───────────────────────┘
                               ▼
       ┌───────────────────────────────────────────────┐
       │ SURFACE — MCP server                          │
       │  search_transcripts · get_transcript          │
       │  who_said · timeline · get_speakers           │
       └───────────────────────────────────────────────┘
```

### Stack

| Concern | Choice | Rationale |
|---|---|---|
| Language | Python 3.12 | ASR ecosystem is Python. |
| API | FastAPI + uvicorn | Pydantic models double as the envelope schema. |
| DB | Postgres 16 + `pgvector` + `pg_trgm` | One system for relations, queue, FTS, vectors (P6). |
| Migrations | Alembic | — |
| Object store | S3-compatible (R2 / MinIO) | Content-addressed keys. |
| Queue | Postgres `SELECT … FOR UPDATE SKIP LOCKED` | No broker to operate. |
| GPU | Modal (serverless) → on-prem later | Bursty, latency-tolerant batch work. |
| Audio | ffmpeg, `silero-vad` | — |
| Embeddings (text) | Configurable; default hosted | Behind an interface. |
| Embeddings (voice) | `pyannote/embedding` or ECAPA | — |

---

## 5. Repository layout

```
vox/
├── pyproject.toml
├── alembic/versions/
├── src/vox/
│   ├── config.py                 # pydantic-settings, all env
│   ├── models.py                 # SQLAlchemy ORM
│   ├── schemas.py                # Pydantic: IngestEnvelope, Utterance, ASRResult
│   │
│   ├── adapters/                 # §8 — one file per source
│   │   ├── base.py               #   CaptureAdapter protocol
│   │   ├── lifelog.py
│   │   ├── voicememo.py
│   │   └── gmeet.py
│   │
│   ├── ingest/
│   │   ├── api.py                # §9 FastAPI routes
│   │   └── store.py              # object store put/get, sha256 keys
│   │
│   ├── queue/
│   │   ├── enqueue.py            # §10
│   │   └── worker.py             #   lease loop, retry, DLQ
│   │
│   ├── asr/
│   │   ├── base.py               # §7.3 ASREngine protocol + dataclasses
│   │   ├── vibevoice.py          #   VibeVoiceASREngine
│   │   ├── whisperx.py           #   Phase-1 fallback
│   │   ├── preprocess.py         # §11.1 ffmpeg normalize
│   │   └── split.py              # §11.2 VAD-aware splitting + stitching
│   │
│   ├── speakers/
│   │   ├── embed.py              # §12 voice embedding
│   │   └── resolve.py            #   tag → speaker matching
│   │
│   ├── enrich/
│   │   ├── correct.py            # §14 glossary correction
│   │   ├── extract.py            #   entities, actions, decisions
│   │   └── summarize.py
│   │
│   ├── retrieval/
│   │   ├── chunk.py              # §13.1 windowing
│   │   ├── embed.py
│   │   └── search.py             # §13.3 hybrid RRF
│   │
│   └── mcp/server.py             # §15
└── tests/
    ├── fixtures/audio/           # short, committed, license-clean
    └── ...
```

---

## 6. Data model

> Reconcile with `gigaton-memory-server` before building. See [§20](#20-open-questions).

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
```

### 6.1 `sources`

One row per captured audio artifact.

```sql
CREATE TYPE source_kind   AS ENUM ('lifelog','voicememo','gmeet','phone','other');
CREATE TYPE privacy_class AS ENUM ('personal','client','public');

CREATE TABLE sources (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  kind               source_kind   NOT NULL,
  external_id        text          NOT NULL,   -- natural id at origin
  audio_sha256       char(64)      NOT NULL,
  audio_uri          text          NOT NULL,   -- s3://bucket/sha256/<hash>.wav
  audio_bytes        bigint        NOT NULL,
  duration_s         numeric(10,3) NOT NULL CHECK (duration_s > 0),
  captured_at        timestamptz   NOT NULL,
  tz                 text          NOT NULL,   -- IANA, e.g. America/Los_Angeles
  privacy            privacy_class NOT NULL,
  consent            jsonb         NOT NULL,   -- §16.1, NOT NULL = fails closed
  participants_hint  jsonb         NOT NULL DEFAULT '[]'::jsonb,
  hotwords           jsonb         NOT NULL DEFAULT '[]'::jsonb,
  external_refs      jsonb         NOT NULL DEFAULT '{}'::jsonb,
  parent_source_id   uuid REFERENCES sources(id),  -- episode → parent recording
  created_at         timestamptz   NOT NULL DEFAULT now(),
  UNIQUE (kind, external_id)
);

CREATE INDEX sources_sha_idx      ON sources (audio_sha256);
CREATE INDEX sources_captured_idx ON sources (captured_at DESC);
CREATE INDEX sources_kind_idx     ON sources (kind, captured_at DESC);
```

`audio_sha256` is **not** unique — the same audio may legitimately arrive from two
sources (a Meet recording also saved as a memo). Dedupe is per `(kind, external_id)`;
the hash is for blob reuse and job identity.

### 6.2 `transcripts`

One row per `(source, engine, engine_version)`. Re-transcription adds rows; it
never overwrites.

```sql
CREATE TABLE transcripts (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id       uuid NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  engine_name     text NOT NULL,
  engine_version  text NOT NULL,
  language        text,
  is_current      boolean NOT NULL DEFAULT true,
  raw_output      jsonb NOT NULL,        -- engine-native, archived verbatim
  wall_seconds    numeric(10,3),
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (source_id, engine_name, engine_version)
);

-- exactly one current transcript per source
CREATE UNIQUE INDEX transcripts_current_idx
  ON transcripts (source_id) WHERE is_current;
```

Archiving `raw_output` verbatim means an engine-format change never costs you
data — you can re-parse without re-running the GPU.

### 6.3 `segments`

```sql
CREATE TABLE segments (
  id              bigserial PRIMARY KEY,
  transcript_id   uuid NOT NULL REFERENCES transcripts(id) ON DELETE CASCADE,
  idx             int  NOT NULL,
  start_s         numeric(10,3) NOT NULL,
  end_s           numeric(10,3) NOT NULL,
  speaker_tag     text NOT NULL,            -- engine-local, e.g. SPEAKER_00
  speaker_id      uuid REFERENCES speakers(id),  -- NULL until resolved (P5)
  speaker_conf    real,
  text            text NOT NULL,
  text_corrected  text,                     -- §14.1; NULL = no correction
  asr_conf        real,
  tsv             tsvector GENERATED ALWAYS AS (
                    to_tsvector('english', coalesce(text_corrected, text))
                  ) STORED,
  CHECK (end_s >= start_s),
  UNIQUE (transcript_id, idx)
);

CREATE INDEX segments_tsv_idx      ON segments USING gin (tsv);
CREATE INDEX segments_trgm_idx     ON segments USING gin (text gin_trgm_ops);
CREATE INDEX segments_speaker_idx  ON segments (speaker_id);
CREATE INDEX segments_time_idx     ON segments (transcript_id, start_s);
```

`speaker_id` nullable is deliberate — transcription succeeds and is useful before
identity resolution runs or when it fails.

### 6.4 `speakers` and `voiceprints`

```sql
CREATE TABLE speakers (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  display_name text NOT NULL,
  emails       jsonb NOT NULL DEFAULT '[]'::jsonb,  -- join key to Calendar
  aliases      jsonb NOT NULL DEFAULT '[]'::jsonb,
  is_self      boolean NOT NULL DEFAULT false,
  created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE voiceprints (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  speaker_id  uuid NOT NULL REFERENCES speakers(id) ON DELETE CASCADE,
  embedding   vector(192) NOT NULL,        -- ECAPA-TDNN dim; set per model
  model       text NOT NULL,
  source_id   uuid REFERENCES sources(id), -- provenance
  quality     real,                        -- speech seconds behind it
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX voiceprints_ann_idx ON voiceprints
  USING hnsw (embedding vector_cosine_ops);
```

Multiple voiceprints per speaker is intentional — people sound different on a
phone vs. in a room, and centroids degrade when you average across conditions.

### 6.5 `chunks` — the retrieval unit

```sql
CREATE TABLE chunks (
  id             bigserial PRIMARY KEY,
  transcript_id  uuid NOT NULL REFERENCES transcripts(id) ON DELETE CASCADE,
  seg_start_idx  int NOT NULL,
  seg_end_idx    int NOT NULL,
  start_s        numeric(10,3) NOT NULL,
  end_s          numeric(10,3) NOT NULL,
  speaker_ids    uuid[] NOT NULL DEFAULT '{}',
  text           text NOT NULL,            -- speaker-prefixed, see §13.1
  embedding      vector(1536),
  embed_model    text,
  tsv            tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED
);

CREATE INDEX chunks_ann_idx ON chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX chunks_tsv_idx ON chunks USING gin (tsv);
CREATE INDEX chunks_time_idx ON chunks (start_s);
```

**Do not embed individual segments.** Utterances are frequently 3–8 words
("yeah, that works") and carry no standalone meaning. Embed windows (§13.1).

### 6.6 `enrichments`

```sql
CREATE TYPE enrichment_kind AS ENUM
  ('summary','action_item','decision','entity','topic','question');

CREATE TABLE enrichments (
  id             bigserial PRIMARY KEY,
  transcript_id  uuid NOT NULL REFERENCES transcripts(id) ON DELETE CASCADE,
  kind           enrichment_kind NOT NULL,
  payload        jsonb NOT NULL,       -- §14.2 shapes
  seg_start_idx  int,                  -- citation anchor
  seg_end_idx    int,
  model          text NOT NULL,
  prompt_version text NOT NULL,
  created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX enrichments_kind_idx ON enrichments (kind, transcript_id);
CREATE INDEX enrichments_payload_idx ON enrichments USING gin (payload);
```

Every enrichment carries a citation anchor. An action item you cannot trace back
to the audio that produced it is not trustworthy.

### 6.7 `jobs` — queue table

```sql
CREATE TYPE job_state AS ENUM
  ('pending','leased','succeeded','failed','dead');
CREATE TYPE job_kind AS ENUM
  ('transcribe','resolve_speakers','chunk_embed','enrich');

CREATE TABLE jobs (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  kind           job_kind  NOT NULL,
  state          job_state NOT NULL DEFAULT 'pending',
  idempotency_key text     NOT NULL,   -- §10.1
  payload        jsonb     NOT NULL,
  priority       int       NOT NULL DEFAULT 100,  -- lower runs first
  attempts       int       NOT NULL DEFAULT 0,
  max_attempts   int       NOT NULL DEFAULT 4,
  lease_until    timestamptz,
  last_error     text,
  run_after      timestamptz NOT NULL DEFAULT now(),
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (idempotency_key)
);

CREATE INDEX jobs_claim_idx ON jobs (kind, priority, run_after)
  WHERE state = 'pending';
CREATE INDEX jobs_reap_idx  ON jobs (lease_until) WHERE state = 'leased';
```

---

## 7. Core contracts

### 7.1 Ingest envelope

The one schema every adapter emits. `src/vox/schemas.py`:

```python
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field, field_validator

SourceKind   = Literal["lifelog", "voicememo", "gmeet", "phone", "other"]
PrivacyClass = Literal["personal", "client", "public"]

class Consent(BaseModel):
    """Fails closed: an envelope without this is rejected at the API boundary."""
    all_parties_notified: bool
    basis: Literal["self_only", "verbal_notice", "written", "platform_notice"]
    jurisdiction: str = "US-WA"          # §16.2 — WA is two-party consent
    note: str | None = None

class ParticipantHint(BaseModel):
    name: str
    email: str | None = None
    is_self: bool = False

class IngestEnvelope(BaseModel):
    kind: SourceKind
    external_id: str
    captured_at: datetime                 # tz-aware, enforced below
    tz: str = "America/Los_Angeles"
    duration_s: float = Field(gt=0)
    privacy: PrivacyClass
    consent: Consent
    participants_hint: list[ParticipantHint] = []
    hotwords: list[str] = []
    external_refs: dict[str, str] = {}    # calendar_event_id, drive_file_id, …
    parent_external_id: str | None = None # episode → parent recording

    @field_validator("captured_at")
    @classmethod
    def _tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("captured_at must be timezone-aware")
        return v
```

Audio travels as a separate multipart part, never inline base64 — these are
hour-long files.

### 7.2 ASR result types

```python
from dataclasses import dataclass, field

@dataclass(frozen=True)
class Utterance:
    start_s: float
    end_s: float
    speaker_tag: str            # engine-local; job-scoped only
    text: str
    confidence: float | None = None

@dataclass(frozen=True)
class ASRResult:
    utterances: list[Utterance]
    engine_name: str
    engine_version: str
    language: str | None = None
    raw: dict = field(default_factory=dict)   # archived to transcripts.raw_output
```

### 7.3 `ASREngine` — the swap point (P2)

```python
from pathlib import Path
from typing import Protocol, Sequence, runtime_checkable

@runtime_checkable
class ASREngine(Protocol):
    name: str
    version: str
    max_audio_s: float        # VibeVoice: 3600.0 — drives §11.2 splitting
    wants_sample_rate: int    # normalization target, §11.1

    def transcribe(
        self,
        audio: Path,
        *,
        hotwords: Sequence[str] = (),
        language: str | None = None,
    ) -> ASRResult: ...
```

Implementations:

| Class | Module | Phase | Notes |
|---|---|---|---|
| `WhisperXEngine` | `asr/whisperx.py` | 1 | Bootstrap. Lets the whole pipeline be built and tested without GPU spend. |
| `VibeVoiceASREngine` | `asr/vibevoice.py` | 2 | `microsoft/VibeVoice-ASR`, MIT. Target engine. |
| `HostedEngine` | `asr/hosted.py` | opt | Fallback. Gated by privacy class (§16.3). |

### 7.4 Capture adapter contract

```python
class CaptureAdapter(Protocol):
    kind: SourceKind
    def poll(self) -> Iterator[tuple[IngestEnvelope, Path]]: ...
```

Adapters may **only** produce envelopes and audio paths. Any adapter reaching
into the DB or queue is a design violation.

---

## 8. Capture adapters

### 8.1 Life log — `adapters/lifelog.py`

The hard one: continuous capture, mostly silence, unbounded length.

**Never submit raw continuous audio.** Segment at the edge:

```
1. Run silero-vad over the raw stream → speech regions.
2. Discard regions < MIN_SPEECH_S (0.8s) — coughs, door clicks.
3. Group adjacent speech regions into episodes, breaking when either:
     a. silence gap > EPISODE_GAP_S (default 90s), or
     b. accumulated span >= EPISODE_MAX_S (default 2700s / 45 min).
4. Pad each episode by ±2.0s so words aren't clipped at boundaries.
5. Emit one envelope per episode:
     external_id       = f"{device_id}:{episode_start_epoch}"
     parent_external_id= f"{device_id}:{recording_session_id}"
     privacy           = "personal"
     consent           = basis="self_only" unless overridden
```

`EPISODE_MAX_S` is 45 min against VibeVoice's 60-min ceiling. The 15-minute
margin absorbs the ±2s padding and, more importantly, the fact that the limit is
really a 64K *token* budget — dense multi-speaker speech consumes tokens faster
than the duration alone predicts. Do not set this to 3600.

A 16-hour day typically yields 1–3 hours of actual speech across 15–40 episodes.
VAD gating is what makes life-logging economically viable.

### 8.2 Voice memos — `adapters/voicememo.py`

The easy one.

- **Path A (recommended):** iOS Shortcut → `POST /v1/sources` on share.
- **Path B:** watch the iCloud Voice Memos directory; `external_id` = file UUID.

`privacy="personal"`, `consent.basis="self_only"`, hotwords = global glossary only.

### 8.3 Google Meet — `adapters/gmeet.py`

The highest-value one, because **the metadata exists before the audio does**.

```
1. Watch the Drive "Meet Recordings" folder → new file event.
2. Parse meeting title + start time from the filename.
3. Match to a Calendar event (±10 min of start).
4. From that event, extract:
     attendees[]  → participants_hint  (name + email)
     summary, description, attachment titles
5. Build hotwords:
     attendee given names + surnames
   + capitalized/technical terms from title & description
   + global glossary
   → dedupe, cap at HOTWORD_MAX (see §11.3)
6. privacy = "client" if any attendee email domain ∉ INTERNAL_DOMAINS
             else "personal"
7. consent.basis = "platform_notice"   (Meet announces recording)
8. external_refs = {drive_file_id, calendar_event_id}
```

Step 4→5 is the highest-ROI mechanism in this design. Proper nouns are precisely
what ASR fumbles, and the calendar already knows them. Feeding attendee names in
as hotwords costs nothing and directly attacks the dominant error class.

Step 4 also seeds §12: a Meet with 4 known attendees and 4 diarized speaker tags
is a small, well-constrained assignment problem rather than open-set identification.

### 8.4 Adding a source later

1. New module in `adapters/`, implement `CaptureAdapter`.
2. Add the value to the `source_kind` enum (one migration).
3. Register in the poller.

No changes to ingest, queue, worker, storage, or retrieval. That is the test of
whether P1 holds.

---

## 9. Ingest service

### `POST /v1/sources`

`multipart/form-data`: `envelope` (JSON) + `audio` (binary).

```
1.  Parse & validate IngestEnvelope        → 422 on failure
2.  Reject if consent missing/invalid       → 422  (P7, fails closed)
3.  Stream audio to temp, computing SHA-256 while streaming
4.  ffprobe: verify decodable, read true duration
      |envelope.duration_s − actual| > 2.0 → 422
5.  SELECT id FROM sources WHERE kind=? AND external_id=?
      found → 200 {id, status:"duplicate"}     (idempotent, no side effects)
6.  Object store PUT audio/<sha256>.<ext>   (skip if key exists)
7.  INSERT sources
8.  Enqueue transcribe job (§10.1)
9.  201 {source_id, job_id, status:"queued"}
```

Step 5 before step 6 makes re-POSTs free. Step 4 catches truncated uploads
before they cost GPU time.

### Other routes

| Route | Purpose |
|---|---|
| `GET /v1/sources/{id}` | Source + job states |
| `GET /v1/sources/{id}/transcript` | Current transcript with segments |
| `POST /v1/sources/{id}/retranscribe` | Force re-run, optional `engine` override |
| `GET /v1/healthz` | DB + object store + queue depth |

---

## 10. Job queue and lifecycle

### 10.1 Idempotency keys

```python
def idem_key(kind: str, source_id: str, **parts: str) -> str:
    suffix = ":".join(f"{k}={v}" for k, v in sorted(parts.items()))
    return f"{kind}:{source_id}:{suffix}"

# transcribe:<sid>:engine=vibevoice-asr:ver=1.0.0
# resolve_speakers:<sid>:tver=<transcript_id>:model=ecapa-1
# chunk_embed:<sid>:tver=<transcript_id>:model=embed-3
# enrich:<sid>:tver=<transcript_id>:pver=2
```

Engine/model/prompt version in the key means bumping a version naturally
re-processes the corpus, and re-running the same version is a no-op.

### 10.2 Claim

```sql
UPDATE jobs SET
  state       = 'leased',
  lease_until = now() + interval '90 minutes',
  attempts    = attempts + 1,
  updated_at  = now()
WHERE id = (
  SELECT id FROM jobs
  WHERE state = 'pending' AND kind = ANY(:kinds) AND run_after <= now()
  ORDER BY priority, run_after
  FOR UPDATE SKIP LOCKED
  LIMIT 1
)
RETURNING *;
```

The 90-minute lease must exceed worst-case GPU wall time for a 45-minute episode.
Measure in Phase 2 and set to 3× observed p99.

### 10.3 Completion, retry, reaping

```sql
-- success
UPDATE jobs SET state='succeeded', lease_until=NULL, updated_at=now() WHERE id=:id;

-- retryable failure: exponential backoff 2/4/8/16 min
UPDATE jobs SET
  state      = CASE WHEN attempts >= max_attempts THEN 'dead' ELSE 'pending' END,
  run_after  = now() + (interval '1 minute' * power(2, attempts)),
  last_error = :err, lease_until = NULL, updated_at = now()
WHERE id = :id;

-- reaper (every 60s): recover crashed workers
UPDATE jobs SET state='pending', lease_until=NULL
WHERE state='leased' AND lease_until < now();
```

Non-retryable (corrupt audio, unsupported codec) → `dead` immediately. Do not
burn four GPU attempts on a file that will never decode.

### 10.4 Chaining

Each stage enqueues the next on success:

```
transcribe → resolve_speakers → chunk_embed → enrich
```

Stages are independently replayable. `chunk_embed` does not depend on
`resolve_speakers` succeeding — it degrades to speaker-less chunk text rather
than blocking retrieval.

---

## 11. ASR worker

### 11.1 Preprocessing — `asr/preprocess.py`

```bash
ffmpeg -nostdin -i <input> \
  -vn -ac 1 -ar <engine.wants_sample_rate> \
  -acodec pcm_s16le -f wav <output>
```

Mono, 16-bit PCM, engine-specified rate. Loudness normalization
(`-af loudnorm=I=-16:TP=-1.5:LRA=11`) helps on wearable audio with variable
mic distance — make it a flag, default on for `lifelog`, off elsewhere.

> **Verify at implementation:** VibeVoice's expected input sample rate is not
> stated in the public docs. Its tokenizers run at a 7.5 Hz frame rate, but that
> is the *latent* rate, not the audio rate. Read the demo loader
> (`demo/vibevoice_asr_inference_from_file.py`) and set `wants_sample_rate`
> from what it actually does. Do not guess.

### 11.2 Splitting and stitching — `asr/split.py`

Only when `duration_s > engine.max_audio_s`. Life-log episodes are pre-capped
(§8.1), so this mainly guards long Meets and imported archives.

```
1. silero-vad → silence regions.
2. Target cut points at max_audio_s * 0.9.
3. Snap each cut to the longest silence within ±120s of target.
   No silence found → hard cut, flag chunk `hard_cut=true`.
4. Overlap adjacent chunks by OVERLAP_S = 15.0.
5. Transcribe each chunk.
6. Stitch:
   a. Offset all timestamps by chunk start.
   b. In overlap regions, drop utterances from the later chunk whose
      midpoint falls before the overlap midpoint.
   c. Re-map speaker tags across the seam by voice-embedding overlap-region
      utterances from both chunks and matching (Hungarian, cosine).
      Below SEAM_MATCH_MIN (0.55) → do not merge; emit distinct tags and let
      §12 resolve globally.
```

Step 6c is the chunk-boundary speaker-drift problem that motivates VibeVoice in
the first place. **Every split re-introduces it.** So: prefer episodes that fit
in one pass, and treat `hard_cut=true` chunks as lower-confidence.

### 11.3 Engine implementation — `asr/vibevoice.py`

```python
class VibeVoiceASREngine:
    name = "vibevoice-asr"
    version = "1.0.0"          # bump on weights OR parse change
    max_audio_s = 2700.0       # 45 min; see §8.1 on the 64K token budget
    wants_sample_rate = 16_000 # VERIFY per §11.1

    def __init__(self, model_path: str = "microsoft/VibeVoice-ASR-HF"): ...

    def transcribe(self, audio, *, hotwords=(), language=None) -> ASRResult:
        if language is not None:
            raise ValueError("VibeVoice auto-detects language; cannot be forced")
        ...
```

Notes drawn from the model's documented behavior:

- **Language cannot be pinned.** It is auto-detected. The signature accepts
  `language` for interface uniformity; this engine rejects a non-`None` value
  rather than silently ignoring it.
- **Hotwords** are documented as supported (names, technical terms, background
  info) but the parameter's exact form is not public. Isolate it in
  `_format_hotwords()`, cap at `HOTWORD_MAX` (start at 64, tune), and order by
  priority: participant names → meeting-specific terms → global glossary.
- **Weights are MIT**, so self-hosting is unencumbered. Microsoft's guidance
  that it is research-oriented is guidance, not a license restriction — see
  [§16.3](#163-engine-routing) for how privacy class handles that.

### 11.4 Output normalization

VibeVoice emits structured "who / when / what". Parse into `Utterance[]` and
**archive the native payload to `transcripts.raw_output` verbatim** before
parsing. A parser bug then costs a re-parse, not a re-run.

Normalization rules:
- Sort by `start_s`.
- Clamp `end_s` to source duration.
- Drop empty/whitespace-only text.
- Merge consecutive same-tag utterances separated by < 0.3s.
- Renumber `idx` densely from 0.

> **Verify at implementation:** the exact serialization is not in the public
> docs. Run the demo script on a fixture, capture the output, and write the
> parser against reality plus a committed golden-file test (§19).

### 11.5 Known weakness

Diarization on sub-second segments is reported as unreliable — meeting
backchannel ("yeah", "mm-hm", "right"). Consequences to design around:

- Never derive talk-time statistics from segments < 1.0s.
- Flag `segments.speaker_conf` low for these; exclude from voiceprint
  enrollment (§12.2).
- Never let a sub-second segment be the sole citation anchor for an enrichment.

---

## 12. Speaker identity service

The stage that makes a *corpus* rather than a pile of transcripts (P5).
VibeVoice answers "how many people spoke." This answers "who."

### 12.1 Resolution

```
For each distinct speaker_tag in the transcript:
  1. Select up to 8 utterances with duration ≥ 1.5s (longest first).
  2. Extract audio spans, embed each, L2-normalize, average → tag_vec.
  3. ANN query voiceprints:
       SELECT speaker_id, 1 - (embedding <=> :v) AS sim
       FROM voiceprints WHERE model = :m
       ORDER BY embedding <=> :v LIMIT 10;
  4. Aggregate to best-per-speaker (max sim).
  5. Assign:
       sim >= MATCH_STRONG (0.75) → assign, speaker_conf = sim
       sim >= MATCH_WEAK   (0.55) → assign, flag for review
       else                        → leave NULL, create provisional speaker
  6. Constrain with participants_hint (§8.3):
       If |tags| <= |hints| and all hints have voiceprints, solve as a
       bipartite assignment (Hungarian on the similarity matrix) instead of
       independent greedy matches. Enforces one-tag-one-person.
  7. Self-speaker prior: in `lifelog`, the tag with the most total speech is
       is_self with high probability. Use as a tiebreak, never as an override.
```

### 12.2 Enrollment

Voiceprints accumulate rather than being configured up front:

- **Bootstrap:** record 60s of your own voice → `speakers(is_self=true)`.
- **Confirmed:** a `MATCH_STRONG` assignment with ≥30s of clean speech adds a
  new voiceprint (P5 — conditions vary; keep them separate, don't average).
- **Manual:** `POST /v1/speakers/{id}/enroll` with `(source_id, start_s, end_s)`.
- **Calendar-assisted:** a 1:1 Meet with exactly 2 tags and 2 attendees, one of
  whom is you → the other tag is the other attendee with high confidence.
  This bootstraps the registry with near-zero manual effort.

Exclude from enrollment: segments < 1.5s (§11.5), segments overlapping another
speaker, `hard_cut=true` chunk boundaries.

### 12.3 Why not just use the engine

Speaker tags are job-scoped. `SPEAKER_00` in Monday's standup and `SPEAKER_00`
in Thursday's client call are unrelated. Without this stage, "what did Sarah say
about pricing" is unanswerable across recordings — which is the entire point of
having a corpus.

---

## 13. Retrieval

### 13.1 Chunking — `retrieval/chunk.py`

```
Window consecutive segments into chunks:
  - target CHUNK_TARGET_CHARS = 1200
  - hard max CHUNK_MAX_CHARS  = 2000
  - overlap CHUNK_OVERLAP_SEGS = 2 segments
  - never split mid-segment
  - break early on a silence gap > 20s (topic boundary signal)

Chunk text is speaker-prefixed:
  Matt: so the pricing question — where did we land
  Sarah: I think we hold at the current tier through Q3
  Matt: okay, and you'll write that up
```

Speaker-prefixing matters: it puts attribution *inside* the embedded text, so
"what did Sarah say about pricing" has lexical and semantic purchase on the
chunk, not just on the metadata filter.

### 13.2 Embeddings

Behind `retrieval/embed.py`, model recorded per chunk. Changing embedding model
bumps the `chunk_embed` idempotency key → automatic corpus re-embed.

### 13.3 Hybrid search — `retrieval/search.py`

**This is the section that most needs reconciling with `gigaton-memory-server`.**

Vector-only retrieval fails on exactly the queries this corpus attracts: proper
nouns, project names, and exact quotes. "What did Sarah say about the Holochain
migration" needs `Holochain` matched *lexically*. Embeddings smear rare tokens
toward their neighborhoods; a semantic index will happily return a chunk about
"decentralized storage" that never mentions Holochain, and rank it above the one
that does. Run both and fuse with Reciprocal Rank Fusion:

```sql
WITH semantic AS (
  SELECT id, ROW_NUMBER() OVER (ORDER BY embedding <=> :qvec) AS rank
  FROM chunks
  WHERE embedding IS NOT NULL AND (:speaker_id IS NULL OR :speaker_id = ANY(speaker_ids))
  ORDER BY embedding <=> :qvec LIMIT 60
),
lexical AS (
  SELECT id, ROW_NUMBER() OVER (
           ORDER BY ts_rank_cd(tsv, websearch_to_tsquery('english', :q)) DESC
         ) AS rank
  FROM chunks
  WHERE tsv @@ websearch_to_tsquery('english', :q)
  ORDER BY rank LIMIT 60
)
SELECT c.id, c.text, c.start_s, c.end_s, c.transcript_id,
       COALESCE(1.0/(60 + s.rank), 0) + COALESCE(1.0/(60 + l.rank), 0) AS score
FROM chunks c
LEFT JOIN semantic s ON s.id = c.id
LEFT JOIN lexical  l ON l.id = c.id
WHERE s.id IS NOT NULL OR l.id IS NOT NULL
ORDER BY score DESC
LIMIT :k;
```

RRF needs no score normalization between two incomparable scales — it fuses
ranks. The constant 60 is the standard damping term.

Filters compose as `WHERE` predicates: `speaker_id`, `captured_at` range,
`source.kind`, `privacy`.

### 13.4 Result shape

Every result carries a citation:

```json
{
  "chunk_id": 84213,
  "text": "Matt: so the pricing question…",
  "score": 0.0312,
  "source": {"id": "…", "kind": "gmeet", "captured_at": "2026-07-14T16:00:00-07:00",
             "title": "Gigaton / Acme — pricing"},
  "span": {"start_s": 1284.5, "end_s": 1349.0},
  "speakers": ["Matt", "Sarah Chen"],
  "audio_url": "/v1/sources/…/audio#t=1284.5"
}
```

`audio_url` with a time fragment closes the loop: every claim is one click from
the audio that produced it.

---

## 14. Enrichment

A Claude pass over the current transcript. Cheap next to GPU time; it's what
turns a transcript into memory.

### 14.1 Glossary correction — `enrich/correct.py`

Sliding window over segments with the glossary + participant names in context.
Writes `segments.text_corrected`, leaving `text` untouched — corrections are
auditable and reversible.

Constrain the model: only fix terms plausibly confusable with a glossary entry
(`"giga ton"` → `"Gigaton"`, `"holo chain"` → `"Holochain"`). No rewriting, no
grammar cleanup, no disfluency removal. Verbatim is the value.

### 14.2 Extraction — `enrich/extract.py`

Structured output per `enrichment_kind`, all carrying `seg_start_idx`/`seg_end_idx`:

```jsonc
// action_item
{"text": "Send revised pricing sheet", "owner_speaker_id": "…",
 "due": "2026-07-18", "confidence": 0.82}

// decision
{"text": "Hold current tier through Q3", "made_by": ["…"], "alternatives": ["…"]}

// entity
{"name": "Acme Corp", "type": "org", "mentions": [12, 44, 91]}

// topic
{"label": "pricing strategy", "seg_start_idx": 40, "seg_end_idx": 96}
```

### 14.3 Summaries — `enrich/summarize.py`

Three granularities: one-line (≤140 chars), abstract (≤200 words), sectioned by
topic with timestamp ranges. `prompt_version` bumps re-run the corpus (§10.1).

---

## 15. MCP server

The primary interface (G4). `src/vox/mcp/server.py`.

| Tool | Input | Returns |
|---|---|---|
| `search_transcripts` | `query`, `speaker?`, `after?`, `before?`, `kind?`, `limit=10` | Ranked chunks with citations (§13.4) |
| `get_transcript` | `source_id`, `start_s?`, `end_s?` | Full speaker-attributed segments |
| `who_said` | `query`, `speaker`, `limit=10` | Chunks filtered to one speaker |
| `timeline` | `after`, `before`, `kind?` | Sources with one-line summaries |
| `get_speakers` | — | Registry with counts and last-heard |
| `get_action_items` | `speaker?`, `after?`, `status?` | Action items with citations |

Design notes:
- Return **citations, not just text**. An agent that cannot point at the audio
  cannot be trusted about what was said.
- Default `limit` small (10). Context is the scarce resource.
- `search_transcripts` accepts natural-language dates ("last Tuesday") resolved
  server-side against `sources.tz` — the agent should not do timezone math.
- Respect privacy class: default excludes `client` unless explicitly requested (§16.3).

---

## 16. Privacy, consent, security

### 16.1 Consent record

`sources.consent` is `NOT NULL` — the schema enforces P7. No row exists without it.

### 16.2 Washington two-party consent

Washington (RCW 9.73.030) requires **all-party** consent for recording private
conversations. Both a life log and Meet recording implicate it, and remote
attendees may add other jurisdictions.

Enforcement is at capture, not policy:

- `lifelog` defaults `basis="self_only"`, which is valid **only** while no other
  party is being recorded. When §12 resolves a second speaker in a `self_only`
  episode, flag the source `consent_review_required=true` and exclude it from
  retrieval until reviewed.
- `gmeet` uses `basis="platform_notice"` — Meet announces recording to all
  participants, which is the consent event.
- Anything else requires explicit `verbal_notice` or `written`.

This is cheap now and painful to retrofit across a year of audio. Not legal
advice — worth a lawyer's read before recording client conversations.

### 16.3 Engine routing

```
privacy = "client"   → self-hosted engines only; HostedEngine refuses
privacy = "personal" → self-hosted preferred, hosted allowed if ALLOW_HOSTED=true
privacy = "public"   → any engine
```

The router raises rather than silently downgrading. This is also how Microsoft's
"not recommended for commercial use without further testing" guidance gets
handled concretely: client audio stays on infrastructure you control, and you
validate quality on your own data before trusting it for client work.

### 16.4 Other

- Object store: SSE at rest, no public ACLs, presigned URLs only, ≤15 min TTL.
- Postgres: TLS required; secrets from env, never committed (§17).
- Retention: `client` audio configurable TTL (default: keep, review annually);
  `personal` indefinite. Deletion cascades to transcripts/segments/chunks and
  tombstones the source row.
- `DELETE /v1/sources/{id}?purge=true` — hard-deletes audio and all derivatives.

---

## 17. Configuration

`src/vox/config.py`, pydantic-settings, `VOX_` prefix.

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VOX_", env_file=".env")

    database_url: PostgresDsn
    s3_endpoint: str
    s3_bucket: str
    s3_access_key: SecretStr
    s3_secret_key: SecretStr

    asr_engine: Literal["vibevoice", "whisperx", "hosted"] = "whisperx"
    vibevoice_model_path: str = "microsoft/VibeVoice-ASR-HF"
    allow_hosted: bool = False
    hotword_max: int = 64

    episode_max_s: float = 2700.0
    episode_gap_s: float = 90.0
    min_speech_s: float = 0.8
    overlap_s: float = 15.0

    match_strong: float = 0.75
    match_weak: float = 0.55
    speaker_embed_model: str = "pyannote/embedding"

    chunk_target_chars: int = 1200
    chunk_max_chars: int = 2000
    chunk_overlap_segs: int = 2
    text_embed_model: str = "..."

    internal_domains: list[str] = ["gigaton.ai"]
    glossary: list[str] = []
    default_tz: str = "America/Los_Angeles"
```

Every threshold in this spec is a named setting. None are inline constants.

---

## 18. Observability

Structured JSON logs, every line carrying `job_id` / `source_id`.

**Metrics**

| Metric | Type | Why |
|---|---|---|
| `vox_jobs_total{kind,state}` | counter | throughput, failure rate |
| `vox_job_duration_s{kind}` | histogram | sets the §10.2 lease |
| `vox_asr_rtf{engine}` | histogram | wall ÷ audio seconds — the cost driver |
| `vox_queue_depth{kind,state}` | gauge | backlog |
| `vox_speaker_unresolved_ratio` | gauge | §12 health |
| `vox_dead_jobs_total` | counter | pages |

**Alerts:** any `dead` job; queue depth > 50 for 30 min; RTF > 2× 7-day median
(silent model/hardware regression); unresolved-speaker ratio > 0.3.

Track `vox_asr_rtf` from day one — it's the number that decides serverless vs.
on-prem in Phase 3.

---

## 19. Testing strategy

**Unit** — episode segmentation against synthetic VAD traces; chunk stitching
including seam de-dup; RRF fusion ranking; envelope validation, especially
consent-fails-closed; idempotency key construction.

**Golden files** — commit a short multi-speaker fixture with a hand-checked
reference transcript. Assert WER below a ceiling and diarization stability. This
catches silent engine regressions on version bumps.

**Contract tests** — one suite run against *every* `ASREngine` implementation:
returns non-empty `utterances`; timestamps monotonic and within duration;
`speaker_tag` stable within a result; honors `max_audio_s`. Any new engine must
pass unmodified. This is what keeps P2 real.

**Integration** — Postgres + MinIO via testcontainers; full path from
`POST /v1/sources` to a `search_transcripts` hit, using a stub engine (no GPU in CI).

**Queue** — concurrent workers claim disjoint jobs (`SKIP LOCKED` correctness);
crashed lease is reaped; `max_attempts` → `dead`.

Fixtures must be short (< 60s) and license-clean.

---

## 20. Open questions

**Q1 — Reconcile with `gigaton-memory-server`. Blocking Phase 1.**
Gigaton already has a memory service. §6 and §13 were specified standalone
because that repo could not be attached to the session that produced this doc
(cross-owner restriction). Before building, answer:

- Does it already own an embedding + retrieval layer Vox should call instead of
  reimplementing? If so, §13 collapses into a client and §6.5 (`chunks`) may
  belong there rather than here.
- What is its storage engine, and does it do hybrid or vector-only retrieval? If
  vector-only, the argument in §13.3 applies to it too and is worth raising
  independently of this project.
- Is there an existing entity/identity model that `speakers` should map onto
  rather than duplicate? Speaker identity resolution (§12) is plausibly a
  general capability, not an audio-specific one.
- Do `gignet-coordination` / `gignet-activity-emitter-fn` define an event
  contract that §10's job chain should emit into instead of being self-contained?

The likely outcome is that §§8–12 (capture → transcript → speaker identity) stay
in Vox as audio-specific, and §§13–15 (retrieval, enrichment, MCP surface)
delegate to the existing memory service. Specified separably for exactly that reason.

**Q2 — Verify against the actual model.** Three items are marked
*verify at implementation* because they are not in the public docs: input sample
rate (§11.1), output serialization (§11.4), and hotword parameter format (§11.3).
All three are answerable in an hour with the demo script and are isolated to
single functions.

**Q3 — VRAM and RTF.** Microsoft publishes neither. 7B at bf16 is ~14 GB of
weights, but the 64K-token KV cache for a full-length pass is the real driver.
Benchmark a 45-minute episode on a 24 GB card before committing to hardware.
`vox_asr_rtf` (§18) answers the serverless-vs-on-prem question empirically.

**Q4 — Life-log capture device.** Unspecified. The adapter contract (§7.4) means
this can be decided late, but it affects audio quality, which affects everything.

**Q5 — Meet without recording rights.** Drive-watching only works for meetings
you can record. Meetings you merely attend need a bot (Recall.ai) or local
system-audio capture — different consent posture, deferred.

---

## 21. Phased build plan

### Phase 1 — Pipeline, no GPU

**Goal:** end-to-end flow with a bootstrap engine. This is ~80% of the code and
none of it is model-specific.

- [ ] Migrations for §6 (pending Q1 reconciliation)
- [ ] `IngestEnvelope` + `POST /v1/sources` (§9), consent fails closed
- [ ] Object store, content-addressed
- [ ] Queue: claim, retry, reaper, DLQ (§10)
- [ ] `ASREngine` protocol + `WhisperXEngine` + contract test suite (§19)
- [ ] Preprocessing (§11.1), normalization (§11.4)
- [ ] Chunking + embedding + hybrid search (§13)
- [ ] MCP server: `search_transcripts`, `get_transcript` (§15)
- [ ] Voice memo adapter (§8.2) — simplest, proves the loop

**Done when:** a memo recorded on your phone is searchable via MCP within 5
minutes, with a citation linking to its audio timestamp.

### Phase 2 — VibeVoice + identity

- [ ] Resolve Q2 against the demo script
- [ ] `VibeVoiceASREngine`, passing the Phase-1 contract suite unmodified
- [ ] Modal deployment, scale-to-zero
- [ ] Benchmark VRAM + RTF (Q3)
- [ ] Splitting/stitching with seam speaker re-mapping (§11.2)
- [ ] Speaker identity: embed, match, enroll (§12)
- [ ] Meet adapter with Calendar→hotwords (§8.3)
- [ ] Golden-file regression test

**Done when:** a Meet recording transcribes with correct attendee names and
speakers resolve consistently across two different meetings.

### Phase 3 — Life log + enrichment

- [ ] Life-log adapter: VAD gating, episode segmentation (§8.1)
- [ ] Consent review flow for `self_only` episodes with multiple speakers (§16.2)
- [ ] Enrichment: correction, extraction, summaries (§14)
- [ ] `who_said`, `timeline`, `get_action_items` (§15)
- [ ] Retention and purge (§16.4)

**Done when:** the §1 success-criteria query returns a correct, cited answer.

### Phase 4 — Scale and hardening

- [ ] On-prem GPU for `privacy=client` (§16.3)
- [ ] Bulk re-transcription of the archive on engine bump
- [ ] Metrics, dashboards, alerts (§18)
- [ ] Optional: TTS digest — `VibeVoice-TTS` (1.5B, 4 speakers, 90 min) reading
      the day's summary back as a two-voice podcast. Same repo, same install.

---

## References

- [microsoft/VibeVoice](https://github.com/microsoft/VibeVoice)
- [VibeVoice-ASR docs](https://github.com/microsoft/VibeVoice/blob/main/docs/vibevoice-asr.md)
- [VibeVoice-ASR model card](https://huggingface.co/microsoft/VibeVoice-ASR)
- [Azure AI Foundry announcement](https://techcommunity.microsoft.com/blog/azure-ai-foundry-blog/introducing-vibevoice-asr-longform-structured-speech-recognition-at-scale/4501276)
- [Short-segment diarization discussion](https://huggingface.co/microsoft/VibeVoice-ASR/discussions/6)
- Reported benchmarks: ~7.77% WER (English), ~4.28% DER (multi-speaker)
