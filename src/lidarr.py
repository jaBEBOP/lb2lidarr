"""
lidarr.py — Lidarr API client.
"""

import json
import logging
import time
from typing import Dict, List, Optional

import requests

import config
from cache import TTLCache
from session import create_session

logger = logging.getLogger("lb2lidarr.lidarr")

_session: requests.Session = create_session()
_artist_cache:       TTLCache = TTLCache()
_album_cache:        TTLCache = TTLCache()
# Flat lookup of all known albums by foreignAlbumId, populated by prefetch_albums()
_album_by_foreign:   Dict[str, dict] = {}


def reinit(pool_size: int) -> None:
    global _session
    _session = create_session(pool_size)


def clear_caches() -> None:
    _artist_cache.clear()
    _album_cache.clear()
    _album_by_foreign.clear()


# ---------------------------------------------------------------------------
# Core request helper
# ---------------------------------------------------------------------------
def _is_already_added(resp: requests.Response) -> bool:
    """Return True if a 400 response is specifically 'This album has already been added'."""
    try:
        errors = resp.json()
        return any(e.get("errorCode") == "AlbumExistsValidator" for e in errors)
    except Exception:
        return False


def _request(method: str, endpoint: str, payload=None) -> Optional[dict]:
    url     = f"{config.LIDARR_URL}/api/v1{endpoint}"
    headers = {"X-Api-Key": config.LIDARR_API_KEY}

    try:
        resp = _session.request(
            method, url, headers=headers,
            json=payload, timeout=config.REQUEST_TIMEOUT,
        )

        if resp.status_code == 400:
            if _is_already_added(resp):
                # Expected — album existed from a previous run; handled in add_album()
                logger.debug(f"400 AlbumExistsValidator for {endpoint}")
            else:
                # Unexpected 400 — log full detail for diagnosis
                logger.debug(f">>> {method} {url}")
                if payload is not None:
                    try:
                        logger.debug(f"    body: {json.dumps(payload, indent=2)}")
                    except Exception:
                        logger.debug(f"    body: {payload!r}")
                logger.error(
                    f"Lidarr API error: 400 Bad Request for {url}\n"
                    f"    response body: {resp.text[:500]}"
                )

        if resp.status_code == 404:
            return None

        resp.raise_for_status()

        if resp.status_code == 204 or not resp.content:
            return {}

        return resp.json()

    except requests.RequestException as exc:
        if hasattr(exc, "response") and exc.response is not None:
            logger.error(
                f"Lidarr API error: {exc.response.status_code} "
                f"{exc.response.reason} for {url}"
            )
            logger.error(f"    response body: {exc.response.text[:500]}")
        else:
            logger.error(f"Lidarr API error: {exc}")
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _validate_mbid(mbid: str) -> bool:
    return bool(mbid) and len(mbid) == 36 and mbid.count("-") == 4


def _album_title(album: dict, fallback: str = "") -> str:
    return album.get("title") or album.get("foreignAlbumId") or fallback


# ---------------------------------------------------------------------------
# Artist API
# ---------------------------------------------------------------------------
def get_artist(mbid: str) -> Optional[dict]:
    if not _validate_mbid(mbid):
        return None
    return _artist_cache.get_or_set(mbid, _fetch_artist, mbid)


def _fetch_artist(mbid: str) -> Optional[dict]:
    result = _request("GET", f"/artist/lookup?term=lidarr:{mbid}")
    if not result or not isinstance(result, list):
        return None
    artist = result[0]
    return artist if artist.get("id") else None


def add_artist(mbid: str) -> Optional[dict]:
    """Add an artist to Lidarr (unmonitored, no album search)."""
    if not _validate_mbid(mbid):
        logger.error(f"Invalid artist MBID: {mbid}")
        return None

    lookup = _request("GET", f"/artist/lookup?term=lidarr:{mbid}")
    if not lookup or not isinstance(lookup, list):
        logger.error(f"Could not look up artist {mbid}")
        return None

    data = lookup[0]
    data.update({
        "rootFolderPath":    config.LIDARR_ROOT_FOLDER,
        "qualityProfileId":  config.LIDARR_QUALITY_PROFILE_ID,
        "metadataProfileId": config.LIDARR_METADATA_PROFILE_ID,
        "monitored":         False,
        "monitorNewItems":   "none",
        "addOptions": {
            "searchForMissingAlbums":     False,
            "ignoreEpisodesWithFiles":    False,
            "ignoreEpisodesWithoutFiles": False,
        },
    })

    logger.info(f"Adding artist MBID: {mbid}")
    result = _request("POST", "/artist", data)
    if result:
        logger.info(f"Added artist '{result.get('artistName')}' (ID: {result.get('id')})")
        _artist_cache.set(mbid, result)
    else:
        logger.error(f"Failed to add artist {mbid}")
    return result


def prefetch_artists() -> int:
    """Bulk-load all Lidarr artists into cache. Returns count loaded."""
    result = _request("GET", "/artist")
    if not result or not isinstance(result, list):
        logger.warning("Could not prefetch artists from Lidarr")
        return 0

    count = 0
    for artist in result:
        fid = artist.get("foreignArtistId")
        if fid and _validate_mbid(fid):
            _artist_cache.set(fid, artist)
            count += 1

    logger.info(f"Prefetched {count} artist(s) from Lidarr into cache")
    return count


def wait_for_artist_indexing(poll_interval: int = None, timeout: int = None) -> bool:
    """Poll until all RefreshArtist commands finish. Returns False if timeout hit."""
    poll_interval = poll_interval or config.ARTIST_INDEX_POLL_INTERVAL
    timeout       = timeout       or config.ARTIST_INDEX_TIMEOUT
    deadline      = time.time() + timeout
    logged        = False

    while time.time() < deadline:
        result = _request("GET", "/command")
        if not result or not isinstance(result, list):
            logger.warning("Could not read Lidarr command queue — retrying")
            time.sleep(poll_interval)
            continue

        active = [
            cmd for cmd in result
            if cmd.get("name") in ("RefreshArtist", "RescanArtist")
            and cmd.get("status") in ("queued", "started")
        ]

        if not active:
            msg = "All RefreshArtist commands done" if logged else "No pending RefreshArtist commands"
            logger.info(f"{msg} — proceeding to album phase")
            return True

        labels = [
            (cmd.get("body") or {}).get("artistName")
            or (cmd.get("body") or {}).get("artistId")
            or str(cmd.get("id"))
            for cmd in active
        ]
        logger.info(f"Waiting for Lidarr to index {len(active)} artist(s): {', '.join(labels)}")
        logged = True
        time.sleep(poll_interval)

    logger.warning(
        f"Timed out after {timeout}s waiting for RefreshArtist — "
        "proceeding anyway; some album adds may fail"
    )
    return False


# ---------------------------------------------------------------------------
# Album API
# ---------------------------------------------------------------------------
def prefetch_albums() -> int:
    """Bulk-load ALL Lidarr albums into a foreignAlbumId → album dict.

    This is called once at the start of Phase 3b.  Every subsequent
    'does this album exist?' check is served from this dict rather than
    making individual API calls, eliminating the 400 'already been added'
    cascade that occurs when albums were added in a previous run.

    Returns the number of albums loaded.
    """
    result = _request("GET", "/album")
    if not isinstance(result, list):
        logger.warning("Could not prefetch albums from Lidarr")
        return 0

    _album_by_foreign.clear()
    for album in result:
        fid = album.get("foreignAlbumId")
        if fid:
            _album_by_foreign[fid] = album

    logger.info(f"Prefetched {len(_album_by_foreign)} album(s) from Lidarr into cache")
    return len(_album_by_foreign)


def get_cached_album(foreign_album_id: str) -> Optional[dict]:
    """Return a previously prefetched album by foreignAlbumId, or None."""
    return _album_by_foreign.get(foreign_album_id)


def get_album_by_foreign_id(foreign_album_id: str) -> Optional[dict]:
    """Fetch a specific album from Lidarr directly by its foreignAlbumId."""
    # Check the prefetch dict first
    cached = _album_by_foreign.get(foreign_album_id)
    if cached:
        return cached
    result = _request("GET", f"/album?foreignAlbumId={foreign_album_id}")
    if isinstance(result, list) and result:
        _album_by_foreign[foreign_album_id] = result[0]
        return result[0]
    return None


def monitor_album(album: dict) -> Optional[dict]:
    """Set monitored=True on an album via PUT /album/{id}."""
    album_id = album.get("id")
    if not album_id:
        return None
    result = _request("PUT", f"/album/{album_id}", {**album, "monitored": True})
    if result:
        logger.debug(f"Monitoring enabled for '{_album_title(album)}' (ID: {album_id})")
        _album_by_foreign[album.get("foreignAlbumId", "")] = result
    else:
        logger.warning(f"Failed to enable monitoring for album ID {album_id}")
    return result


def search_album(album_id: int) -> bool:
    """Trigger an AlbumSearch command for *album_id*."""
    result = _request("POST", "/command", {"name": "AlbumSearch", "albumIds": [album_id]})
    if result is not None:
        logger.debug(f"Search queued for album ID {album_id}")
        return True
    logger.warning(f"Failed to queue search for album ID {album_id}")
    return False


def _handle_existing_album(existing: dict) -> bool:
    """Ensure an existing album is monitored and search it if it has no files.

    Returns True if a search was triggered, False if skipped (files exist).
    """
    if not existing.get("monitored"):
        existing = monitor_album(existing) or existing

    track_files = existing.get("statistics", {}).get("trackFileCount", 0)
    title = _album_title(existing)
    if track_files > 0:
        logger.debug(f"'{title}' already has {track_files} file(s) — skipping search")
        return False
    else:
        logger.info(f"'{title}' has no downloaded tracks — triggering search")
        if existing.get("id"):
            search_album(existing["id"])
        return True


def add_album(artist_id: int, rg_mbid: str, artist: dict) -> Optional[dict]:
    """Add an album to Lidarr (monitored) with a one-time search on add.

    Checks the prefetch cache first to skip the POST entirely if the album
    is already known.  Falls back to a direct lookup if POST still returns
    'already been added'.
    """
    if not _validate_mbid(rg_mbid):
        logger.error(f"Invalid release group MBID: {rg_mbid}")
        return None

    # Check prefetch cache before hitting the API
    existing = get_cached_album(rg_mbid)
    if existing:
        return _handle_existing_album(existing)

    lookup = _request("GET", f"/album/lookup?term=mbid:{rg_mbid}")
    if not lookup or not isinstance(lookup, list):
        logger.error(f"Could not look up album {rg_mbid}")
        return None

    data = lookup[0]

    # Patch the nested artist block — lookup returns stubs with profile IDs = 0
    data["artistId"] = artist_id
    if isinstance(data.get("artist"), dict):
        data["artist"]["id"]                = artist_id
        data["artist"]["qualityProfileId"]  = artist.get("qualityProfileId",  config.LIDARR_QUALITY_PROFILE_ID)
        data["artist"]["metadataProfileId"] = artist.get("metadataProfileId", config.LIDARR_METADATA_PROFILE_ID)
        data["artist"]["rootFolderPath"]    = artist.get("rootFolderPath",    config.LIDARR_ROOT_FOLDER)

    data["monitored"]  = True
    data["addOptions"] = {"addType": "automatic", "searchForNewAlbum": True}

    title = _album_title(data, rg_mbid)
    logger.info(f"Adding album '{title}' for artist ID {artist_id}")
    result = _request("POST", "/album", data)

    if result:
        logger.info(f"Added album '{_album_title(result)}' (ID: {result.get('id')})")
        _album_by_foreign[rg_mbid] = result
        return result

    # POST failed — fetch directly and handle (monitors + searches if no files)
    existing = get_album_by_foreign_id(rg_mbid)
    if existing:
        _handle_existing_album(existing)
        return existing

    logger.error(f"Failed to add album {rg_mbid}")
    return None
