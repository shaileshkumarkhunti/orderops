# state_store.py
from typing import Dict, List, Optional
from datetime import date, timedelta, datetime
import copy, time, urllib.parse as _url

def _svg_data_uri(label: str) -> str:
    """Generate a pretty inline SVG with initials so images always render (no internet needed)."""
    text = (label or "Item").strip()
    initials = "".join([w[0] for w in text.split()[:2]]).upper() or "?"
    svg = f'''
    <svg xmlns="http://www.w3.org/2000/svg" width="640" height="400">
      <defs>
        <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stop-color="#6c8bff"/>
          <stop offset="100%" stop-color="#36c1d6"/>
        </linearGradient>
      </defs>
      <rect width="640" height="400" fill="url(#g)"/>
      <rect x="18" y="18" width="604" height="364" rx="18" fill="#0e1430" opacity="0.68"/>
      <text x="50%" y="58%" text-anchor="middle" font-family="Inter, Segoe UI, Arial" font-size="120" fill="#f6f7fb">{initials}</text>
    </svg>'''.strip()
    return "data:image/svg+xml;utf8," + _url.quote(svg)

def _image_for_items(items: List[str]) -> str:
    label = (items or ["Item"])[0]
    return _svg_data_uri(label)

def _make_order(oid: str, status: str, items: List[str], phone: str,
                courier: Optional[str], tracking: Optional[str],
                ship: Optional[str], eta: Optional[str], delivered: Optional[str],
                return_ok_until: Optional[str], address: str, order_date: str,
                refund_status: Optional[str] = None) -> Dict:
    return {
        "order_id": oid,
        "status": status,
        "items": items,
        "phone": phone,
        "courier": courier,
        "tracking_id": tracking,
        "ship_date": ship,
        "est_delivery_date": eta,
        "delivered_date": delivered,
        "return_eligible_until": return_ok_until,
        "address_line": address,
        "order_date": order_date,
        "image_url": _image_for_items(items),  # <-- guaranteed image via data URI
        "refund_status": refund_status,
    }

def _seed_demo(phone: str = "9876543210") -> Dict[str, Dict]:
    today = date.today()
    d: Dict[str, Dict] = {}
    d["ORD10071"] = _make_order("ORD10071","Delivered",["Wireless Earbuds"],phone,"BlueDart","BDX81234",
        (today- timedelta(days=7)).isoformat(), (today- timedelta(days=3)).isoformat(), (today- timedelta(days=3)).isoformat(),
        (today+ timedelta(days=7)).isoformat(),"12 Park Lane, Mumbai",(today- timedelta(days=10)).isoformat(),"Completed")
    d["ORD10072"] = _make_order("ORD10072","Shipped",["Smartwatch Series X"],phone,"Delhivery","DLV93811",
        (today- timedelta(days=2)).isoformat(), (today+ timedelta(days=2)).isoformat(), None,None,"221B Baker Street, Delhi",(today- timedelta(days=3)).isoformat())
    d["ORD10073"] = _make_order("ORD10073","Out for Delivery",["Gaming Mouse Pro"],phone,"Ekart","EKT11229",
        (today- timedelta(days=3)).isoformat(), today.isoformat(), None,None,"Hitech City, Hyderabad",(today- timedelta(days=4)).isoformat())
    d["ORD10074"] = _make_order("ORD10074","Processing",["Bluetooth Speaker Mini"],phone,None,None,
        None,(today+ timedelta(days=4)).isoformat(), None,None,"MG Road, Bengaluru",(today- timedelta(days=1)).isoformat())
    d["ORD10075"] = _make_order("ORD10075","Return Initiated",["Running Shoes 9"],phone,"BlueDart","BDX84544",
        (today- timedelta(days=9)).isoformat(), (today- timedelta(days=5)).isoformat(), (today- timedelta(days=4)).isoformat(),
        (today+ timedelta(days=6)).isoformat(),"Sector 18, Noida",(today- timedelta(days=12)).isoformat(),"Pending Pickup")
    d["ORD10076"] = _make_order("ORD10076","Refunded",["Phone Case Clear"],phone,"Ekart","EKT22001",
        (today- timedelta(days=14)).isoformat(), (today- timedelta(days=10)).isoformat(), (today- timedelta(days=9)).isoformat(),
        (today- timedelta(days=2)).isoformat(),"Baner, Pune",(today- timedelta(days=16)).isoformat(),"Completed")
    d["ORD10077"] = _make_order("ORD10077","Delivered",["Laptop Sleeve 14 inch"],phone,"Delhivery","DLV22881",
        (today- timedelta(days=6)).isoformat(), (today- timedelta(days=2)).isoformat(), (today- timedelta(days=2)).isoformat(),
        (today+ timedelta(days=8)).isoformat(),"Salt Lake, Kolkata",(today- timedelta(days=8)).isoformat(),None)
    d["ORD10078"] = _make_order("ORD10078","Processing",["USB-C Cable 2m"],phone,None,None,
        None,(today+ timedelta(days=3)).isoformat(), None,None,"Anna Nagar, Chennai",today.isoformat())
    d["ORD10079"] = _make_order("ORD10079","Shipped",["Mechanical Keyboard TKL"],phone,"BlueDart","BDX99901",
        (today- timedelta(days=1)).isoformat(), (today+ timedelta(days=3)).isoformat(), None,None,"Navi Mumbai",(today- timedelta(days=2)).isoformat())
    return d

class Store:
    def __init__(self):
        self.orders: Dict[str, Dict] = _seed_demo("9876543210")
        self.actions: List[dict] = []
        self.last_action: Optional[dict] = None
        self._action_seq = 0
        self.undo_grace_seconds = 300

    def get_order(self, oid: str) -> Optional[Dict]: return self.orders.get(oid)
    def find_by_phone(self, phone: str) -> List[Dict]: return [o for o in self.orders.values() if o.get("phone")==phone]
    def search_by_item_keyword(self, q: str) -> List[Dict]:
        ql = (q or "").lower()
        return [o for o in self.orders.values() if any(ql in it.lower() for it in o.get("items", []))]

    def snapshot_order(self, oid: str) -> Optional[Dict]:
        o = self.get_order(oid); return copy.deepcopy(o) if o else None

    def push_action(self, action_type: str, oid: str, before: Dict, after: Dict) -> dict:
        import copy as _copy
        self._action_seq += 1
        entry = {"id": self._action_seq, "type": action_type, "oid": oid, "ts": time.time(),
                 "before": _copy.deepcopy(before), "after": _copy.deepcopy(after)}
        self.actions.append(entry); self.last_action = entry; return entry

    def can_undo(self) -> bool:
        if not self.last_action: return False
        return (time.time() - self.last_action["ts"]) <= self.undo_grace_seconds

    def last_action_info(self) -> Optional[Dict]:
        if not self.last_action: return None
        age = int(time.time() - self.last_action["ts"])
        remaining = max(0, int(self.undo_grace_seconds - age))
        return {"type": self.last_action["type"], "oid": self.last_action["oid"],
                "age_sec": age, "remaining_sec": remaining, "can_undo": remaining>0,
                "before": self.last_action["before"], "after": self.last_action["after"]}

    def undo_last(self) -> (bool, str): # type: ignore
        if not self.can_undo(): return False, "Undo window has expired."
        act = self.last_action; oid = act.get("oid"); before = act.get("before")
        if not before or not oid or oid not in self.orders:
            self.last_action = None; return False, "Nothing to undo."
        self.orders[oid] = before; self.last_action = None
        return True, f"Reverted **{act.get('type','change')}** on **{oid}**."

    # mutations
    def set_status(self, oid: str, new_status: str):
        if oid in self.orders: self.orders[oid]["status"] = new_status
    def set_address(self, oid: str, new_addr: str):
        if oid in self.orders: self.orders[oid]["address_line"] = new_addr
    def set_refund_status(self, oid: str, status: str):
        if oid in self.orders: self.orders[oid]["refund_status"] = status
