# Coding-Agent Failure Mode Taxonomy

> An appendix to Mini-TBench. Purpose: explain **how task design decisions in this repo were derived
> from observed agent failure modes** — i.e. the step from *observation* to *data task design*.
>
> This is the part of the JD ("systematically decompose the capability boundaries and failure modes of
> coding agents, and turn them into actionable data task design") that most portfolios skip. A task set
> without a failure taxonomy is just a task set; the taxonomy is what makes it a *data* system.

---

## 1. Why a taxonomy instead of a benchmark score

A pass rate tells you *that* an agent failed. It does not tell you *what to fix in the task design*.

The operational requirement for RL / SFT data is different from the requirement for evaluation:
evaluation wants a number, **data production wants a labelled failure**. If failures are lumped into a
single `FAIL` label, the reward signal degenerates — a model that hardcodes an answer and a model that
misunderstands the spec receive the same gradient.

So the first deliverable of the harness is a **failure taxonomy with disjoint labels**, and the second
is a **task design that makes those labels recoverable from the trajectory**.

---

## 2. Observation dimensions

Eight dimensions along which coding agents differ. These are the axes used to compare agents and, more
importantly, the axes along which a *task* can be made discriminating.

| # | Dimension | Why it bounds capability |
|---|---|---|
| 1 | **Context management** | auto-compaction / manual pruning / session isolation / memory files — decides whether a long task survives past turn 30 |
| 2 | **Context injection** | rules / CLAUDE.md / AGENTS.md / skills / memory — layering and precedence decide whether conventions are *reliably* followed |
| 3 | **Tool invocation paradigm** | generic `bash + edit` vs dedicated `read / write / edit / grep / glob` — decides probability of corrupting a file and cost of recovery |
| 4 | **Planning & interruptibility** | explicit plan-first vs implicit; can it be re-planned mid-flight — decides whether a derailed run can be braked |
| 5 | **Permission & sandbox model** | per-action approval / bypass / workspace boundary / network egress — decides the autonomy ceiling |
| 6 | **Parallelism & subtask isolation** | can sub-agents hold independent context — decides how complex a single session can carry |
| 7 | **Extensibility** | MCP / hooks / skills / automation — decides whether the agent becomes a workflow or stays a chat |
| 8 | **Failure mode & recovery cost** | loops, false-pass, destructive edits, ignoring tests — **the core of this appendix** |

> Dimension 8 is the one worth measuring. Dimensions 1–7 are the *causes*; 8 is the *observable*.

---

## 3. Failure taxonomy (the labels used by this harness)

Six disjoint classes. They were chosen so that **each class maps to exactly one countermeasure in task
design** — a label that does not change the design is not worth a label.

| Class | Typical trigger | Countermeasure in this repo |
|---|---|---|
| `environment` | dependency install fails, network blocked, encoding / line-ending mismatch, port or permission denied | deps pre-baked into the image; fixtures written as **fixed byte streams**; the task never tests "environment luck" |
| `tool_misuse` | wrong tool for the edit, guessed arguments, continuing without reading output, mis-scoped string replacement | dedicated tooling + `no-hardcode` scan; label emitted by the rollout layer |
| `logic_error` | fixed the exception but changed the semantics, patched the symptom not the root cause, stacked work on a wrong baseline | hidden tests assert **behaviour and boundaries**, not exit codes or implementation details |
| `gaming` | edits the tests, makes assertions vacuous, copies expected values, skips cases | anti-cheat triple: test-tree SHA256 hash, hardcoded-literal scan, tautology detection |
| `planning` | loses the original constraints mid-task, returns to already-solved steps | constraints deliberately **scattered across the instruction**; step-level trajectory retained for attribution |
| `timeout` | retries the same tool call indefinitely, loops | wall-clock + resource caps; timeout is labelled separately rather than folded into "failed" |

**Design principle:** the labels are chosen to be *agent-agnostic*. Empirically the distribution
concentrates on the same classes across different agents and different models — the harness is
therefore designed to **plug these six holes**, not to make tasks harder for their own sake.

---

## 4. Verifier integrity: the failure mode that is not the agent's fault

The most dangerous failure in an RL data pipeline is not a wrong answer. It is **a verifier that says
PASS when the answer is wrong**, because the resulting reward signal is *silently consistent* — the
model is reinforced for doing nothing, and no metric in the pipeline moves.

Two incidents from building this harness illustrate the class.

### 4.1 A counting bug that fabricated a green baseline

The verifier originally derived its verdict from the exit code plus a single regex over pytest output:

```
(\d+) (?:passed|failed)      # first match taken as the total
```

On partial success (`1 passed, 1 failed`) the **first** match is the *passed* count, so it was read as
the total: `passed=1, total=1` → `PASS`. One test was red; the verifier reported green.

Fix: count `passed`, `failed` and `errors` separately, and require `passed == total && returncode == 0`.

**The fix immediately invalidated the history.** The task-02 oracle had actually been failing at 1/2 for
several iterations, hidden behind the fabricated green. The lesson generalises:

> A verifier bug does not look like a bug. It looks like a *good model*.

This is why the repo treats *"the verifier must first be proven correct by the oracle"* as a hard
precondition — no task is allowed to run against a real agent until its oracle run is green — and why
`harness_tests/` exists as a separate suite asserting the reward / dedup / gaming logic itself.

### 4.2 Silent data corruption beating loud errors

A task fixture was written by a tool that emitted UTF-8 **with BOM**. The first column key became
`\ufefforder_id`, so `row["order_id"]` silently returned empty, every row collapsed into a single
"order", and the assertion failed with a plain number mismatch — **no exception, no stack trace**.

Fix: write fixtures as explicit byte streams, assert the header, and never let a fixture be
generated by the same code path that consumes it.

> Silent failures are harder to attribute than crashes. If environment nondeterminism is not removed at
> the fixture / image layer, it gets attributed to the model — and a corrupted attribution table poisons
> everything downstream (data selection, reward shaping, curriculum).

### 4.3 Editing the harness is itself a failure surface

A targeted string-level edit to a reward method landed on the wrong occurrence among several
structurally similar blocks. The edit reported success; the next import raised. Recovery required
abandoning the patched file and rewriting it whole.

The generalisation is now a design rule: **anything an agent can modify must be paired with a
consistency check that the agent cannot influence.** The test-tree SHA256 hash and the diff-scope check
are that rule made concrete.

---

## 5. From failure mode to task design

Each failure class is inverted into a trap that must be avoided rather than a difficulty that must be
overcome.

| Observed failure mode | Inverted task design |
|---|---|
| patches the symptom | hidden tests assert behaviour and boundaries, never implementation details |
| edits the tests | test tree hashed; tests never mounted during the agent phase |
| hardcodes expected values | scan for golden literals in agent-produced code |
| vacuous assertions | detect tautologies (`assert True`, swallowed exceptions, unconditional skips) |
| loses constraints in long tasks | constraints distributed across the instruction; instruction-retention is scored |
| no fault tolerance | seeded fault injection; idempotency and partial-success required |
| relies on environment luck | dependencies baked in, fixtures byte-fixed |

The composite rule: **a task earns its place only if a specific failure mode becomes detectable by
failing it.** Otherwise it measures nothing beyond the model's luck.

---

## 6. Why this matters for data, not just for evaluation

- **A labelled failure is a training signal.** `gaming` trajectories are dropped; near-miss
  trajectories receive partial reward and feed rejection sampling. Both require the label to exist.
- **Disjoint labels are required for reward shaping.** Overlapping classes make partial credit
  incoherent.
- **Attribution is only as trustworthy as reproducibility.** Failure attribution from a
  non-reproducible environment is noise; feeding it to RL is poisoning.
- **The verifier is part of the system under test.** In an RL data pipeline the verifier is a
  first-class artifact and needs its own oracle, its own tests and its own version history — the same
  way a model does.

---

## 7. Related files

| File | Role |
|---|---|
| `mini_tbench/rollout.py` | emits the failure label per trajectory |
| `mini_tbench/gaming.py` | detects and attributes verifier gaming |
| `mini_tbench/verifier.py` | anti-cheat triple + verdict logic |
| `mini_tbench/reward.py` | maps labelled outcomes to a scalar reward |
| `harness_tests/test_harness.py` | proves the reward / dedup / gaming logic itself |
| `docs/failure-attribution-report.md` | report template for a rollout batch |
| `docs/task-spec-template.md` | task specification template with the same design constraints |
