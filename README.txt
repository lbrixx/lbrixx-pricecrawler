LBrixx Price-Crawler V20

NEU:
- 'Edit' als erste Spalte jeder Tabellenzeile.
- Klick öffnet ein Bearbeitungsformular für SKU, LBrixx-Nr., Beschreibung,
  LBrixx-Verkaufspreis und Brickmo-Produktlink.
- Speichern erfolgt über dieselbe GitHub-CSV-Speicherfunktion wie in V19.
- Bei geänderter Brickmo-URL werden alte Händlerpreise zurückgesetzt.
  Danach 'Alle Preise aktualisieren' verwenden.
- Grün/gelb/rot hinterlegte Brickmo-Preiszellen bleiben anklickbar.

INSTALLATION:
1. V19 zuerst als CSV exportieren (Sicherung).
2. app.py und requirements.txt in GitHub aktualisieren.
3. Streamlit lädt V20 automatisch neu.
4. Testweise einen Artikel über 'Edit' ändern und danach
   data/lbrixx_products.csv in GitHub kontrollieren.

Die GitHub-Secrets bleiben unverändert.
Bestehende Artikel werden nicht absichtlich gelöscht oder überschrieben.
Die CSV-Struktur ist identisch mit V19.
