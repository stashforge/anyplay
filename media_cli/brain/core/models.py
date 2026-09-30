from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class ReleaseType(Enum):
    UNKNOWN = "UNKNOWN"
    TV = "TV"
    MOVIE = "MOVIE"
    OVA = "OVA"
    ONA = "ONA"
    SPECIAL = "SPECIAL"

class LanguageType(Enum):
    UNKNOWN = "UNKNOWN"
    SUB = "SUB"
    DUB = "DUB"
    RAW = "RAW"

@dataclass
class EpisodeToken:
    """Represents a parsed episode value, supporting decimals and absolute mapping."""
    raw_label: str
    absolute_number: Optional[float] = None
    relative_number: Optional[float] = None
    season_number: Optional[int] = None
    is_special: bool = False

@dataclass
class CanonicalAnime:
    """The universal output format of the Anime Brain."""
    raw_title: str
    franchise: str = ""

    # Episode & Season Info
    episode: Optional[EpisodeToken] = None
    season: Optional[int] = None
    part: Optional[int] = None

    # Classification
    release_type: ReleaseType = ReleaseType.UNKNOWN
    language: LanguageType = LanguageType.UNKNOWN

    # Metadata stripped from string
    quality: Optional[str] = None       # e.g., "1080p", "4K"
    release_group: Optional[str] = None # e.g., "Erai-raws"
    tags: list[str] = field(default_factory=list) # e.g., ["Uncensored", "v2"]

    @property
    def title(self) -> str:
        """Returns a standardized display title including season, part, and format (e.g., 'Slime Season 2 OVA')."""
        title = self.franchise
        if self.season:
            title += f" Season {self.season}"
        if self.part:
            title += f" Part {self.part}"

        # Append release formats (OVA, MOVIE, etc) back to the display title
        format_tags = [t for t in self.tags if t.upper() in ["OVA", "OAD", "MOVIE", "SPECIAL", "SPECIALS"]]
        if format_tags:
            title += f" {' '.join(format_tags)}"

        return title

    def to_dict(self) -> dict[str, Any]:
        """Convenience method to cast the dataclass to a dictionary."""
        return {
            "raw_title": self.raw_title,
            "franchise": self.franchise,
            "title": self.title,
            "episode": self.episode.__dict__ if self.episode else None,
            "season": self.season,
            "part": self.part,
            "release_type": self.release_type.value,
            "language": self.language.value,
            "quality": self.quality,
            "release_group": self.release_group,
            "tags": self.tags
        }
