LBrixx Price-Crawler V28.1

Installation: app.py und db_store.py im bestehenden GitHub-Repository ersetzen. requirements.txt unverändert.
Supabase-Tabellen und Streamlit Secrets nicht verändern.

Neu:
- Keine LBrixx-Artikelnummer in Formular und Tabellenansicht
- Allgemeines Formular „Lootstore-Artikel hinzufügen“ (SKU + Produkt-URL)
- Bereinigung der Produktlinks um Suchparameter
- Preisverlauf direkt in der Artikelbearbeitung, Versandkosten optional
- Eigene LBrixx-Preisänderungen in bestehender price_history, ohne Odoo-Writeback

Hinweis: Vorhandene LBrixx-Preise haben noch keine rückwirkende Historie.
Vor Upload GitHub-Commit/Backup sichern.
