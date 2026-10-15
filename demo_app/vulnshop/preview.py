import requests


def fetch_preview(url: str) -> str:
    resp = requests.get(url, timeout=5)
    return resp.text[:500]
