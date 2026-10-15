"""Tests never touch cloud services: force local FalkorDB and disable real GitHub writes."""
import os

from reachable import config

config.FALKORDB_HOST = os.environ.get("TEST_FALKORDB_HOST", "localhost")
config.FALKORDB_PORT = int(os.environ.get("TEST_FALKORDB_PORT", "6379"))
config.FALKORDB_USERNAME = None
config.FALKORDB_PASSWORD = None
config.FALKORDB_SSL = False
config.GITHUB_REPO = ""
config.PUBLIC_MODE = False
