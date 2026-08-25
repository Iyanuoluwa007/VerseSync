"""VerseSync backend package.

`__version__` is the single source of truth for the release version.
The FastAPI app, the `/` health endpoint and the docs all read it from
here so they can never drift apart again.
"""

__version__ = "0.6.2"
