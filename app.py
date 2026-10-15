import re
from datetime import datetime, timezone
from html import unescape

import requests
import streamlit as st
from bs4 import BeautifulSoup

st.set_page_config(page_title="LBrixx Amazon-Diagnose", page_icon="🔎")
st.title("LBrixx Price-Crawler – Amazon-Diagnose")
st.write("**Artikel:** 17003 · FUNWHOLE Future Bus · **ASIN:** B0DYV3XVZ6")
URL = "https://www.amazon.de/dp/B0DYV3XVZ6"
st.link_button("Produkt bei Amazon öffnen", URL)
st.caption("Diagnose der öffentlich abrufbaren Produktseite. Keine Garantie für einen Live-Preis.")


def diagnose():
    try:
        response = requests.get(
            URL,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
                "Accept-Language": "de-DE,de;q=0.9,en;q=0.7",
                "Accept": "text/html,application/xhtml+xml",
            },
            timeout=20,
            allow_redirects=True,
        )
    except requests.RequestException as exc:
        return {"error": f"Netzwerkfehler: {type(exc).__name__}: {exc}"}

    html = response.text
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else "(kein Seitentitel)"
    lower = html.lower()
    challenge_signals = [
        "captcha", "robot check", "automated access", "validatecaptcha",
        "enter the characters you see below",
    ]
    detected = [s for s in challenge_signals if s in lower]
    selectors = [
        "#corePrice_feature_div .a-offscreen",
        "#corePriceDisplay_desktop_feature_div .a-offscreen",
        "#priceblock_ourprice", "#priceblock_dealprice",
        ".priceToPay .a-offscreen",
        "#apex_desktop .a-price .a-offscreen",
    ]
    found = []
    for selector in selectors:
        for element in soup.select(selector)[:3]:
            value = element.get_text(" ", strip=True)
            if value:
                found.append((selector, value[:100]))
    # Only report potential values; do not claim these are the final checkout price.
    metadata = []
    for attr in ("price", "product:price:amount"):
        for tag in soup.find_all("meta", attrs={"property": attr})[:2]:
            metadata.append((attr, tag.get("content", "")))
    return {
        "http": response.status_code,
        "url": response.url,
        "bytes": len(response.content),
        "title": title[:200],
        "captcha": detected,
        "prices": found,
        "metadata": metadata,
        "currency_eur": "€" in html or "EUR" in html,
    }

if st.button("Amazon-Abfrage diagnostizieren", type="primary"):
    with st.spinner("Amazon-Antwort wird geprüft …"):
        result = diagnose()
    st.write("**Prüfzeitpunkt (UTC):**", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))
    if "error" in result:
        st.error(result["error"])
    else:
        st.write("**HTTP-Status:**", result["http"])
        st.write("**Seitentitel:**", result["title"])
        st.write("**Antwortgröße:**", f"{result['bytes']:,} Bytes")
        st.write("**Weiterleitungsziel:**", result["url"])
        st.write("**CAPTCHA-/Blockadehinweise:**", ", ".join(result["captcha"]) if result["captcha"] else "Keine typischen Hinweise erkannt")
        st.write("**EUR-Zeichen im HTML:**", "Ja" if result["currency_eur"] else "Nein")
        if result["prices"] or result["metadata"]:
            st.warning("Preis-Kandidaten gefunden. Noch nicht als gültiger Angebotspreis verifiziert.")
            st.table([{"Fundstelle": s, "Text": v} for s, v in result["prices"] + result["metadata"]])
        else:
            st.info("Kein Preis in den geprüften HTML-Elementen gefunden.")
        if result["captcha"] or result["http"] in (403, 429, 503):
            st.error("Amazon liefert möglicherweise eine Zugriffssperre oder Prüfseite.")
        elif result["http"] == 200 and not result["prices"]:
            st.info("Seite erreichbar, aber Preis nicht in den geprüften HTML-Elementen enthalten.")
        st.caption("Bitte nur diese Diagnosewerte teilen; keine Cookies, Login-Daten oder kompletten HTML-Seiten veröffentlichen.")
