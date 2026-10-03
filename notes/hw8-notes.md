# HW8 notes for teaching

Concrete terminology and worked reasoning from comparing agent
configurations by accuracy and cost. See [concepts.md](concepts.md) for
generic framing, [hw6-notes.md](hw6-notes.md) for where the evaluation
cases used here originally come from.

## Why HW8 reuses HW6's 10 eval cases instead of writing new ones

HW8 does not create a new evaluation suite. The handout is explicit:
*"Homework 8 uses the evaluation cases from Homework 6 and the judges you
accepted in Homework 5 ... Do not edit the judges or evaluation cases."*
`eval_cases/cases.jsonl` is that fixed suite from HW6 -- 10 cases, 4
regression / 6 capability.

Reasoning for reusing rather than writing fresh cases:

1. **Each case already has a hand-authored golden answer.** A case pairs a
   scripted `input` with a known seeded `initial_state` and an `expected`
   block (deterministic `checks` and/or LLM `judges`) that decides pass or
   fail. That golden-answer property is expensive to build correctly (see
   [hw6-notes.md](hw6-notes.md) on baseline classification needing 5 runs
   per new case) and is exactly what makes a case usable as a regression
   test at all -- HW8 doesn't ask for that investment twice.
2. **Continuity across homeworks.** The same 10 cases underpin HW6's
   CI-style regression gating, HW7's `mishandles_vague_requests` judge
   calibration, and now HW8's dev/test split and search budget. Reusing
   them means every later assignment reasons about the same ground truth
   instead of a moving target.
3. **The case-count minimum is about this same fixed file.** The handout
   needs at least six cases with at least two regression and two
   capability, "so that both the development set and the test set contain
   each kind." The existing 10 (4 regression / 6 capability) already clear
   that bar, so no new cases were needed before `optimize.prepare` could
   run.

The only case-related thing HW8 itself does is *split* those same 10 cases
into development (7) and test (3) sets -- see [[hw8-dev-test-split]] below --
it never adds to or replaces the set. (If a student's HW6 work was
incomplete, the fallback is `homework/module-5/hw8-reference-cases.patch`,
which adds reference cases rather than inventing the requirement to write
new ones from scratch.)

## The fixed development/test split, and why it's locked before Part C

`optimize.prepare` divides the 10 cases roughly one third test / two
thirds development, keeping the regression/capability ratio in each split
(so with 10 cases: 7 development, 3 test). The split is written once to
`optimize/state/split.json` and is not to be deleted, recreated, or edited
afterward.

The reason for locking it *before* any manual or automated agent change
(Part C/D) is the same reason a model's test set stays untouched during
training: if the fix were iterated against the same cases it's later
scored on, the final comparison would measure "did this memorize these 10
examples" rather than "does this generalize to the behavior the failure
mode actually describes." Concretely in this run: `e-008` (a capability
case for the chosen target mode, `mishandles_vague_requests`) landed in
development, and `e-009`/`e-010` (regression + capability, same mode)
landed in test -- so the fix can be iterated against `e-008`'s shape of
failure while `e-009`/`e-010` stay unseen until Part F.

## The 150-run search budget is separate from the split

`optimize.prepare` also writes `optimize/state/search_budget.json`,
starting at 150 **evaluated case runs** (one case, run once, scored; a
data-changing case counts as 5 runs because one success doesn't establish
reliable write behavior). This budget only applies to Part D's automated
search (`optimize.runner --search`, used by GEPA or the improve loop) --
manual experiments in Part C and the Part B/E baseline-and-final runs
never touch it. The two protections work differently: the split prevents
*leakage* (peeking at or tuning against held-out cases); the budget
prevents *unbounded brute-force search* against the development set it is
allowed to see.

## Not every case runs 5x -- only data-changing ones do

Easy to misread "a case that expects the agent to change data runs five
times" as applying to every case in a split. It only applies to cases
whose `expected.checks` require a tool call that actually mutates state
(`issue_refund`, `escalate_to_human`, etc.) -- not read-only tools like
`get_order`, and not cases scored purely by reply-text/judge checks with
no required tool call.

Checked against this run's 7 development cases:

| case | expected tool(s) | data-changing? |
|---|---|---|
| e-001 | `get_order` | no |
| e-002 | `get_order` | no |
| e-003 | `issue_refund` | yes -> 5 runs |
| e-004 | `get_order`, `escalate_to_human` | yes -> 5 runs |
| e-006 | `escalate_to_human` | yes -> 5 runs |
| e-007 | `escalate_to_human` | yes -> 5 runs |
| e-008 | none (clarifying-question judge check only) | no |

So the Part B development baseline is 4 cases x 5 runs + 3 cases x 1 run =
**23 evaluated case runs**, not 7x5=35 and not 7x1=7.

The reasoning: an LLM agent is non-deterministic, so one successful run of
a *write* action is weak evidence -- the model could have gotten lucky, or
could skip/duplicate/mis-target the action on a retry. Repeating a
write-expecting case 5 times and tracking `write_pass_5` (5/5 successes)
measures whether the write behavior is actually reliable, not just
occasionally correct. A read-only or text-only case doesn't carry that
same risk of an irreversible or duplicated side effect, so one run is
enough evidence for it.

## What the 5 runs of a case actually produce: two different rollups

Nothing special happens *during* each of the 5 runs -- each one is scored
against the case's own `checks`/`judges` exactly like a single-run case
would be, producing a plain `passed` true/false, `failure_modes`, latency,
and token usage (`optimize/runner.py`'s `run_evaluation`). What's
different is how the 5 results get aggregated afterward, into two
separate numbers that answer different questions:

1. **`pass_rate` for that case** -- `passes / 5` (e.g. 4/5 -> 0.8). This
   feeds the **overall `score`** for the run, which is the mean of every
   case's pass rate across the whole split (a write case contributes its
   fractional rate; a single-run case contributes a plain 0 or 1).
2. **`write_pass_5`** -- computed *only* across the write-expecting cases,
   as the share of them that hit a perfect 5/5. This is deliberately
   stricter than averaging: a case that went 4/5 still counts fully (0.8)
   toward `score`, but counts as a flat zero toward `write_pass_5`. The
   handout wants an occasional-flake-on-a-write-action surfaced as its own
   reliability signal, not smoothed into the overall mean score.

So one write case run 5x yields: an averaged `pass_rate` for the overall
score, a pooled set of `failure_modes` across all 5 attempts (for
diagnosis), and an all-or-nothing contribution to `write_pass_5` -- plus
cost and latency, which simply accumulate across every run regardless of
case type.
