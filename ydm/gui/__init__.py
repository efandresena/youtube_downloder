"""
YDM GUI Package — GTK4 + libadwaita interface.

Exports the main application class and top-level window so that
`main.py` only needs a single import.
"""

from .app import YDMApp
from .main_window import MainWindow

__all__ = [
    "YDMApp",
    "MainWindow",
]
