"""
DSPy prompt optimization using BootstrapFewShotWithRandomSearch.
Groq Dev Tier (6M TPD) — full optimization with llama-3.3-70b.

Run: uv run python eval/optimize_dspy.py
"""
import json
import os
import sys
import random

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

import logging
logging.getLogger("LiteLLM").setLevel(logging.CRITICAL)

import litellm
litellm.cache = None
litellm.suppress_debug_info = True
litellm.num_retries = 5
litellm.retry_after = 30

import dspy

# --- Configure DSPy LM ---
lm = dspy.LM(
    model="groq/llama-3.3-70b-versatile",
    api_key=os.environ.get("GROQ_API_KEY"),
    max_tokens=512,
    temperature=0.0,
    num_retries=5,
    request_timeout=60,
)
dspy.configure(lm=lm, experimental=True)

# --- Load dataset ---
CASES_FILE = os.path.join(os.path.dirname(__file__), "test_cases.json")
with open(CASES_FILE, encoding="utf-8") as f:
    raw_cases = json.load(f)

all_examples = [
    dspy.Example(
        subject=c["subject"],
        body=c["body"],
        merchant=str(c["expected"]["merchant"] or ""),
        amount=str(c["expected"]["amount"] or ""),
        currency=str(c["expected"]["currency"] or ""),
        billing_period=str(c["expected"]["billing_period"] or ""),
    ).with_inputs("subject", "body")
    for c in raw_cases
]

# --- Stratified train/dev split (ensures both sets have all edge case types) ---
random.seed(42)
buckets = {"active": [], "cancelled": [], "one-time": [], "null_merchant": [], "sip": []}
for i, c in enumerate(raw_cases):
    exp = c["expected"]
    if exp.get("merchant") is None:
        buckets["null_merchant"].append(i)
    elif "sip" in c["body"].lower() or "units allocated" in c["body"].lower():
        buckets["sip"].append(i)
    elif exp.get("status") == "Cancelled":
        buckets["cancelled"].append(i)
    elif exp.get("billing_period") == "one-time":
        buckets["one-time"].append(i)
    else:
        buckets["active"].append(i)

train_idx, dev_idx = [], []
for name, indices in buckets.items():
    random.shuffle(indices)
    # 75/25 split per bucket
    split = max(1, int(len(indices) * 0.75))
    train_idx.extend(indices[:split])
    dev_idx.extend(indices[split:])

trainset = [all_examples[i] for i in sorted(train_idx)]
devset = [all_examples[i] for i in sorted(dev_idx)]

print(f"Dataset: {len(trainset)} train, {len(devset)} dev (stratified split)")
for name, indices in buckets.items():
    in_train = sum(1 for i in indices if i in set(train_idx))
    in_dev = sum(1 for i in indices if i in set(dev_idx))
    print(f"  {name}: {in_train} train / {in_dev} dev")

# --- DSPy Signature ---
class BillingExtraction(dspy.Signature):
    """Extract billing details from a subscription or payment email.

    Rules:
    - merchant: The product/service the user paid for. NOT the payment processor.
      Stripe, PayPal, Razorpay, FastSpring, Groww, Google Play = NOT the merchant.
      Look inside the email for the actual product/service name.
    - amount: Numeric only (e.g. 499.98). Strip currency symbols. Use empty string if no amount.
    - currency: Detect from symbols: ₹/Rs/GST = INR, $ = USD, € = EUR.
      For Indian services (Hotstar, JioCinema, Groww SIPs) default to INR.
      For cancelled emails with no symbol, infer from the service's country.
    - billing_period: monthly | annual | one-time.
      SIP/auto-debit = monthly. Cancelled/trial emails keep the subscription's original period.
      A single purchase = one-time."""

    subject: str = dspy.InputField(desc="Email subject line")
    body: str = dspy.InputField(desc="Email body text")
    merchant: str = dspy.OutputField(desc="Product/service name (e.g. Netflix, HDFC Mid Cap Fund). Empty string if truly ambiguous.")
    amount: str = dspy.OutputField(desc="Numeric amount as string (e.g. '499.98'). Empty string if cancelled/unknown.")
    currency: str = dspy.OutputField(desc="3-letter code: INR, USD, EUR. Empty string only if completely unknown.")
    billing_period: str = dspy.OutputField(desc="monthly, annual, or one-time. Empty string only if completely unknown.")


# --- DSPy Module ---
class BillingExtractor(dspy.Module):
    def __init__(self):
        self.extract = dspy.ChainOfThought(BillingExtraction)

    def forward(self, subject, body):
        return self.extract(subject=subject, body=body)


# --- Metric (lenient on edge cases, strict on core fields) ---
def extraction_metric(example, pred, trace=None):
    score = 0
    total = 4

    # Merchant: first 3 words match (handles "GitHub Copilot" vs "GitHub Copilot Individual")
    exp_m = str(example.merchant or "").strip().lower()
    got_m = str(pred.merchant or "").strip().lower()
    if exp_m == "" and got_m in ("", "unknown", "none", "n/a"):
        score += 1
    elif exp_m and got_m:
        # Check if first 2 words match (handles minor suffix differences)
        if exp_m.split()[:2] == got_m.split()[:2]:
            score += 1
        # Also accept if expected is contained in got or vice versa
        elif exp_m in got_m or got_m in exp_m:
            score += 1

    # Amount: within 0.01
    try:
        exp_a = float(example.amount) if example.amount else 0
        got_a = float(pred.amount) if pred.amount else 0
        if abs(got_a - exp_a) < 0.01:
            score += 1
    except Exception:
        if not example.amount and not pred.amount:
            score += 1  # both empty = correct for cancelled

    # Currency: exact match
    exp_c = str(example.currency or "").strip().upper()
    got_c = str(pred.currency or "").strip().upper()
    if exp_c == got_c:
        score += 1
    elif not exp_c and not got_c:
        score += 1

    # Billing period: exact match
    exp_bp = str(example.billing_period or "").strip().lower()
    got_bp = str(pred.billing_period or "").strip().lower()
    if exp_bp == got_bp:
        score += 1

    return score / total


# --- Optimize ---
print("\nRunning BootstrapFewShotWithRandomSearch...")
print(f"Groq Dev Tier: 6M TPD — full optimization with 70b\n")
from dspy.teleprompt import BootstrapFewShotWithRandomSearch

config = dict(
    max_bootstrapped_demos=4,
    max_labeled_demos=4,
    num_candidate_programs=10,
    num_threads=4,   # parallel threads — Dev Tier can handle it
)

optimizer = BootstrapFewShotWithRandomSearch(metric=extraction_metric, **config)
optimized = optimizer.compile(BillingExtractor(), trainset=trainset, valset=devset)

# --- Final evaluation on full dataset ---
print("\nEvaluating optimized program on dev set...")
scores = []
details = []
for ex in devset:
    try:
        pred = optimized(subject=ex.subject, body=ex.body)
        s = extraction_metric(ex, pred)
        scores.append(s)
        if s < 1.0:
            details.append(f"  {ex.subject[:50]}: {s*100:.0f}% | m={pred.merchant!r} a={pred.amount!r} c={pred.currency!r} bp={pred.billing_period!r}")
    except Exception as e:
        print(f"  Error: {e}")
        scores.append(0)

avg = sum(scores) / len(scores) * 100
print(f"\n{'='*60}")
print(f"  OPTIMIZED ACCURACY: {avg:.1f}%")
print(f"{'='*60}")

if details:
    print(f"\nPartial matches ({len(details)}):")
    for d in details:
        print(d)

# --- Save ---
save_path = os.path.join(os.path.dirname(__file__), "optimized_extractor.json")
optimized.save(save_path)
print(f"\nSaved to eval/optimized_extractor.json")
print("Next: extract few-shot demos from this file into core/extractor.py")
