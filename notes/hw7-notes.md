# HW7 notes for teaching

Concrete terminology and worked reasoning from setting up post-deployment
monitoring for one failure mode. See [concepts.md](concepts.md) for generic
framing, [hw6-notes.md](hw6-notes.md) for the CI-side companion (pre-merge
gating vs. this homework's post-deployment watching).

## The theme: monitoring after deployment, not gating before it

HW6 asks "does this change pass known regression tests before it merges?"
HW7 asks a different question: "now that something is running, did its real
failure rate move?" Cartwheel has no live user traffic, so the homework
fakes the passage of time with two manually produced snapshots:

- **Before period** — the original HW3 run of the 50 scenarios in
  `scenarios/monitoring_scenarios.jsonl`, whose traces already existed in
  Langfuse from that earlier assignment.
- **After period** — a brand new execution of the *same* 50 scenarios on
  the *same* model, run now.

Holding scenarios and model constant isolates the one variable actually
being measured: whatever (if anything) changed about the agent's behavior
between the two runs — a prompt edit, a tool change, a policy doc update.
In the "simple path" we ran first, nothing was deliberately changed, so
before/after is a sanity check that the pipeline reports "no real
difference" correctly before we trust it to report a real one.

### Before/after period found for this run

- Before: `2026-09-14T15:45:59.104Z` → `16:00:36.941Z` (gpt-5.4-nano).
  A separate 5-scenario fragment at `14:47` that session was excluded —
  it didn't contain all 50 scenario ids, so combining it in would have
  silently mixed two unrelated runs into one "period."
- After: `2026-10-01T01:32:20.682Z` → `01:43:59.511Z` (gpt-5.4-nano).
- Both verified to contain exactly the 50 scenario ids with matching
  per-scenario turn counts before being accepted as a period.

## Why the judge's TPR/TNR are fixed, not recomputed per period

Easy to assume each period gets its own freshly measured judge accuracy.
It does not. TPR/TNR are a one-time property of the *frozen judge*,
measured once against a held-out human-labeled test set back in HW5
(`mishandles_vague_requests-v0`: TPR 0.8542, TNR 0.6, n=63). Freezing
means locking the prompt, model, and that measured error profile together
so every later use of the judge can be trusted to behave the same way
without re-labeling data every time it runs — which is the entire point
of being able to automate monitoring instead of hand-reviewing traces
forever.

## What actually changes per period: corrected prevalence

What *is* computed fresh for each period is the corrected prevalence — an
estimate of the true failure rate among that period's 50 conversations.
Chain per period:

1. The judge runs on the sampled traces (10 random + any risk-group
   traces) and produces a raw flag rate from the random sample only, e.g.
   "flagged 3 of 10 random conversations" → raw rate 0.3.
2. The raw rate is a biased estimate, because the judge itself makes
   mistakes: it misses some real failures (`1 - TNR`) and wrongly flags
   some real passes (`1 - TPR`). The Rogan-Gladen correction uses the
   judge's fixed TPR/TNR to back out what the true population failure
   rate must have been, given the observed raw rate and that known error
   profile (`corrected_mode_prevalence` in `monitoring/correct.py`).
3. Output is one corrected prevalence number plus a bootstrap confidence
   interval, per period — so after Part C there are two independent
   numbers to compare: `corrected_rate_before` and `corrected_rate_after`,
   each with its own interval.

This is also why only the *random* sample feeds the correction — the risk
groups are not a random sample of the population, so their flag rate can't
be extrapolated into a population-wide estimate. They exist only to
surface traces worth a human look, not to move the statistic.

## The threshold is a business rule, not a statistical property

`monitoring/config.json`'s `threshold: 0.15` has nothing to do with
TPR/TNR. It's a judgment call, made *before* looking at either period's
judge results (chosen in Part A, deliberately before inspection, so it
can't be picked after the fact to make a result look better or worse). It
answers one question: how high does the corrected failure rate have to
get before it's worth triggering a new round of error analysis on the
flagged traces, potentially turning confirmed failures into new HW6
evaluation cases.

The threshold is scoped to **one specific failure mode** —
`mishandles_vague_requests` in this run, tracked via `judge_mode` in
`monitoring/config.json`. It is not a global "overall agent health"
number. A monitor only ever watches the one failure mode its frozen judge
was built for; `unsupported_policy_claim` or any other mode would need its
own frozen judge, its own measured TPR/TNR, its own corrected-prevalence
calculation, and its own threshold, chosen independently because different
failure modes can have very different acceptable base rates (a rare but
severe failure mode like a policy-contradicting promise might warrant a
much lower threshold than a merely-annoying one like asking an extra
clarifying question).

Running several failure modes side by side means several independent
monitor configs/judges/thresholds, each producing its own
before/after comparison — not one combined score. Nothing in this
homework's structure aggregates across modes; each `monitoring/history.jsonl`
line is already scoped to one `judge_mode`, and a second failure mode would
get its own judge id, its own risk-group definitions, and its own line in
that history file, checked against its own threshold independently.

## Approaching multiple failure-mode monitoring in practice

Mechanically, each additional mode means repeating the whole HW7 pipeline
with that mode's own frozen judge, substituting into
`monitoring/config.json`'s `judge_id`/`judge_mode`/`risk_groups`/`threshold`.
A few things that *do* carry across modes without needing to be redone:

- The two periods themselves (the same before/after Langfuse windows,
  the same 50 conversation records built from them) are shared raw
  material — `select_traces`/`run.py`'s trace-fetch and conversation-build
  step doesn't care which failure mode you're about to judge.
- The *random* sample can be judged by every mode's judge in the same
  pass if calls are cheap enough, since "20% random sample" is a property
  of the period, not of the mode.

What does not carry across modes:

- Risk groups, because "what counts as risky for this mode" is
  mode-specific (e.g. `policy_lookup` makes sense for
  `unsupported_policy_claim`, but `multi_turn`/`write_action` make more
  sense for `mishandles_vague_requests`).
- The threshold, because different modes warrant different tolerances —
  a severe-but-rare mode usually gets a lower threshold than a common,
  low-severity one.
- The corrected prevalence calculation and its interval, because each
  mode's judge has its own TPR/TNR, so Rogan-Gladen correction must run
  once per mode even on the same raw sample.

In a real system this is usually organized as one monitor config per
mode (own judge id, own risk groups, own threshold) all reading from the
same underlying trace stream, each writing its own row to history/score
storage, and each independently able to cross its own threshold and
trigger its own error-analysis follow-up — rather than merging modes into
a single pass/fail number, which would hide which specific failure is
driving a regression.

## Code checks vs. LLM judge checks: not both wired into this monitor

HW6's eval cases (`eval_cases/cases.jsonl`) mix two kinds of evaluators per
case: a `checks` list (deterministic/code-based, e.g. "cancel_order must
succeed because the order is still in `placed` state" — computable
directly from DB state with no model call) and a `judges` list (LLM-based,
for criteria that need interpretation). HW7's monitor, as implemented
here, is **judge-only** — it samples conversations and sends them to one
frozen LLM judge for one subjective failure mode. It does not run any of
HW6's deterministic checks against the monitored traces.

`monitoring/run_judges.py`'s docstring describes a broader "two-track
plan" as the conceptual reason sampling exists at all: in a fuller
production design, cheap deterministic code checks could run on 100
percent of live traffic for free (schema validity, tool call succeeded,
required field present, latency within SLA), while the expensive LLM
judge only runs on a sampled subset, reserved for failure modes that
genuinely require interpretation and can't be reduced to a deterministic
rule (which is exactly why `mishandles_vague_requests` needs a judge in
the first place — "did the agent handle an under-specified request well"
isn't checkable by a regex or a DB assertion). That two-track idea is
mentioned as motivation/context, not something this homework asks you to
build; nothing in `homework/module-3/hw7.md` requests adding code checks
to the monitor.

Also worth noting why HW6-style checks don't directly transplant into
live monitoring even if you wanted them to: HW6's checks work because each
case scripts a known expected outcome against a known seeded DB state
(a controlled Harbor container, not open-ended live traffic). Live
monitored conversations don't come with a hand-authored golden answer the
way a CI eval case does, so a production-style "code check" track would
look different in shape — closer to schema/latency/error-status
assertions than to "does this match the golden output" — not a reuse of
HW6's exact case-level checks.

## General concept: how production monitoring is layered (not HW7-specific)

Zooming out past this one assignment, continuous monitoring for an AI
agent in industry is usually layered cheapest-to-most-expensive, and
HW7's design is one narrow slice of that stack (layer 4 below, applied to
one mode):

1. **Infra/observability telemetry** — latency, error rate, token cost,
   retry counts. Not evaluation, just operational health; this is what a
   trace store (Langfuse here) gives for free on every request.
2. **Deterministic/code-based checks ("guardrails")** — run on 100
   percent of traffic because they're nearly free and need no model call:
   schema/JSON validity, tool call succeeded vs. errored, PII-pattern
   detection, banned-word filters, output length bounds. These check a
   *structural or policy property of the output itself*, not whether it
   matches a predetermined correct answer — which is exactly why they
   scale to unlimited live traffic with no foreknowledge required.
3. **Cheap statistical/behavioral signals** — computed from logs without
   a model call: thumbs-down rate, user-retry rate, session abandonment,
   sentiment via a lightweight classifier. Still roughly free per
   request; used as leading indicators.
4. **LLM-judge-based checks** — for criteria that need interpretation and
   can't be reduced to a rule: faithfulness, tone, "did this handle an
   ambiguous request well," policy-compliance nuance. Cost money and
   latency per call, which is exactly why they're *sampled* rather than
   run on every request — the gap HW7's `select_traces` sampling models.
5. **Human review** — sparsest, most expensive. Used to calibrate the
   judges themselves (exactly what the HW5 held-out labels did) and to
   catch what judges miss or disagree on.

### How this relates to CI/CD-style pre-merge evals (HW6's shape)

CI typically reuses the *same evaluator types* — code checks and LLM
judges — but applied differently along three axes:

- **Trigger.** CI runs on every PR/merge, a fixed small batch. Monitoring
  runs continuously on open-ended live traffic.
- **Ground truth.** CI cases are curated with a *known expected outcome*
  scripted against a controlled, seeded environment, so a code check can
  literally assert "the order should now show cancelled." Live traffic
  has no such scripted answer, so a deterministic check in production can
  only verify structural/policy properties of the output (valid JSON,
  tool succeeded, no PII) — not "is this the objectively correct
  resolution." That correctness question either needs a judge, or a
  downstream ground-truth signal if one actually exists (e.g., did the
  payment processor confirm the refund actually posted).
- **Sampling.** CI evaluates every case every time, because the suite is
  small and fixed by design. Monitoring must sample, because traffic
  volume is unbounded and judge calls cost money at scale — a random
  slice for an unbiased rate estimate, plus targeted risk slices for
  catching likely failures faster. That's exactly the two-part sampling
  design in `select_traces`.

So: code checks absolutely exist in real continuous monitoring, and are
usually the majority of what runs by volume — but the kind of code check
that transfers from CI to production is the rule/structural kind, not the
golden-answer kind. The golden-answer kind is CI-only by nature, because
it depends on a scripted, known-correct expected outcome that live
traffic never comes with.

## How TPR/TNR were actually calculated (walkthrough, not recomputed here)

Worth spelling out precisely, since it's easy to think a confusion matrix
gets built fresh somewhere in the monitoring pipeline. It doesn't — it was
built exactly once, back in HW5, and only read (not recomputed) in HW7.

1. A held-out test split existed: 63 real conversations
   (`splits.json`'s `mishandles_vague_requests.test`), each with a human
   label from HW5's labeling work — ground truth on whether the agent
   actually mishandled a vague request in that conversation.
2. The frozen judge prompt (`mishandles_vague_requests-v0`, `gpt-4o-mini`)
   ran once over all 63, producing a predicted pass/fail per conversation.
3. Human label and judge prediction were compared as a confusion matrix,
   with **Pass as the positive class**:

   | | Judge: Pass | Judge: Fail |
   |---|---|---|
   | **Human: truly Pass** | TP = 41 | FN = 7 |
   | **Human: truly Fail** | FP = 6 | TN = 9 |

4. From those four counts:
   - **TPR** (sensitivity for Pass) = TP / (TP + FN) = 41/48 = **0.8542** —
     of all genuinely-fine conversations, the judge correctly called
     85.4% of them Pass.
   - **TNR** (the judge's ability to catch real failures) =
     TN / (TN + FP) = 9/15 = **0.60** — of all conversations that
     genuinely had the failure mode, the judge only correctly caught 60%.
5. Wilson confidence intervals were computed around each rate (TNR's
   interval is wide because there were only 15 true-failure examples in
   the test set — a small denominator).

This whole computation happens once, at freeze time, and is never
rebuilt afterward. It's what the Rogan-Gladen correction in HW7 reuses
unchanged for both the before and after period.

## General practice: a confusion matrix needs paired ground truth, which production data doesn't have

The reason TPR/TNR is a one-time computation generalizes well beyond
LLM judges, and is worth holding onto as a general principle:

**Phase 1 — judge calibration (confusion matrix required, done rarely).**
A confusion matrix needs paired (human ground truth, judge prediction)
data on the *same* examples. This only happens on a small, deliberately
labeled held-out set, because getting a human label for every example is
exactly the expensive thing a judge exists to avoid. This phase produces
a judge's TPR/TNR (or precision/recall, or whatever metric pair fits) as
a fixed property of that judge version.

**Phase 2 — production monitoring (no confusion matrix, no ground
truth).** On live/new data there is never a human label — automating
with a judge is precisely what removes that requirement. So a confusion
matrix cannot be built there. All that exists is the judge's raw verdict
on each sampled conversation, giving a raw positive rate (e.g. "the judge
flagged 30% of this sample"). That raw rate is biased in a *known* way
(the judge's TPR/TNR from Phase 1), so it gets algebraically corrected
into an estimate of the true population rate — no new ground truth
needed. That is the entire purpose of Rogan-Gladen: invert a known, fixed
error profile to de-bias an observed rate, without re-verifying anything.

This pattern long predates LLM judges. Rogan-Gladen itself comes from
1970s epidemiology: a diagnostic test's sensitivity/specificity is
measured once against a gold-standard clinical reference (a confusion
matrix on a small curated sample), then the test is deployed at scale to
estimate disease prevalence in a whole population using only the test's
own positive rate — nobody re-diagnoses every patient with the
gold-standard method to double check the test each time. The same
structure shows up in spam filtering, content-moderation classifiers at
scale, manufacturing QC (sample-inspect a few units, extrapolate the
batch's defect rate), and now LLM-judge-based eval monitoring.

**Caveat: frozen TPR/TNR can go stale.** Because Phase 1's numbers are
reused indefinitely, they drift out of validity if the live data
distribution moves away from what the held-out test set looked like (new
scenario types, a model update, different user phrasing). That is why
"revalidate before reusing a judge for a different model or a different
failure mode" is a hard rule — the frozen TPR/TNR is only trustworthy for
the exact (prompt, model) pair it was measured on, and only for as long
as the data it's applied to still resembles what it was calibrated
against.

## How risk groups were chosen, and how they're chosen in general

For this run, the choice was constrained and fairly intuitive rather
than data-driven: `monitoring/sample.py`'s `DEFAULT_RISK_GROUPS` only
defines three predicates over structural trace evidence (tools called,
turn count) — `policy_lookup`, `write_action`, `multi_turn`. Reasoning
from the semantics of `mishandles_vague_requests`: a vague request
plausibly needs a clarifying follow-up, so `multi_turn` is a plausible
carrier; acting on an under-specified request before clarifying is risky
even if less directly tied to "vagueness," so `write_action` was added
too. No statistical analysis of this repo's actual data went into the
choice beforehand — it was a reasoned hypothesis, picked from the three
predicates that already existed in code, not a measurement.

General practice for choosing risk groups, usually combined:

1. **Domain/engineering intuition** — a new feature rollout, a
   particular tool, a known-fragile code path, a user segment believed
   more likely to trigger the failure. A hypothesis, not a measurement.
   What was done above.
2. **Prior error analysis** — if open coding / manual trace review (HW4
   style) already happened, you likely already know empirically which
   conditions co-occur with the failure. Strongest basis: "last time we
   manually reviewed failing traces, most involved tool Y" directly
   motivates a risk group on tool Y.
3. **Empirical correlation from labeled data** — with a labeled dev/test
   set (HW5 style), directly measure: among labeled failures, what
   fraction have property P, versus among labeled passes? A property
   disproportionately common in failures is a good candidate. More
   rigorous than guessing, but needs labels to already exist.
4. **Severity/impact, not just likelihood** — sometimes a group is
   chosen because a failure *there* is worse, not because it's more
   likely. `write_action` fits this: a refund/cancellation mistake has
   real consequences, worth the extra scrutiny even if its failure rate
   isn't actually elevated.
5. **Iterative refinement** — risk groups are hypotheses; the judge's
   risk-verdict data checks them after the fact. A group whose flag rate
   comes back no higher than the random sample's was a wrong hypothesis,
   worth dropping or replacing; one that comes back much higher confirms
   it's worth continuing to watch.

The common thread: risk groups are never derived from the live
monitoring data itself (circular — there are no failure labels for live
traffic, that's the problem monitoring exists to solve). They're set in
advance from intuition, prior labeled analysis, or known severity, then
validated after the fact by whether their judge verdicts actually came
back elevated.

## The big catch: sampling bias (statistics vs. auditing)

A risk group's own flag rate must never be read as "the AI's overall
failure rate," and the reverse mistake — inspecting only risk-group
traces and reporting that rate as representative — is the single most
important trap this whole design protects against.

**The bias.** Deliberately looking in a high-risk bucket (multi-turn
chats, refund requests) finds a higher percentage of errors than exists
across the entire platform, by construction — that is what "risk group"
means. Treating that elevated rate as the platform-wide failure rate
would overstate how often the agent actually fails, sometimes
dramatically.

**The rule, cleanly split by purpose:**

- **Random sampling measures.** Use it to estimate the actual overall
  failure rate ("our agent fails on 2% of total traffic"). This is the
  only sample `corrected_mode_prevalence` is ever allowed to touch,
  exactly because it is the only one drawn without regard to suspected
  risk.
- **Risk groups audit.** Use them to find and inspect specific mistakes
  ("let's go look at these flagged refund conversations to see why the
  agent failed"), never to produce a rate. Their verdicts get written to
  Langfuse as `_risk_verdict` scores for a human to review, and stop
  there — they never enter the correction math.

This is exactly why `select_traces` keeps `"random"` and `"risk_groups"`
as separate keys in its returned plan even after their traces get judged
together as one deduplicated union, and exactly why
`corrected_mode_prevalence`'s docstring is explicit that `sample_preds`
must be "the UNIFORM BASE sample only (never the risk strata; they are
biased toward failure by design)."

**Enforcing the split in this codebase, concretely:**

- Only the random bucket's verdicts are ever passed into
  `corrected_mode_prevalence` as `sample_preds` — that is the one
  function that produces the overall failure-rate estimate, and it stays
  clean/unbiased precisely because the risk bucket never reaches it.
- The risk bucket's verdicts are written to Langfuse purely as
  `<mode>_risk_verdict` scores (`build_score_records`), strictly so a
  developer/evaluator can manually pull up and inspect those specific
  flagged conversations later — never consumed by any statistic.

## Why this whole pipeline runs after the conversation, not during it

Everything in `monitoring/` is a post-hoc / batch evaluation pipeline: it
reads completed traces out of Langfuse and judges them well after the
conversation ended. Worth being explicit about why, and about the
different thing "risk" means at runtime versus here.

**Why offline/after-the-fact is the right shape for this pipeline:**

- **A complete trace is required.** `multi_turn` needs a total turn
  count across the whole conversation; judging whether the agent should
  have asked a clarifying question instead of taking a write action
  needs the full exchange and its final outcome. Neither is decidable
  from a single in-flight turn.
- **Sampling needs a closed population.** `select_traces` draws a
  uniform random sample and deduplicates a risk-group union over a
  *fixed* batch — "the 50 conversations in this period." That requires
  the time window to have already closed and every trace in it to be
  saved (Langfuse here), the same way `monitoring/run.py --last-hours
  24` only ever evaluates a window that has already fully elapsed.

**How this differs from live/runtime risk tagging.** Cartwheel already
has the live half of this picture, separate from anything in
`monitoring/`:

- `agent/guards.py`'s `refund_needs_human` and `injection_input_guardrail`
  intercept a turn or tool call *as it happens* — before the refund
  executes, before a suspicious input reaches the model — to trigger a
  human-in-the-loop gate or block the action outright.
- `agent/killswitch.py`'s `kill_switch` and `seed/eligibility.py`'s
  `refund_needs_approval` are the same kind of real-time control: a
  per-call check that can stop an action before it executes, not a
  judgment made by rereading the conversation afterward.

**The distinction that matters:** live tagging exists for runtime safety
and control — stopping a bad action before it executes. Post-hoc
annotation (`monitoring/sample.py`'s risk groups, the whole HW7 pipeline)
exists for offline auditing and evaluation — measuring accuracy, catching
failure modes, and tracking a failure rate over time, on conversations
that have already finished and can no longer be stopped. The two are
complementary, not redundant: a live guardrail only ever sees the one
call in front of it and must decide in milliseconds with no hindsight; a
post-hoc judge sees the whole finished conversation and can take as long
(and cost as much) as the batch job allows, but can never prevent
anything — it can only report that something already happened.
