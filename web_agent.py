# web_agent.py
import time
from typing import Dict, List
from duckduckgo_search import DDGS
import requests
from bs4 import BeautifulSoup
import html
import re
import os

def _clean_text(t: str) -> str:
    t = html.unescape(t or "")
    t = re.sub(r"\s+", " ", t).strip()
    return t

def _fetch(url: str, timeout=8) -> str:
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent":"Mozilla/5.0"})
        if r.status_code == 200 and "text" in r.headers.get("Content-Type",""):
            return r.text
    except Exception:
        pass
    return ""

def _summarize_with_openai(query: str, snippets: List[Dict], depth: str = "normal") -> str:
    try:
        from openai import OpenAI
        key = os.getenv("OPENAI_API_KEY","")
        if not key or not key.startswith("sk-"):
            # fallback summary without OpenAI
            join = " • ".join(s.get("snippet","") for s in snippets[:3] if s.get("snippet"))
            return f"{join or 'I could not summarize results.'}"
        client = OpenAI(api_key=key)
        system = "You concisely answer the user's question by synthesizing provided snippets. Include numeric facts only if present."
        user_bundle = {
            "question": query,
            "snippets": snippets,
            "style": depth
        }
        resp = client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL","gpt-4o-mini"),
            messages=[{"role":"system","content":system},
                      {"role":"user","content":str(user_bundle)}],
            temperature=0.4,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception:
        return "I tried, but couldn’t summarize results right now."

def answer_with_web(query: str, depth: str = "normal", max_sources: int = 4) -> Dict:
    """
    Returns: {"answer": str, "sources":[{"index":1,"title":..., "url":...}, ...]}
    """
    sources = []
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_sources))
    except Exception:
        results = []

    snippets = []
    for i, r in enumerate(results[:max_sources], start=1):
        title = _clean_text(r.get("title",""))
        url = r.get("href") or r.get("url") or ""
        descr = _clean_text(r.get("body",""))
        page_text = ""
        if url:
            html_doc = _fetch(url)
            if html_doc:
                try:
                    soup = BeautifulSoup(html_doc, "html.parser")
                    paras = " ".join(_clean_text(p.get_text(" ", strip=True)) for p in soup.find_all("p")[:8])
                    page_text = (paras[:1200] + "...") if len(paras) > 1200 else paras
                except Exception:
                    page_text = descr
        text_for_llm = page_text or descr
        if text_for_llm:
            snippets.append({"title": title, "url": url, "snippet": text_for_llm})
        sources.append({"index": i, "title": title or url, "url": url})
        time.sleep(0.15)  # be gentle

    answer = _summarize_with_openai(query, snippets, depth=depth)
    return {"answer": answer, "sources": sources}
