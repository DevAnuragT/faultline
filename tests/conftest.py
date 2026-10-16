"""Tests never touch GitHub or cloud services: no repo to open PRs or issues against, no model keys."""
from faultline import config

config.GITHUB_REPO = ""
config.PUBLIC_MODE = False
