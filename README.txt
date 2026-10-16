LBrixx Price-Crawler V28.3 – Brickmo-Produktindex (Beta)

Installation:
1. Den bisherigen GitHub-Stand sichern.
2. app.py durch die neue Version ersetzen.
3. brickmo_index.py neu ins gleiche Verzeichnis hochladen.
4. db_store.py, quickmatch.py und requirements.txt können unverändert bleiben.
5. Streamlit neu starten.

Bedienung:
1. Bereich "Brickmo-Produktindex verwalten" öffnen.
2. "Brickmo-Katalog aktualisieren" anklicken.
3. Diagnose ansehen. Der Import kann einige Minuten dauern.
4. Bereich "Schnellzuordnung" öffnen, Brickmo wählen und F9054 eingeben.
5. Kandidatenlink öffnen und nur bei tatsächlicher Übereinstimmung bestätigen.

Technik:
- Neue PostgreSQL-Tabelle public.brickmo_catalog wird erst bei erfolgreichem Sitemap-Abruf automatisch erstellt.
- Dafür muss der Datenbankbenutzer CREATE-Rechte im Schema public besitzen.
- Nur erkannte Produkt-URLs mit SKU aus öffentlich zugänglichen Brickmo-Sitemaps werden übernommen.
- Keine Änderungen an products, offers, current_prices oder price_history.
- Bestehende Zuordnungen und Daten bleiben erhalten.
- Keine Garantie, dass Brickmo alle Artikel in seinen Sitemaps aufführt.
- Keine automatische Zuordnung.
- Bei 0 gefundenen Artikeln bitte Diagnose mitteilen. Der Code wurde nicht live gegen Brickmo getestet.
