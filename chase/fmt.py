"""Terminal color output helpers."""

import os
import sys

_NO_COLOR = os.environ.get("NO_COLOR") or not sys.stdout.isatty()


def _wrap(code: str, text: str) -> str:
    if _NO_COLOR:
        return text
    return f"\033[{code}m{text}\033[0m"


def green(text: str) -> str:
    return _wrap("32", text)


def red(text: str) -> str:
    return _wrap("31", text)


def yellow(text: str) -> str:
    return _wrap("33", text)


def cyan(text: str) -> str:
    return _wrap("36", text)


def bold(text: str) -> str:
    return _wrap("1", text)


def print_green(text: str) -> None:
    print(green(text))


def print_red(text: str) -> None:
    print(red(text), file=sys.stderr)


def print_yellow(text: str) -> None:
    print(yellow(text))


def print_bold(text: str) -> None:
    print(bold(text))
