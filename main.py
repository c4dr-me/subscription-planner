import re
import os
from datetime import datetime, date
from email.utils import parsedate_to_datetime
from dateutil.relativedelta import relativedelta
from dotenv import load_dotenv

load_dotenv()

if os.getenv("LANGSMITH_API_KEY"):
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGCHAIN_API_KEY"] = os.getenv("LANGSMITH_API_KEY")
    os.environ.setdefault("LANGCHAIN_PROJECT", os.getenv("LANGSMITH_PROJECT", "subscription-tracker"))
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry import trace
    from langsmith.integrations.otel import OtelSpanProcessor
    from openinference.instrumentation.crewai import CrewAIInstrumentor
    from openinference.instrumentation.openai import OpenAIInstrumentor
    _existing = trace.get_tracer_provider()
    if not isinstance(_existing, TracerProvider):
        _provider = TracerProvider()
        trace.set_tracer_provider(_provider)
    else:
        _provider = _existing
    _provider.add_span_processor(OtelSpanProcessor())
    CrewAIInstrumentor().instrument()
    OpenAIInstrumentor().instrument()

from services.gmail import authenticate_gmail, fetch_emails, send_email
from services.sheets import (
    connect_sheet, insert_row, update_merchant_row,
    find_merchant_row, get_all_subscriptions, sort_sheet_by_last_charged
)
from services.digest import build_digest, build_digest_html
from agents.classifier import classify_email, gemini_classify_email
from core.extractor import extract_billing_info, retry
import core.extractor as _extractor
from core.rule_engine import sanitize_email, extract_domain, is_noise_email, looks_like_billing_email
from core.state_manager import load_state, save_state, is_seen, mark_seen
from logs.logger import log_info, log_error, log_warning
from config import MY_EMAIL, DIGEST_RECIPIENT


def _parse_date(date_str):
    try:
        return parsedate_to_datetime(date_str).strftime("%Y-%m-%d")
    except Exception:
        return datetime.now().strftime("%Y-%m-%d")


def _calc_next_renewal(charged_date_str, billing_period):
    if not charged_date_str or billing_period in ("one-time", "unknown", ""):
        return ""
    try:
        from datetime import datetime
        d = datetime.strptime(charged_date_str, "%Y-%m-%d")
        if billing_period == "monthly":
            return (d + relativedelta(months=1)).strftime("%Y-%m-%d")
        elif billing_period == "annual":
            return (d + relativedelta(years=1)).strftime("%Y-%m-%d")
    except Exception:
        pass
    return ""


def _annual_projection(amount, billing_period):
    try:
        amt = float(amount) if amount else 0.0
    except Exception:
        return ""
    if billing_period == "monthly":
        return round(amt * 12, 2)
    elif billing_period == "annual":
        return round(amt, 2)
    return ""


def _status_from_label(label):
    mapping = {
        "Renewal": "Active",
        "Trial": "Trial",
        "Cancelled": "Cancelled",
        "One-time": "One-time",
        "Noise": None,
    }
    return mapping.get(label)


def process_email(email, sheet, state, stats):
    try:
        subject = email["subject"]
        body = email["body"]
        sender = email["from"]

        if MY_EMAIL and MY_EMAIL.lower() in sender.lower():
            log_info(f"Skipping self-sent email: {subject}")
            stats["skipped"] += 1
            return state

        if is_noise_email(subject, sender):
            log_info(f"Skipping noise email: {subject}")
            stats["skipped"] += 1
            return state

        clean_body = sanitize_email(body)

        if not looks_like_billing_email(subject, clean_body):
            log_info(f"Skipping — not a billing email: {subject}")
            stats["skipped"] += 1
            return state

        log_info(f"Processing: {subject} | From: {sender}")

        if _extractor._groq_rate_limited:
            try:
                label = gemini_classify_email(subject, clean_body)
            except Exception as e:
                log_warning(f"Gemini classify failed: {e} — skipping email")
                stats["skipped"] += 1
                return state
        else:
            label = retry(
                fn=lambda: classify_email(subject, clean_body),
                state=state,
                gemini_fn=lambda: gemini_classify_email(subject, clean_body)
            )
        if hasattr(label, 'raw'):
            label = label.raw.strip()
        label = str(label).strip() if label else "Renewal"

        for valid in ["Renewal", "Trial", "Cancelled", "One-time", "Noise"]:
            if valid.lower() in label.lower():
                label = valid
                break

        log_info(f"Classified as: {label}")

        status = _status_from_label(label)
        if status is None:
            log_info(f"Skipping Noise email: {subject}")
            stats["skipped"] += 1
            return state

        info = extract_billing_info(subject, clean_body, state)
        log_info(f"Extracted: {info}")

        charged_date = _parse_date(email.get("date", ""))

        merchant = info.get("merchant") or extract_domain(sender) or "Unknown"
        # Truncate merchant if it leaked email body (over 60 chars is a sign of this)
        if merchant and len(merchant) > 60:
            merchant = merchant[:60].rsplit(" ", 1)[0]
        amount = info.get("amount")
        currency = info.get("currency") or "USD"
        billing_period = info.get("billing_period") or "monthly"
        next_renewal = info.get("next_renewal") or ""
        plan_name = info.get("plan_name") or ""

        # SIP override: rule-based found SIP AMOUNT → always Active + monthly
        if info.get("_sip"):
            status = "Active"
            billing_period = "monthly"
            log_info("SIP detected via rule-based extractor — forcing Active/monthly")

        if not next_renewal:
            next_renewal = _calc_next_renewal(charged_date, billing_period)
        annual_proj = _annual_projection(amount, billing_period)

        data = {
            "first_seen": charged_date,
            "last_charged": charged_date,
            "merchant": merchant,
            "plan_name": plan_name,
            "amount": amount or "",
            "currency": currency,
            "billing_period": billing_period,
            "status": status,
            "annual_projection": annual_proj,
            "next_renewal": next_renewal,
            "email_source": sender,
        }

        existing_row = find_merchant_row(sheet, merchant)
        if existing_row:
            update_merchant_row(sheet, existing_row, data)
            log_info(f"✓ Updated existing row for {merchant} → {status}")
        else:
            insert_row(sheet, data)
            log_info(f"✓ Logged new subscription: {merchant} | {currency} {amount} | {billing_period} | {status}")

        state = mark_seen(state, email["id"])
        stats["logged"] += 1

    except Exception as e:
        log_error(f"Failed to process email '{email.get('subject', '')}': {e}")
        stats["failed"] += 1

    return state


def send_weekly_digest(service, sheet):
    try:
        subscriptions = get_all_subscriptions(sheet)
        if not subscriptions:
            log_info("No subscriptions found — skipping digest")
            return
        digest = build_digest(subscriptions)
        html = build_digest_html(digest)
        subject = f"💳 Subscription Digest — {datetime.now().strftime('%B %Y')}"
        send_email(service, DIGEST_RECIPIENT, subject, html)
        log_info(f"✓ Weekly digest sent to {DIGEST_RECIPIENT}")
    except Exception as e:
        log_error(f"Failed to send digest: {e}")


def main():
    import sys
    send_digest_only = "--digest" in sys.argv

    log_info("=== Subscription Agent run started ===")
    state = load_state()
    sheet = connect_sheet()
    service = authenticate_gmail("account1")

    if send_digest_only:
        send_weekly_digest(service, sheet)
        log_info("=== Digest sent — done ===")
        return

    emails = fetch_emails(service, max_results=50)
    log_info(f"Fetched {len(emails)} emails from Gmail")

    stats = {"logged": 0, "skipped": 0, "failed": 0}

    for email in emails:
        if is_seen(state, email["id"]):
            log_info(f"Skipping already-processed: {email['id']}")
            stats["skipped"] += 1
            continue
        state = process_email(email, sheet, state, stats)

    save_state(state)
    log_info(
        f"=== Run complete — logged: {stats['logged']} | "
        f"skipped: {stats['skipped']} | failed: {stats['failed']} ==="
    )
    if stats["logged"] > 0:
        sort_sheet_by_last_charged(sheet)
        log_info("Sheet sorted by Last_Charged (newest first)")

    if date.today().weekday() == 0:
        log_info("Monday detected — auto-sending weekly digest")
        send_weekly_digest(service, sheet)


if __name__ == "__main__":
    main()
