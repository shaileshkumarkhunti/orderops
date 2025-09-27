# app.py
import os, re, json
from datetime import datetime, date
from typing import Optional, List, Dict
import streamlit as st

from state_store import Store
from tools import Tools, extract_order_id
from web_agent import answer_with_web
from prompts import SYSTEM_PROMPT, PLANNER_PROMPT, COMPOSER_PROMPT
import ui_loader as ui

# ---- config / env ----
DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
DEMO_FALLBACK_PHONE = os.getenv("DEMO_FALLBACK_PHONE", "9876543210")
WEB_DEFAULT = os.getenv("WEB_ENABLED_DEFAULT", "1") == "1"

PHONE_10D_RE = re.compile(r"^\D*(\d\D*){10}$"); ORDER_ID_RE = re.compile(r"\bORD\d{5}\b", re.IGNORECASE)

# ---- OpenAI helpers ----
def get_openai_key() -> Optional[str]:
    try:
        val = os.getenv("OPENAI_API_KEY", "").strip()
        if not val and hasattr(st, "secrets"):
            val = str(st.secrets.get("OPENAI_API_KEY", "")).strip()
    except Exception: val = ""
    return val if val.startswith("sk-") else None

def _openai_client():
    try:
        from openai import OpenAI
        key = get_openai_key()
        return OpenAI(api_key=key) if key else None
    except Exception: return None

# ---- safety for "cancel" negations ----
NEG_CANCEL_PATTERNS = [
    r"\b(no need to\s+cancel)\b", r"\b(don'?t\s+cancel)\b", r"\b(do\s+not\s+cancel)\b",
    r"\b(no\s+cancel)\b", r"\b(not\s+cancel)\b", r"\b(never\s+cancel)\b",
    r"\b(keep\s+the\s+order)\b", r"\b(i\s+want\s+the\s+order)\b", r"\b(cancel\s+isn'?t\s+needed)\b",
]
def _neg_cancel(text: str) -> bool: return any(re.search(p, (text or "").lower()) for p in NEG_CANCEL_PATTERNS)

def _fallback_intent(t: str) -> str:
    tl = (t or "").lower()
    if _neg_cancel(tl): return "keep_order"
    if any(w in tl for w in ["where","status","track","eta"]): return "track"
    if "cancel" in tl: return "cancel"
    if "return" in tl and "status" not in tl: return "start_return"
    if "refund" in tl: return "refund_policy_or_status"
    if any(w in tl for w in ["address","change address","update address"]): return "change_address"
    if any(w in tl for w in ["orders","my orders","recent orders","list my orders"]): return "list_orders"
    if any(w in tl for w in ["so much time","so long","taking so long","delay","delayed"]): return "delay_reason"
    if "average" in tl and "time" in tl: return "avg_time"
    return "general_question"

def ai_plan(user_text: str, active_oid: Optional[str], has_orders: bool) -> dict:
    client = _openai_client()
    if not client:
        return {"intent": _fallback_intent(user_text),"need_web": False,"target_order_id": active_oid,
                "item_name": None,"address_text": None,"ask_clarify": False,"clarifying_question": None,
                "actions": ["general_chat"],"web_queries": [],"notes": "fallback"}
    payload = {"user_text": user_text, "ACTIVE_ORDER_ID": active_oid or "", "HAS_ORDERS": has_orders}
    try:
        resp = client.chat.completions.create(model=DEFAULT_MODEL, temperature=0.2,
            messages=[{"role":"system","content": PLANNER_PROMPT},{"role":"user","content": json.dumps(payload)}])
        raw = (resp.choices[0].message.content or "").strip()
        start, end = raw.find("{"), raw.rfind("}")
        plan = json.loads(raw[start:end+1]) if start!=-1 and end!=-1 and end>start else {}
    except Exception: plan = {}
    if _neg_cancel(user_text):
        plan["intent"] = "keep_order"; plan["actions"] = plan.get("actions") or []
        if "general_chat" not in plan["actions"]: plan["actions"].insert(0, "general_chat")
    plan.setdefault("intent", _fallback_intent(user_text)); plan.setdefault("need_web", False)
    plan.setdefault("target_order_id", active_oid); plan.setdefault("item_name", None)
    plan.setdefault("address_text", None); plan.setdefault("ask_clarify", False)
    plan.setdefault("clarifying_question", None); plan.setdefault("actions", ["general_chat"])
    plan.setdefault("web_queries", []); plan.setdefault("notes", "ok")
    return plan

def ai_compose(user_text: str, plan: dict, order_ctx: dict,
               local_result: Optional[str], web_result: Optional[str], sources: List[Dict]) -> str:
    client = _openai_client()
    src_lines = "\n".join(f"[{s['index']}] {s['title']} — {s['url']}" for s in (sources or []))
    if not client:
        parts = [x for x in [local_result, web_result] if x]
        if plan.get("ask_clarify") and plan.get("clarifying_question"): parts.append(f"Quick question: {plan['clarifying_question']}")
        if src_lines: parts.append("Sources:\n"+src_lines)
        return "\n\n".join(parts) or "I’m here to help."
    bundle = {"USER_TEXT": user_text,"PLAN": plan,"ORDER_CTX": order_ctx,"LOCAL_RESULT": local_result,
              "WEB_RESULT": web_result,"SOURCES_TEXT": src_lines}
    try:
        resp = client.chat.completions.create(model=DEFAULT_MODEL, temperature=0.5,
            messages=[{"role":"system","content": SYSTEM_PROMPT + "\n" + COMPOSER_PROMPT},
                      {"role":"user","content": json.dumps(bundle, ensure_ascii=False)}])
        return (resp.choices[0].message.content or "").strip()
    except Exception:
        parts = [x for x in [local_result, web_result] if x]
        if plan.get("ask_clarify") and plan.get("clarifying_question"): parts.append(f"Quick question: {plan['clarifying_question']}")
        if src_lines: parts.append("Sources:\n"+src_lines)
        return "\n\n".join(parts) or "I’m here to help."

# ---- analytics helpers ----
def _parse_date(d: Optional[str]): 
    try: return datetime.fromisoformat(d).date() if d else None
    except Exception: return None

def compute_avg_delivery_days(store: Store, item: Optional[str] = None, courier: Optional[str] = None):
    days=[]; 
    for o in store.orders.values():
        if o.get("status")!="Delivered": continue
        if item and item not in o.get("items", []): continue
        if courier and courier != o.get("courier"): continue
        ship=_parse_date(o.get("ship_date")); delivered=_parse_date(o.get("delivered_date"))
        if ship and delivered and delivered>=ship: days.append((delivered-ship).days)
    if not days: return None,0
    return sum(days)/len(days), len(days)

def explain_delay_for_order(order: dict, store: Store) -> str:
    if not order: return "I don’t see an active order. Please set one on the left or share its Order ID."
    oid=order.get("order_id"); status=order.get("status")
    edd=_parse_date(order.get("est_delivery_date")); ship=_parse_date(order.get("ship_date"))
    courier=order.get("courier") or "the courier"; today=date.today()
    delayed= edd and today>edd and status not in {"Delivered","Cancelled","Refunded"}
    msg=[f"**{oid}** is currently **{status}**."]
    if delayed: msg.append(f"It’s past the estimated delivery date (**{edd.isoformat()}**).")
    if status=="Processing": msg.append("It’s still being prepared. High demand or batching can add 1–2 days.")
    elif status in {"Shipped","Out for Delivery"}:
        msg.append(f"It’s with **{courier}**; hub backlogs and handovers sometimes add a day.")
        if ship: msg.append(f"Days in transit so far: **{(today-ship).days}**.")
    elif status=="Delivered": msg.append("It has already been delivered.")
    item=(order.get("items") or [None])[0]
    avg_item,n_item=compute_avg_delivery_days(store,item=item); overall,n_all=compute_avg_delivery_days(store)
    if avg_item and n_item>=3: msg.append(f"Typical for **{item}**: ~**{avg_item:.1f} days** (n={n_item}).")
    elif overall: msg.append(f"Overall average: ~**{overall:.1f} days** (n={n_all}).")
    if status in {"Processing","Shipped","Out for Delivery"}:
        msg.append("You can wait another day, escalate, or (if not shipped) cancel.")
    return " ".join(msg)

def get_orders_for_session(store: Store, phone: Optional[str]):
    try:
        if phone:
            hits=store.find_by_phone(phone)
            if hits: return hits
    except Exception: pass
    return store.find_by_phone(DEMO_FALLBACK_PHONE) or list(store.orders.values())

# ====================== APP STATE & OVERLAY ======================
def _init_state():
    st.set_page_config(page_title="OrderAi Copilot", page_icon="📦", layout="wide")
    if "processing" not in st.session_state: st.session_state.processing=False
    if "pending_user_text" not in st.session_state: st.session_state.pending_user_text=None
    if "pending_already_logged" not in st.session_state: st.session_state.pending_already_logged=False
    if "store" not in st.session_state: st.session_state.store=Store()
    if "tools" not in st.session_state: st.session_state.tools=Tools(st.session_state.store)
    if "messages" not in st.session_state: st.session_state.messages=[]
    if "active_oid" not in st.session_state: st.session_state.active_oid=None
    if "active_item" not in st.session_state: st.session_state.active_item=None
    if "last_ctx" not in st.session_state: st.session_state.last_ctx={}
    if "search_filter" not in st.session_state: st.session_state.search_filter={"mode":"none","value":None}
    if "search_q" not in st.session_state: st.session_state.search_q=""
    if "last_sources" not in st.session_state: st.session_state.last_sources=[]
    if "depth" not in st.session_state: st.session_state.depth="normal"
    if "max_sources" not in st.session_state: st.session_state.max_sources=4
    if "web_enabled" not in st.session_state: st.session_state.web_enabled=WEB_DEFAULT
    if "pending_action" not in st.session_state: st.session_state.pending_action=None
    if "logged_in" not in st.session_state: st.session_state.logged_in=False
    if "user_phone" not in st.session_state: st.session_state.user_phone=None

def _disabled() -> bool: return bool(st.session_state.get("processing", False))

# =========================== LOGIN ===========================
def login_view():
    st.set_page_config(page_title="OrderAi Copilot — Login", page_icon="📦", layout="centered")
    ui.inject_css()
    st.markdown('<div class="hero"><h1>Welcome to 📦 OrderAi Copilot</h1><p>Log in with your phone number to see your orders and get AI help instantly.</p></div>',
                unsafe_allow_html=True)
    with st.form("login_form", clear_on_submit=False):
        phone = st.text_input("Phone Number", placeholder="10-digit number")
        submitted = st.form_submit_button("Continue")
        if submitted:
            if PHONE_10D_RE.match(phone or ""):
                digits = "".join(ch for ch in phone if ch.isdigit())[-10:]
                st.session_state.logged_in=True; st.session_state.user_phone=digits; st.rerun()
            else:
                st.error("Please enter any 10-digit phone number (digits only).")

# =========================== RUN ===========================
if "logged_in" not in st.session_state or not st.session_state.get("logged_in"):
    login_view(); raise SystemExit

_init_state()
ui.inject_css()
ui.show_loading_overlay(
    st.session_state.processing,
    st.session_state.get("loading_title"),
    st.session_state.get("loading_subtitle"),
)

# HERO
phone = st.session_state.user_phone or "9876543210"
ui.render_hero(phone)

# Sidebar (settings + diagnostics)
with st.sidebar:
    st.header("⚙️ Settings")
    web_enabled = st.toggle("Use Internet for general knowledge", value=st.session_state.web_enabled, disabled=_disabled())
    depth = st.selectbox("Answer depth", ["brief","normal","deep"],
                         index=["brief","normal","deep"].index(st.session_state.depth), disabled=_disabled())
    max_sources = st.slider("Max sources", 2, 6, st.session_state.max_sources, disabled=_disabled())
    st.session_state.web_enabled=web_enabled; st.session_state.depth=depth; st.session_state.max_sources=max_sources

    key = get_openai_key(); connected = bool(key)
    st.caption(f"OpenAI: {'✅ Connected' if connected else '❌ Not set'}")
    st.caption(f"Model: {DEFAULT_MODEL}")
    if st.button("Verify OpenAI key now", disabled=_disabled()):
        try:
            cli = _openai_client()
            if not cli: st.error("No usable key found (needs to start with 'sk-').")
            else: _ = cli.models.list(); st.success("Key looks valid and API is reachable.")
        except Exception as e:
            st.error(f"Key check failed: {e}")

# Layout
store: Store = st.session_state.store
tools: Tools = st.session_state.tools

col_left, col_mid, col_right = st.columns([0.30, 0.42, 0.28])

# LEFT: Orders + Search
def _clear_search():
    st.session_state.search_q=""; st.session_state.search_filter={"mode":"none","value":None}; st.session_state.active_item=None
def _reset_session():
    st.session_state.clear()

with col_left:
    st.subheader("🛒 Your Orders")
    c1, c2 = st.columns(2)
    with c1: st.button("Clear", on_click=_clear_search, disabled=_disabled())
    with c2: st.button("Reset Session", on_click=_reset_session, disabled=_disabled())

    q = st.text_input("Search by Order ID or product name", key="search_q", disabled=_disabled())

    if q:
        with st.container(border=True):
            st.caption("Suggestions")
            try:
                s_ids = [o["order_id"] for o in (store.find_by_phone(phone) or []) if q.upper() in o["order_id"].upper()][:5]
                if not s_ids:
                    s_ids = [o["order_id"] for o in (get_orders_for_session(store, phone) or []) if q.upper() in o["order_id"].upper()][:5]
            except Exception: s_ids = []
            try:
                s_items = tools.suggest_item_names(q, limit=5)
            except Exception: s_items = []
            if s_ids:
                st.write("Order IDs:"); cols = st.columns(len(s_ids))
                for i, oid in enumerate(s_ids):
                    with cols[i]:
                        if st.button(oid, key=f"sug_oid_{oid}", disabled=_disabled()):
                            if store.get_order(oid):
                                st.session_state.active_oid=oid; st.session_state.active_item=None
                                st.session_state.search_filter={"mode":"id","value":oid}; st.rerun()
            if s_items:
                st.write("Items:"); cols = st.columns(len(s_items))
                for i, it in enumerate(s_items):
                    with cols[i]:
                        if st.button(it, key=f"sug_item_{it}", disabled=_disabled()):
                            my_orders=get_orders_for_session(store, phone)
                            hits=[o for o in my_orders if it in o.get("items", [])] or [o for o in store.orders.values() if it in o.get("items", [])]
                            if hits: st.session_state.active_oid=hits[0]["order_id"]
                            st.session_state.active_item=it; st.session_state.search_filter={"mode":"item","value":it}; st.rerun()

    if st.button("Search", disabled=_disabled()):
        if q and ORDER_ID_RE.search(q):
            oid=ORDER_ID_RE.search(q).group(0).upper()
            if store.get_order(oid):
                st.session_state.active_oid=oid; st.session_state.active_item=None
                st.session_state.search_filter={"mode":"id","value":oid}; st.rerun()
            else: st.warning(f"No order found with ID {oid}.")
        elif q:
            my_orders=get_orders_for_session(store, phone)
            hits=[o for o in my_orders if any(q.lower() in it.lower() for it in o.get("items", []))] or store.search_by_item_keyword(q)
            if hits:
                st.session_state.active_oid=hits[0]["order_id"]
                best=next((it for it in hits[0].get("items", []) if q.lower() in it.lower()), None)
                st.session_state.active_item=best
                st.session_state.search_filter={"mode":"item","value": (best or q)}; st.rerun()
            else:
                st.session_state.active_item=None; st.session_state.search_filter={"mode":"none","value":None}; st.warning("No matching orders found.")

    all_mine=get_orders_for_session(store, phone)
    sf=st.session_state.search_filter
    filtered=[o for o in all_mine if (sf["mode"]=="id" and sf["value"]==o["order_id"]) or
                                   (sf["mode"]=="item" and sf["value"] in o.get("items", [])) or
                                   (sf["mode"]=="none")]
    if not filtered:
        st.info("No orders to show.")
    else:
        status_filter = st.selectbox("Filter by status",
            ["All","Processing","Shipped","Out for Delivery","Delivered","Return Initiated","Refunded"], index=0, disabled=_disabled())
        for o in filtered:
            if status_filter!="All" and o.get("status")!=status_filter: continue
            is_active = (o["order_id"] == st.session_state.active_oid)
            ui.render_order_card(o, highlight=is_active, focused_item=st.session_state.active_item)
            c1, c2 = st.columns(2)
            with c1:
                if st.button("Set Active", key=f"set_{o['order_id']}", disabled=_disabled()):
                    st.session_state.active_oid=o['order_id']; st.rerun()
            with c2:
                its=o.get("items", [])
                if its and st.button("Focus item", key=f"focus_{o['order_id']}", disabled=_disabled()):
                    st.session_state.active_oid=o['order_id']; st.session_state.active_item=its[0]; st.rerun()

# MIDDLE: Chat + Pending + Undo
with col_mid:
    st.subheader("🤖 Assistant")

    la=store.last_action_info()
    if la and la.get("can_undo"):
        with st.container(border=True):
            st.info(f"Recently performed: **{la['type']}** on **{la['oid']}**. You can **Undo** for another **{la['remaining_sec']}s**.")
            if st.button("↩️ Undo last action", disabled=_disabled()):
                ok,msg=store.undo_last(); st.session_state.messages.append({"role":"assistant","content": msg}); st.rerun()

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]): st.markdown(msg["content"])

    if st.session_state.pending_action:
        pa=st.session_state.pending_action
        with st.container(border=True):
            if pa["type"]=="cancel": st.warning(f"Confirm cancellation for **{pa['oid']}**?")
            elif pa["type"]=="start_return": st.warning(f"Confirm starting a **return** for **{pa['oid']}**?")
            elif pa["type"]=="change_address": st.warning(f"Confirm updating address for **{pa['oid']}** to:\n\n> {pa.get('address','(missing)')}")
            c1,c2=st.columns(2)
            with c1:
                if st.button("✅ Confirm", disabled=_disabled()):
                    if pa["type"]=="cancel": ok, result_msg=tools.cancel_order(pa["oid"])
                    elif pa["type"]=="start_return": ok, result_msg=tools.start_return(pa["oid"])
                    elif pa["type"]=="change_address": ok, result_msg=tools.change_address(pa["oid"], pa.get("address",""))
                    else: ok, result_msg=False,"Unknown action."
                    st.session_state.pending_action=None; st.session_state.messages.append({"role":"assistant","content": result_msg}); st.rerun()
            with c2:
                if st.button("❌ Dismiss", disabled=_disabled()):
                    st.session_state.pending_action=None; st.session_state.messages.append({"role":"assistant","content":"Okay, I won’t proceed with that action."}); st.rerun()

    typed = st.chat_input("Type your message...", disabled=_disabled())
    if typed and not st.session_state.processing:
        st.session_state.pending_user_text = typed
        st.session_state.pending_already_logged = False
        # Set the big centered loader text
        st.session_state.loading_title = "RUNNING…"
        st.session_state.loading_subtitle = "Thinking with OpenAI, searching the web, and updating your order context."
        st.session_state.processing = True
        st.rerun()


    # Process while overlay is visible
    if st.session_state.processing and st.session_state.pending_user_text:
        user_text=st.session_state.pending_user_text
        if not st.session_state.pending_already_logged:
            st.session_state.messages.append({"role":"user","content": user_text})
            st.session_state.pending_already_logged=True

        active = store.get_order(st.session_state.active_oid) if st.session_state.active_oid else None
        has_orders = len(get_orders_for_session(store, phone))>0
        plan = ai_plan(user_text, (active or {}).get("order_id"), has_orders)

        ctx={"intent": plan.get("intent")}
        oid = plan.get("target_order_id") or st.session_state.active_oid
        local_result=None; web_result=None; sources: List[Dict]=[]

        def set_pending(kind, oid, address=None):
            st.session_state.pending_action={"type": kind, "oid": oid}
            if address: st.session_state.pending_action["address"]=address

        for act in (plan.get("actions") or []):
            if act=="set_active_from_text":
                maybe=extract_order_id(user_text)
                if maybe and store.get_order(maybe): st.session_state.active_oid=maybe; oid=maybe
            elif act=="track_order":
                if not oid: local_result="Share your order ID (e.g., ORD10015) or select one on the left.)"; break
                order=tools.lookup_order(oid)
                if not order: local_result=f"I couldn't find {oid}."; break
                ctx.update({"order":order,"oid":oid})
                status=order["status"]; edd=order.get("est_delivery_date"); cr=order.get("courier"); tr=order.get("tracking_id")
                if status in {"Shipped","Out for Delivery"}: extra=f" ETA **{edd}** via {cr}, tracking **{tr}**." if edd and cr else ""
                elif status=="Delivered": extra=f" Delivered on **{order.get('delivered_date')}**."
                elif status=="Processing": extra=" Being prepared for shipment."
                else: extra=""
                local_result=f"**{oid}** status: **{status}**.{extra}"
            elif act=="cancel_order":
                if _neg_cancel(user_text): ctx["intent"]="keep_order"; local_result="Understood — I’ll keep your order as is. No cancellation."
                else:
                    if not oid: local_result="Which order should I cancel? (e.g., ORD10015)"
                    else:
                        order=tools.lookup_order(oid)
                        if order: ctx.update({"order":order,"oid":oid}); set_pending("cancel", oid)
                        local_result=f"Please confirm: cancel **{oid}**? (Use the buttons below.)"
            elif act=="start_return":
                if not oid: local_result="Share the order ID to start a return."
                else:
                    order=tools.lookup_order(oid)
                    if order: ctx.update({"order":order,"oid":oid}); set_pending("start_return", oid)
                    local_result=f"Please confirm: start a **return** for **{oid}**? (Use the buttons below.)"
            elif act=="change_address":
                if not oid: local_result="Which order should I update the address for? Include the ID or pick one on the left."
                else:
                    new_addr=plan.get("address_text")
                    if not new_addr: local_result="Tell me the new address like: 'change address for ORD10015 to 12 Park Lane, Mumbai'"
                    else:
                        order=tools.lookup_order(oid)
                        if order: ctx.update({"order":order,"oid":oid}); set_pending("change_address", oid, address=new_addr)
                        local_result=f"Please confirm: update **{oid}** address to:\n\n> {new_addr}\n\n(Use the buttons below.)"
            elif act=="list_orders":
                hits=get_orders_for_session(store, phone) or []
                if not hits: local_result=("I don’t see any orders on this login yet. If you placed orders with another number, tell me that number and I’ll look it up.")
                else:
                    hits=sorted(hits, key=lambda o:o.get("order_date",""), reverse=True); top=hits[0]
                    st.session_state.active_oid=top["order_id"]; ctx["oid"]=top["order_id"]; ctx["order"]=top
                    lines=[f"- `{o['order_id']}` • {o.get('status','—')} • {', '.join(o.get('items', [])) or '—'}" for o in hits[:5]]
                    local_result=(f"I found **{len(hits)}** orders on your account. I’ve set your most recent order **{top['order_id']}** as active.\n\nHere are a few recent ones:\n"+"\n".join(lines))
            elif act=="explain_delay":
                if not oid: local_result="Which order are you referring to? Set an active order or share its ID."
                else:
                    order=tools.lookup_order(oid)
                    local_result=f"I couldn't find {oid}." if not order else (ctx.update({"order":order,"oid":oid}) or explain_delay_for_order(order, store))
            elif act=="compute_avg":
                order=tools.lookup_order(oid) if oid else None
                item=(order.get("items") or [None])[0] if order else None
                avg,n=compute_avg_delivery_days(store,item=item); 
                if not avg: avg,n=compute_avg_delivery_days(store)
                if avg:
                    ctx.update({"order":order,"oid":oid}); scope=f"for **{item}**" if item else "overall"
                    local_result=f"Average delivery time {scope} in your dataset is about **{avg:.1f} days** (n={n})."
            elif act=="web_research":
                if st.session_state.web_enabled:
                    qlist=plan.get("web_queries") or [user_text]
                    qa=answer_with_web(qlist[0], depth=st.session_state.depth, max_sources=st.session_state.max_sources)
                    web_result=qa["answer"]; sources=qa.get("sources", [])
                else: web_result="Internet is disabled in the sidebar."
            elif act=="general_chat":
                if not web_result:
                    client=_openai_client()
                    if client:
                        try:
                            resp=client.chat.completions.create(model=os.getenv("OPENAI_MODEL","gpt-4o-mini"), temperature=0.6,
                              messages=[{"role":"system","content":"You are a friendly, practical e-commerce assistant. Be concise and specific."},
                                        {"role":"user","content": user_text}])
                            web_result=(resp.choices[0].message.content or "Hi!").strip()
                        except Exception:
                            web_result="Hi! I’m here to help with your orders and questions."
                    else: web_result="Hi! I’m here to help with your orders and questions."

        # Compose final
        active_now = store.get_order(st.session_state.active_oid) if st.session_state.active_oid else None
        order_ctx = {
            "order_id": (ctx.get("oid") or (active_now or {}).get("order_id")),
            "status": (ctx.get("order") or active_now or {}).get("status"),
            "est_delivery_date": (ctx.get("order") or active_now or {}).get("est_delivery_date"),
            "courier": (ctx.get("order") or active_now or {}).get("courier"),
            "tracking_id": (ctx.get("order") or active_now or {}).get("tracking_id"),
            "delivered_date": (ctx.get("order") or active_now or {}).get("delivered_date"),
            "return_eligible_until": (ctx.get("order") or active_now or {}).get("return_eligible_until"),
            "address_line": (ctx.get("order") or active_now or {}).get("address_line"),
            "items": (ctx.get("order") or active_now or {}).get("items"),
        }
        final_answer = ai_compose(user_text, plan, order_ctx, local_result, web_result, sources)

        audit=[f"Intent → {plan.get('intent')}", f"Need web → {plan.get('need_web')}",
               f"Active OID → {st.session_state.active_oid}", f"Focused item → {st.session_state.active_item or '(none)'}",
               f"Pending → {(st.session_state.pending_action or {}).get('type','(none)')}"]
        final_answer += "\n\n—\n_Audit:_\n" + "\n".join(f"- {a}" for a in audit)

        st.session_state.messages.append({"role":"assistant","content": final_answer})
        st.session_state.last_ctx=ctx; st.session_state.last_sources=sources

        # end processing
        st.session_state.pending_user_text=None; st.session_state.pending_already_logged=False
        st.session_state.processing=False; st.rerun()

# RIGHT: Focus / Order card / Summary / Sources
def _render_focus_card(order: dict, highlight: bool = False, focused_item: Optional[str] = None):
    if not order: st.info("🚫 No order selected."); return
    ui.render_order_card(order, highlight=highlight, focused_item=focused_item)

with col_right:
    st.subheader("🎯 Focus")
    last_ctx=st.session_state.get("last_ctx") or {}
    st.write(f"**Intent:** `{last_ctx.get('intent','(none)')}`")
    st.write("**Active Order:** `{}`".format(st.session_state.active_oid or "(none)"))

    if st.session_state.active_oid:
        order=store.get_order(st.session_state.active_oid)
        items=order.get("items", []) if order else []
        if items:
            chosen=st.selectbox("Focus item (optional):", ["(none)"]+items, index=0, disabled=_disabled())
            st.session_state.active_item=None if chosen=="(none)" else chosen

    st.markdown("---")
    if st.session_state.active_oid:
        _render_focus_card(store.get_order(st.session_state.active_oid), highlight=True, focused_item=st.session_state.active_item)
    else:
        st.info("🚫 No order selected.")

    st.markdown("---")
    st.subheader("🧮 Summary")
    if st.button("Generate short summary", disabled=_disabled()):
        client=_openai_client()
        if client:
            try:
                transcript="\n".join(f"{m['role']}: {m['content']}" for m in st.session_state.messages[-10:])
                resp=client.chat.completions.create(model=DEFAULT_MODEL, temperature=0.2,
                    messages=[{"role":"system","content":"You are a concise operations note-taker."},
                              {"role":"user","content": transcript + "\n\nSummarize the conversation in 3 bullets."}])
                summary=(resp.choices[0].message.content or "").strip()
            except Exception:
                summary="- Active order: not set\n- Intent: see Focus panel\n- Next: continue assisting."
        else:
            summary="- Active order: not set\n- Intent: see Focus panel\n- Next: continue assisting."
        st.markdown(summary)

    st.markdown("---")
    st.subheader("🔗 Sources (last web answer)")
    for s in (st.session_state.get("last_sources") or []):
        st.markdown(f"[{s['index']}] {s['title']} — {s['url']}")
    if not (st.session_state.get("last_sources") or []):
        st.caption("No web sources yet.")

