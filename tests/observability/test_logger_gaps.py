"""Coverage completion: get_logger, __main__ blocks, RedactFilter no-args."""

from __future__ import annotations

import logging
import runpy
import sys

import pytest

from dev_harness.observability.logging import RedactFilter, get_logger

pytestmark = pytest.mark.unit


class TestGetLogger:
    def test_get_logger_creates_handler(self) -> None:
        logger = get_logger("test.get_logger", correlation_id="corr-x")
        assert logger.level == logging.INFO
        assert logger.propagate is False
        assert len(logger.handlers) == 1

    def test_get_logger_reuses_existing_handler(self) -> None:
        logger1 = get_logger("test.reuse")
        logger2 = get_logger("test.reuse")
        assert logger1 is logger2
        assert len(logger1.handlers) == 1  # not duplicated

    def test_get_logger_custom_level(self) -> None:
        logger = get_logger("test.level", level=logging.WARNING)
        assert logger.level == logging.WARNING


class TestRedactFilterNoArgs:
    def test_filter_no_args(self) -> None:
        record = logging.LogRecord(
            name="t",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="plain message",
            args=None,
            exc_info=None,
        )
        filt = RedactFilter()
        assert filt.filter(record) is True


class TestMainBlocks:
    def test_schema_main_block(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "argv", ["schema", "--check"])
        with pytest.raises(SystemExit) as ei:
            runpy.run_module("dev_harness.contracts.schema", run_name="__main__")
        assert ei.value.code == 0

    def test_transitions_main_block(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "argv", ["transitions", "--table"])
        with pytest.raises(SystemExit) as ei:
            runpy.run_module("dev_harness.contracts.transitions", run_name="__main__")
        assert ei.value.code == 0
