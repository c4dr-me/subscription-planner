import logging
import os

LOG_FILE = "app.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger("subscription-agent")


def log_info(msg):
    logger.info(msg)


def log_error(msg):
    logger.error(msg)


def log_warning(msg):
    logger.warning(msg)
