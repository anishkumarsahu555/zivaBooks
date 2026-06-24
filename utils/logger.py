import glob
import logging
import threading
import time
from logging.handlers import QueueHandler, QueueListener
import os
import inspect
from datetime import datetime, timedelta
from queue import Queue

# root project path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

_log_queue = Queue()
_listener = None
_file_handler = None
_current_log_date = None
_rotation_thread = None
_stop_rotation = False

# Flags to control logging destinations
LOG_TO_FILE = os.getenv("WRITE_LOG_ON_FILE", "true").lower() == "true"
LOG_TO_CONSOLE = os.getenv("WRITE_LOG_ON_CONSOLE", "true").lower() == "true"

def _get_caller_app_name():
    stack = inspect.stack()
    for frame_info in stack:
        module = inspect.getmodule(frame_info.frame)
        if module and hasattr(module, "__package__") and module.__package__:
            return module.__package__.split(".")[0]
    return "app"


def _cleanup_old_logs(days=5):
    cutoff = datetime.now() - timedelta(days=days)
    for log_file in glob.glob(os.path.join(LOG_DIR, "*.log")):
        file_time = datetime.fromtimestamp(os.path.getmtime(log_file))
        if file_time < cutoff:
            try:
                os.remove(log_file)
            except OSError:
                pass


def _get_log_file_path():
    return os.path.join(LOG_DIR, datetime.now().strftime("%Y-%m-%d") + ".log")


def _create_file_handler():
    global _file_handler, _current_log_date
    _current_log_date = datetime.now().date()

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(app_name)s | %(filename)s:%(funcName)s:%(lineno)d | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    class AppNameFilter(logging.Filter):
        def filter(self, record):
            record.app_name = _get_caller_app_name()
            return True

    handler = logging.FileHandler(_get_log_file_path(), encoding="utf-8")
    handler.setFormatter(formatter)
    handler.addFilter(AppNameFilter())
    _file_handler = handler

    _cleanup_old_logs(days=5)
    return handler


def _rotation_worker(listener):
    global _file_handler, _current_log_date, _stop_rotation
    while not _stop_rotation:
        time.sleep(60)
        if datetime.now().date() != _current_log_date and LOG_TO_FILE:
            new_handler = _create_file_handler()
            if len(listener.handlers) > 1:
                listener.handlers[1].close()
                listener.handlers[1] = new_handler


def _init_logger():
    global _listener, _rotation_thread

    logger_instance = logging.getLogger("myproject")
    logger_instance.setLevel(logging.INFO)
    logger_instance.propagate = False

    if not logger_instance.handlers:
        handlers = []

        if LOG_TO_CONSOLE:
            formatter = logging.Formatter(
                "%(asctime)s | %(levelname)s | %(app_name)s | %(filename)s:%(funcName)s:%(lineno)d | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S"
            )

            class AppNameFilter(logging.Filter):
                def filter(self, record):
                    record.app_name = _get_caller_app_name()
                    return True

            console_handler = logging.StreamHandler()
            console_handler.setFormatter(formatter)
            console_handler.addFilter(AppNameFilter())
            handlers.append(console_handler)

        if LOG_TO_FILE:
            file_handler = _create_file_handler()
            handlers.append(file_handler)

        queue_handler = QueueHandler(_log_queue)
        logger_instance.addHandler(queue_handler)

        if _listener is None:
            _listener = QueueListener(_log_queue, *handlers)
            _listener.start()

        if _rotation_thread is None and LOG_TO_FILE:
            _rotation_thread = threading.Thread(target=_rotation_worker, args=(_listener,), daemon=True)
            _rotation_thread.start()

    return logger_instance


logger = _init_logger()