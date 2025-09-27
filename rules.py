# rules.py
from datetime import date, datetime

def can_cancel(order: dict) -> bool:
    return order and order.get("status") == "Processing"

def is_return_eligible(order: dict, today: date | None = None) -> bool:
    if not order:
        return False
    if order.get("status") not in {"Delivered", "Return Initiated"}:
        return False
    due = order.get("return_eligible_until")
    if not due:
        return False
    today = today or date.today()
    try:
        due_date = datetime.fromisoformat(due).date()
        return today <= due_date
    except Exception:
        return False

def can_change_address(order: dict) -> bool:
    # Only allow before shipment (Processing)
    return order and order.get("status") == "Processing"
