from datetime import date, datetime
import urllib.request
import json

_RATES_CACHE = {}


def _fetch_rates():
    global _RATES_CACHE
    if _RATES_CACHE:
        return _RATES_CACHE
    try:
        url = "https://open.er-api.com/v6/latest/USD"
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read())
            _RATES_CACHE = data.get("rates", {})
            return _RATES_CACHE
    except Exception:
        return {}


def _to_inr(amount, currency, rates):
    if not amount or not rates:
        return 0.0
    try:
        amt = float(amount)
        currency = currency.upper().strip()
        if currency == "INR":
            return amt
        usd_rate = rates.get("INR", 84.0)
        from_rate = rates.get(currency, 1.0)
        return round(amt * (usd_rate / from_rate), 2)
    except Exception:
        return 0.0


def _safe_float(val):
    try:
        return float(str(val).replace(",", "").strip())
    except Exception:
        return 0.0


_CATEGORIES = {
    "Dev Tools":    ["github", "gitlab", "cursor", "copilot", "vercel", "netlify", "railway", "render",
                     "heroku", "supabase", "planetscale", "neon", "fly.io", "linear", "jira",
                     "sentry", "datadog", "postman", "retool", "clerk", "auth0"],
    "AI & LLM":     ["openai", "anthropic", "groq", "gemini", "mistral", "perplexity", "chatgpt",
                     "claude", "midjourney", "runway", "elevenlabs", "huggingface"],
    "Productivity": ["notion", "obsidian", "todoist", "clickup", "airtable", "monday", "asana",
                     "trello", "coda", "craft", "roam", "logseq", "calendar"],
    "Design":       ["figma", "canva", "framer", "sketch", "adobe", "invision", "zeplin", "lottie"],
    "Cloud":        ["aws", "amazon", "gcp", "google cloud", "azure", "cloudflare", "digitalocean"],
    "Entertainment":["netflix", "spotify", "youtube", "hotstar", "prime", "apple", "disney",
                     "audible", "kindle", "zee", "sonyliv"],
    "Storage":      ["dropbox", "google one", "icloud", "box", "mega", "backblaze", "pcloud"],
    "Communication":["slack", "zoom", "loom", "discord", "telegram", "notion ai", "intercom"],
}


def _categorise(merchant):
    m = merchant.lower()
    for category, keywords in _CATEGORIES.items():
        if any(k in m for k in keywords):
            return category
    return "Other"


def _spend_analysis(active, rates):
    category_totals = {}
    for s in active:
        cat = _categorise(s.get("Merchant", ""))
        inr = _to_inr(s.get("Amount", 0), s.get("Currency", "USD"), rates)
        category_totals[cat] = category_totals.get(cat, 0.0) + inr

    sorted_cats = sorted(category_totals.items(), key=lambda x: x[1], reverse=True)

    top_spenders = sorted(
        active,
        key=lambda s: _to_inr(s.get("Amount", 0), s.get("Currency", "USD"), rates),
        reverse=True
    )[:5]

    monthly_count = sum(1 for s in active if str(s.get("Billing_Period", "")).lower() == "monthly")
    annual_count = sum(1 for s in active if str(s.get("Billing_Period", "")).lower() == "annual")

    return {
        "category_totals": sorted_cats,
        "top_spenders": top_spenders,
        "monthly_count": monthly_count,
        "annual_count": annual_count,
    }


def build_digest(subscriptions):
    active = [s for s in subscriptions if str(s.get("Status", "")).strip() == "Active"]
    trials = [s for s in subscriptions if str(s.get("Status", "")).strip() == "Trial"]
    cancelled = [s for s in subscriptions if str(s.get("Status", "")).strip() == "Cancelled"]
    one_time = [s for s in subscriptions if str(s.get("Status", "")).strip() == "One-time"]

    rates = _fetch_rates()

    monthly_spend_inr = sum(
        _to_inr(s.get("Amount", 0), s.get("Currency", "USD"), rates)
        for s in active if str(s.get("Billing_Period", "")).lower() == "monthly"
    )
    annual_charges_inr = sum(
        _to_inr(s.get("Amount", 0), s.get("Currency", "USD"), rates)
        for s in active if str(s.get("Billing_Period", "")).lower() == "annual"
    )
    annual_projection_inr = (monthly_spend_inr * 12) + annual_charges_inr

    total_spend_inr = sum(
        _to_inr(s.get("Amount", 0), s.get("Currency", "USD"), rates)
        for s in active
    )

    monthly_spend = monthly_spend_inr
    annual_projection = annual_projection_inr

    trial_ending_soon = []
    today = date.today()
    for s in trials:
        renewal = s.get("Next_Renewal", "")
        if renewal:
            try:
                renewal_date = datetime.strptime(str(renewal).strip(), "%Y-%m-%d").date()
                days_left = (renewal_date - today).days
                if 0 <= days_left <= 7:
                    trial_ending_soon.append((s.get("Merchant", "Unknown"), renewal, days_left))
            except Exception:
                pass

    analysis = _spend_analysis(active, rates)

    return {
        "active": active,
        "trials": trials,
        "cancelled": cancelled,
        "one_time": one_time,
        "monthly_spend_inr": monthly_spend_inr,
        "annual_projection_inr": annual_projection_inr,
        "total_spend_inr": total_spend_inr,
        "monthly_spend": monthly_spend,
        "annual_projection": annual_projection,
        "rates": rates,
        "trial_ending_soon": trial_ending_soon,
        "total_subscriptions": len(active) + len(trials),
        "analysis": analysis,
    }


def _analysis_html(analysis, total_inr, rates):
    if not analysis or not analysis.get("category_totals"):
        return ""

    BAR_COLORS = [
        "#1a73e8", "#34a853", "#fbbc04", "#ea4335",
        "#9c27b0", "#00bcd4", "#ff5722", "#607d8b"
    ]

    cat_rows = ""
    for i, (cat, amt) in enumerate(analysis["category_totals"]):
        pct = (amt / total_inr * 100) if total_inr else 0
        bar_w = max(4, int(pct * 1.8))
        color = BAR_COLORS[i % len(BAR_COLORS)]
        cat_rows += f"""
        <tr>
            <td style="padding:5px 8px;font-size:13px;width:120px;">{cat}</td>
            <td style="padding:5px 8px;">
                <div style="background:{color};height:14px;width:{bar_w}px;border-radius:3px;display:inline-block;"></div>
            </td>
            <td style="padding:5px 8px;font-size:13px;color:#555;">₹{amt:,.0f}</td>
            <td style="padding:5px 8px;font-size:12px;color:#999;">{pct:.0f}%</td>
        </tr>"""

    top_rows = ""
    for s in analysis.get("top_spenders", []):
        inr = _to_inr(s.get("Amount", 0), s.get("Currency", "USD"), rates)
        cur = s.get("Currency", "USD")
        amt = s.get("Amount", "")
        orig = f"{cur} {amt}" if cur.upper() != "INR" else f"₹{float(amt):,.0f}"
        top_rows += (
            f"<tr><td style='padding:5px 8px;font-size:13px;'>{s.get('Merchant','')}</td>"
            f"<td style='padding:5px 8px;font-size:13px;color:#555;'>{orig}</td>"
            f"<td style='padding:5px 8px;font-size:13px;font-weight:bold;'>₹{inr:,.0f}</td></tr>"
        )

    mc = analysis.get("monthly_count", 0)
    ac = analysis.get("annual_count", 0)
    total_c = mc + ac or 1
    m_pct = int(mc / total_c * 100)
    a_pct = 100 - m_pct

    return f"""
    <div style="margin-top:28px;">
        <h3 style="color:#1a73e8;margin-bottom:8px;">📊 Spend Analysis</h3>

        <h4 style="color:#555;margin:16px 0 6px 0;font-size:13px;text-transform:uppercase;letter-spacing:0.5px;">
            By Category
        </h4>
        <table style="width:100%;border-collapse:collapse;">
            {cat_rows}
        </table>

        <h4 style="color:#555;margin:20px 0 6px 0;font-size:13px;text-transform:uppercase;letter-spacing:0.5px;">
            Top 5 by spend
        </h4>
        <table style="width:100%;border-collapse:collapse;font-size:14px;">
            <tr style="background:#f1f3f4;font-weight:bold;">
                <td style="padding:6px 8px;">Service</td>
                <td style="padding:6px 8px;">Original</td>
                <td style="padding:6px 8px;">INR</td>
            </tr>
            {top_rows}
        </table>

        <h4 style="color:#555;margin:20px 0 6px 0;font-size:13px;text-transform:uppercase;letter-spacing:0.5px;">
            Billing period split
        </h4>
        <div style="background:#f1f3f4;border-radius:6px;overflow:hidden;height:20px;width:100%;">
            <div style="background:#1a73e8;height:20px;width:{m_pct}%;display:inline-block;float:left;"></div>
            <div style="background:#34a853;height:20px;width:{a_pct}%;display:inline-block;float:left;"></div>
        </div>
        <div style="font-size:12px;color:#666;margin-top:4px;">
            <span style="color:#1a73e8;">■</span> Monthly: {mc} ({m_pct}%) &nbsp;
            <span style="color:#34a853;">■</span> Annual: {ac} ({a_pct}%)
        </div>
    </div>
    """


def build_digest_html(digest):
    today_str = date.today().strftime("%B %d, %Y")
    rates = digest.get("rates", {})

    def _inr_cell(s):
        amt = s.get('Amount', '')
        cur = s.get('Currency', 'USD')
        if not amt:
            return '—'
        inr = _to_inr(amt, cur, rates)
        if cur.upper() == 'INR':
            return f"₹{float(amt):,.0f}"
        return f"{cur} {amt} <span style='color:#888;font-size:12px;'>(₹{inr:,.0f})</span>"

    active_rows = "".join(
        f"<tr><td style='padding:6px 8px;'>{s.get('Merchant','')}</td>"
        f"<td style='padding:6px 8px;'>{s.get('Plan','')}</td>"
        f"<td style='padding:6px 8px;'>{_inr_cell(s)}</td>"
        f"<td style='padding:6px 8px;'>{s.get('Billing_Period','')}</td>"
        f"<td style='padding:6px 8px;'>{s.get('Next_Renewal','')}</td></tr>"
        for s in digest["active"]
    )

    trial_rows = "".join(
        f"<tr><td>{s.get('Merchant','')}</td><td>{s.get('Next_Renewal','')}</td></tr>"
        for s in digest["trials"]
    )

    cancelled_rows = "".join(
        f"<tr><td>{s.get('Merchant','')}</td><td>{s.get('Last_Charged','')}</td></tr>"
        for s in digest["cancelled"]
    )

    trial_alert = ""
    if digest["trial_ending_soon"]:
        items = "".join(
            f"<li><b>{m}</b> — expires {d} ({days} day{'s' if days != 1 else ''} left)</li>"
            for m, d, days in digest["trial_ending_soon"]
        )
        trial_alert = f"""
        <div style="background:#fff8e1;border-left:4px solid #f9a825;padding:12px 16px;margin:16px 0;border-radius:4px;">
            <b>⚠️ Trials ending this week:</b>
            <ul style="margin:8px 0 0 0;">{items}</ul>
        </div>
        """

    html = f"""
    <html><body style="font-family:Arial,sans-serif;color:#333;max-width:680px;margin:auto;padding:24px;">
        <h2 style="color:#1a73e8;">💳 Subscription Digest — {today_str}</h2>

        {trial_alert}

        <table style="width:100%;border-collapse:collapse;margin:8px 0 20px 0;">
            <tr>
                <td style="background:#f1f3f4;padding:12px;border-radius:8px;text-align:center;">
                    <div style="font-size:22px;font-weight:bold;">{digest['total_subscriptions']}</div>
                    <div style="color:#666;font-size:13px;">Active + Trial</div>
                </td>
                <td style="width:8px;"></td>
                <td style="background:#e8f5e9;padding:12px;border-radius:8px;text-align:center;">
                    <div style="font-size:22px;font-weight:bold;color:#2e7d32;">₹{digest['total_spend_inr']:,.0f}</div>
                    <div style="color:#666;font-size:13px;">Total charged (INR)</div>
                </td>
                <td style="width:8px;"></td>
                <td style="background:#f1f3f4;padding:12px;border-radius:8px;text-align:center;">
                    <div style="font-size:22px;font-weight:bold;">₹{digest['monthly_spend_inr']:,.0f}</div>
                    <div style="color:#666;font-size:13px;">Monthly recurring</div>
                </td>
                <td style="width:8px;"></td>
                <td style="background:#f1f3f4;padding:12px;border-radius:8px;text-align:center;">
                    <div style="font-size:22px;font-weight:bold;">₹{digest['annual_projection_inr']:,.0f}</div>
                    <div style="color:#666;font-size:13px;">Annual projection</div>
                </td>
            </tr>
        </table>

        <h3 style="color:#1a73e8;">✅ Active Subscriptions ({len(digest['active'])})</h3>
        <table style="width:100%;border-collapse:collapse;font-size:14px;">
            <tr style="background:#e8f0fe;font-weight:bold;">
                <td style="padding:8px;">Service</td>
                <td style="padding:8px;">Plan</td>
                <td style="padding:8px;">Amount</td>
                <td style="padding:8px;">Period</td>
                <td style="padding:8px;">Next Renewal</td>
            </tr>
            {active_rows if active_rows else '<tr><td colspan="5" style="padding:8px;color:#999;">None</td></tr>'}
        </table>

        {"<h3 style='color:#f9a825;margin-top:24px;'>🟡 Trials (" + str(len(digest['trials'])) + ")</h3><table style='width:100%;border-collapse:collapse;font-size:14px;'><tr style='background:#fff8e1;font-weight:bold;'><td style='padding:8px;'>Service</td><td style='padding:8px;'>Trial Ends</td></tr>" + trial_rows + "</table>" if digest['trials'] else ""}

        {"<h3 style='color:#d32f2f;margin-top:24px;'>❌ Cancelled (" + str(len(digest['cancelled'])) + ")</h3><table style='width:100%;border-collapse:collapse;font-size:14px;'><tr style='background:#ffebee;font-weight:bold;'><td style='padding:8px;'>Service</td><td style='padding:8px;'>Last Charged</td></tr>" + cancelled_rows + "</table>" if digest['cancelled'] else ""}

        {_analysis_html(digest.get('analysis', {}), digest.get('total_spend_inr', 0), rates)}

        <p style="color:#999;font-size:12px;margin-top:32px;">
            All amounts converted to INR at live exchange rates · Generated by Subscription Tracker Agent · {today_str}
        </p>
    </body></html>
    """
    return html
