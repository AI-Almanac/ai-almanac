import logging


def test_migrations_preserve_existing_loggers() -> None:
    from ai_almanac.server.app import _apply_migrations

    uvicorn_logger = logging.getLogger("uvicorn.error")
    was_disabled = uvicorn_logger.disabled
    uvicorn_logger.disabled = False

    try:
        _apply_migrations()
        assert uvicorn_logger.disabled is False
    finally:
        uvicorn_logger.disabled = was_disabled
