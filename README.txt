LBrixx Price-Crawler V28 – PostgreSQL-Umstellung (erste Testversion)

1. In GitHub die bisherigen Dateien app.py und requirements.txt ersetzen.
2. NEU: db_store.py in das gleiche Repository-Verzeichnis wie app.py hochladen.
3. SUPABASE_DB_URL muss in den Secrets der BESTEHENDEN Streamlit-App hinterlegt sein.
4. Vorher V27 in GitHub sichern (Commit/Tag oder ZIP-Download).
5. App neu starten und Odoo-Verbindung zunächst mit 5 Produkten testen.
6. Danach vollständigen Odoo-Import durchführen. Alte GitHub-CSV wird NICHT eingelesen.
7. Brickmo/Lootstore-Links neu zuordnen und Preisabrufe einzeln testen.

Enthalten:
- PostgreSQL als einzige Datenquelle; kein GitHub-CSV-Schreibzugriff.
- Odoo-Import inklusive EAN/Barcode; Produkt-ID in SQL unabhängig von Odoo.
- Bestehende Händlerzuordnung, Preise, Status, Ausblenden/Löschen.
- Preisverlauf für Händler nach erfolgreichen Preisabfragen, optional inkl. Versand.
- Tabellenkopf fixiert; Sortieren durch Klick auf Spaltenüberschriften.
- Fehlermarkierung im Händlerpreisfeld; Shop- und Einzelaktualisierung.
- LBrixx-Preis im Edit-Formular bearbeitbar, nur intern in PostgreSQL.

Noch NICHT enthalten:
- Rückschreiben der LBrixx-Preise nach Odoo (aus Sicherheitsgründen gesperrt).
- LBrixx-Eigenpreis-Historie und Hintergrund-Jobs.
- Automatische Händler-Zuordnung (geplant für V29).
- Odoo-Bilder werden nicht mehr als Base64 in der Datenbank gespeichert;
  dafür wird eine separate Bildspeicherlösung benötigt.
- Tabellenaktionen sind noch aus V27 übernommen; bitte nach jedem Import testen.

Wichtig: Die SQL-Struktur muss wie zuvor angelegt sein. Das Datenbankschema
hat UNIQUE(product_id,merchant_id); pro Produkt/Händler ist ein Angebot möglich.
Es werden keine Zugangsdaten im Code gespeichert.
