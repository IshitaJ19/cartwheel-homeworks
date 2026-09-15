# Teaching notes

Working notes on concepts and concrete examples surfaced while doing the
Cartwheel homeworks, kept for drafting a syllabus/class later. Not part of
any homework submission — safe to edit, reorganize, or delete freely.

- [concepts.md](concepts.md) — generic, reusable material: spec-vs-
  implementation layers, trace/span, gen_ai.* vs application-specific
  fields, the OTel/OpenLLMetry/Langfuse stack, prompt-version hashing,
  max_turns, request/trace/span/session distinctions, judgment-schema
  design, coverage vs. challenge sets, and practical gotchas (stale pinned
  Docker images; regenerating an input file mid-run without retracting
  earlier output).
- [hw1-notes.md](hw1-notes.md) — HW1 case studies: two real prompt-gap
  findings (with before/after evidence) and the find_order contract-drift
  example.
- [hw2-notes.md](hw2-notes.md) — HW2 case studies: the missing
  session-id span attribute, the stale MinIO image, and a suggested
  teaching sequence.
- [hw3-notes.md](hw3-notes.md) — HW3 case studies: coverage vs. challenge
  examples, scaling scenario generation from 30 to 250, a stale-trace
  export bug (and fix), the support-role coverage-gap design call, and
  keeping order numbers in scenario messages.
