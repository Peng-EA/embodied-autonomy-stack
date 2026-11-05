# Embodied Autonomy Stack

**Draw a boundary around the LLM; give embodied agents what the LLM cannot do —
homeostasis, reflex, affect modulation, need generation, and execution repair.**

![status: v0.1 design RFC](https://img.shields.io/badge/status-v0.1%20design%20RFC-orange)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

> Chinese is the canonical version: **[README.md](README.md)**.
> **Status**: v0.1 design RFC. Contracts are drafts; a **10-second MuJoCo loop already runs**
> (`sim/`, no GPU, no API key). This is an architecture sandbox, not a released framework.

## Claim

The LLM is an important component of embodied AI, but it should not be the center of
real-time control or homeostatic regulation. This project draws the boundary — the LLM does
semantic planning and dialogue — and adds what lies outside its competence:
homeostasis, reflex, affect modulation, need generation, execution repair.

> **Core proposition**: only after a robot can solve *its own* needs can it autonomously
> solve needs that no program spelled out.

## Four layers

| Layer | Timescale | Responsibility | LLM |
|---|---|---|---|
| **L1** interoception / homeostasis | 10–100 ms | setpoint deviation detection, global broadcast, highest preemption | no |
| **L2** reflex | 10–100 ms | obstacle/balance/force/contact, hard veto | no |
| **L3** affect / motivation | 0.1–1 s | continuous affect vector modulating parameters, goal candidates | no |
| **L4** planning coordination | 1–10 s+ | goal ordering, task decomposition, calls the LLM, dispatches | yes (calls an external service) |

L4 is **not** the LLM. The LLM is an external, asynchronous, replaceable, disconnectable
service called by L4.

## Repository map

| Path | What | Start with |
|---|---|---|
| [`docs/`](docs/) | Body: vision, architecture, contracts, evaluation, safety, roadmap, ADRs, blog | [docs/README.md](docs/README.md) |
| [`contracts/`](contracts/) | JSON Schemas + examples for inter-layer signals | [contracts/README.md](contracts/README.md) |
| [`evals/`](evals/) | Four baselines, metric definitions, log format | [evals/README.md](evals/README.md) |
| [`sim/`](sim/) | MuJoCo stage-1 minimal loop (L1–L4, preemption, degradation) | [sim/README.md](sim/README.md) |
| [`site/`](site/) | Documentation site (VitePress, dark, monospace) | [site/README.md](site/README.md) |

## Run it

```bash
python -m venv sim/.venv
sim/.venv/Scripts/python -m pip install -r sim/requirements.txt   # Linux/macOS: sim/.venv/bin/python
sim/.venv/Scripts/python -m sim.main          # 10 s loop, live ASCII panel of L1–L4
sim/.venv/Scripts/python -m sim.main --trace  # one line every 0.25 s
```

The LLM is mocked on purpose: this verifies the **boundary** (asynchronous, disconnectable,
preemptible), not model quality.

## Non-goals

- Not a complete humanoid platform
- Not an LLM agent wrapper
- Not a promise of AGI
- Not "emotion as chat labels"
- Not "YOLO as a control dependency"
- Not autonomy without a physical closed loop
- Not a socially performative robot: affect is internal-state modulation, not pleasing animation
- Not a replacement for the LLM — this project gives it a boundary and a job
- Not a turnkey API

## Participate

**Issue/RFC first → PR second → attach evidence.** Contract changes require an ADR.
See [CONTRIBUTING.md](CONTRIBUTING.md), [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md),
[SECURITY.md](SECURITY.md) (including physical-safety reporting).

## License

[Apache-2.0](LICENSE).
