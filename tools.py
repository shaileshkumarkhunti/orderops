# tools.py
import re
from typing import List, Tuple, Optional
from datetime import date, datetime
from state_store import Store

ORDER_ID_RE = re.compile(r"\bORD\d{5}\b", re.IGNORECASE)
EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
PHONE_RE = re.compile(r"\b\d{10}\b")

def extract_order_id(text: str) -> Optional[str]:
    m = ORDER_ID_RE.search(text or "")
    return m.group(0).upper() if m else None

def extract_email(text: str) -> Optional[str]:
    m = EMAIL_RE.search(text or "")
    return m.group(0) if m else None

def extract_phone(text: str) -> Optional[str]:
    m = PHONE_RE.search(text or "")
    return m.group(0) if m else None

class Tools:
    def __init__(self, store: Store):
        self.store = store

    # lookups
    def lookup_order(self, oid: str) -> Optional[dict]: return self.store.get_order(oid)
    def search_items(self, q: str) -> List[dict]: return self.store.search_by_item_keyword(q)

    # suggestions
    def suggest_order_ids(self, q: str, user_phone: Optional[str] = None, limit: int = 5) -> List[str]:
        q = (q or "").upper()
        pool = self.store.find_by_phone(user_phone) if user_phone else list(self.store.orders.values())
        return [o["order_id"] for o in pool if q in o["order_id"].upper()][:limit]

    def suggest_item_names(self, q: str, limit: int = 10) -> List[str]:
        ql = (q or "").lower()
        seen = []
        for o in self.store.orders.values():
            for it in o.get("items", []):
                if ql in it.lower() and it not in seen:
                    seen.append(it)
                    if len(seen) >= limit: return seen
        return seen

    # ops (with undo logging)
    def cancel_order(self, oid: str) -> Tuple[bool, str]:
        o = self.store.get_order(oid)
        if not o: return False, f"I couldn't find {oid}."
        status = o.get("status")
        if status == "Processing":
            before = self.store.snapshot_order(oid)
            self.store.set_status(oid, "Cancelled")
            after = self.store.snapshot_order(oid)
            self.store.push_action("cancel", oid, before, after)
            return True, f"**{oid}** has been **cancelled**."
        if status in {"Shipped", "Out for Delivery"}:
            return False, f"**{oid}** is already {status}, so I can’t cancel it."
        if status in {"Delivered","Return Initiated","Refunded","Cancelled"}:
            return False, f"**{oid}** is {status}, so cancellation isn’t applicable."
        return False, f"I can’t cancel **{oid}** at the current stage."

    def start_return(self, oid: str) -> Tuple[bool, str]:
        o = self.store.get_order(oid)
        if not o: return False, f"I couldn't find {oid}."
        status = o.get("status"); ret_until = o.get("return_eligible_until")
        if status == "Delivered":
            try:
                if ret_until and date.today() > datetime.fromisoformat(ret_until).date():
                    return False, f"Return window has expired for **{oid}**."
            except Exception: pass
            before = self.store.snapshot_order(oid)
            self.store.set_status(oid, "Return Initiated")
            self.store.set_refund_status(oid, "Pending Pickup")
            after = self.store.snapshot_order(oid)
            self.store.push_action("start_return", oid, before, after)
            return True, f"Return initiated for **{oid}**. We’ll share pickup details shortly."
        return False, f"**{oid}** isn’t delivered yet, so return can’t be started."

    def change_address(self, oid: str, new_addr: str) -> Tuple[bool, str]:
        o = self.store.get_order(oid)
        if not o: return False, f"I couldn't find {oid}."
        status = o.get("status")
        if status == "Processing":
            before = self.store.snapshot_order(oid)
            self.store.set_address(oid, new_addr)
            after = self.store.snapshot_order(oid)
            self.store.push_action("change_address", oid, before, after)
            return True, f"Address updated for **{oid}**."
        return False, f"**{oid}** is {status}, so I can’t change the address."

    # FAQs / policy
    def faq(self, topic: str) -> str:
        t = (topic or "").lower()
        if "refund" in t:
            return ("Refunds typically complete in **3–5 business days** after pickup and QC. "
                    "You’ll see the credit in your original payment method.")
        if "return" in t:
            return ("Most items are returnable within **10 days** of delivery if unused and in original packaging. "
                    "Some items may be non-returnable for hygiene/safety.")
        return "Ask me about refunds/returns/address changes or tracking any order."
