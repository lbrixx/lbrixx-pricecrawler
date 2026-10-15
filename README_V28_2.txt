LBrixx Price-Crawler V28.2 – Schnellzuordnung mit manueller Bestätigung

Installation: app.py und db_store.py ersetzen, quickmatch.py zusätzlich ins gleiche GitHub-Verzeichnis hochladen. requirements.txt unverändert.

Bedienung:
1. Bereich „Schnellzuordnung – Shops automatisch durchsuchen“ öffnen.
2. Brickmo oder Lootstore auswählen; zunächst nur 5 Artikel suchen.
3. Treffer durch Klick auf „Shop-Artikel öffnen“ kontrollieren.
4. „Zuordnung bestätigen“ anklicken. Nur der eine Link wird in Supabase gespeichert.
5. Danach bestehende Preisabfrage für den Shop ausführen.

Wichtig: Shop-Suchseiten können sich ändern oder Zugriffe blockieren. Dann gibt es keinen Treffer oder einen Suchhinweis; es erfolgt niemals eine automatische Zuordnung. Keine Änderung des DB-Schemas oder der Secrets erforderlich. Nicht verknüpfte Artikel bleiben unverändert. Der Suchlauf prüft die ersten noch nicht zugeordneten Artikel; eine gezielte Auswahl/Seitennavigation kann später ergänzt werden.
