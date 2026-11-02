"""Shared window icons and Windows taskbar identity for the desktop tools."""
from pathlib import Path
import sys
import tkinter as tk


def set_taskbar_identity():
    """Separate GeoCANOE windows from the Python taskbar group."""
    if sys.platform == 'win32':
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('GeoCANOE.Interface')


def set_window_icon(window, root):
    """Load the existing PNG, which intentionally has no filename extension."""
    logo = Path(root) / 'GeoCANOE Logo'
    if not logo.is_file():
        return
    original = tk.PhotoImage(master=window, file=str(logo))
    icons = [original.subsample(max(1, round(original.width() / size)))
             for size in (16, 32, 48, 64, 128, 256)]
    window.iconphoto(True, *icons)
    # Keep Tk image objects alive for this window's lifetime.
    window._geocanoe_icons = icons
