"""Exporter registry.

An exporter is a function that takes a Report and returns a str.
Register it with the @register("name") decorator, and import its module below.
"""

_REGISTRY = {}
ALIASES = {"md": "markdown"}


def register(name):
    def decorator(func):
        _REGISTRY[name] = func
        return func
    return decorator


def get_exporter(name):
    name = ALIASES.get(name, name)
    if name not in _REGISTRY:
        raise ValueError(f"unknown format: {name}")
    return _REGISTRY[name]


def available():
    return sorted(_REGISTRY)


def export(report, fmt):
    return get_exporter(fmt)(report)


from . import csv_export, html_export, json_export, markdown_export, text  # noqa: E402,F401
