from datetime import datetime
import json
from pathlib import Path
import statistics
import sys
import time

# Ensure project root is in sys.path for direct script execution
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deadline_normalizer import normalize_deadline
from email_analyzer import analyze_email
from llm_client import OllamaClient


def normalized_text(value: str) -> str:
    return " ".join(value.strip().lower().split())


def calculate_metrics(results: list[dict]) -> dict:
    total = len(results)
    if total == 0:
        raise ValueError("Benchmark contains no cases")

    successful = [r for r in results if r["prediction"] is not None]
    failures = total - len(successful)

    category_correct = sum(
        r["prediction"]["category"] == r["expected"]["category"]
        for r in successful
    )
    category_accuracy = category_correct / total

    intent_cases = [r for r in successful if "intent" in r["expected"]]
    intent_correct = sum(
        r["prediction"].get("intent") == r["expected"]["intent"]
        for r in intent_cases
    )
    intent_accuracy = intent_correct / len(intent_cases) if intent_cases else 1.0

    importance_cases = [r for r in successful if "importance" in r["expected"]]
    importance_correct = sum(
        r["prediction"].get("importance") == r["expected"]["importance"]
        for r in importance_cases
    )
    importance_accuracy = importance_correct / len(importance_cases) if importance_cases else 1.0

    urgency_correct = sum(
        r["prediction"]["urgency"] == r["expected"]["urgency"]
        for r in successful
    )
    urgency_accuracy = urgency_correct / total

    # Treat failed analyses as not predicting an actionable email.
    tp = sum(
        r["prediction"] is not None
        and r["prediction"]["requires_action"] is True
        and r["expected"]["requires_action"] is True
        for r in results
    )
    fp = sum(
        r["prediction"] is not None
        and r["prediction"]["requires_action"] is True
        and r["expected"]["requires_action"] is False
        for r in results
    )
    fn = sum(
        (
            r["prediction"] is None
            or r["prediction"]["requires_action"] is False
        )
        and r["expected"]["requires_action"] is True
        for r in results
    )

    action_precision = tp / (tp + fp) if tp + fp else 0.0
    action_recall = tp / (tp + fn) if tp + fn else 0.0

    # Promotional Noise False Positive Rate
    promo_cases = [
        r for r in results
        if r["expected"].get("intent") in ("promotional", "newsletter")
        or r["expected"].get("spam_status") in ("suspected_spam", "confirmed_spam")
    ]
    promo_fp = sum(
        r["prediction"] is not None
        and (
            r["prediction"]["requires_action"] is True
            or r["prediction"].get("importance") in ("high", "critical")
            or r["prediction"].get("spam_status") == "not_spam"
        )
        for r in promo_cases
    )
    promotional_false_positive_rate = promo_fp / len(promo_cases) if promo_cases else 0.0

    # Important Email Recall (Interviews & Competitions)
    important_cases = [
        r for r in results
        if r["expected"].get("importance") in ("high", "critical")
        or r["expected"].get("intent") in ("interview_invitation", "interview_scheduling", "competition", "hackathon")
    ]
    important_retrieved = sum(
        r["prediction"] is not None
        and r["prediction"].get("importance") in ("high", "critical")
        and r["prediction"].get("spam_status") == "not_spam"
        for r in important_cases
    )
    important_email_recall = important_retrieved / len(important_cases) if important_cases else 1.0

    deadline_presence_correct = sum(
        r["prediction"] is not None
        and (
            r["prediction"]["deadline"] is None
        ) == (
            r["expected"].get("deadline") is None
        )
        for r in results
    )
    deadline_cases = [
        r for r in results
        if r["expected"].get("deadline") is not None
    ]
    deadline_text_correct = sum(
        r["prediction"] is not None
        and r["prediction"]["deadline"] is not None
        and normalized_text(r["prediction"]["deadline"])
        == normalized_text(r["expected"]["deadline"])
        for r in deadline_cases
    )

    # Normalized datetime evaluation
    norm_coverage_count = 0
    norm_accuracy_count = 0
    for r in deadline_cases:
        received_at = datetime.fromisoformat(r["received_at"]) if r.get("received_at") else None
        if received_at is None:
            continue
        expected_dt = normalize_deadline(r["expected"]["deadline"], received_at)
        pred_raw = r["prediction"]["deadline"] if r["prediction"] else None
        pred_dt = normalize_deadline(pred_raw, received_at) if pred_raw else None

        if pred_dt is not None:
            norm_coverage_count += 1
            if expected_dt is not None and pred_dt == expected_dt:
                norm_accuracy_count += 1

    deadline_normalization_coverage = (
        norm_coverage_count / len(deadline_cases) if deadline_cases else 0.0
    )
    deadline_normalization_accuracy = (
        norm_accuracy_count / len(deadline_cases) if deadline_cases else 0.0
    )

    latencies = [r["latency_seconds"] for r in results]

    return {
        "total_cases": total,
        "successful_analyses": len(successful),
        "failed_analyses": failures,
        "schema_or_analysis_failure_rate": failures / total,
        "intent_accuracy": intent_accuracy,
        "importance_accuracy": importance_accuracy,
        "category_accuracy": category_accuracy,
        "urgency_accuracy": urgency_accuracy,
        "action_precision": action_precision,
        "action_recall": action_recall,
        "promotional_false_positive_rate": promotional_false_positive_rate,
        "important_email_recall": important_email_recall,
        "deadline_presence_accuracy": deadline_presence_correct / total,
        "deadline_normalization_coverage": deadline_normalization_coverage,
        "deadline_normalization_accuracy": deadline_normalization_accuracy,
        "deadline_text_match": (
            deadline_text_correct / len(deadline_cases) if deadline_cases else 0.0
        ),
        "median_latency_seconds": statistics.median(latencies) if latencies else 0.0,
        "max_latency_seconds": max(latencies) if latencies else 0.0,
        "min_latency_seconds": min(latencies) if latencies else 0.0,
    }


def main() -> None:
    cases_path = Path(__file__).parent / "cases.json"
    results_path = Path(__file__).parent / "results.json"

    with open(cases_path, "r", encoding="utf-8") as f:
        cases = json.load(f)

    # Use a generous timeout for local 30B model execution
    client = OllamaClient(timeout_seconds=300.0)
    results = []

    print(f"Running benchmark on {len(cases)} cases...")
    for index, case in enumerate(cases, start=1):
        case_id = case["id"]
        print(f"[{index}/{len(cases)}] Evaluating {case_id}: {case['scenario']}...")
        start_time = time.perf_counter()
        prediction = None
        error = None

        received_at = None
        if case.get("received_at"):
            received_at = datetime.fromisoformat(case["received_at"])

        try:
            analysis = analyze_email(
                subject=case["subject"],
                body=case["body"],
                client=client,
                received_at=received_at,
            )
            prediction = analysis.model_dump()
        except Exception as exc:
            error = str(exc)
            print(f"  FAILED: {error}")

        elapsed = time.perf_counter() - start_time
        print(f"  Finished in {elapsed:.2f}s")

        results.append({
            "id": case_id,
            "scenario": case["scenario"],
            "received_at": case.get("received_at"),
            "expected": case["expected"],
            "prediction": prediction,
            "error": error,
            "latency_seconds": elapsed,
        })

    metrics = calculate_metrics(results)

    output = {
        "metrics": metrics,
        "results": results,
    }

    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    print("\n=== BENCHMARK SUMMARY ===")
    print(f"Total Cases:                     {metrics['total_cases']}")
    print(f"Successful Analyses:             {metrics['successful_analyses']}")
    print(f"Failed Analyses:                 {metrics['failed_analyses']}")
    print(f"Intent Accuracy:                 {metrics['intent_accuracy']:.1%}")
    print(f"Importance Accuracy:             {metrics['importance_accuracy']:.1%}")
    print(f"Category Accuracy:               {metrics['category_accuracy']:.1%}")
    print(f"Urgency Accuracy:                {metrics['urgency_accuracy']:.1%}")
    print(f"Action Precision:                {metrics['action_precision']:.1%}")
    print(f"Action Recall:                   {metrics['action_recall']:.1%}")
    print(f"Promotional False Positive Rate: {metrics['promotional_false_positive_rate']:.1%}")
    print(f"Important Email Recall:          {metrics['important_email_recall']:.1%}")
    print(f"Deadline Presence Accuracy:      {metrics['deadline_presence_accuracy']:.1%}")
    print(f"Deadline Normalization Accuracy: {metrics['deadline_normalization_accuracy']:.1%}")
    print(f"Latency (Median / Min / Max):    {metrics['median_latency_seconds']:.2f}s / {metrics['min_latency_seconds']:.2f}s / {metrics['max_latency_seconds']:.2f}s")
    print(f"\nDetailed results written to: {results_path}")


if __name__ == "__main__":
    main()
