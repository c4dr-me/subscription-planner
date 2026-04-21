import json
import re
import time
import logging
import litellm

litellm.num_retries = 0

_groq_rate_limited = False
_gemini_disabled = False
_gemini_model = "gemini/gemini-3.1-flash-lite-preview"
_gemini_fallback_model = "gemini/gemini-2.5-flash"
_gemini_fallback_model_2 = "gemini/gemma-3-27b-it"
_gemini_use_fallback = False
_gemini_use_fallback_2 = False


def _is_daily_limit(err_str):
    err_l = err_str.lower()
    return "tokens per day" in err_l or "tpd" in err_l


def _parse_retry_after(err_str):
    m = re.search(r'try again in (\d+(?:\.\d+)?)s', err_str)
    if m:
        return min(float(m.group(1)) + 2, 65)
    return 20


def retry(fn, retries=3, state=None, gemini_fn=None):
    global _groq_rate_limited
    for attempt in range(retries):
        try:
            if _groq_rate_limited and gemini_fn and state:
                return _try_gemini(gemini_fn, state)
            return fn()
        except litellm.RateLimitError as e:
            err = str(e)
            logging.warning(f"Groq RateLimitError (attempt {attempt+1}): {e}")
            if _is_daily_limit(err):
                logging.warning("Groq daily token limit hit — switching to Gemini for rest of run")
                _groq_rate_limited = True
                if gemini_fn and state:
                    return _try_gemini(gemini_fn, state)
                return None
            wait = _parse_retry_after(err)
            logging.warning(f"Groq per-minute limit — waiting {wait}s then retrying")
            time.sleep(wait)
            if attempt == retries - 1:
                logging.warning("Groq exhausted after retries — falling back to Gemini")
                _groq_rate_limited = True
                if gemini_fn and state:
                    return _try_gemini(gemini_fn, state)
                return None
        except Exception as e:
            logging.warning(f"LLM call failed (attempt {attempt+1}): {e}")
            time.sleep(2)
    return None


def _try_gemini(gemini_fn, state):
    global _gemini_disabled, _gemini_use_fallback, _gemini_use_fallback_2
    from core.state_manager import gemini_quota_ok, increment_gemini_usage
    if _gemini_disabled:
        logging.warning("Gemini fallback disabled for this run")
        return None
    if not gemini_quota_ok(state):
        logging.error("Gemini daily quota exhausted")
        return None
    for attempt in range(3):
        try:
            time.sleep(6)
            result = gemini_fn()
            increment_gemini_usage(state)
            return result
        except litellm.RateLimitError as e:
            err = str(e)
            if "PerDay" in err or "per_day" in err.lower() or "requests per day" in err.lower():
                if not _gemini_use_fallback:
                    logging.warning(f"Gemini primary daily quota exhausted — switching to {_gemini_fallback_model}")
                    _gemini_use_fallback = True
                    return _try_gemini_fallback(gemini_fn, state)
                if not _gemini_use_fallback_2:
                    logging.warning(f"Gemini fallback daily quota exhausted — switching to {_gemini_fallback_model_2}")
                    _gemini_use_fallback_2 = True
                    return _try_gemini_fallback(gemini_fn, state)
                logging.error("All Gemini models daily quota exhausted — disabling for this run.")
                _gemini_disabled = True
                return None
            wait = _parse_retry_after(err)
            logging.warning(f"Gemini per-minute limit — waiting {wait}s (attempt {attempt+1})")
            time.sleep(wait)
        except litellm.ServiceUnavailableError:
            if not _gemini_use_fallback:
                logging.warning(f"Gemini primary model unavailable (503) — switching to {_gemini_fallback_model}")
                _gemini_use_fallback = True
                return _try_gemini_fallback(gemini_fn, state)
            if not _gemini_use_fallback_2:
                logging.warning(f"Gemini fallback unavailable (503) — switching to {_gemini_fallback_model_2}")
                _gemini_use_fallback_2 = True
                return _try_gemini_fallback(gemini_fn, state)
            logging.warning(f"All Gemini models unavailable (attempt {attempt+1}) — retrying in 10s")
            time.sleep(10)
        except Exception as e:
            logging.error(f"Gemini fallback failed: {e}")
            return None
    _gemini_disabled = True
    return None


def _try_gemini_fallback(gemini_fn, state):
    from core.state_manager import gemini_quota_ok, increment_gemini_usage
    if not gemini_quota_ok(state):
        return None
    try:
        time.sleep(6)
        result = gemini_fn()
        increment_gemini_usage(state)
        return result
    except Exception as e:
        logging.error(f"Gemini fallback model also failed: {e}")
        return None


def parse_extraction(resp):
    if resp is None:
        return {}
    try:
        raw = resp.raw if hasattr(resp, 'raw') else str(resp)
        raw = raw.strip()
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            return json.loads(match.group())
    except Exception:
        pass
    return {}


EXTRACT_PROMPT = """
You are a billing email parser. Extract subscription/payment details from this email.

Return ONLY valid JSON with these exact keys:
{{
  "merchant": "<service or company name, e.g. Vercel, Notion, GitHub, HDFC Mid Cap Fund or empty string if unknown>",
  "amount": <numeric amount as float, e.g. 20.0 or 499.98>,
  "currency": "<3-letter currency code: INR, USD, EUR or empty string if unknown>",
  "billing_period": "<monthly | annual | one-time or empty string if unknown>",
  "next_renewal": "<YYYY-MM-DD or null>",
  "plan_name": "<plan tier or fund name or null>"
}}

Rules:
- merchant: The product/service the user paid for. NOT the payment processor.
  - Stripe, PayPal, Razorpay, FastSpring, Groww, Google Play, Apple App Store, UPI, CAMS = NOT the merchant.
  - Look inside the body for the actual product/service/game/fund name.
- amount: Numeric only (e.g. 499.98). Strip currency symbols. Use empty string if no amount (e.g. cancelled).
  - Strip ₹, $, €, £ etc. e.g. ₹449.00 → 449.0
- currency: Detect from symbols: ₹/Rs/GST = INR, $ = USD, € = EUR.
  - For Indian services (Hotstar, JioCinema, Groww SIPs) default to INR.
  - For cancelled emails with no symbol, infer from the service's country.
- billing_period: monthly | annual | one-time.
  - SIP/auto-debit = monthly. Cancelled/trial emails keep the subscription's original period.
  - A single purchase = one-time.
- If you cannot determine a field, use empty string (not null).
- Do NOT add any explanation, only the JSON object.

Examples:

Subject: YouTube Premium subscription cancelled
Body: Your YouTube Premium subscription has been cancelled. No further billing.
Reasoning: The email states that the subscription has been cancelled and no further billing will occur, implying that the original billing period is still relevant. The merchant is YouTube Premium. No amount shown. Currency is INR for Indian services. Billing period is monthly.
Output: {{"merchant": "YouTube Premium", "amount": "", "currency": "INR", "billing_period": "monthly", "next_renewal": null, "plan_name": null}}

Subject: Your purchase of Gold Pass
Body: Thank you for your purchase. Product: Clash of Clans Gold Pass. Amount: ₹449.00. Order processed by FastSpring.
Reasoning: The email mentions a purchase of a product, and the product name is Clash of Clans Gold Pass. The payment processor is FastSpring, but the actual merchant is the provider of the product, which is Clash of Clans. The amount is specified as ₹449.00, indicating the currency is INR. Since it's a purchase, the billing period is one-time.
Output: {{"merchant": "Clash of Clans", "amount": 449.0, "currency": "INR", "billing_period": "one-time", "next_renewal": null, "plan_name": "Gold Pass"}}

Subject: Payment received
Body: ₹12 charged for your premium plan.
Reasoning: Payment received but no specific merchant named. Amount is ₹12, currency INR, billing period is monthly based on "plan".
Output: {{"merchant": "", "amount": 12.0, "currency": "INR", "billing_period": "monthly", "next_renewal": null, "plan_name": null}}

Subject: Your free trial is ending soon
Body: Your Notion Plus free trial ends soon. Then $16.00/month.
Reasoning: The merchant is Notion. The trial will convert to $16.00/month, so amount is 16.0, currency is USD, billing period is monthly.
Output: {{"merchant": "Notion", "amount": 16.0, "currency": "USD", "billing_period": "monthly", "next_renewal": null, "plan_name": "Plus"}}

Now extract from this email:

Subject: {subject}
Body: {body}
"""


def _run_extraction(agent_fn, subject, body):
    from crewai import Agent, Task, Crew
    agent = agent_fn()
    task = Task(
        description=EXTRACT_PROMPT.format(subject=subject, body=body),
        expected_output='Valid JSON object with keys: merchant, amount, currency, billing_period, next_renewal, plan_name',
        agent=agent
    )
    crew = Crew(agents=[agent], tasks=[task])
    return crew.kickoff()


def _groq_agent():
    from crewai import Agent
    return Agent(
        role="Billing Email Parser",
        goal="Extract merchant, amount, currency, billing period from billing emails",
        backstory="Expert financial data extractor specializing in SaaS billing emails",
        llm="groq/llama-3.3-70b-versatile",
        verbose=False
    )


def _gemini_agent():
    from crewai import Agent
    if _gemini_use_fallback_2:
        model = _gemini_fallback_model_2
    elif _gemini_use_fallback:
        model = _gemini_fallback_model
    else:
        model = _gemini_model
    return Agent(
        role="Billing Email Parser",
        goal="Extract merchant, amount, currency, billing period from billing emails",
        backstory="Expert financial data extractor specializing in SaaS billing emails",
        llm=model,
        verbose=False
    )


def _rule_based_extract(subject, body):
    """Fast regex extraction for well-structured Indian financial emails before hitting LLM."""
    combined = subject + " " + body

    result = {}

    # SIP / mutual fund: extract fund name and SIP amount
    sip_amount = re.search(r'SIP\s*AMOUNT\s*[:\-]?\s*[₹Rs.]*\s*([\d,]+(?:\.\d+)?)', combined, re.IGNORECASE)
    if sip_amount:
        result["amount"] = float(sip_amount.group(1).replace(",", ""))
        result["currency"] = "INR"
        result["billing_period"] = "monthly"
        result["_sip"] = True

    scheme = re.search(r'SCHEME\s*NAME\s*[:\-]?\s*\n?\s*([^\n]+)', combined, re.IGNORECASE)
    if scheme:
        result["merchant"] = scheme.group(1).strip()

    # Generic ₹ amount extractor — Total line
    if "amount" not in result:
        total = re.search(r'Total[:\s]*[₹Rs.]*\s*([\d,]+(?:\.\d+)?)', combined, re.IGNORECASE)
        if total:
            result["amount"] = float(total.group(1).replace(",", ""))
            result["currency"] = "INR"

    # Standalone ₹ amount if nothing found yet
    if "amount" not in result:
        inr = re.search(r'[₹]\s*([\d,]+(?:\.\d+)?)', combined)
        if inr:
            result["amount"] = float(inr.group(1).replace(",", ""))
            result["currency"] = "INR"

    return result if result else None


def extract_billing_info(subject, body, state):
    # Try fast rule-based extraction first
    rule_result = _rule_based_extract(subject, body)

    result = retry(
        fn=lambda: _run_extraction(_groq_agent, subject, body),
        state=state,
        gemini_fn=lambda: _run_extraction(_gemini_agent, subject, body)
    )
    llm_result = parse_extraction(result)

    # If all LLMs failed and rule_result has data, use it directly
    if not llm_result and rule_result:
        return {k: v for k, v in rule_result.items() if v is not None}

    # Merge: rule-based wins for fields it found (more reliable for structured emails)
    if rule_result:
        for key, val in rule_result.items():
            if val is None:
                continue
            existing = llm_result.get(key)
            # Always trust rule-based for billing_period and _sip flag
            if key in ("billing_period", "_sip"):
                llm_result[key] = val
            # Override if field is empty OR if merchant looks like a domain (e.g. groww.in)
            elif not existing or (key == "merchant" and isinstance(existing, str) and "." in existing and " " not in existing):
                llm_result[key] = val

    return llm_result
