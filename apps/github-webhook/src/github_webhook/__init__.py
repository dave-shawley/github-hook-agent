from importlib import metadata

version = metadata.version('github-webhook')
__version__ = version

del metadata
