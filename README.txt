LBrixx V28.2 – Brickmo-Suchfix

GitHub: app.py und quickmatch.py ersetzen.
db_store.py und requirements.txt sind unverändert.

Verbesserungen:
- F9054 und FO9054 als SKU-Varianten
- Exakte SKU-Treffer im Produktlink erhalten hohen Score
- Auch nicht eindeutige Kandidaten werden zur manuellen Prüfung angezeigt
- Ausführlichere Suchdiagnose
- Kein automatisches Zuordnen

Test: Schnellzuordnung > Brickmo > F9054 > Passende Shop-Artikel suchen.
Hinweis: Der Code ist lokal geprüft; ob Brickmo den konkreten Link in seinen
Suchergebnissen liefert, kann erst im Streamlit-Betrieb geprüft werden.
