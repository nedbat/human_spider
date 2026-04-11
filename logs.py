import logging


fetch_log = logging.getLogger("fetch")
fetch_log.setLevel(logging.DEBUG)
fetch_handler = logging.FileHandler("fetch.log", mode="w")
fetch_handler.setFormatter(
    logging.Formatter("%(asctime)s.%(msecs)03d %(message)s", datefmt="%H:%M:%S")
)
fetch_log.addHandler(fetch_handler)
