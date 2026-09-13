# Teaching notes

Working notes on concepts and concrete examples surfaced while doing the
Cartwheel homeworks, kept for drafting a syllabus/class later. Not part of
any homework submission — safe to edit, reorganize, or delete freely.

- [concepts.md](concepts.md) — generic, reusable material: spec-vs-
  implementation layers, trace/span, gen_ai.* vs application-specific
  fields, the OTel/OpenLLMetry/Langfuse stack, prompt-version hashing,
  max_turns, request/trace/span/session distinctions, judgment-schema
  design, and practical gotchas (e.g. stale pinned Docker images).
- [hw1-notes.md](hw1-notes.md) — HW1 case studies: two real prompt-gap
  findings (with before/after evidence) and the find_order contract-drift
  example.
- [hw2-notes.md](hw2-notes.md) — HW2 case studies: the missing
  session-id span attribute, the stale MinIO image, and a suggested
  teaching sequence.
