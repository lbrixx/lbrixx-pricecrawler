Separater LBrixx Supabase-Verbindungstest (kein Ersatz für V27).

In GitHub in einem separaten Test-Ordner ablegen oder eine temporäre Test-App mit Hauptdatei db_test.py bereitstellen.
Die App muss dieselben Streamlit-Secrets (SUPABASE_DB_URL) haben wie die Haupt-App; Secrets sind app-spezifisch.
In Streamlit Community Cloud beim Anlegen der Test-App db_test.py als Main file path auswählen.
Die App verbindet sich nur lesend und überprüft sieben Tabellen. Keine Datenänderungen.
Wichtig: Keine Zugangsdaten ins Repository hochladen.
