# ui_loader.py
import os, re
from functools import lru_cache
import streamlit as st

ASSETS_DIR = os.path.join(os.path.dirname(__file__), "assets")
HTML_PATH = os.path.join(ASSETS_DIR, "ui.html")
CSS_PATH = os.path.join(ASSETS_DIR, "styles.css")

SECTION_RE = re.compile(r"<!--\s*TEMPLATE:(?P<name>[A-Z_]+)\s*-->(?P<html>.*?)<!--\s*END TEMPLATE\s*-->", re.S)

@lru_cache(maxsize=1)
def _load_html_sections():
    if not os.path.exists(HTML_PATH):
        return {}
    with open(HTML_PATH, "r", encoding="utf-8") as f:
        data = f.read()
    sections = {}
    for m in SECTION_RE.finditer(data):
        sections[m.group("name")] = m.group("html").strip()
    return sections

def get_tpl(name: str) -> str:
    return _load_html_sections().get(name.upper(), "")

def inject_css():
    if os.path.exists(CSS_PATH):
        with open(CSS_PATH, "r", encoding="utf-8") as f:
            st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)

def render_hero(phone: str):
    tpl = get_tpl("HERO")
    if not tpl:
        return
    phone_fmt = f"{phone[:3]}-{phone[3:6]}-{phone[6:]}" if phone and len(phone) == 10 else (phone or "—")
    st.markdown(tpl.format(phone_formatted=phone_fmt), unsafe_allow_html=True)

def status_pill_html(status: str) -> str:
    s = (status or "").lower()
    cls = "status-processing"
    if s == "processing": cls = "status-processing"
    elif s == "shipped": cls = "status-shipped"
    elif s == "out for delivery": cls = "status-out"
    elif s == "delivered": cls = "status-delivered"
    elif s == "return initiated": cls = "status-return"
    elif s == "refunded": cls = "status-refunded"
    elif s == "cancelled": cls = "status-cancelled"
    return f'<span class="status-pill {cls}">{status}</span>'

def render_order_card(order: dict, highlight: bool = False, focused_item: str | None = None):
    tpl = get_tpl("ORDER_CARD")
    if not tpl:
        return
    items = order.get("items", []) or []
    if focused_item and focused_item in items:
        items = [f"**{it}**" if it == focused_item else it for it in items]
    html = tpl.format(
        image_url=order.get("image_url") or "",
        order_id=order.get("order_id") or "—",
        active_dot="🟢 " if highlight else "",
        status_pill=status_pill_html(order.get("status") or "—"),
        items_list=", ".join(items),
        courier=order.get("courier") or "—",
        tracking=order.get("tracking_id") or "—",
        eta=order.get("est_delivery_date") or "—",
        ship=order.get("ship_date") or "—",
        delivered=order.get("delivered_date") or "—",
        return_until=order.get("return_eligible_until") or "—",
        address=order.get("address_line") or "—",
    )
    st.markdown(html, unsafe_allow_html=True)

def show_loading_overlay(is_on: bool, title: str | None = None, subtitle: str | None = None):
    """Render a centered blocking loader with large text."""
    if not is_on:
        return
    tpl = get_tpl("LOADING")
    if tpl:
        t = title or "RUNNING…"
        sub = subtitle or "Please wait while we process your request."
        st.markdown(tpl.format(title=t, subtitle=sub), unsafe_allow_html=True)
