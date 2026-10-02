"""Fetch one monitoring period, sample it, judge it, correct it, score it.

Two modes:

  --period before|after   Reads the named period's time window from
                           monitoring/config.json, groups traces by
                           ``cartwheel.scenario_id``, and validates that all
                           50 scenarios in scenarios/monitoring_scenarios.jsonl
                           are present on the configured model before
                           proceeding. This is the manual two-period
                           comparison from Part A-C.

  --last-hours N           Reads the last N hours of traces, groups them by
                           ``meta.session_id`` instead (there is no fixed
                           scenario set on live traffic), and never rejects
                           the window: zero eligible conversations is a valid
                           outcome, recorded with a zero count, no judge call.
                           This is the scheduled GitHub Actions form (Part D).

Judge calls cost money, so the plan (selected trace counts, judge call count)
is always printed before any judge runs. Pass --confirm to actually invoke
the judge; without it the command prints the plan and exits.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from observability.instrument import load_env

CONFIG_PATH = Path("monitoring/config.json")
SCENARIOS_PATH = Path("scenarios/monitoring_scenarios.jsonl")
HISTORY_PATH = Path("monitoring/history.jsonl")


# ---------------------------------------------------------------------------
# config / scenario loading
# ---------------------------------------------------------------------------


def _write_history_row(row: dict[str, Any]) -> None:
    """Replace any existing row for this period label; keep one line per period.

    A scheduled run can legitimately re-process the same period/batch label
    (e.g. a manual re-trigger of the same day's `--last-hours` window), and
    the handout requires one line per period, mirroring the idempotency of
    the Langfuse scores themselves rather than letting the log grow per run.
    """
    rows = []
    if HISTORY_PATH.exists():
        rows = [
            json.loads(line)
            for line in HISTORY_PATH.read_text().splitlines()
            if line.strip()
        ]
    rows = [r for r in rows if r.get("period") != row["period"]]
    rows.append(row)
    HISTORY_PATH.write_text("".join(json.dumps(r) + "\n" for r in rows))


def _load_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text())


def _period_config(config: dict[str, Any], label: str) -> dict[str, Any]:
    for period in config["periods"]:
        if period["label"] == label:
            return period
    raise ValueError(f"no period labeled {label!r} in {CONFIG_PATH}")


def _expected_scenario_ids() -> set[str]:
    return {
        json.loads(line)["id"] for line in SCENARIOS_PATH.read_text().splitlines() if line.strip()
    }


def _active_risk_groups(config: dict[str, Any]) -> dict[str, Any]:
    from monitoring.sample import DEFAULT_RISK_GROUPS

    names = config["risk_groups"]
    missing = [name for name in names if name not in DEFAULT_RISK_GROUPS]
    if missing:
        raise ValueError(f"unknown risk group(s) in config: {missing}")
    return {name: DEFAULT_RISK_GROUPS[name] for name in names}


# ---------------------------------------------------------------------------
# fetch + build conversation records
# ---------------------------------------------------------------------------


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _fetch_window_traces(from_ts: datetime, to_ts: datetime, client: Any = None) -> list[dict[str, Any]]:
    """Pull and normalize every trace in [from_ts, to_ts), full records only."""
    from langfuse import Langfuse

    from analysis.helpers.normalization import normalize_trace

    lf = client or Langfuse()
    collected: list[Any] = []
    page = 1
    page_size = 100
    while True:
        resp = lf.api.trace.list(
            from_timestamp=from_ts, to_timestamp=to_ts, page=page, limit=page_size
        )
        batch = list(resp.data or [])
        collected.extend(batch)
        if len(batch) < page_size:
            break
        page += 1

    normalized = []
    for summary in collected:
        full = lf.api.trace.get(summary.id)
        normalized.append(normalize_trace(full))
    return normalized


def _build_conversations(
    traces: list[dict[str, Any]], group_key: str
) -> list[dict[str, Any]]:
    """Group traces by group_key (scenario_id or session_id) into one
    conversation record per group, keyed by the FINAL trace's id."""
    from analysis.helpers.normalization import _flatten

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trace in traces:
        key = trace["meta"].get(group_key)
        if key:
            groups[key].append(trace)

    conversations = []
    for key, group in groups.items():
        group.sort(key=lambda t: t.get("timestamp") or "")
        merged_messages: list[dict[str, Any]] = []
        tools: set[str] = set()
        models: set[str] = set()
        for trace in group:
            merged_messages.extend(trace["trace"])
            tools.update(
                str(m.get("name")) for m in trace["trace"] if m.get("role") == "tool_call"
            )
            models.update(trace["models"])
        final = group[-1]
        conversations.append(
            {
                "id": final["id"],
                "group_key": key,
                "text": _flatten(merged_messages),
                "tools": sorted(tools),
                # One turn = one user message (+ its reply), matching the
                # scenario file's own "one plus the number of followups"
                # convention -- NOT a raw message count, which DEFAULT_RISK_
                # GROUPS' multi_turn predicate (`turn_count > 1`) would
                # otherwise match on every completed single-turn exchange.
                "turn_count": sum(m.get("role") == "user" for m in merged_messages),
                "models": sorted(models),
                "trace_count": len(group),
                "timestamp": final["timestamp"],
            }
        )
    return conversations


def _select_monitored_conversations(
    conversations: list[dict[str, Any]], expected_ids: set[str], model: str
) -> list[dict[str, Any]]:
    """Keep only the 50 monitored scenarios from a period's raw conversations.

    A period's time window is a slice of the full HW3 scenario session, so it
    legitimately also contains conversations for scenarios outside the
    monitored 50 (the rest of that session's 250). Those are irrelevant, not
    an error -- only a MISSING monitored id or a monitored id on the wrong
    model is a reason to reject the period (don't combine separate retries).
    """
    by_id = {c["group_key"]: c for c in conversations}
    missing = expected_ids - set(by_id)
    if missing:
        raise RuntimeError(
            f"period is missing {len(missing)} of the 50 scenario ids "
            f"({sorted(missing)[:5]}); do not combine separate retries into "
            "one period, re-check the configured time window"
        )
    # Sorted, not set-iteration order: a plain set's iteration order is
    # randomized per process (hash randomization), which would silently
    # break select_traces' "same seed -> same sample" reproducibility
    # guarantee across separate `monitoring.run` invocations.
    selected = [by_id[sid] for sid in sorted(expected_ids)]
    off_model = [
        c["group_key"] for c in selected if not any(m.startswith(model) for m in c["models"])
    ]
    if off_model:
        raise RuntimeError(
            f"{len(off_model)} monitored conversations used a different "
            f"model than {model!r} ({off_model[:5]}); do not mix models "
            "within a period"
        )
    return selected


# ---------------------------------------------------------------------------
# the pipeline shared by both modes
# ---------------------------------------------------------------------------


def _run_pipeline(
    conversations: list[dict[str, Any]],
    config: dict[str, Any],
    batch_label: str,
    *,
    confirm: bool,
    trace_count: int,
) -> dict[str, Any]:
    from monitoring.correct import corrected_mode_prevalence
    from monitoring.run_judges import judge_sample, judge_test_data
    from monitoring.sample import select_traces
    from monitoring.write_scores import build_score_records, post_scores

    mode = config["judge_mode"]
    judge_id = config["judge_id"]
    risk_groups = _active_risk_groups(config)

    plan = select_traces(conversations, config["random_rate"], risk_groups, seed=7)
    n_random = len(plan["random"])
    n_risk = sum(len(v) for v in plan["risk_groups"].values())
    n_judge_calls = len(plan["to_judge"])

    print(f"period: {batch_label}")
    print(f"judge: {judge_id} ({mode})")
    print(f"conversations: {len(conversations)}  langfuse traces: {trace_count}")
    print(f"random sample: {n_random}  risk-group selections: {n_risk}")
    print(f"judge calls required (union, deduplicated): {n_judge_calls}")

    if not n_judge_calls:
        raise RuntimeError("selection produced no traces to judge")

    if not confirm:
        print("\nPass --confirm to run the judge and write scores/history.")
        return {"plan_only": True}

    verdicts = judge_sample(judge_id, plan["to_judge"])

    random_ids = {t["id"] for t in plan["random"]}
    risk_ids = {t["id"] for group in plan["risk_groups"].values() for t in group}
    random_verdicts = {tid: v for tid, v in verdicts.items() if tid in random_ids}
    risk_verdicts = {tid: v for tid, v in verdicts.items() if tid in risk_ids}

    test_labels, test_preds = judge_test_data(judge_id)
    sample_preds = [random_verdicts[t["id"]] for t in plan["random"]]
    estimate = corrected_mode_prevalence(sample_preds, test_labels, test_preds)

    records = build_score_records(mode, random_verdicts, risk_verdicts, estimate, batch_label)

    # Which named risk group(s) actually matched each trace -- not part of
    # build_score_records' tested contract (comment must stay None there),
    # so attached here as score metadata instead. Without this, a
    # "_risk_verdict" score in Langfuse is indistinguishable from any other:
    # there's no way to tell whether multi_turn, write_action, or both
    # flagged a given trace for review.
    risk_group_names_by_trace: dict[str, list[str]] = {}
    for group_name, group in plan["risk_groups"].items():
        for t in group:
            risk_group_names_by_trace.setdefault(t["id"], []).append(group_name)
    for record in records:
        if record["name"] == f"{mode}_risk_verdict":
            record["metadata"] = {"risk_groups": sorted(risk_group_names_by_trace[record["trace_id"]])}

    from analysis.helpers import langfuse_io

    written = 0
    if langfuse_io.is_configured():
        written = post_scores(records)
    print(f"scores written: {written} (langfuse configured: {langfuse_io.is_configured()})")

    history_row = {
        "period": batch_label,
        "judge_id": judge_id,
        "mode": mode,
        "model": config.get("model"),
        "langfuse_trace_count": trace_count,
        "conversation_count": len(conversations),
        "random_sample_count": n_random,
        "risk_sample_count": n_risk,
        "raw": estimate["raw"],
        "corrected": estimate["corrected"],
        "ci_low": estimate["ci_low"],
        "ci_high": estimate["ci_high"],
        "failure_sensitivity": estimate["failure_sensitivity"],
        "pass_specificity": estimate["pass_specificity"],
        "threshold": config.get("threshold"),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_history_row(history_row)
    print(f"wrote history row for period {batch_label!r} to {HISTORY_PATH}")

    return history_row


def _zero_history_row(batch_label: str, config: dict[str, Any]) -> dict[str, Any]:
    row = {
        "period": batch_label,
        "judge_id": config["judge_id"],
        "mode": config["judge_mode"],
        "model": config.get("model"),
        "langfuse_trace_count": 0,
        "conversation_count": 0,
        "random_sample_count": 0,
        "risk_sample_count": 0,
        "raw": None,
        "corrected": None,
        "ci_low": None,
        "ci_high": None,
        "failure_sensitivity": None,
        "pass_specificity": None,
        "threshold": config.get("threshold"),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_history_row(row)
    return row


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--period", choices=("before", "after"))
    group.add_argument("--last-hours", type=float)
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="actually call the judge and write scores/history (costs money)",
    )
    args = parser.parse_args()

    load_env()
    config = _load_config()

    if args.period:
        period = _period_config(config, args.period)
        # Pad by a couple seconds: the Langfuse to_timestamp filter is
        # exclusive, and a trace can legitimately land exactly on the
        # recorded window boundary (seen with a multi-turn scenario's final
        # trace). The two periods are weeks apart, so this can't blur them.
        from_ts = _parse_ts(period["from"]) - timedelta(seconds=2)
        to_ts = _parse_ts(period["to"]) + timedelta(seconds=2)
        raw_traces = _fetch_window_traces(from_ts, to_ts)
        all_conversations = _build_conversations(raw_traces, group_key="scenario_id")
        conversations = _select_monitored_conversations(
            all_conversations, _expected_scenario_ids(), config["model"]
        )
        _run_pipeline(
            conversations,
            config,
            batch_label=args.period,
            confirm=args.confirm,
            trace_count=len(raw_traces),
        )
        return

    to_ts = datetime.now(timezone.utc)
    from_ts = to_ts - timedelta(hours=args.last_hours)
    raw_traces = _fetch_window_traces(from_ts, to_ts)
    conversations = _build_conversations(raw_traces, group_key="session_id")
    batch_label = to_ts.strftime("%Y-%m-%d")

    if not conversations:
        print(f"no eligible conversations in the last {args.last_hours} hours; recording zero count")
        _zero_history_row(batch_label, config)
        return

    _run_pipeline(
        conversations,
        config,
        batch_label=batch_label,
        confirm=args.confirm,
        trace_count=len(raw_traces),
    )


if __name__ == "__main__":
    main()
