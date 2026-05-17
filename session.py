"""
http.py — shared HTTP session factory used by all API modules.
"""

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def create_session(pool_size: int = 10) -> requests.Session:
    """Create a requests Session with retry logic and sized connection pool.

    pool_size should equal MAX_PARALLEL_REQUESTS so urllib3 never needs more
    connections than there are active threads, avoiding 'Resetting dropped
    connection' noise from pool overflow.
    """
    session = requests.Session()
    adapter = HTTPAdapter(
        pool_connections=pool_size,
        pool_maxsize=pool_size,
        pool_block=True,          # block rather than open extra connections
        max_retries=Retry(
            total=5,
            read=5,
            connect=5,
            backoff_factor=0.3,
            allowed_methods=None,  # retry on all HTTP methods (GETs are safe)
            status_forcelist=(429, 500, 502, 503, 504),
            raise_on_status=False,
        ),
    )
    session.mount("http://",  adapter)
    session.mount("https://", adapter)
    return session
