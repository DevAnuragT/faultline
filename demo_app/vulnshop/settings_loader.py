import yaml


def parse_settings(text: str) -> dict:
    return yaml.full_load(text) or {}


def load_user_settings(raw: bytes) -> dict:
    return parse_settings(raw.decode("utf-8"))
