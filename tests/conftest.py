"""Tests never touch GitHub, cloud services or model APIs unless a test sets a key on purpose."""
from faultline import config

config.GITHUB_REPO = ""
config.PUBLIC_MODE = False
config.OPENROUTER_API_KEY = ""
config.GEMINI_API_KEY = ""
