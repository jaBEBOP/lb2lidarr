"""
musicbrainz.py — MusicBrainz API lookups and parallel track resolution.
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional, Set, Tuple

import requests

import config
from cache import RateLimiter, TTLCache
from session import create_session

logger = logging.getLogger("lb2lidarr.musicbrainz")

_session: requests.Session = create_session()
_rate_limiter: RateLimiter = RateLimiter()
_recording_cache: TTLCache = TTLCache()
_release_cache:   TTLCache = TTLCache()


def reinit(pool_size: int, rate_limiter: RateLimiter) -> None:
    """Recreate the session and rate limiter after config is loaded."""
    global _session, _rate_limiter
    _session = create_session(pool_size)
    _rate_limiter = rate_limiter


def clear_caches() -> None:
    _recording_cache.clear()
    _release_cache.clear()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _validate_mbid(mbid: str) -> bool:
    return bool(mbid) and len(mbid) == 36 and mbid.count("-") == 4


def _mb_get(path: str, params: dict) -> Optional[dict]:
    """GET from the configured MusicBrainz server with rate limiting."""
    _rate_limiter.wait()
    url = f"{config.MUSICBRAINZ_URL}/ws/2/{path}"
    try:
        resp = _session.get(url, params=params, timeout=config.REQUEST_TIMEOUT)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        logger.error(f"MusicBrainz request failed ({path}): {exc}")
        return None


# ---------------------------------------------------------------------------
# Public lookups
# ---------------------------------------------------------------------------
def lookup_recording(mbid: str) -> Optional[dict]:
    if not _validate_mbid(mbid):
        return None
    return _recording_cache.get_or_set(
        mbid, _mb_get, f"recording/{mbid}",
        {"inc": "releases+artist-credits", "fmt": "json"},
    )


def get_release_group_mbid(release_mbid: str) -> Optional[str]:
    if not _validate_mbid(release_mbid):
        return None

    def _fetch(mbid: str) -> Optional[str]:
        data = _mb_get(f"release/{mbid}", {"fmt": "json", "inc": "release-groups"})
        return data.get("release-group", {}).get("id") if data else None

    return _release_cache.get_or_set(release_mbid, _fetch, release_mbid)


# ---------------------------------------------------------------------------
# Track resolution
# ---------------------------------------------------------------------------
def resolve_track_to_release(meta: dict) -> Optional[Tuple[str, str]]:
    """Return (artist_mbid, release_group_mbid) for a track, or None."""
    # Fast path: release MBID + artist MBID already in track metadata
    if meta.get("release_mbid") and meta.get("artist_mbids"):
        rg = get_release_group_mbid(meta["release_mbid"])
        if rg:
            return meta["artist_mbids"][0], rg

    # Slow path: resolve via recording lookup
    if meta.get("recording_mbid"):
        rec = lookup_recording(meta["recording_mbid"])
        if not rec:
            return None
        credits  = rec.get("artist-credit", [])
        releases = rec.get("releases", [])
        if not credits or not releases:
            return None
        artist = credits[0].get("artist", {}).get("id")
        rg     = get_release_group_mbid(releases[0].get("id"))
        if artist and rg:
            return artist, rg

    return None


def resolve_tracks_parallel(
    tracks: List[dict],
    max_workers: int = None,
) -> Set[Tuple[str, str]]:
    """Resolve *tracks* in parallel; return unique (artist, release-group) pairs."""
    if max_workers is None:
        max_workers = config.MAX_PARALLEL_REQUESTS

    resolved: Set[Tuple[str, str]] = set()
    failed = 0

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(resolve_track_to_release, t): t for t in tracks}
        for future in as_completed(futures):
            meta = futures[future]
            try:
                result = future.result(timeout=config.REQUEST_TIMEOUT)
                if result:
                    resolved.add(result)
                    logger.debug(f"Resolved: {result}")
                else:
                    failed += 1
                    logger.debug(f"Could not resolve: {meta.get('title')} — {meta.get('artist_name')}")
            except Exception as exc:
                failed += 1
                logger.error(f"Error resolving {meta.get('title')!r}: {exc}")

    if failed:
        logger.warning(f"Failed to resolve {failed}/{len(tracks)} tracks")

    return resolved
