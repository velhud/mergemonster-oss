import logging

def test_logger_propagation_disabled(tmp_path):
    from codemerge import logger as log_mod

    log = log_mod.get_logger("test_logger_propagation")
    assert log.propagate is False

    # Emitting a log should not add duplicate handlers to the root logger.
    before = len(logging.getLogger().handlers)
    log.info("hello")
    after = len(logging.getLogger().handlers)
    assert after == before


def test_provider_credentials_are_redacted_from_tracebacks():
    import logging
    import sys

    from codemerge.logger import RedactingFormatter

    marker = "gsk_" + "fake-test-credential"
    try:
        raise ValueError(f"Invalid credential {marker}")
    except ValueError:
        record = logging.LogRecord("test", logging.ERROR, "", 0, "request failed", (), sys.exc_info())
    rendered = RedactingFormatter().format(record)
    assert marker not in rendered
    assert "Invalid credential ***" in rendered
