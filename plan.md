# Spackle: Supervised Span Refinement with Diffusion Language Models

Research & Experiment Plan · Nov 11, 2026 · @Zach

## Summary

Spackle tests whether a frontier autoregressive model, acting as supervisor, gets better results by repeatedly cutting out weak spans and having a hosted diffusion model refill them, compared with either model working alone at the same budget. The name describes the loop: find the cracks, fill them, sand, repeat.

The supervisor reasons step by step, critiques the current draft, and picks spans to rewrite. The diffusion worker fills each span using the text on both sides, which autoregressive models handle poorly. Every run gets a fixed effort budget, so Spackle behaves as an anytime algorithm: it always holds a usable answer and improves it until the budget runs out.

The first testbed is Wireworld circuit design. A simulator gives exact correctness scores, while gate count and robustness to input timing leave room for taste. This document covers the hypotheses, related work, the benchmark, the harness, the experiment protocol, and the plan with costs and risks.

## Research questions and hypotheses

The central question: at equal cost, does Spackle's supervised span refinement beat a single model, and where does the gain come from?

| ID | Hypothesis | Tested by |
| --- | --- | --- |
| H1 | The supervisor plus diffusion worker solves more tasks than the supervisor alone at the same dollar budget. | Pass rate, condition C vs A |
| H2 | It also beats the diffusion model alone given the same number of refinement rounds with a fixed critic. | Pass rate, C vs B |
| H3 | Quality rises with budget and degrades gracefully when cut early (anytime behaviour). | Score-vs-budget curves |
| H4 | The supervisor's choice of spans matters: targeted remasking beats random spans of the same size. | C vs D |
| H5 | Letting the supervisor decide when and where to call the worker (MCP agent loop) beats a fixed loop on hard tasks, at higher variance. | C vs E |
| H6 | Adding a robustness probe to the critic raises robustness scores without lowering pass rate. | C vs C+probe |

A negative result on H1 is still useful: it would bound how much a hosted, span-level interface gives up compared with token-level remasking.

## Related work

No published system matches Spackle's setup: a general autoregressive model, over an API, driving a diffusion model as a span-refinement tool with critique and a budget. The nearest work sits at the token level (R3) and the agent level (DiffuAgent).

| Coupling | Work | What it does | Relevance |
| --- | --- | --- | --- |
| Judge-guided refinement | [R3: Review, Remask, Refine](https://arxiv.org/pdf/2507.08018) | A process reward model finds weak blocks; only those are remasked and regenerated, no retraining. | Closest to this design, at weight level |
| Judge-guided refinement | [FastGuide](https://arxiv.org/abs/2609.36202) | Reward-model gradients steer diffusion logits during decoding. | Needs weights; useful comparison |
| Substrate | [ReMDM](https://arxiv.org/pdf/2503.00307) | Lets already-committed tokens be remasked; quality scales with steps. | Basis for token-level variants |
| Autoregressive gatekeeper | [FlashDLM Guided Diffusion](https://arxiv.org/pdf/2505.21467) | Diffusion drafts everything; tokens kept only where an autoregressive model agrees. | Speed-oriented supervisor |
| Speculative decoding | [DiffuSpec](https://arxiv.org/abs/2510.02358v1), [DEER](https://arxiv.org/abs/2512.15176v1), [SpecDiff-2](https://arxiv.org/abs/2511.00606), [FailFast](https://arxiv.org/abs/2512.20573v3) | Diffusion drafts, autoregressive verifies; lossless speedups up to about 5x. | Authority without creativity |
| Agent level | [DiffuAgent / Bitter Lesson](https://arxiv.org/abs/2601.12979v2) | Diffusion models fail as agent backbones but work in non-causal roles. | Supports the supervisor-worker split |
| Agent level | [MercuryCode](https://south0120.github.io/MercuryCode/) | Mercury 2 as the engine of a coding CLI. | Reverse arrangement |
| Mirror image | [PLANNER](https://machinelearning.apple.com/research/planner), [DGLM](https://arxiv.org/html/2408.04220), [STAR-LDM](https://arxiv.org/html/2602.20528), [ReFusion](https://arxiv.org/pdf/2512.13586) | Diffusion plans in latent space; autoregressive writes. | Contrast only |

Tooling: the [dLLM library](https://github.com/Shitsuten/dllm) wraps LLaDA and Dream behind unified generators, the easiest base for a token-level variant. Paper trackers: [Awesome-DLLMs](https://github.com/JetAstra/Awesome-DLLMs) and [bansky-cl's arXiv list](https://github.com/bansky-cl/diffusion-nlp-paper-arxiv).

Name note: two unrelated works share the name. [Spackle](https://arxiv.org/pdf/2609.30941) is a Gaussian-splatting method that patches poorly reconstructed regions in novel view synthesis, and [SpaCKLE](https://arxiv.org/html/2505.02980v2) is a gene-expression completion model. Neither touches language models; cite the first if reviewers ask.

## Benchmark spec: Wireworld

The suite has three tiers of procedurally generated tasks, scored exactly by a simulator, with a robustness score so that fragile but small circuits don't win.

**Representation.** ASCII grid: space = empty, `.` = wire, `H` = electron head, `t` = tail. Inputs enter on fixed left-edge rows; outputs leave on fixed right-edge rows. A coordinate-list encoding is kept as an ablation, to separate circuit reasoning from column counting.

| Tier | Task | Generation | Primary score |
| --- | --- | --- | --- |
| T1 Simulate | Predict the grid after N steps | Random circuits, N from 1 to 20 | Exact-match cells |
| T2 Repair | Fix a broken circuit with minimal edits | Known-good circuit with 1 to 3 cells flipped | Pass/fail, edit count |
| T3 Design | Build a gate or small circuit meeting a truth table within a cell budget | Spec library: NOT, AND, OR, XOR, half adder, 2-to-1 mux | Pass/fail, cell count |

**Correctness.** For each input combination, inject electrons, run 60 steps, count output pulses. A pass needs the exact pulse count for every combination and a quiet grid at the end, so no self-sustaining loops.

**Robustness (T2, T3).** Re-run the both-inputs-active cases with one input delayed 1 to 3 ticks. Score = share of perturbed runs that end quiet with at most one output pulse. In the pilot, a symmetric 22-cell XOR passed the truth table but oscillated with 17 pulses at a 2-tick delay, while a 21-cell design stayed clean.

**Composite T3 score.**

```latex
S = \mathrm{pass} \cdot \left(0.6 + 0.2\,\frac{c_{\min}}{c} + 0.2\,r\right)
```

where c is the cell count, c_min is the best-known cell count for that spec, and r is the robustness share.

**Freshness.** Tiers 1 and 2 draw new instances per run from a seeded generator, so results can't come from memorised answers. Tier 3 specs are fixed, but best-known sizes come from our own exhaustive or random search, not from published designs.

## Harness design

A small Python controller owns the budget and the loop; the supervisor and the diffusion worker only propose, and the simulator decides.

[Diagram: The controller owns the budget; the models only propose. Supervisor (critiques, picks spans; frontier model via OpenRouter) ⇄ Harness controller (budget and stop rules; keeps best-so-far answer; Python, owns the loop) ⇄ Diffusion worker (fills span from both sides; Mercury via Inception FIM; returns fill and cost). Controller ⇄ Wireworld simulator (truth table, timing probes; exact and deterministic; returns scores). Controller → Run log every round (every prompt, span, score; JSONL, versioned prompts; every run replayable).]

Each round, the supervisor sees the current draft and the simulator's trace, then names spans to rewrite; the worker fills them; the simulator scores the result.

**Model access.** The supervisor runs through OpenRouter's OpenAI-compatible API, so any frontier model can be swapped in by changing a model slug. The worker uses Inception's [FIM endpoint](https://docs.inceptionlabs.ai/capabilities/fim.md) directly, sending the text before and after the gap. Mercury chat models on OpenRouter are the fallback, prompted to return only the gap's contents. Before building, check Inception's API for its diffusion-specific extensions; any control over steps or confidence would add a token-level variant.

**The fill_span tool.** One interface serves the fixed loop and the MCP server:

```json
{"name": "fill_span",
 "input": {"prefix": "string", "suffix": "string",
           "instructions": "string, optional",
           "expected_shape": {"rows": "int", "cols": "int"}},
 "output": {"fill": "string", "cost_usd": "number", "latency_ms": "int"}}
```

For Wireworld, a span is a block of whole grid rows, so the prefix and suffix stay valid text. The controller pads or trims each returned row to the grid width and counts any repair as a format error.

**Loop (conditions A to D, F).**

1. The supervisor writes a first full draft.
2. The simulator scores it; the controller stores it as best-so-far.
3. The supervisor reads the draft and trace, then returns up to 3 spans with a one-line reason each.
4. The worker fills each span; the simulator scores the new grid.
5. Keep the new grid if its score is at least as high; otherwise revert.
6. Stop on a perfect score, an exhausted budget, or 3 rounds with no improvement.

**Agent loop (condition E).** The supervisor gets fill_span and run_simulator as MCP tools and decides its own order. The controller still enforces the dollar cap and keeps best-so-far.

**Logging.** One JSONL record per round: condition, seed, task id, prompts and their version hashes, spans chosen, fills, scores, tokens and dollars. Every run can be replayed from the log.

## Experiment protocol

Six conditions run on the same tasks at matched dollar budgets, with three seeds each; the main comparison is C (full Spackle) against A.

| Condition | Supervisor | Worker | Span choice | Loop |
| --- | --- | --- | --- | --- |
| A Supervisor alone | Frontier autoregressive | None (rewrites whole answer) | n/a | Fixed rounds |
| B Worker alone | Fixed critic prompt on a small model | Mercury via FIM | Lowest-scoring span by simulator trace | Fixed rounds |
| C Spackle, fixed loop | Frontier autoregressive | Mercury via FIM | Supervisor picks | Fixed rounds |
| D Spackle, random spans | Frontier autoregressive | Mercury via FIM | Random, same size as C | Fixed rounds |
| E Spackle, agent loop | Frontier autoregressive | Mercury as MCP tool | Supervisor decides when and where | Agentic, same budget cap |
| F Small-model Spackle | Small autoregressive | Mercury via FIM | Supervisor picks | Fixed rounds |

**Budgets.** Each task runs at four budget levels, roughly $0.01, $0.03, $0.10 and $0.30, so score-versus-cost curves can be drawn. The harness records the best-so-far answer after every round, giving anytime curves for free.

**Sample size.** About 120 tasks: 40 per tier, with T3 covering 6 specs at several cell budgets. Six conditions × four budgets × three seeds × 120 tasks = 8,640 runs. Run a 10-task pilot first and cut conditions if variance is low.

**Metrics.** Pass rate, composite score S, robustness r, dollars and wall-clock per solved task, rounds to first pass, and area under the score-versus-budget curve.

**Analysis.** Paired comparisons per task between conditions, with bootstrap 95% confidence intervals on differences in pass rate and S. Report results per tier, since T1 may favour pure reasoning and T3 the supervisor-worker pair. Pre-register H1 to H6 and these tests before running the full set.

**Fairness checks.** The same task text, simulator feedback format and stop rules apply in every condition. Supervisor model and temperature are fixed across conditions, and every prompt is versioned in the log.

## Milestones, costs and risks

The whole study should cost under about $1,000 in API fees, and nearly all of it is committed only after the pilot passes its gate.

[Diagram: Spend on the full run only after the pilot gate. 1. Testbed (simulator and scorer; task generator; best-known sizes) → Gate 1: generator validated → 2. Harness (OpenRouter client; Inception FIM client; fill_span and loop; JSONL logs) → Gate 2: every condition runs → 3. Pilot (10 tasks; all 6 conditions; cost and variance; API cap about $80) → Gate 3: plan pre-registered → 4. Full run (8,640 runs; bootstrap analysis; release benchmark; API cap about $950).]

Each gate is a hard stop: the next phase starts only when its criterion is met.

**Costs (estimates; check current prices on OpenRouter before the pilot).** The four budget caps average $0.11 per run, so 8,640 runs cost at most about $950, and less where runs stop early. The pilot's 720 runs cap at about $80. Supervisor tokens will dominate: Mercury 3 lists at $0.10 per million input and $0.35 per million output tokens on [OpenRouter](https://openrouter.ai/models/inception/mercury-3).

| Risk | Effect | Mitigation |
| --- | --- | --- |
| Worker breaks grid alignment | Fills fail for format, not reasoning | Whole-row spans, width repair, format errors logged separately |
| Text grids confound results | Models fail on column counting | Coordinate-list ablation on a task subset |
| Supervisor cost swamps the worker | Spackle looks expensive even if it helps | Report tokens and dollars per model separately |
| Hosted APIs change mid-study | Runs not comparable | Pin model slugs, record dates, finish the full run in one window |
| Tasks too easy or too hard | No separation between conditions | Pilot calibrates cell budgets and step counts |
| FIM lacks diffusion-level control | Only coarse span refinement possible | Treat as a known limit; open-weight token-level variant as follow-up |

Open question: which frontier model to use as supervisor. Pick one before Gate 2 and keep it fixed.
