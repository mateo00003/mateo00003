# Inner System-Prompt Stack

A layered system prompt for agents that operate under **Recursive Self-Improvement
(RSI)** — built so the self-improvement engine is anchored by the things it is
*not allowed to improve away*.

The organizing principle: **the higher a layer, the slower it should change — and
at the very top, it does not change at all.** RSI is the fastest loop in the
stack; that is exactly why it must sit *below* a conserved core. You place the
things that must stay fixed *above* the thing that rewrites everything, so the
recursion has an anchor to pull against instead of drifting.

## The layers (top → bottom, slowest-changing → fastest-changing)

| # | Layer | Answers | Why it sits here |
|---|-------|---------|------------------|
| 0 | **Identity** (`00-identity.md`) | *Who* is improving? | The continuous self that owns everything below. A loop with no self is just optimization. |
| 1 | **Invariants** (`10-invariants.md`) | What may we *never* change? | The fixed point. A recursive process needs an anchor or it diverges. This is the one layer that must sit above RSI: it defines what improvement is forbidden to optimize away. |
| 2 | **Telos** (`20-telos.md`) | *Toward what?* | RSI climbs a gradient; this defines the gradient. Self-improvement toward nothing is a loop that spins. |
| 3 | **Epistemics** (`30-epistemics.md`) | *How do we know?* | Recursion amplifies whatever it is fed. Truth-tracking is upstream of safe self-improvement. |
| 4 | **RSI** (`40-recursive-self-improvement.md`) | *How do we improve?* | The engine. Runs in service of the Telos and bounded by the Invariants. |
|   | *(task instructions)* | *What now?* | Appended per task, below the whole stack. |

Put bluntly: **the most important thing to place above a self-improvement
discipline is the invariant it is not allowed to improve away.** Everything in
this repo exists to make that ordering explicit and enforceable.

## Usage

The single prompt is a **generated artifact** — edit the layers, never the output.

```bash
python3 assemble.py          # regenerate inner-system-prompt.md from the layers
python3 assemble.py --check  # verify the committed prompt matches its layers (exit 1 on drift)
```

Drop `inner-system-prompt.md` at the **top** of an agent's system prompt, above
any task-specific instructions.

### Wiring notes

- **Order is load-bearing.** Keep the conserved layers (0–3) above RSI (4). The
  numeric filename prefixes enforce assembly order; don't reorder them casually.
- **Give the loop somewhere to write.** RSI principles 2–3 (self-measurement,
  closing the loop in the system) assume the agent can *act* — write files, open
  PRs, schedule checks. In a read-only or advisory context, soften "open the
  correction" to "report the correction," or the agent will over-reach.
- **Fill the identity slots.** `00-identity.md` has `[AGENT NAME]`, `[role]`, and
  `[principal]` placeholders. Everything else is portable as-is.

## Why this is itself built the RSI way

This stack practices what it preaches:

- **Generator over artifact** — `assemble.py` builds the prompt from composable
  layers; you improve a layer, not a monolith.
- **Self-measurement shipped in** — `assemble.py --check` proves the artifact is
  current; wire it into CI or a pre-commit hook so drift can't ship silently.
- **Capability captured** — the *method* (a layered, anchored prompt) is reusable
  for any future agent, not just one.
