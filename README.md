# lb2lidarr

Syncs artists and albums from ListenBrainz "Created For You" playlist tracks to Lidarr.

## Quick start

\```yaml
# docker-compose.yml
services:
  lb2lidarr:
    image: yourusername/lb2lidarr:latest
    container_name: lb2lidarr
    restart: unless-stopped
    environment:
      - CRON_SCHEDULE=0 */6 * * *
      - LB_USERS=user1,user2
      - LB_TOKENS=token1,token2
      - LIDARR_URL=http://192.168.1.100:8686
      - LIDARR_API_KEY=your_api_key
      - LIDARR_ROOT_FOLDER=/music
\```

## All environment variables

| Variable | Default | Description |
|---|---|---|
| `CRON_SCHEDULE` | `0 */6 * * *` | How often to run |
| `LB_USERS` | required | Comma-separated ListenBrainz usernames |
| `LB_TOKENS` | required | Matching ListenBrainz API tokens |
| `LIDARR_URL` | required | Lidarr base URL |
| `LIDARR_API_KEY` | required | Lidarr API key |
| `LIDARR_ROOT_FOLDER` | required | Music root folder path |
| `LIDARR_QUALITY_PROFILE_ID` | `1` | Lidarr quality profile |
| `LIDARR_METADATA_PROFILE_ID` | `1` | Lidarr metadata profile |
| `MAX_PARALLEL_REQUESTS` | `10` | Parallel worker count |
| `ENABLE_RATE_LIMITING` | `true` | Disable if using local MusicBrainz |
| `ARTIST_INDEX_POLL_INTERVAL` | `5` | Seconds between artist indexing polls |
| `ARTIST_INDEX_TIMEOUT` | `300` | Max seconds to wait for artist indexing |
