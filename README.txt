LBrixx V28.5 – gezielte SKU-Prüfung in Brickmo-Sitemaps

Installation: Nur app.py und brickmo_index.py in GitHub ersetzen.
Andere Dateien, Secrets und Supabase-Daten bleiben unverändert.

1. Brickmo-Produktindex verwalten öffnen.
2. Im Feld "Artikelnummer in sämtlichen Sitemap-URLs prüfen" FO9054 belassen.
3. "Brickmo-Katalog aktualisieren und SKU prüfen" klicken.
4. Katalog-Diagnose zeigt Anzahl geprüfter URLs und exakte FO9054-Treffer.

Hinweis: Ein Treffer wird nur dann gemeldet, wenn die SKU als eigenständiger
Teil der URL oder eines Query-Parameters vorkommt. Fehlt FO9054 in der
Sitemap, ist das kein Beweis, dass der Artikel bei Brickmo nicht existiert.
Die App ändert durch die Diagnose keine Produktzuordnungen.
