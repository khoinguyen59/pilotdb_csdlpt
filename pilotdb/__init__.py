"""PilotDB public API.

Imports are intentionally lazy so lightweight submodules (for example optimizer
unit tests) do not require the full SQL rewriting dependency stack at import
collection time.
"""


def connect(*args, **kwargs):
    from .execute import connect as _connect

    return _connect(*args, **kwargs)


def run(*args, **kwargs):
    from .execute import run as _run

    return _run(*args, **kwargs)


def close(*args, **kwargs):
    from .execute import close as _close

    return _close(*args, **kwargs)
