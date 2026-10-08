"""Crashcourt: a conformance and reliability lab for durable agent execution."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("crashcourt")
except PackageNotFoundError:  # running from a source tree without installation
    __version__ = "0.0.0+local"
