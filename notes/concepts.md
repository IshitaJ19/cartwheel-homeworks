# Concepts and practical knowledge (generic, not homework-specific)

Reusable material for a syllabus — ideas and gotchas that apply beyond this
one repo. See [hw1-notes.md](hw1-notes.md) and [hw2-notes.md](hw2-notes.md)
for the concrete case studies that surfaced each of these.

## An agent spec is not the running system

A written spec (requirements doc, PRD, whatever) states intended behavior,
but the running application never reads that document. A requirement only
becomes real behavior through one of three implementation layers, and
*which* layer it lands in matters:

| Kind of requirement | Where it belongs | Why |
| --- | --- | --- |
| What to answer/refuse, tone, escalation triggers | The system prompt | The model decides |
| Who can see/do what | Auth code + each tool/function | Must hold even if the model misbehaves |
| Numeric policy (thresholds, windows, limits) | Config/data + deterministic code | Testable, not left to model judgment |

Teaching hook: give students one requirement from a spec and have them
guess which layer it belongs in *before* looking at the code. Deliberately
pick one that's easy to misplace (e.g. something that sounds like a
"tell the model" rule but must actually be code-enforced) to make the
distinction stick.

## HTTP endpoint & session

A CLI-style chat keeps identity in memory for the life of the process. An
HTTP server has no memory between requests — each call is a fresh
connection. That's why authenticated systems split into two steps:
verify identity once (issue a signed credential), then require every
later call to present that credential; the server looks up the stored
identity by an id, never by anything claimed in the request body/message.
This is *why* the server, not the conversation, decides who's talking.

## Trace & span

**Trace** — the complete record of one request, start to finish.
**Span** — one unit of work inside a trace (e.g. "call this tool," "call
the model"). Spans nest: a tool-call span sits inside the overall request
span, which is why a trace viewer shows a tree, not a flat list.

`trace.get_current_span()` (OpenTelemetry) returns whichever span is
"open" right now via an ambient context — conceptually like
`sys.exc_info()`, but for spans. When no span is open (tracing not
configured), it returns a no-op placeholder rather than `None` — check
`span.is_recording()` to tell the two cases apart.

## Vendor-neutral fields vs. application-specific fields

Auto-instrumentation records vendor-neutral fields (model name, messages,
tool name/args/result — defined by a shared spec, e.g. OTel's GenAI
semantic conventions: `gen_ai.*`). Anything the application alone knows
(which authenticated role/user made this call, whether a permission check
failed) has to be added by hand, in its own namespace (e.g. `cartwheel.*`
in this repo). Both kinds of fields end up as attributes on the same
spans — the split is about *who* can know the fact, not where it's stored.

## The three-layer observability stack (OpenTelemetry / OpenLLMetry / Langfuse)

Easy to conflate three different things:
- **OpenTelemetry** — the general-purpose tracing standard/API (spans,
  traces, exporters). Not AI-specific.
- **OpenLLMetry** (by Traceloop) — AI-specific instrumentation built on
  top of OTel. Ships pre-built instrumentors per AI library/SDK (OpenAI,
  Anthropic, LangChain, various agent frameworks) that auto-generate spans
  and `gen_ai.*` attributes for model/tool calls, so application code never
  hand-writes span creation.
- **Langfuse** — a trace *backend*: where OTel spans get exported to and
  viewed. OTel-compatible, built specifically for LLM traces (though the
  standard itself isn't tied to any one backend).

## Prompt version hashing

A prompt-version hash is a *fingerprint*, not a copy of the content — you
can't recover the prompt text from it. Its job is correlation: stamping
every trace/log with a short hash lets you group or filter records by
"which instructions were active," compare behavior across two prompt
revisions, or notice drift when the hash changes unexpectedly.

Design trap to watch for: hash only the *fixed* instructions, not anything
that gets filled in per-request (user id, role, session variables). If you
hash the fully-rendered prompt, every user/session gets a different hash
for otherwise-identical instructions — which defeats the entire point of
"group these by which prompt produced them." The actual content should
still be preserved somewhere durable (e.g. version control) — the hash
only ever answers "same or different," never "what was it."

## `max_turns` / tool-call loop limits

In an agentic loop, a single incoming request can trigger multiple
internal LLM calls: call model → maybe call a tool → feed the result back
→ call model again → ... until the model stops calling tools and returns
a final answer. A turn limit caps that internal cycle count — separate
from (and usually much smaller than) how many messages a user might send
in an ongoing conversation. It exists purely as a safety bound against a
run that never converges on a final answer.

## Request vs. trace vs. span vs. session — keeping four things straight

- **Request**: one call from a client to an endpoint.
- **Trace**: the full observability record of that one request.
- **Span**: one piece of work inside a trace; a trace's *root span* is
  whichever span is created first, with nothing else already open.
- **Session**: an identity + memory binding that outlives any single
  request — created once, then referenced by many separate requests
  (and therefore many separate traces) over time. A session is not a
  tracing construct; the link between "this trace" and "which session
  produced it" only exists if something explicitly stamps the session's
  id onto the trace. Don't assume that link exists by default — check.

**Multi-turn conversations produce multiple traces, not one.** Every
follow-up message in an ongoing conversation is its own new request, so it
gets its own new trace with its own root span — there is no single trace
that spans a whole back-and-forth conversation. A session id stamped on
each trace (e.g. `cartwheel.session_id` here) is the join key that lets
you regroup those separately-recorded traces back into "everything that
happened in this one conversation" after the fact — by filtering or
querying on that shared value. Whether a trace *viewer's UI* automatically
clusters same-session-id traces into one visual timeline (rather than you
manually filtering for them) depends on that specific backend recognizing
a particular attribute name as its own reserved session-linking field —
worth confirming per-tool rather than assuming a custom attribute name
triggers it automatically.

## Designing a judgment schema (met/unmet + reason codes)

A simple eval-logging schema (a boolean "did this meet the requirement"
plus a small enum of failure-cause categories, e.g. "the tool was wrong"
vs. "the model chose poorly" vs. "the spec doesn't say") forces a clear
binary call but loses nuance — e.g. "technically correct but unnecessarily
verbose" has nowhere to go. Worth discussing as a deliberate simplicity
trade-off when designing any lightweight eval-logging format: strictness
of the schema vs. richness of what it can express.

## Vibe-coding a custom annotation UI vs. the platform's generic one

A generic annotation UI (Langfuse's default trace/review view, or any
similar tool) "serves everyone, which means it serves no one perfectly."
It shows raw spans, token counts, and JSON regardless of what a given
review task actually needs. An LLM-assisted ("vibe-coded") custom UI can
instead render only the fields one specific review task cares about —
e.g. request, final reply, the one relevant evidence source, and a single
accept/revise/reject control — because annotation queues, scoring APIs,
and the underlying data model already exist in the platform; building
custom is then a thin frontend layer, not a system from scratch.

Reasons it can pay off:
- **Cognitive load** — a reviewer doing the same judgment call hundreds of
  times benefits from seeing only the relevant fields, not reconstructing
  them from a generic span tree each time.
- **Domain-specific rendering** — code wants syntax highlighting, emails
  want formatting, structured data wants collapsible sections; a one-size
  interface can't specialize for all of them.
- **Non-technical reviewers** — someone without an OTel/tracing background
  gets more value from a tailored view than from a generic trace explorer.
- **Volume** — shortcuts and auto-advance only compound into real time
  savings once review counts get large (tens to hundreds of items).

The explicit caveat: don't default to building one. "If you're the only
reviewer or your traces render fine in a generic interface, stick with
the platform UI" — a custom UI is an ongoing maintenance cost, so it's
worth it only when volume, non-technical reviewers, or specialized
rendering needs actually justify it.

(Source: [Langfuse: Vibe Coding a Custom Annotation UI](https://langfuse.com/blog/2025-11-25-vibe-coding-custom-annotation-ui))

## End-user feedback vs. expected-result evals

An in-chat feedback button/textbox is a useful triage signal (which traces to prioritize reviewing) but measures user *sentiment*, not correctness — it can't replace expected-result checks grounded in the database/policy docs, since a user can be happy with a wrong answer or unhappy with a correct one.

## Practical gotcha: self-hosted Docker images going stale

Reference `docker-compose.yml` files for observability stacks (Langfuse
and others) often pin third-party images by a moving tag (`:latest`) on
Docker Hub. Some vendors (MinIO is a real example encountered here) later
restrict or stop free distribution of images on Docker Hub entirely,
turning a previously-working `docker compose up` into a sudden "access
denied" failure with no code change on your side. When that happens:
check whether the vendor publishes on an alternate registry (MinIO also
mirrors to Quay) before assuming the compose file itself is broken.
Pinning to a specific release tag (rather than `:latest`) reduces surprise
but doesn't eliminate this class of failure — the whole tag lineage can
still get pulled.

## References

- [Hamel Husain: Why is error analysis so important in LLM evals, and how is it performed?](https://hamel.dev/blog/posts/evals-faq/why-is-error-analysis-so-important-in-llm-evals-and-how-is-it-performed.html) — relevant to the open-coding/failure-taxonomy work in Homework 4.
