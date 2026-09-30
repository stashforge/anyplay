"""Base classes and helpers for Movie streaming providers."""

from typing import Any

from ...core.tmdb import TMDBClient
from .base import Provider
from .schema import build_catalog, build_episode, build_title


class MovieProvider(Provider):
    """
    Abstract base class for Movie providers.
    Automatically handles TMDB searching and metadata, delegating only
    the stream scraping logic (episode_catalog, episode_sources) to subclasses.
    """

    def __init__(self, request_json_fn=None):
        self.request_json_fn = request_json_fn

    @property
    def tmdb(self) -> TMDBClient:
        if not hasattr(self, "_tmdb"):
            self._tmdb = TMDBClient()
        return self._tmdb

    def search(self, query: str, ttype: str = "sub") -> list[dict[str, Any]]:
        """Search TMDB for movies and TV shows and map to our schema."""
        results = self.tmdb.search_multi(query)
        titles = []
        for res in results:
            media_type = res.get("media_type")
            if media_type not in ("movie", "tv"):
                continue

            title = res.get("title") or res.get("name")
            tmdb_id = str(res.get("id"))
            poster_path = res.get("poster_path")
            thumbnail = f"https://image.tmdb.org/t/p/w500{poster_path}" if poster_path else ""

            titles.append(
                build_title(
                    provider=self.id,
                    provider_name=self.name,
                    provider_id=f"{media_type}:{tmdb_id}",
                    name=title,
                    description=res.get("overview", ""),
                    thumbnail=thumbnail,
                    media_type="MOVIE" if media_type == "movie" else "TV",
                    aired_start=res.get("release_date") or res.get("first_air_date"),
                )
            )
        return titles

    def get_title(self, provider_id: str) -> dict[str, Any] | None:
        """Fetch full metadata from TMDB based on media type."""
        try:
            if ":" in provider_id:
                media_type, tmdb_id = provider_id.split(":", 1)
            else:
                media_type, tmdb_id = "movie", provider_id

            if media_type == "movie":
                res = self.tmdb.get_movie_details(tmdb_id)
            else:
                res = self.tmdb.get_tv_details(tmdb_id)
        except Exception:
            return None

        title = res.get("title") or res.get("name")
        poster_path = res.get("poster_path")
        thumbnail = f"https://image.tmdb.org/t/p/w500{poster_path}" if poster_path else ""
        genres = [g.get("name") for g in res.get("genres", [])]

        return build_title(
            provider=self.id,
            provider_name=self.name,
            provider_id=str(provider_id),
            name=title,
            description=res.get("overview", ""),
            thumbnail=thumbnail,
            media_type="MOVIE",
            aired_start=res.get("release_date"),
            genres=genres,
        )

    def episode_catalog(self, provider_id: str, ttype: str = "sub") -> dict[str, Any]:
        """Return the available episodes (1 for movie, many for TV)."""
        if ":" in provider_id:
            media_type, tmdb_id = provider_id.split(":", 1)
        else:
            media_type, tmdb_id = "movie", provider_id

        episodes_list = []

        if media_type == "movie":
            episodes_list.append(
                build_episode(
                    episode_id="1",
                    label="Movie",
                    title="Watch Movie"
                )
            )
        else:
            # TV Show logic
            try:
                tv_details = self.tmdb.get_tv_details(tmdb_id)
                seasons = tv_details.get("seasons", [])

                # Iterate through each season (excluding season 0 which is usually specials if you want, or just include them)
                for season in seasons:
                    season_number = season.get("season_number", 0)
                    if season_number == 0:
                        continue # Skip specials for now

                    season_data = self.tmdb.get_tv_season(tmdb_id, season_number)
                    for ep in season_data.get("episodes", []):
                        ep_num = ep.get("episode_number")
                        episodes_list.append(
                            build_episode(
                                episode_id=f"s{season_number}e{ep_num}",
                                label=f"S{season_number} E{ep_num}",
                                title=ep.get("name", f"Episode {ep_num}"),
                                date=ep.get("air_date")
                            )
                        )
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"Failed to fetch TV episodes: {e}")

        episodes = {
            "sub": episodes_list
        }
        return build_catalog(provider_id=provider_id, episodes=episodes)

    def browser_url(
        self,
        provider_id: str,
        episode: str | None = None,
        ttype: str = "sub",
        cfg: dict[str, Any] | None = None,
    ) -> str:
        """Fallback to a TMDB link or standard search link."""
        return f"https://www.themoviedb.org/movie/{provider_id}"
