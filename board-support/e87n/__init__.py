"""Debian-native E87N hardware readers and display configuration.

Importing this package performs no I/O. Fan control belongs to the kernel.
"""

__all__ = ["HardwareError", "load_display_config", "snapshot"]


def __getattr__(name):
    # `python -m e87n.display --preview` first imports this package. Keep the
    # hardware backend entirely unloaded until a caller requests its public API.
    if name not in __all__:
        raise AttributeError(name)
    from . import hardware
    return getattr(hardware, name)
