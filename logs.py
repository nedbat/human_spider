import logging
import time


class RelativeFormatter(logging.Formatter):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.start = time.time()

    def format(self, record):
        record.relsecs = record.created - self.start
        return super().format(record)


fetch_log = logging.getLogger("fetch")
fetch_log.setLevel(logging.DEBUG)
fetch_handler = logging.FileHandler("fetch.log", mode="w")
fetch_handler.setFormatter(
    RelativeFormatter(
        "%(asctime)s %(relsecs)8.3f %(message)s", datefmt="%H:%M:%S"
    )
)
fetch_log.addHandler(fetch_handler)
