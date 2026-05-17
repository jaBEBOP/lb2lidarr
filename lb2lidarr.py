#!/usr/bin/env python3
"""
lb2lidarr — ListenBrainz "Created For You" → Lidarr bridge.

Fetches tracks from LB playlists, resolves them to MusicBrainz release groups,
then ensures the corresponding artists and albums exist in Lidarr.
"""

import argparse
import logging
import sys
from typing import List, Set, Tuple

import config
import lidarr
import listenbrainz
import musicbrainz
from cache import RateLimiter

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO,
)
logging.getLogger("urllib3.connectionpool").setLevel(logging.WARNING)
logger = logging.getLogger("lb2lidarr")


# ---------------------------------------------------------------------------
# Processing pipeline
# ---------------------------------------------------------------------------
def process_all(dry_run: bool = False) -> None:
    stats = {
        "total_tracks":   0,
        "resolved_albums": 0,
        "added_artists":  0,
        "added_albums":   0,
        "searched_albums": 0,
        "errors":         0,
    }

    # ------------------------------------------------------------------ #
    # Phase 1 — collect tracks from all users                            #
    # ------------------------------------------------------------------ #
    logger.info("Phase 1: Collecting tracks from all users...")
    all_tracks: List[dict] = []

    for user, token in zip(config.LISTENBRAINZ_USERS, config.LISTENBRAINZ_TOKENS):
        logger.info(f"Processing user: {user}")
        playlists = listenbrainz.get_created_for_you_playlists(user, token)
        if not playlists:
            logger.warning(f"No 'Created For You' playlists found for {user}")
            continue

        for pl in playlists:
            logger.info(f"Processing playlist: {pl['title']}")
            tracks = listenbrainz.get_playlist_tracks(pl["mbid"], token)
            stats["total_tracks"] += len(tracks)
            all_tracks.extend(listenbrainz.extract_track_metadata(t) for t in tracks)

    logger.info(f"Collected {len(all_tracks)} tracks from all users")
    if not all_tracks:
        logger.info("No tracks to process")
        return

    # Deduplicate before resolution — many tracks across playlists share the
    # same release or recording MBID and would produce identical API calls.
    seen: Set[str] = set()
    unique_tracks: List[dict] = []
    for meta in all_tracks:
        key = meta.get("release_mbid") or meta.get("recording_mbid")
        if key:
            if key not in seen:
                seen.add(key)
                unique_tracks.append(meta)
        else:
            unique_tracks.append(meta)

    deduped = len(all_tracks) - len(unique_tracks)
    if deduped:
        logger.info(f"Deduped {deduped} duplicate tracks — resolving {len(unique_tracks)} unique")

    # ------------------------------------------------------------------ #
    # Phase 2 — resolve tracks → (artist MBID, release-group MBID)      #
    # ------------------------------------------------------------------ #
    logger.info(f"Phase 2: Resolving {len(unique_tracks)} tracks...")
    resolved: Set[Tuple[str, str]] = musicbrainz.resolve_tracks_parallel(unique_tracks)
    stats["resolved_albums"] = len(resolved)
    logger.info(f"Phase 2 complete: {stats['resolved_albums']} unique albums resolved")

    if not resolved:
        logger.info("No albums to process")
        return

    # ------------------------------------------------------------------ #
    # Phase 3a — ensure every artist exists in Lidarr                   #
    # ------------------------------------------------------------------ #
    logger.info("Phase 3a: Adding missing artists to Lidarr...")
    if not dry_run:
        lidarr.prefetch_artists()

    unique_artists: Set[str] = {artist_mbid for artist_mbid, _ in resolved}

    for artist_mbid in unique_artists:
        if dry_run:
            cached = lidarr._artist_cache.get(artist_mbid)
            if cached:
                logger.debug(f"[DRY RUN] Artist already exists: {cached.get('artistName')}")
            else:
                logger.info(f"[DRY RUN] Would add artist: {artist_mbid}")
            continue

        existing = lidarr.get_artist(artist_mbid)
        if existing:
            logger.debug(f"Artist already in Lidarr: {existing.get('artistName')}")
            continue

        if lidarr.add_artist(artist_mbid):
            stats["added_artists"] += 1
        else:
            logger.error(f"Failed to add artist {artist_mbid}")
            stats["errors"] += 1

    logger.info(f"Phase 3a complete: {stats['added_artists']} new artist(s) added")

    # Wait for Lidarr to finish indexing newly added artists before adding albums.
    if not dry_run and stats["added_artists"] > 0:
        lidarr.wait_for_artist_indexing()
        lidarr.prefetch_artists()
        logger.info("Artist cache refreshed — proceeding to album phase")

    # ------------------------------------------------------------------ #
    # Phase 3b — add albums                                              #
    # ------------------------------------------------------------------ #
    logger.info("Phase 3b: Adding albums to Lidarr...")

    # Bulk-load all existing albums keyed by foreignAlbumId.
    # This means the existence check below is a dict lookup rather than a
    # per-artist API call, and eliminates the 400 "already been added" cascade
    # for albums added in previous runs.
    if not dry_run:
        lidarr.prefetch_albums()

    for idx, (artist_mbid, rg_mbid) in enumerate(resolved, 1):
        logger.debug(f"[{idx}/{stats['resolved_albums']}] artist={artist_mbid} album={rg_mbid}")

        if dry_run:
            logger.info(f"[DRY RUN] Would add album {rg_mbid} for artist {artist_mbid}")
            continue

        artist = lidarr.get_artist(artist_mbid)
        if not artist:
            logger.error(f"Artist {artist_mbid} not in Lidarr — skipping album {rg_mbid}")
            stats["errors"] += 1
            continue

        artist_id = artist["id"]

        # Check prefetch cache before making any API call.
        existing_album = lidarr.get_cached_album(rg_mbid)

        if existing_album:
            lidarr._handle_existing_album(existing_album)
            stats["searched_albums"] += 1
            continue

        album = lidarr.add_album(artist_id, rg_mbid, artist)
        if album:
            stats["added_albums"] += 1
            stats["searched_albums"] += 1
        else:
            logger.error(f"Failed to add album {rg_mbid} for artist {artist_mbid}")
            stats["errors"] += 1

    logger.info(f"Phase 3b complete: {stats['added_albums']} new album(s) added")

    # ------------------------------------------------------------------ #
    # Summary                                                             #
    # ------------------------------------------------------------------ #
    logger.info("=" * 60)
    logger.info("Run complete")
    logger.info(f"  Tracks collected    : {stats['total_tracks']}")
    logger.info(f"  Albums resolved     : {stats['resolved_albums']}")
    logger.info(f"  Artists added       : {stats['added_artists']}")
    logger.info(f"  Albums added        : {stats['added_albums']}")
    logger.info(f"  Searches triggered  : {stats['searched_albums']}")
    if stats["errors"]:
        logger.warning(f"  Errors              : {stats['errors']}")
    logger.info("=" * 60)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="ListenBrainz 'Created For You' → Lidarr bridge",
        epilog="Example: python lb2lidarr.py --dry-run --log-level DEBUG",
    )
    parser.add_argument("--dry-run",      action="store_true", help="Simulate without making changes")
    parser.add_argument("--clear-cache",  action="store_true", help="Clear all caches before running")
    parser.add_argument("--log-level",    default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument("--workers",      type=int, help="Override parallel worker count")
    args = parser.parse_args()

    logging.getLogger().setLevel(args.log_level)

    # Load and validate config first
    if not config.load():
        sys.exit(1)

    # Allow CLI to override worker count
    if args.workers:
        config.MAX_PARALLEL_REQUESTS = args.workers

    # Build a shared rate limiter, then wire it into every API module
    rate_limiter = RateLimiter(config.ENABLE_RATE_LIMITING, config.RATE_LIMIT_DELAY)
    pool_size    = config.MAX_PARALLEL_REQUESTS

    listenbrainz.reinit(pool_size, rate_limiter)
    musicbrainz.reinit(pool_size, rate_limiter)
    lidarr.reinit(pool_size)

    if args.clear_cache:
        logger.info("Clearing all caches")
        musicbrainz.clear_caches()
        lidarr.clear_caches()

    logger.info("Configuration:\n" + config.summary())
    logger.info(f"  Dry run             : {args.dry_run}")
    logger.info(f"  Log level           : {args.log_level}")

    try:
        process_all(dry_run=args.dry_run)
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(0)
    except Exception as exc:
        logger.exception(f"Unexpected error: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
