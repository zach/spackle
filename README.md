# spackle

Spackle: Supervised Span Refinement with Diffusion Language Models.

The research plan is in [`plan.md`](plan.md). This repository currently holds
Phase 1 of that plan, the Wireworld testbed: an exact simulator and scorer, a
seeded task generator for the three tiers, and best-known circuit sizes from
our own search. The design is in [`docs/testbed.md`](docs/testbed.md) and the
Gate 1 result in [`docs/gate1_report.md`](docs/gate1_report.md).

## Quick start

Python 3.10 or newer; no dependencies (pytest for the tests).

```bash
python -m spackle validate                       # Gate 1 checks, writes docs/gate1_report.md
python -m spackle generate --seed 0 --out tasks.jsonl
python -m spackle show tasks.jsonl T3-XOR-a-b17  # the task statement a model sees
python -m spackle score tasks.jsonl T3-XOR-a-b17 answer.txt
python -m pytest
```

To extend the best-known designs (needs a C compiler for speed):

```bash
python -m spackle search --geoms XOR/a --seconds 60
```
