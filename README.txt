LBrixx V25 – Fehlerbehebung GitHub CSV

Ursache: Die GitHub Contents API gibt für Dateien über 1 MB ein leeres content-Feld zurück. Nach dem Odoo-Bildimport kann die CSV diese Grenze überschreiten.

V25 lädt in diesem Fall den tatsächlichen Inhalt über die GitHub Git-Blobs-API nach.
Eine wirklich leere Datei wird ausdrücklich als Fehler gemeldet und nicht stillschweigend ersetzt.

Installation: app.py auf GitHub ersetzen, Streamlit neu starten. Vorher nichts speichern und den GitHub Versionsverlauf sichern.
