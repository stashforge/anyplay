"""Choose installed download engines that can preserve the requested tracks."""

import shutil
from dataclasses import dataclass

from ..core.api import ProviderDependencyError
from ..services.requirements import require_executable


@dataclass(frozen=True)
class DownloadPlan:
    engine: str
    hints: tuple[str, ...] = ()


def plan_download(preferred: str, url: str = "", *, external_audio: bool = False,
                  embed_subtitles: bool = False, worker: bool = False) -> DownloadPlan:
    """Prefer resumable acquisition; FFmpeg combines local tracks when available."""
    engine = str(preferred or "auto").strip() or "auto"
    supported = ("ffmpeg", "yt-dlp", "aria2c") if worker else ("ffmpeg", "yt-dlp")
    if engine != "auto" and engine not in supported:
        raise ProviderDependencyError(
            "Unsupported download engine. Choose 'auto' or one of: " + ", ".join(supported) + "."
        )
    hls = ".m3u8" in str(url).casefold()
    if engine == "aria2c" and hls:
        raise ProviderDependencyError(
            "aria2c cannot assemble this HLS playlist into video. Choose 'auto', 'ffmpeg' or 'yt-dlp'."
        )
    native_mux = external_audio or (worker and embed_subtitles)
    if native_mux or embed_subtitles:
        require_executable("ffmpeg", "Single-file audio/subtitle downloads")
    if native_mux:
        if engine == "aria2c":
            raise ProviderDependencyError(
                "This download needs yt-dlp plus ffmpeg to combine external tracks. Choose 'auto' or 'yt-dlp'."
            )
        if engine == "yt-dlp" or (engine == "auto" and shutil.which("yt-dlp")):
            require_executable("yt-dlp", "Resumable track downloads")
            return DownloadPlan("yt-dlp")
        return DownloadPlan("ffmpeg")

    if engine == "auto":
        order: tuple[str, ...] = ("yt-dlp", "ffmpeg")
        if worker and not hls:
            order += ("aria2c",)
        engine = next((candidate for candidate in order if shutil.which(candidate)), "")
        if not engine:
            raise ProviderDependencyError(
                "Downloads require an available engine: " + ", ".join(order) + ". "
                "Install one using your system package manager, then retry."
            )
    else:
        require_executable(engine, "Downloads")
    if engine == "yt-dlp" and hls and not shutil.which("ffmpeg"):
        return DownloadPlan(engine, ("HLS download fallback may fail; install ffmpeg for direct playlist handling.",))
    if engine == "aria2c":
        return DownloadPlan(engine, ("Direct-file download fallback; install ffmpeg or yt-dlp for stream handling.",))
    return DownloadPlan(engine)
