import pickle
import subprocess

import requests
import yaml


def load_snapshot(blob: bytes):
    return pickle.loads(blob)


def load_config(text: str):
    return yaml.load(text)


def fetch_prices(url: str) -> dict:
    return requests.get(url).json()


def backup(path: str) -> None:
    subprocess.run(f"tar czf backup.tgz {path}", shell=True, check=True)


def evaluate_rule(expression):
    return eval(expression)


def read_quantity(path: str) -> int:
    try:
        with open(path) as handle:
            return int(handle.read())
    except:
        pass
    return 0


def safe_ratio(a: float, b: float) -> float:
    try:
        return a / b
    except ZeroDivisionError:
        return 0.0
