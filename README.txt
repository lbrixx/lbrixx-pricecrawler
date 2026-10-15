LBrixx Price-Crawler V18
=======================
1. app.py und requirements.txt im GitHub-Repository lbrixx-pricecrawler ersetzen.
2. Streamlit neu starten lassen.
3. Artikel im Formular hinzufügen oder CSV importieren.
4. Für dauerhafte Speicherung Streamlit App settings > Secrets konfigurieren:
   GITHUB_REPO = "BENUTZERNAME/lbrixx-pricecrawler"
   GITHUB_TOKEN = "github_pat_..."
   GITHUB_DATA_PATH = "data/lbrixx_products.csv"
   Das Token muss Fine-grained sein und nur Contents: Read and write für dieses Repo erlauben.
   Keine Secrets in GitHub-Dateien oder im Chat veröffentlichen.
5. Ohne GitHub-Token werden Daten lokal gespeichert und können bei Streamlit-Neustarts verloren gehen.
6. CSV-Export zur Sicherung verwenden.

Hinweise: Versandkosten 5,49 EUR gelten für eine einzelne Brickmo-Bestellung nach DE.
Nur ausdrücklich als lieferbar erkannte Artikel gelten als kaufbar.
Die App ruft nur vom Nutzer angegebene Brickmo-URLs ab.
Bei Änderungen der Shop-Struktur muss der Parser angepasst werden.
