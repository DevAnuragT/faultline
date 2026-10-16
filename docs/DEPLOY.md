# Deploying the public site

The app needs five FalkorDB values, a Gemini key and `REACHABLE_PUBLIC=true`. Seed the cloud graph first from your laptop
(`make seed` with `.env` pointing at the cloud instance). The FalkorDB Cloud free tier accepts plain connections
(`FALKORDB_SSL=false`) and has no IP allow-list, so if the app shows "Cannot connect ... TimeoutError" the host it runs on
cannot open a TCP socket to the instance port. The error page runs a DNS + TCP probe and says which it is.

## Streamlit Community Cloud (first choice)

App settings > Secrets: paste `.streamlit/secrets.toml.example` with real values (all quoted strings). The app reads
`st.secrets` and the environment, so either works. Main file `streamlit_app.py`, Python 3.12.

## Render (fallback, uses the Dockerfile)

1. Render > New > **Blueprint**, pick this repo. `render.yaml` defines a free web service.
2. Fill the five prompted secrets (`FALKORDB_HOST/PORT/USERNAME/PASSWORD`, `GEMINI_API_KEY`).
3. Deploy. The free plan sleeps after 15 min idle and takes ~1 min to wake: open the URL once before a demo.

## Hugging Face Spaces (fallback, Docker SDK)

Create a Space with SDK **Docker**, push this repo to it, and add to the top of the Space's own `README.md`:

```yaml
---
title: Reachable
sdk: docker
app_port: 8501
---
```

Set the same variables under Settings > Variables and secrets.

## Smoke test after any deploy

Open the site, confirm the Triage tab loads the 33 advisories, then press **Plan & verify a fix now** on the
Graph-checked fix tab: it must end VERIFIED with 11 → 0 and leave the shared graph unchanged.
