"""Import application from this file in the hosting provider's WSGI config."""

from pathlib import Path

from ritm.hosting import create_application, load_settings
from ritm.database import open_store

settings = load_settings(Path(__file__).resolve().parent)
application = create_application(open_store(settings), settings)
