# LBrixx Amazon Preisabruf – Test

Testet den öffentlichen HTML-Abruf für Amazon.de ASIN B0DYV3XVZ6. Der Preis ist nur ein unbestätigter Seitenpreis; Versand, Verkäufer und Coupons werden nicht erfasst. Bei Sperre oder fehlendem Preis wird eine Fehlermeldung statt eines Schätzwerts angezeigt.

Lokal starten:

```bash
pip install -r requirements.txt
streamlit run app.py
```

Für ein kostenloses öffentliches Hosting kann der Ordner in ein GitHub-Repository gelegt und über Streamlit Community Cloud veröffentlicht werden. Die HTML-Abfrage kann von Amazon blockiert werden; für produktiven Einsatz ist ein autorisierter Datenzugang erforderlich.
