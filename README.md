# LBrixx Amazon Diagnose V5

## Streamlit Community Cloud

1. Upload `app.py`, `requirements.txt`, and `packages.txt` to the repository root.
2. Open Streamlit Cloud → Manage app → Settings → Secrets.
3. Add this TOML configuration:

```toml
[server]
# No settings required
```

4. IMPORTANT: Browser binaries are not installed by Python package installation alone. On Streamlit Community Cloud, a root-level `postBuild` hook may not be executed reliably. The robust option is to install Chromium from the Streamlit app at runtime on first launch, or use a deployment platform with a Docker build.

V5 uses a browser bootstrap below; consult app.py for its logic.
