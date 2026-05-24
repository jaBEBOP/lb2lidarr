"""
config.py — all runtime configuration for lb2lidarr.

Values are module-level variables so every other module can do:

    from config import LIDARR_URL, MAX_PARALLEL_REQUESTS, ...

load() must be called once from main() before anything else runs.
"""

import logging
import os
from typing import List

from dotenv import load_dotenv

logger = logging.getLogger("lb2lidarr.config")

# ---------------------------------------------------------------------------
# ListenBrainz
# ---------------------------------------------------------------------------
LISTENBRAINZ_USERS: List[str] = []
LISTENBRAINZ_TOKENS: List[str] = []

# Collaborative filtering recommendations
# Set to False to disable the recommendations source entirely
ENABLE_RECOMMENDATIONS: bool = True
# How many recommended recordings to fetch per user (max 1000 per API call)
RECOMMENDATION_COUNT: int = 100

# ---------------------------------------------------------------------------
# Lidarr
# ---------------------------------------------------------------------------
LIDARR_URL: str = ""
LIDARR_API_KEY: str = ""
LIDARR_ROOT_FOLDER: str = ""
LIDARR_QUALITY_PROFILE_ID: int = 1
LIDARR_METADATA_PROFILE_ID: int = 1

# ---------------------------------------------------------------------------
# MusicBrainz
# ---------------------------------------------------------------------------
MUSICBRAINZ_URL: str = "https://musicbrainz.org"
USE_LOCAL_MUSICBRAINZ: bool = False
LOCAL_MUSICBRAINZ_URL: str = ""

# ---------------------------------------------------------------------------
# Rate limiting & performance
# ---------------------------------------------------------------------------
ENABLE_RATE_LIMITING: bool = True
RATE_LIMIT_DELAY: float = 0.5
MAX_PARALLEL_REQUESTS: int = 10
REQUEST_TIMEOUT: int = 30

# ---------------------------------------------------------------------------
# Artist-indexing poll
# ---------------------------------------------------------------------------
ARTIST_INDEX_POLL_INTERVAL: int = 5
ARTIST_INDEX_TIMEOUT: int = 300

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
CACHE_TTL: int = 3600


def load() -> bool:
    """Load configuration from environment / .env file. Returns True if valid."""
    load_dotenv()

    global LISTENBRAINZ_USERS, LISTENBRAINZ_TOKENS
    global ENABLE_RECOMMENDATIONS, RECOMMENDATION_COUNT
    global LIDARR_URL, LIDARR_API_KEY, LIDARR_ROOT_FOLDER
    global LIDARR_QUALITY_PROFILE_ID, LIDARR_METADATA_PROFILE_ID
    global MUSICBRAINZ_URL, USE_LOCAL_MUSICBRAINZ, LOCAL_MUSICBRAINZ_URL
    global ENABLE_RATE_LIMITING, RATE_LIMIT_DELAY
    global MAX_PARALLEL_REQUESTS, REQUEST_TIMEOUT
    global ARTIST_INDEX_POLL_INTERVAL, ARTIST_INDEX_TIMEOUT

    LISTENBRAINZ_USERS  = [u.strip() for u in os.getenv("LISTENBRAINZ_USERS",  "").split(",") if u.strip()]
    LISTENBRAINZ_TOKENS = [t.strip() for t in os.getenv("LISTENBRAINZ_TOKENS", "").split(",") if t.strip()]

    ENABLE_RECOMMENDATIONS = os.getenv("ENABLE_RECOMMENDATIONS", "true").lower() == "true"
    RECOMMENDATION_COUNT   = int(os.getenv("RECOMMENDATION_COUNT", "100"))

    LIDARR_URL                 = os.getenv("LIDARR_URL", "").rstrip("/")
    LIDARR_API_KEY             = os.getenv("LIDARR_API_KEY", "")
    LIDARR_ROOT_FOLDER         = os.getenv("LIDARR_ROOT_FOLDER", "")
    LIDARR_QUALITY_PROFILE_ID  = int(os.getenv("LIDARR_QUALITY_PROFILE_ID", "1"))
    LIDARR_METADATA_PROFILE_ID = int(os.getenv("LIDARR_METADATA_PROFILE_ID", "1"))

    USE_LOCAL_MUSICBRAINZ = os.getenv("USE_LOCAL_MUSICBRAINZ", "false").lower() == "true"
    LOCAL_MUSICBRAINZ_URL = os.getenv("LOCAL_MUSICBRAINZ_URL", "")
    MUSICBRAINZ_URL = (
        LOCAL_MUSICBRAINZ_URL.rstrip("/")
        if USE_LOCAL_MUSICBRAINZ and LOCAL_MUSICBRAINZ_URL
        else "https://musicbrainz.org"
    )

    ENABLE_RATE_LIMITING       = os.getenv("ENABLE_RATE_LIMITING", "true").lower() == "true"
    RATE_LIMIT_DELAY           = float(os.getenv("RATE_LIMIT_DELAY", "0.5"))
    MAX_PARALLEL_REQUESTS      = int(os.getenv("MAX_PARALLEL_REQUESTS", "10"))
    REQUEST_TIMEOUT            = int(os.getenv("REQUEST_TIMEOUT", "30"))
    ARTIST_INDEX_POLL_INTERVAL = int(os.getenv("ARTIST_INDEX_POLL_INTERVAL", "5"))
    ARTIST_INDEX_TIMEOUT       = int(os.getenv("ARTIST_INDEX_TIMEOUT", "300"))

    return validate()


def validate() -> bool:
    """Check that all required settings are present."""
    missing = [
        name for name, val in {
            "LIDARR_URL": LIDARR_URL,
            "LIDARR_API_KEY": LIDARR_API_KEY,
            "LIDARR_ROOT_FOLDER": LIDARR_ROOT_FOLDER,
        }.items()
        if not val
    ]
    if missing:
        logger.error(f"Missing required environment variables: {', '.join(missing)}")
        return False
    if not LISTENBRAINZ_USERS or not LISTENBRAINZ_TOKENS:
        logger.error("No ListenBrainz users/tokens configured (LISTENBRAINZ_USERS, LISTENBRAINZ_TOKENS)")
        return False
    if len(LISTENBRAINZ_USERS) != len(LISTENBRAINZ_TOKENS):
        logger.error("LISTENBRAINZ_USERS and LISTENBRAINZ_TOKENS must have the same number of entries")
        return False
    return True


def summary() -> str:
    """Return a human-readable config summary for startup logging."""
    rec_status = f"enabled ({RECOMMENDATION_COUNT} per user)" if ENABLE_RECOMMENDATIONS else "disabled"
    return "\n".join([
        f"  ListenBrainz users   : {len(LISTENBRAINZ_USERS)}",
        f"  Recommendations      : {rec_status}",
        f"  Lidarr URL           : {LIDARR_URL}",
        f"  Lidarr root folder   : {LIDARR_ROOT_FOLDER}",
        f"  MusicBrainz server   : {MUSICBRAINZ_URL}",
        f"  Rate limiting        : {'enabled (' + str(RATE_LIMIT_DELAY) + 's)' if ENABLE_RATE_LIMITING else 'disabled'}",
        f"  Parallel workers     : {MAX_PARALLEL_REQUESTS}",
        f"  Artist index poll    : {ARTIST_INDEX_POLL_INTERVAL}s (timeout {ARTIST_INDEX_TIMEOUT}s)",
    ])
