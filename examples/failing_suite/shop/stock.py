import json


def average(total, count):
    return total / count


def load(text):
    return json.loads(text)


def label(n):
    if n > 0:
        return "positive"
    return "negative"


class Wrapper:
    def run(self, value):
        try:
            return int(value)
        except ValueError as exc:
            raise RuntimeError("bad value") from exc
