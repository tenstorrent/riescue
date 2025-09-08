# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import os
import sys
import logging
import argparse
from pathlib import Path

from logging import ERROR as ERROR
from logging import WARNING as WARN
from logging import INFO as INFO
from logging import DEBUG as DEBUG


class LoggerError(Exception):
    "Generic Error for Logger class"

    pass


class MaxSizeHandler(logging.Handler):
    """
    Logging handler that checks file size between writes.
    Throws RuntimeError if file size exceeds max_bytes.
    Errors throw LoggerError
    """

    def __init__(self, filename, max_bytes, mode="a"):
        super().__init__()
        self.filename = filename
        self.max_bytes = max_bytes
        self.mode = mode
        self.stream = None
        self._open_stream()

    def _open_stream(self):
        # if self.filename.exists():
        #     print("log file already exists, deleting it")
        self.stream = open(self.filename, self.mode)

    def emit(self, record):
        if os.path.getsize(self.filename) > self.max_bytes:
            error = "Error: Logger MaxSizeFileHandler file size exceeded" f"- wrote {os.path.getsize(self.filename)} / {self.max_bytes} bytes"
            raise RuntimeError(error)
        msg = self.format(record)
        if self.stream is None:
            raise RuntimeError("Stream is not open but emit was called")
        self.stream.write(msg + "\n")

    def close(self):
        if self.stream is not None:
            self.stream.close()
        super().close()


def log_format() -> logging.Formatter:
    """
    Formatter for log messages.

    :returns: Configured formatter
    :rtype: logging.Formatter
    """
    level = "%(levelname)s: "
    timestamp = "[%(asctime)s] "
    funcname = "%(funcName)s: "
    filename = "%(filename)s:"
    linenum = "%(lineno)s - "
    msg = "%(message)s "
    fmt = timestamp + level + funcname + filename + linenum + msg
    fmt = "[%(asctime)s] %(levelname)s: %(name)s:%(lineno)s %(funcName)s - %(message)s "
    return logging.Formatter(fmt, datefmt="%Y-%m-%dT%H:%M:%S")


def add_args(parser: argparse.ArgumentParser):
    """Add logger arguments to parser.

    :param parser: ArgumentParser to add logger arguments to
    :type parser: argparse.ArgumentParser
    """
    logger_parser = parser.add_argument_group("Logger", description="Arguments that affect Logger behavior")
    logger_parser.add_argument("--logger_level", type=str.upper, default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="Logger level")
    logger_parser.add_argument("--logger_file", type=Path, default=None, help="Logger file path")
    logger_parser.add_argument("--logger_no_tee", dest="logger_tee", action="store_false", default=True, help="Do not tee log output. Default command line behavior is to tee to stderr")
    logger_parser.add_argument("--logger_no_timestamp", dest="logger_timestamp", action="store_false", default=True, help="Do not include timestamp in log messages")
    logger_parser.add_argument("--logger_max_file_size_gb", type=int, default=1, help="Max size of log file in GB. Throws error if exceeded")
    logger_parser.add_argument("--logger_verbose", dest="verbose_logging", action="store_true", default=False, help="Enable verbose logging (filename, function name)")


def from_args(args: argparse.Namespace, default_logger_file=None):
    "Create a Logger instance from command-line arguments. Optional `default_logger_file` if `--logger_file` argument isn't used"
    logger_file = args.logger_file
    if args.logger_file is None:
        if default_logger_file is None:
            raise LoggerError("No --logger_file specified and no default_logger_file set")
        logger_file = default_logger_file
    return init_logger(
        logger_file, level=args.logger_level, max_log_size=args.logger_max_file_size_gb, tee_to_stderr=args.logger_tee, logger_timestamp=args.logger_timestamp, verbose=args.verbose_logging
    )


def init_logger(log_path: Path, level: str = "WARNING", max_log_size: int = 1, tee_to_stderr: bool = False, logger_timestamp: bool = True, verbose: bool = False) -> None:
    """
    Initializes a logger instance.

    :param log_path: Path to log file
    :type log_path: str or Path
    :param level: Logging level (DEBUG, INFO, WARN, ERROR)
    :type level: int
    :param max_log_size: Maximum log file size in GB
    :type max_log_size: int
    :param verbose: Enable verbose logging
    :type verbose: bool

    .. code-block:: python

        # Initializing for first time:
        from riescue.lib.logger import init_logger
        log = init_logger(log_path="riescue.log", level="DEBUG", max_log_size=1, tee_to_stderr=True, verbose=True)

        # Subsequent times:
        import logging
        ...
        log = logging.getLogger(__name__)
        log.info("Hello, world!")
    """

    logger = logging.getLogger("riescue")
    # If already configured, return the existing logger
    if any(not isinstance(h, logging.NullHandler) for h in logger.handlers):
        return

    # Verbose logging includes filename and function name
    default_fmt = "%(levelname)s %(name)s:%(lineno)d  %(message)s"
    verbose_fmt = "%(levelname)s %(name)s %(filename)s:%(lineno)d %(funcName)s(): %(message)s"
    if verbose:
        fmt = verbose_fmt
    else:
        fmt = default_fmt
    if logger_timestamp:
        fmt = "[%(asctime)s]" + fmt
    logger_format = logging.Formatter(fmt, datefmt="%Y-%m-%dT%H:%M:%S")
    logger.setLevel(INFO)

    if tee_to_stderr:
        stderr_handler = logging.StreamHandler(sys.stderr)  # Log to stderr
        stderr_handler.setFormatter(logger_format)
        logger.addHandler(stderr_handler)

    max_size_handler = MaxSizeHandler(
        filename=log_path,
        mode="w",
        max_bytes=int(1024 * 1024 * 1024 * max_log_size),
    )
    max_size_handler.setFormatter(logger_format)  # Log to file with max size
    logger.addHandler(max_size_handler)

    logger.propagate = False  # Riescue-wide logger, not to external loggers
    logging.getLogger(__name__).info(f"Logger initialized, setting log level to {level}")
    logger.setLevel(getattr(logging, level))
    return


def close_logger():
    """
    Close all logger handlers. Ensures that logger can be relaunched with a new file after opening.

    Used to prevent logging to the previous file when relaunching the logger with a new file.
    """

    logger = logging.getLogger("riescue")
    for handler in logger.handlers[:]:
        handler.close()
    logger.setLevel(logging.WARNING)


def set_package_log_level(package_name, level):
    """
    Set log level for a package and all its modules for all modules loaded
    This is a workaround for propagate=False, to avoid polluting the root logger

    E.g. to set the log level for all modules in the riescue.dtest_framework.lib package to INFO:
    .. code-block:: python

        from riescue.lib.logger import set_package_log_level
        set_package_log_level("riescue.dtest_framework.lib", logging.INFO)

    """
    logger = logging.getLogger(package_name)
    logger.setLevel(level)
    # For modules already loaded
    prefix = package_name + "."
    for name in list(logging.Logger.manager.loggerDict.keys()):
        if name.startswith(prefix):
            logging.getLogger(name).setLevel(level)
