"""
listenbrainz.py — ListenBrainz API calls.
"""

import logging
from typing import List, Optional
from urllib.parse import urlparse

import requests

import config
from cache import RateLimiter
from session import create_session

logger = logging.getLogger("lb2lidarr.listenbrainz")

_session: requests.Session = create_session()
_rate_limiter: RateLimiter = RateLimiter()

ALLOWED_PLAYLIST_TYPES = {"weekly-exploration", "weekly-jams", "daily-jams"}


def reinit(pool_size: int, rate_limiter: RateLimiter) -> None:
    """Recreate the session and rate limiter after config is loaded."""
    global _session, _rate_limiter
    _session = create_session(pool_size)
    _rate_limiter = rate_limiter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def mbid_from_url(url: str) -> Optional[str]:
    """Extract a MusicBrainz UUID from a MusicBrainz URL."""
    try:
        if not url:
            return None
        parts = urlparse(url).path.strip("/").split("/")
        mbid = parts[-1] if parts else ""
        return mbid if len(mbid) == 36 and mbid.count("-") == 4 else None
    except Exception as exc:
        logger.debug(f"Error extracting MBID from {url!r}: {exc}")
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def get_created_for_you_playlists(username: str, token: str) -> List[dict]:
    """Return the most-recent playlist of each allowed type for *username*."""
    url = f"https://api.listenbrainz.org/1/user/{username}/playlists/createdfor"
    _rate_limiter.wait()
    try:
        resp = _session.get(
            url,
            headers={"Authorization": f"Token {token}"},
            timeout=config.REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.error(f"Failed to fetch playlists for {username}: {exc}")
        return []

    playlists: List[dict] = []
    seen_types: set = set()

    for entry in resp.json().get("playlists", []):
        pl = entry.get("playlist")
        if not pl:
            continue

        jspf   = pl.get("extension", {}).get("https://musicbrainz.org/doc/jspf#playlist", {})
        meta   = jspf.get("additional_metadata", {})
        patch  = meta.get("algorithm_metadata", {}).get("source_patch")

        if patch not in ALLOWED_PLAYLIST_TYPES:
            continue

        # ListenBrainz returns newest-first; keep only the first of each type.
        if patch in seen_types:
            logger.debug(f"Skipping older '{patch}' playlist: {pl.get('title')}")
            continue
        seen_types.add(patch)

        mbid = mbid_from_url(pl.get("identifier", ""))
        if not mbid:
            continue

        playlists.append({"mbid": mbid, "title": pl.get("title"), "type": patch})

    logger.info(f"Found {len(playlists)} playlist(s) for {username}")
    return playlists


def get_playlist_tracks(mbid: str, token: str) -> List[dict]:
    """Return raw track objects from a playlist."""
    _rate_limiter.wait()
    try:
        resp = _session.get(
            f"https://api.listenbrainz.org/1/playlist/{mbid}",
            headers={"Authorization": f"Token {token}"},
            timeout=config.REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.error(f"Failed to fetch playlist {mbid}: {exc}")
        return []

    return resp.json().get("playlist", {}).get("track", [])


def get_recommendations(username: str, token: str, count: int = 100) -> List[dict]:
    """Fetch collaborative filtering recommendations for *username*.

    Returns a list of track metadata dicts in the same shape as
    extract_track_metadata() so they feed directly into the resolution pipeline.

    Handles 204 No Content (recommendations not yet generated) gracefully.
    """
    url = f"https://api.listenbrainz.org/1/cf/recommendation/user/{username}/recording"
    _rate_limiter.wait()
    try:
        resp = _session.get(
            url,
            headers={"Authorization": f"Token {token}"},
            params={"count": count, "offset": 0},
            timeout=config.REQUEST_TIMEOUT,
        )

        if resp.status_code == 204:
            logger.info(f"No recommendations available yet for {username}")
            return []

        resp.raise_for_status()

    except requests.RequestException as exc:
        logger.error(f"Failed to fetch recommendations for {username}: {exc}")
        return []

    mbids = resp.json().get("payload", {}).get("mbids", [])
    tracks = [
        {
            "recording_mbid": entry.get("recording_mbid"),
            "release_mbid":   None,
            "artist_mbids":   None,
            "title":          None,
            "artist_name":    None,
            "_source":        "recommendation",
        }
        for entry in mbids
        if entry.get("recording_mbid")
    ]

    logger.info(f"Fetched {len(tracks)} recommendation(s) for {username}")
    return tracks


def extract_track_metadata(track: dict) -> dict:
    """Pull MusicBrainz IDs and basic info from a JSPF track object."""
    identifiers = track.get("identifier", [])
    recording_mbid = mbid_from_url(identifiers[0]) if identifiers else None

    jspf   = track.get("extension", {}).get("https://musicbrainz.org/doc/jspf#track", {})
    meta   = jspf.get("additional_metadata", {})
    artists = meta.get("artists", [])

    return {
        "recording_mbid": recording_mbid,
        "release_mbid":   meta.get("caa_release_mbid"),
        "artist_mbids":   [a["artist_mbid"] for a in artists if a.get("artist_mbid")] or None,
        "title":          track.get("title"),
        "artist_name":    track.get("creator"),
    }