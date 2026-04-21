import os
import base64
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.modify',
    'https://www.googleapis.com/auth/gmail.send',
]

SUBSCRIPTION_QUERY = (
    "(label:purchases OR "
    "subject:(receipt OR invoice OR \"payment confirmation\" OR subscription "
    "OR billing OR charged OR \"your plan\" OR \"trial ending\" OR renewal "
    "OR \"successful payment\" OR \"payment received\" OR \"order confirmation\" "
    "OR \"purchase confirmation\" OR \"you've been charged\" OR \"auto-renewal\")) "
    "-subject:(\"job alert\" OR \"OTP\" OR \"verification code\" OR \"security code\" "
    "OR \"sign in\" OR \"login attempt\")"
)


def authenticate_gmail(account_name="account1"):
    token_file = f"token_{account_name}.json"
    creds = None
    if os.path.exists(token_file):
        creds = Credentials.from_authorized_user_file(token_file, SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(token_file, 'w') as f:
            f.write(creds.to_json())
    elif not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file('client_secret.json', SCOPES)
        creds = flow.run_local_server(port=0)
        with open(token_file, 'w') as f:
            f.write(creds.to_json())
    return build('gmail', 'v1', credentials=creds)


def get_email_body(payload):
    plain = _extract_part(payload, 'text/plain')
    if plain:
        return plain
    return _extract_part(payload, 'text/html') or ""


def _extract_part(payload, mime_type):
    if payload.get('mimeType') == mime_type:
        data = payload['body'].get('data')
        if data:
            return base64.urlsafe_b64decode(data).decode(errors='replace')
    for part in payload.get('parts', []):
        result = _extract_part(part, mime_type)
        if result:
            return result
    return None


def fetch_emails(service, max_results=50):
    results = service.users().messages().list(
        userId='me', maxResults=max_results, q=SUBSCRIPTION_QUERY
    ).execute()
    emails = []
    for msg in results.get('messages', []):
        msg_data = service.users().messages().get(userId='me', id=msg['id']).execute()
        headers = msg_data['payload']['headers']
        subject = next((h['value'] for h in headers if h['name'] == 'Subject'), "")
        sender = next((h['value'] for h in headers if h['name'] == 'From'), "")
        date_str = next((h['value'] for h in headers if h['name'] == 'Date'), "")
        body = get_email_body(msg_data['payload'])
        emails.append({
            "id": msg_data['id'],
            "subject": subject,
            "body": body,
            "from": sender,
            "date": date_str,
        })
    return emails


def send_email(service, to, subject, body_html):
    import base64
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    msg = MIMEMultipart('alternative')
    msg['To'] = to
    msg['Subject'] = subject
    msg.attach(MIMEText(body_html, 'html'))
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    service.users().messages().send(userId='me', body={'raw': raw}).execute()
