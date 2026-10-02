"""Bias-corrected failure prevalence for a monitoring period."""

from __future__ import annotations

from typing import Any, Sequence

import numpy as np


def _sensitivity_specificity(
    labels: Sequence[int], preds: Sequence[int]
) -> tuple[float, float] | None:
    """Failure sensitivity and pass specificity from paired labels/preds.

    Both use the monitoring convention that 1 means a failure is present.
    Returns None when either class is absent (the rate is undefined)."""
    tp = fn = tn = fp = 0
    for label, pred in zip(labels, preds):
        if label not in (0, 1) or pred not in (0, 1):
            raise ValueError("labels and predictions must be 0 or 1")
        if label == 1 and pred == 1:
            tp += 1
        elif label == 1 and pred == 0:
            fn += 1
        elif label == 0 and pred == 0:
            tn += 1
        else:
            fp += 1
    if (tp + fn) == 0 or (tn + fp) == 0:
        return None
    return tp / (tp + fn), tn / (tn + fp)


def corrected_mode_prevalence(
    sample_preds: Sequence[int],
    test_labels: Sequence[int],
    test_preds: Sequence[int],
    confidence: float = 0.95,
    bootstrap_iterations: int = 20000,
    seed: int | None = 7,
) -> dict[str, Any]:
    """Bias-corrected live prevalence for one mode from sampled verdicts.

    The contract, precisely:

      1. ``raw`` is the uncorrected flag rate: ``mean(sample_preds)``.
      2. Compute the frozen judge's failure sensitivity and pass specificity
         from ``test_labels`` and ``test_preds``. Both use the monitoring
         convention that 1 means a failure is present. Failure sensitivity is
         the flagged fraction of human-labeled failures. Pass specificity is
         the unflagged fraction of human-labeled passes.
      3. Compute the Rogan-Gladen point estimate, then resample the held-out
         records and sampled predictions to obtain a percentile-bootstrap
         interval. Use a seeded NumPy generator so the committed result is
         reproducible.
      4. Resample the monitoring predictions and the paired held-out records
         independently with replacement. Keep their original sample sizes.
         Discard a draw if the correction cannot be computed. Clamp each
         retained estimate to [0, 1], then take the percentile interval.
         Raise ``ValueError`` if no replicate is valid.

    Args:
        sample_preds: the judge's 0/1 verdicts over the UNIFORM BASE sample
            only (never the risk strata; they are biased toward failure by
            design).
        test_labels: human labels for the frozen Homework 5 judge's test
            split.
        test_preds: the frozen judge's predictions on that test split.
        confidence: interval confidence level.
        bootstrap_iterations: number of percentile-bootstrap replicates.
        seed: numpy seed for a reproducible interval; None leaves the RNG
            untouched.

    Returns:
        {"raw", "corrected", "ci_low", "ci_high", "confidence",
         "failure_sensitivity", "pass_specificity", "n_sample"}
        with "corrected" clamped to [0, 1] and rates rounded to 4 places.

    Raises:
        ValueError: if an input is empty, the held-out inputs have different
            lengths, a value is not 0 or 1, a class is absent, the judge is
            missing a usable correction, or no bootstrap replicate is valid.
    """
    if not sample_preds:
        raise ValueError("sample_preds must be nonempty")
    if not test_labels or not test_preds:
        raise ValueError("test_labels and test_preds must be nonempty")
    if len(test_labels) != len(test_preds):
        raise ValueError("test_labels and test_preds must be the same length")
    for value in (*sample_preds, *test_labels, *test_preds):
        if value not in (0, 1):
            raise ValueError("every value must be 0 or 1")

    raw = sum(sample_preds) / len(sample_preds)
    point = _sensitivity_specificity(test_labels, test_preds)
    if point is None:
        raise ValueError("test split is missing a class needed for correction")
    sensitivity, specificity = point
    denom = sensitivity + specificity - 1
    if denom == 0:
        raise ValueError("sensitivity + specificity - 1 is zero; correction is undefined")
    corrected_point = (raw + specificity - 1) / denom

    rng = np.random.default_rng(seed)
    n_sample = len(sample_preds)
    n_test = len(test_labels)
    sample_arr = np.asarray(sample_preds)
    label_arr = np.asarray(test_labels)
    pred_arr = np.asarray(test_preds)

    replicates: list[float] = []
    for _ in range(bootstrap_iterations):
        sample_idx = rng.integers(0, n_sample, n_sample)
        test_idx = rng.integers(0, n_test, n_test)
        resampled_raw = float(sample_arr[sample_idx].mean())
        resampled = _sensitivity_specificity(
            label_arr[test_idx].tolist(), pred_arr[test_idx].tolist()
        )
        if resampled is None:
            continue
        rep_sensitivity, rep_specificity = resampled
        rep_denom = rep_sensitivity + rep_specificity - 1
        if rep_denom == 0:
            continue
        value = (resampled_raw + rep_specificity - 1) / rep_denom
        replicates.append(min(1.0, max(0.0, value)))

    if not replicates:
        raise ValueError("no valid bootstrap replicate; correction is undefined for this data")

    alpha = (1 - confidence) / 2
    ci_low = float(np.percentile(replicates, 100 * alpha))
    ci_high = float(np.percentile(replicates, 100 * (1 - alpha)))
    corrected = min(1.0, max(0.0, corrected_point))

    return {
        "raw": round(raw, 4),
        "corrected": round(corrected, 4),
        "ci_low": round(ci_low, 4),
        "ci_high": round(ci_high, 4),
        "confidence": confidence,
        "failure_sensitivity": round(sensitivity, 4),
        "pass_specificity": round(specificity, 4),
        "n_sample": n_sample,
    }
