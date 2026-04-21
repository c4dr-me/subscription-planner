import os
from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
MY_EMAIL = os.getenv("MY_EMAIL", "")
DIGEST_RECIPIENT = os.getenv("DIGEST_RECIPIENT", "")

SHEET_NAME = "Subscription Tracker"
STATE_FILE = "state.json"
GEMINI_DAILY_LIMIT = 1400
