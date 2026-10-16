LBrixx V28.2 – Brickmo-Suche: exakte Treffer

Installation: NUR quickmatch.py im GitHub-Repository ersetzen.
Alle anderen Dateien bleiben unverändert. Kein Supabase-Schemawechsel.

Korrektur:
- Unpassende Kandidaten (FO9051 usw.) werden nicht mehr vorgeschlagen.
- F9054 und FO9054 gelten als Varianten; die Artikelnummer im Link wird geprüft.
- Drei Brickmo-Suchpfade werden versucht.
- Falls Brickmo keine exakten Links zurückgibt, wird ein öffentlicher Suchindex versucht.
- Ohne exakten Treffer erfolgt KEINE Zuordnung.

WICHTIG: Der Suchindex kann Anfragen blockieren oder nicht indexierte Produkte
übersehen. Ein ausbleibender Treffer bedeutet daher nicht, dass der Artikel
bei Brickmo fehlt. Bitte F9054 testen und Suchdiagnose zurückmelden.
