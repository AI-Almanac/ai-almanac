"""Database URL conversions shared by synchronous database consumers."""

from sqlalchemy.engine import make_url


def sync_database_url(database_url: str) -> str:
    """Return the synchronous-driver equivalent of an application database URL."""
    url = make_url(database_url)
    if url.drivername.startswith("sqlite"):
        url = url.set(drivername="sqlite")
    elif url.drivername.startswith("postgresql"):
        url = url.set(drivername="postgresql+psycopg")
    return url.render_as_string(hide_password=False)
