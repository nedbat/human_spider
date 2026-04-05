import sys


def error(msg: str, e: Exception | None = None) -> None:
    if e is not None:
        msg += f": {e.__class__.__name__}"
        if str(e):
            msg += f": {e}"
    print_both(f"** Error {msg}")


def print_both(msg: str) -> None:
    print(msg)
    print(msg, file=sys.stderr)
