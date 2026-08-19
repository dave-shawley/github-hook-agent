from importlib import metadata

version = metadata.version("github-hook-agent")
__version__ = version

del metadata
