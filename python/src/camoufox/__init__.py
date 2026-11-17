from .addons import DefaultAddons
from .async_api import AsyncCamoufox, AsyncNewBrowser, AsyncNewContext, AsyncNewIdentity
from .sync_api import Camoufox, NewBrowser, NewContext, NewIdentity
from .utils import launch_options

__all__ = [
    "Camoufox",
    "NewBrowser",
    "NewContext",
    "NewIdentity",
    "AsyncCamoufox",
    "AsyncNewBrowser",
    "AsyncNewContext",
    "AsyncNewIdentity",
    "DefaultAddons",
    "launch_options",
]
