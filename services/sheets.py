import gspread
from config import SHEET_NAME

HEADERS = [
    "First_Seen", "Last_Charged", "Merchant", "Plan",
    "Amount", "Currency", "Billing_Period", "Status",
    "Annual_Projection", "Next_Renewal", "Email_Source"
]

STATUS_COLORS = {
    "Active":    {"red": 0.6,  "green": 1.0,  "blue": 0.6},
    "Trial":     {"red": 1.0,  "green": 0.95, "blue": 0.6},
    "Cancelled": {"red": 1.0,  "green": 0.6,  "blue": 0.6},
    "One-time":  {"red": 0.78, "green": 0.89, "blue": 1.0},
}

STATUS_COL_INDEX = HEADERS.index("Status")


def _setup_conditional_formatting(spreadsheet, sheet):
    sheet_id = sheet._properties["sheetId"]
    existing = spreadsheet.fetch_sheet_metadata()
    delete_requests = []
    for s in existing.get("sheets", []):
        if s["properties"]["sheetId"] == sheet_id:
            rules = s.get("conditionalFormats", [])
            for i in range(len(rules) - 1, -1, -1):
                delete_requests.append({
                    "deleteConditionalFormatRule": {"sheetId": sheet_id, "index": i}
                })
    add_requests = []
    for status, color in STATUS_COLORS.items():
        add_requests.append({
            "addConditionalFormatRule": {
                "rule": {
                    "ranges": [{
                        "sheetId": sheet_id,
                        "startRowIndex": 1,
                        "startColumnIndex": STATUS_COL_INDEX,
                        "endColumnIndex": STATUS_COL_INDEX + 1
                    }],
                    "booleanRule": {
                        "condition": {
                            "type": "TEXT_EQ",
                            "values": [{"userEnteredValue": status}]
                        },
                        "format": {"backgroundColor": color}
                    }
                },
                "index": 0
            }
        })
    if delete_requests:
        spreadsheet.batch_update({"requests": delete_requests})
    if add_requests:
        spreadsheet.batch_update({"requests": add_requests})


def _setup_status_dropdown(spreadsheet, sheet):
    sheet_id = sheet._properties["sheetId"]
    requests = [{
        "setDataValidation": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": 1,
                "startColumnIndex": STATUS_COL_INDEX,
                "endColumnIndex": STATUS_COL_INDEX + 1
            },
            "rule": {
                "condition": {
                    "type": "ONE_OF_LIST",
                    "values": [{"userEnteredValue": s} for s in STATUS_COLORS]
                },
                "showCustomUi": True,
                "strict": False
            }
        }
    }]
    spreadsheet.batch_update({"requests": requests})


def connect_sheet():
    gc = gspread.service_account(filename="credentials.json")
    spreadsheet = gc.open(SHEET_NAME)
    sheet = spreadsheet.sheet1
    if not sheet.row_values(1):
        sheet.insert_row(HEADERS, 1)
    _setup_status_dropdown(spreadsheet, sheet)
    _setup_conditional_formatting(spreadsheet, sheet)
    return sheet


def apply_status_color(sheet, row_index, status):
    color = STATUS_COLORS.get(status)
    if not color:
        return
    sheet_id = sheet._properties["sheetId"]
    sheet.spreadsheet.batch_update({"requests": [{
        "repeatCell": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": row_index - 1,
                "endRowIndex": row_index,
                "startColumnIndex": STATUS_COL_INDEX,
                "endColumnIndex": STATUS_COL_INDEX + 1
            },
            "cell": {"userEnteredFormat": {"backgroundColor": color}},
            "fields": "userEnteredFormat.backgroundColor"
        }
    }]})


def _merchant_key(name):
    """Normalize merchant to first 4 words lowercase for fuzzy matching."""
    words = (name or "").strip().lower().split()
    return " ".join(words[:4])


def find_merchant_row(sheet, merchant):
    merchant_col = HEADERS.index("Merchant") + 1
    target_key = _merchant_key(merchant)
    for i, row in enumerate(sheet.get_all_values()[1:], start=2):
        row_merchant = row[merchant_col - 1] if len(row) >= merchant_col else ""
        # Exact match first
        if row_merchant.strip().lower() == (merchant or "").strip().lower():
            return i
        # Fuzzy match on first 4 words
        if target_key and _merchant_key(row_merchant) == target_key:
            return i
    return None


def update_merchant_row(sheet, row_index, data):
    last_charged_col = HEADERS.index("Last_Charged") + 1
    amount_col = HEADERS.index("Amount") + 1
    status_col = HEADERS.index("Status") + 1
    next_renewal_col = HEADERS.index("Next_Renewal") + 1
    annual_col = HEADERS.index("Annual_Projection") + 1
    plan_col = HEADERS.index("Plan") + 1

    sheet.update_cell(row_index, last_charged_col, data.get("last_charged", ""))
    sheet.update_cell(row_index, amount_col, data.get("amount", ""))
    sheet.update_cell(row_index, status_col, data.get("status", "Active"))
    sheet.update_cell(row_index, next_renewal_col, data.get("next_renewal", ""))
    sheet.update_cell(row_index, annual_col, data.get("annual_projection", ""))
    if data.get("plan_name"):
        sheet.update_cell(row_index, plan_col, data["plan_name"])
    apply_status_color(sheet, row_index, data.get("status", "Active"))


def insert_row(sheet, data):
    sheet.insert_row([
        data.get("first_seen", ""),
        data.get("last_charged", ""),
        data.get("merchant", ""),
        data.get("plan_name", ""),
        data.get("amount", ""),
        data.get("currency", ""),
        data.get("billing_period", ""),
        data.get("status", "Active"),
        data.get("annual_projection", ""),
        data.get("next_renewal", ""),
        data.get("email_source", ""),
    ], index=2)
    apply_status_color(sheet, 2, data.get("status", "Active"))


def get_all_subscriptions(sheet):
    rows = sheet.get_all_records()
    return rows


def sort_sheet_by_last_charged(sheet):
    """Sort all data rows by Last_Charged descending (newest first)."""
    all_rows = sheet.get_all_values()
    if len(all_rows) <= 2:
        return
    header = all_rows[0]
    data_rows = all_rows[1:]
    last_charged_idx = header.index("Last_Charged")

    def sort_key(row):
        val = row[last_charged_idx] if len(row) > last_charged_idx else ""
        return val or "0000-00-00"

    data_rows.sort(key=sort_key, reverse=True)

    # Clear data rows and rewrite
    end_row = len(all_rows)
    sheet.delete_rows(2, end_row)
    if data_rows:
        sheet.append_rows(data_rows, value_input_option="USER_ENTERED")
