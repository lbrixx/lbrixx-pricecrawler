"""LBrixx: isolated Supabase connectivity check; no writes."""
import streamlit as st
import psycopg

st.set_page_config(page_title="LBrixx – Datenbanktest", page_icon="🗄️")
st.title("LBrixx – Supabase-Verbindungstest")
st.caption("Nur lesender Test. Es werden keine Tabellen oder Daten verändert.")

EXPECTED = ["brands", "products", "merchants", "offers", "current_prices", "price_history", "odoo_products"]

if st.button("Verbindung prüfen", type="primary"):
    try:
        url = st.secrets.get("SUPABASE_DB_URL")
        if not url:
            st.error("SUPABASE_DB_URL fehlt in den Streamlit Secrets.")
            st.stop()
        # Never display the connection URL or raw exception details (may contain secrets).
        with psycopg.connect(url, connect_timeout=12, sslmode="require") as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
                present = {row[0] for row in cur.fetchall()}
        missing = [name for name in EXPECTED if name not in present]
        if missing:
            st.warning("Verbindung erfolgreich, aber es fehlen Tabellen: " + ", ".join(missing))
        else:
            st.success("Verbindung erfolgreich! Alle sieben LBrixx-Tabellen gefunden.")
    except psycopg.OperationalError:
        st.error("Verbindung fehlgeschlagen. Bitte Session-Pooler-URI, Passwort und SSL-Einstellungen in den Secrets kontrollieren.")
    except Exception:
        st.error("Test fehlgeschlagen. Bitte die Datenbankeinstellungen prüfen; Zugangsdaten nicht im Chat teilen.")
