# LBrixx Amazon-Diagnose V6

## Streamlit Community Cloud

1. `app.py`, `requirements.txt` und `packages.txt` in das **Hauptverzeichnis** des GitHub-Repositories hochladen. Vorhandene Dateien ersetzen.
2. Die App neu starten: Streamlit Community Cloud > Manage app > Reboot app. Logs auf Paketinstallationsfehler prüfen.
3. Streamlit installiert Python-Pakete aus `requirements.txt`. Die Browser-Binärdatei von Playwright wird dadurch **nicht automatisch** installiert.
4. Für einen ersten Test kann Chromium über die App-Verwaltung/Terminal der Laufzeit mit `python -m playwright install chromium` installiert werden, falls dort eine Shell bereitsteht. Bei Community Cloud gibt es normalerweise keinen dauerhaften Shell-Zugang. Deshalb ist der Browser auf dieser Plattform ohne geeigneten Build-Schritt möglicherweise nicht verfügbar.
5. Wenn der Browser fehlt, ist das kein Amazon-Preisfehler. Dann eine Hosting-Umgebung mit reproduzierbarem Build (z. B. Docker) nutzen, statt Downloads bei jedem Nutzerklick auszuführen.

**Hinweis:** Der Test ist rein diagnostisch. Es werden keine unbestätigten Preise gespeichert. Keine CAPTCHA-Umgehung oder Tarnung.
