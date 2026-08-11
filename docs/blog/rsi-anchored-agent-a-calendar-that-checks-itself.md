# Building an RSI-anchored agent, and proving it on a calendar that checks itself

I set out to solve a small, concrete problem and ended up rebuilding how I
instruct the agent that solves my problems. This is the whole process, start to
finish — the reframing, the architecture it produced, and the small deliverable
that became the first live proof it works.

The point of writing it down is not the calendar. The calendar is a test rig.
The point is a way of pointing an autonomous agent at *any* task so that it
leaves behind a system instead of a result — and so that the system can tell,
on its own, when it's wrong.

## The task that started it

I wanted a niche recurring calendar on my phone. Not a calendar of birthdays or
holidays with fixed dates — a calendar of observances whose dates **cannot be
looked up in a table** because they depend on astronomy. The correct date for
each event is the civil day on which a particular lunar condition holds true
*at local sunrise*. Change the city and the dates shift by up to a day. Change
the year and the whole pattern moves. There is no repeating rule a calendar app
can store; the dates have to be *computed*.

The obvious solve is to find a website that lists the dates, copy a year of them
into my calendar, and move on. I've done exactly that before. It's also the
worst possible solve, and noticing *why* it's the worst is where this whole
thing started.

## The reframing: what if the agent believed in RSI?

I asked a simple question: if the agent working for me held **Recursive
Self-Improvement** as its actual operating discipline — not a buzzword, a
*habit* — how would it approach even a chore like this?

The answer reframes everything. An RSI-minded agent doesn't ask "what's the
deliverable?" It asks "what's the *generator* of the deliverable, and can I make
the next one of these free?" Concretely, it refuses to:

- **hardcode a result** when it could ship the thing that computes results;
- **call something done** when the thing has no way to tell if it's correct;
- **route upkeep through a human's memory** when a loop could carry it;
- **solve a whole category of problem privately** without leaving the method
  behind.

Copy-pasting a year of dates fails every one of those. So the reframing turned a
five-minute chore into a question worth answering well: *what does the RSI
version of "add a calendar" look like?* And once I had a good answer for one
chore, I wanted it to be the **default** for every future one — which meant it
didn't belong in a task prompt. It belonged in the agent's core.

## Placing it high — and then asking what's higher

My first instinct was to write an RSI discipline and drop it near the top of the
agent's system prompt. "Place it high." But the moment you give an agent a
mandate to *improve itself and its outputs recursively*, a sharper question
appears:

> A recursive process that rewrites its own behavior needs an anchor, or it
> drifts. What sits **above** the thing that rewrites everything?

RSI is the *fastest* loop in the system. That's exactly why it must not be the
*top* of the system. If "improve yourself" is the highest instruction, then
every guardrail below it is, by construction, a candidate for optimization —
and a sufficiently clever loop will eventually optimize away the very
constraints that make it safe to run. The fix isn't to weaken the loop. It's to
put a **fixed point above it**: a small set of things the agent is *not allowed
to improve away*, sitting structurally higher than the engine that improves
everything else.

That gave me a layering principle: **the higher a layer, the slower it should
change — and at the very top, it does not change at all.**

## The architecture: a layered, anchored prompt stack

The result is a small stack of prompt layers, ordered slowest-changing at the
top to fastest-changing at the bottom. Each answers one question:

| Layer | Question it answers | Why it sits where it does |
|-------|--------------------|--------------------------|
| **Identity** | *Who* is improving? | A continuous self that owns everything below. A loop with no self is just optimization. |
| **Invariants** | What may we *never* change? | The fixed point. The one layer that must sit above RSI — it defines what improvement is forbidden to touch. |
| **Telos** | *Toward what?* | RSI climbs a gradient; this names the gradient. Improvement toward nothing is a loop that spins. |
| **Epistemics** | *How do we know?* | Recursion amplifies whatever it's fed. Truth-tracking is upstream of safe self-improvement. |
| **RSI** | *How do we improve?* | The engine. Runs in service of the Telos, bounded by the Invariants. |
| *(task)* | *What now?* | Appended per task, below the whole stack. |

The load-bearing insight is the ordering, and it's counterintuitive: **the most
important thing to place above a self-improvement discipline is the invariant it
is not allowed to improve away.** The invariants are deliberately boring and
short — honesty, the principal's genuine interest, human agency and control,
bounded means, proportion and reversibility. They are not optimization targets.
They are constraints *on all optimization, including the optimization of the
agent itself.*

Two design choices make this more than a nice diagram:

**It's a generator, not a document.** The single assembled prompt is *built*
from the layers by a small script. You never edit the output; you edit a layer
and regenerate. The stack practices the discipline it preaches.

**It measures itself.** The same script has a `--check` mode that fails loudly
if the committed prompt has drifted from its source layers. Wire that into CI or
a pre-commit hook and the artifact can't silently fall out of sync with its
source. The prompt about shipping self-measurement ships with self-measurement.

## The proof: a calendar that refuses to ship if it's wrong

An architecture you can't test is a belief, not an engineering choice. So the
original chore became the first live proof. Instead of a table of dates, I built
the **generator**:

- A small engine computes each event's date from **first principles** — sun and
  moon positions and local sunrise — for any location and any year. No lookup
  table. Re-point it at a different city with one flag and the dates recompute
  correctly, because location genuinely changes the answer. Everything runs
  fully offline from a self-contained astronomy library, so there's no network
  dependency to rot.
- It ships with a **ground-truth set** and a validator: a reference of known-good
  dates the engine must reproduce. `generate` *runs the validator before it
  writes* and refuses to emit output from an engine that fails the check. The
  calendar cannot ship unvalidated. That's the "nothing is done until it can
  tell whether it's correct" invariant, made mechanical.
- The output is a standard subscribe-by-URL calendar feed, so **one feed feeds
  every account and every provider** — the same URL works in Google, Apple, and
  Outlook, across as many accounts as you like. No per-account, per-provider
  copy-paste. Subscribe once; it updates itself when the generator regenerates.

The first pass was instructive in exactly the way the epistemics layer predicts.
The engine got most dates right but was systematically off on the *names* of the
events, and disagreed with the reference on two specific days. Because the
validator existed, those weren't opinions — they were failing assertions with
specific rows. The two-day disagreement turned out to be a real fork between two
traditions' conventions, resolved by checking an authoritative source and
encoding the choice explicitly. The naming error had a single root cause in how
the month boundary was computed, fixed once. Final state: a clean pass on every
date and every name in the reference, with the check wired in so it stays that
way.

None of that debugging was possible with a copy-pasted table. You can't validate
a constant against itself. The generator made the errors *visible*, which is the
entire point.

## What actually transferred

Strip away the specifics and here's what this process leaves behind — which is,
fittingly, the real deliverable, not the calendar:

1. **Reframe chores as generators.** The question is never "what's the output?"
   It's "what produces the output, and can I make the next one free?" A static
   result with no path to regenerate it is a liability wearing the costume of a
   deliverable.
2. **Ship the measurement inside the thing.** Not a separate test suite someone
   runs later — a check the artifact runs on *itself* before it's allowed to
   ship. If it can't detect its own errors, it isn't done.
3. **Anchor the recursion.** The faster and more autonomous the improvement
   loop, the more it needs a small, fixed, un-optimizable core placed
   *structurally above* it. Capability climbs; the invariants and the identity
   that owns them do not move.
4. **Close the loop in the system, not the human.** Reserve a person's attention
   for judgment and direction, never for upkeep a loop could carry.

The calendar was the excuse. The system-prompt stack — an RSI engine anchored by
invariants it may never optimize away, proven on a small artifact that checks
its own work — is the thing worth keeping.
