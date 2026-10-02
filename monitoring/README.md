# HW7 monitor: scheduling setup

`monitor.yml`'s underlying command was verified directly (`uv run python -m
monitoring.run --last-hours 24`, including the zero-eligible-conversations
path), and the workflow YAML itself was then validated end to end with
[`act`](https://github.com/nektos/act) (Docker-based local GitHub Actions
emulation), standing in for a real self-hosted runner and GitHub's secrets
UI without needing either:

```bash
act workflow_dispatch -W .github/workflows/monitor.yml \
  -P self-hosted=catthehacker/ubuntu:act-latest \
  --secret-file <local secrets file, not committed> \
  --var-file <local vars file, not committed> \
  --container-architecture linux/amd64
```

`LANGFUSE_HOST` was overridden to `http://host.docker.internal:3000` for
this run only, since the workflow's container needs Docker's host-gateway
hostname to reach the Mac host's `localhost:3000` Langfuse stack — the real
self-hosted runner (set up below) runs directly on the host, not nested in
a container, so it uses plain `localhost` as configured.

**Result:** `checkout` -> `setup-uv` -> `uv sync` -> the monitor command all
ran for real against the live local Langfuse stack: it fetched real traces,
sampled, called the real frozen judge (`gpt-4o-mini`, 20 calls, $0.01),
wrote 21 real scores back to Langfuse, and appended a real history row
(`2026-10-01`, alongside the `before`/`after` comparison rows) to
`monitoring/history.jsonl`. Only the final `actions/upload-artifact@v4`
step failed, because `act`'s lightweight test image has no Node.js
installed to run that JS-based action — a known limitation of `act`'s
image, not a bug in the workflow; a real GitHub-hosted or self-hosted
runner ships Node as part of the runner software, so this step is expected
to work there unmodified.

What's left is one-time GitHub/infra setup, done manually rather than by
the coding agent, since it involves credentials and a persistent service on
this machine.

## 1. Register a self-hosted runner

The workflow targets `runs-on: self-hosted`, not `ubuntu-latest`, because
`LANGFUSE_HOST` here is `http://localhost:3000` (the self-hosted stack from
`observability/docker-compose.yml`) — a GitHub-hosted runner in the cloud
cannot reach your laptop's localhost.

On this machine:

1. GitHub repo -> Settings -> Actions -> Runners -> "New self-hosted runner".
2. Follow GitHub's displayed download + configure commands (they include a
   one-time registration token scoped to this repo).
3. Run it as a background service so it survives logout/reboot, e.g.
   `./svc.sh install && ./svc.sh start` (from the runner's own setup
   instructions) rather than leaving `./run.sh` in a foreground terminal.
4. Confirm it shows "Idle" under Settings -> Actions -> Runners before
   continuing.

## 2. Set repository secrets and variables

Settings -> Secrets and variables -> Actions:

- **Secrets**: `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`,
  `OPENAI_API_KEY` (the frozen judge `mishandles_vague_requests-v0` is
  pinned to `gpt-4o-mini`; the workflow does not need the Cartwheel model's
  own provider key, since it only reads existing traces).
- **Variables**: `LANGFUSE_HOST` (plain value, not a secret, since it's a
  non-sensitive URL: `http://localhost:3000`).

## 3. Push and trigger

```bash
git add .github/workflows/monitor.yml monitoring/
git commit -m "Add HW7 scheduled monitor"
git push
gh workflow run monitor.yml
gh run watch
```

Confirm the run completes and `monitoring/history.jsonl` /
`monitoring/last_run.log` show up as a workflow artifact on that run (the
upload step runs with `if: always()`, so it uploads even on failure for
debugging).

---

# Part E answers

## 1. Did the corrected failure estimate move between the two periods?

No. The corrected rate was identical in both periods: **0.1193** before,
**0.1193** after. Expected, since this run deliberately changed nothing
between the two periods (same prompt, same model, same scenario script) —
it was a sanity check on the pipeline itself, not a real deployment
comparison.

## 2. Do the intervals support a conclusion, or is the result uncertain?

Uncertain. Both 95% confidence intervals are very wide and almost
completely overlap: before = [0.0, 0.897], after = [0.0, 0.914]. With only
10 random samples feeding the correction per period, the data can't
confidently place the true rate on either side of the 0.15 threshold, let
alone distinguish before from after. A real conclusion here would need a
larger random sample per period.

## 3. What did the risk groups reveal that the random estimate did not?

Two things the flat random rate (steady 20% both periods) never showed:

- The `multi_turn` risk group (only 2 of the 50 scenarios are even
  multi-turn) flagged **2/2 in "before"** and **1/2 in "after"** — both
  well above the random rate, a lead worth investigating even though the
  group is too small (n=2) to be statistically confident on its own.
  `write_action` showed no independent signal once separated from its
  overlap with `multi_turn`.
- A concrete behavioral finding on scenario `support-0236` (one of the two
  multi-turn scenarios): in "before" the agent called `issue_refund`
  directly; in "after", for the identical script with no prompt change,
  it instead ran a string of policy/help-center lookups and never called
  `issue_refund` at all. Pure run-to-run model variance — a reminder that
  "nothing changed" doesn't mean "the agent behaved identically," and
  part of why the intervals above are as wide as they are.

## 4. What action should happen if the estimate crosses the threshold?

Per the monitor's design: a crossing should trigger manual error analysis
on the traces flagged by both the random sample and the risk groups, and
any **human-confirmed** failures (not just judge-flagged ones — the frozen
judge itself has a known ~15% false-positive rate on true passes) should
become new evaluation cases in the Homework 6 suite, so CI regression-tests
against a recurrence of that specific failure.

In this run, the corrected rate (0.1193) stayed below the 0.15 threshold in
both periods, so no crossing occurred and no action was triggered. Given
how wide the intervals are, though, that "below threshold" result itself
isn't a confident all-clear — it would be worth re-running the monitor
with a larger random sample before concluding there's no issue here.
