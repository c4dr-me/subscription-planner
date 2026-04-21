import re
from html.parser import HTMLParser


class _HTMLStripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self._parts = []

    def handle_data(self, data):
        self._parts.append(data)

    def get_text(self):
        return " ".join(self._parts)


def sanitize_email(text):
    p = _HTMLStripper()
    p.feed(text)
    text = p.get_text()
    text = re.sub(r'http\S+', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:2000]


def extract_domain(sender):
    try:
        return sender.split("@")[-1].strip(">").strip()
    except Exception:
        return None


NOISE_SUBJECT_PATTERNS = [
    "security code", "verification code", "your otp", "one-time password",
    "confirm your email", "verify your email", "sign in attempt",
    "new sign-in", "login attempt", "unusual activity",
    "job alert", "jobs for you", "recommended jobs",
    "unsubscribe", "you have been unsubscribed",
    "weekly digest", "monthly newsletter", "we miss you",
]

NOISE_SENDER_DOMAINS = [
    "accounts.google.com", "security.google.com",
    "camsonline.com",
]

NOISE_SUBJECT_PATTERNS_EXTRA = [
    "portfolio", "half-yearly", "half yearly", "scheme portfolio",
    "statement of account", "account statement", "scheme performance",
    "newsletter", "investor service",
]


def _word_match(text, phrase):
    return bool(re.search(r'\b' + re.escape(phrase) + r'\b', text, re.IGNORECASE))


def is_noise_email(subject, sender=""):
    subject_lower = subject.lower().strip()
    sender_lower = sender.lower()
    if any(d in sender_lower for d in NOISE_SENDER_DOMAINS):
        return True
    if any(_word_match(subject_lower, p) for p in NOISE_SUBJECT_PATTERNS):
        return True
    if any(p in subject_lower for p in NOISE_SUBJECT_PATTERNS_EXTRA):
        return True
    return False


BILLING_KEYWORDS = [
    "receipt", "invoice", "payment confirmation", "subscription",
    "billing", "charged", "your plan", "trial ending", "renewal",
    "successful payment", "payment received", "payment processed",
    "order confirmation", "purchase confirmation", "you've been charged",
    "auto-renewal", "auto renewal", "next billing", "charge",
    "sip", "units allocated", "units purchased", "mutual fund",
    "folio", "nav", "debit", "amount debited", "installment",
    "emi", "mandate", "nach", "auto debit", "transaction successful",
]


def looks_like_billing_email(subject, body):
    combined = (subject + " " + body).lower()
    return any(k in combined for k in BILLING_KEYWORDS)
