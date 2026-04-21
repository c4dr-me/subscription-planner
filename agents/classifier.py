from crewai import Agent, Task, Crew
import core.extractor as _extractor

_GROQ_LLM = "groq/llama-3.3-70b-versatile"
_GEMINI_LLM = "gemini/gemini-3.1-flash-lite-preview"
_GEMINI_FALLBACK_LLM = "gemini/gemma-3-27b-it"

CLASSIFY_PROMPT = """
Classify this billing/subscription email into exactly ONE category.

Categories:
- Renewal     : recurring subscription was charged (monthly or annual payment processed)
- Trial       : free trial started, trial ending soon, or trial expired
- Cancelled   : subscription was cancelled or will not renew
- One-time    : single one-off purchase, not a recurring subscription
- Noise       : promotional offer, marketing, discount offer, unrelated email

Return ONLY the single word category. Nothing else.

Subject: {subject}
Body: {body}
"""


def _make_agent(llm):
    return Agent(
        role="Subscription Email Classifier",
        goal="Classify billing emails into Renewal, Trial, Cancelled, One-time, or Noise",
        backstory="Expert at reading SaaS billing and subscription emails",
        llm=llm,
        verbose=False
    )


def _classify(llm, subject, body):
    agent = _make_agent(llm)
    task = Task(
        description=CLASSIFY_PROMPT.format(subject=subject, body=body),
        expected_output="One of: Renewal, Trial, Cancelled, One-time, Noise",
        agent=agent
    )
    crew = Crew(agents=[agent], tasks=[task])
    result = crew.kickoff()
    raw = (result.raw if hasattr(result, 'raw') else str(result)).strip()
    for label in ["Renewal", "Trial", "Cancelled", "One-time", "Noise"]:
        if label.lower() in raw.lower():
            return label
    return "Renewal"


def classify_email(subject, body):
    return _classify(_GROQ_LLM, subject, body)


def gemini_classify_email(subject, body):
    llm = _GEMINI_FALLBACK_LLM if _extractor._gemini_use_fallback else _GEMINI_LLM
    return _classify(llm, subject, body)
