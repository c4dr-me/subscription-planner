"""
Extraction eval — runs each test case through extract_billing_info and
scores merchant, amount, currency, billing_period correctness.
Run: uv run python eval/run_eval.py
"""
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

from core.extractor import extract_billing_info
from core.state_manager import load_state

CASES_FILE = os.path.join(os.path.dirname(__file__), "test_cases.json")

FIELDS = ["merchant", "amount", "currency", "billing_period"]


def _merchant_match(got, expected):
    if not got or not expected:
        return False
    got_words = got.strip().lower().split()[:4]
    exp_words = expected.strip().lower().split()[:4]
    return got_words == exp_words


def _score(got, expected):
    results = {}
    for field in FIELDS:
        exp_val = expected.get(field)
        got_val = got.get(field)
        if field == "merchant":
            results[field] = _merchant_match(str(got_val or ""), str(exp_val or ""))
        elif field == "amount":
            try:
                results[field] = abs(float(got_val or 0) - float(exp_val or 0)) < 0.01
            except Exception:
                results[field] = False
        else:
            results[field] = str(got_val or "").strip().lower() == str(exp_val or "").strip().lower()
    return results


def run():
    with open(CASES_FILE) as f:
        cases = json.load(f)

    state = load_state()
    totals = {f: 0 for f in FIELDS}
    results = []

    print(f"\n{'='*70}")
    print(f"{'ID':<10} {'Merchant':^6} {'Amount':^6} {'Currency':^8} {'Period':^8}  Expected Merchant → Got")
    print(f"{'='*70}")

    for case in cases:
        extracted = extract_billing_info(case["subject"], case["body"], state)
        scores = _score(extracted, case["expected"])

        for f in FIELDS:
            if scores[f]:
                totals[f] += 1

        row = {
            "id": case["id"],
            "scores": scores,
            "expected_merchant": case["expected"]["merchant"],
            "got_merchant": extracted.get("merchant", ""),
            "expected_amount": case["expected"]["amount"],
            "got_amount": extracted.get("amount", ""),
        }
        results.append(row)

        def tick(v): return "✓" if v else "✗"
        print(
            f"{case['id']:<10} "
            f"{tick(scores['merchant']):^6} "
            f"{tick(scores['amount']):^6} "
            f"{tick(scores['currency']):^8} "
            f"{tick(scores['billing_period']):^8}  "
            f"{row['expected_merchant']} → {row['got_merchant']}"
        )

    n = len(cases)
    print(f"\n{'='*70}")
    print("ACCURACY SUMMARY")
    print(f"{'='*70}")
    for f in FIELDS:
        pct = totals[f] / n * 100
        bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
        print(f"  {f:<16} {bar}  {totals[f]}/{n}  ({pct:.0f}%)")

    overall = sum(totals.values()) / (n * len(FIELDS)) * 100
    print(f"\n  Overall accuracy: {overall:.1f}%")
    print(f"{'='*70}\n")

    out_file = os.path.join(os.path.dirname(__file__), "results.json")
    with open(out_file, "w") as f:
        json.dump({"totals": totals, "n": n, "overall_pct": round(overall, 1), "cases": results}, f, indent=2)
    print(f"Results saved to eval/results.json")


if __name__ == "__main__":
    run()
