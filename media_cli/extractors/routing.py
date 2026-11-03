"""Single source of extractor routing metadata; no extractor/network imports."""

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse


def matches_url(url: str, domains, patterns) -> bool:
    if not url:
        return False
    host = (urlparse(url).netloc or "").casefold()
    return any(domain in host for domain in domains) or any(pattern.search(url) for pattern in patterns)


@dataclass(frozen=True)
class ExtractorRoute:
    module: str
    class_name: str
    domains: list[str]
    patterns: list[re.Pattern] = field(default_factory=list)

    def matches(self, url: str) -> bool:
        return matches_url(url, self.domains, self.patterns)


# Registration order is selection precedence, as in the original EXTRACTORS list.
ROUTES = {
    "uni": ExtractorRoute(
        "uni", "UniExtractor", ["uns.bio", "watchanime.uns.bio"], [re.compile("uns\\.bio", re.IGNORECASE)]
    ),
    "vidnest": ExtractorRoute(
        "vidnest",
        "VidnestExtractor",
        ["vidnest.io", "vidnest.net", "vidnest.fun", "vidnest.com"],
        [re.compile("vidnest\\.", re.IGNORECASE)],
    ),
    "odysee": ExtractorRoute(
        "odysee", "OdyseeExtractor", [],
        [re.compile(r"^https?://(?:www\.)?(?:odysee\.com|lbry\.tv)/", re.IGNORECASE)],
    ),
    "streamwish": ExtractorRoute(
        "streamwish",
        "StreamWishExtractor",
        [
            "streamwish.to",
            "streamwish.com",
            "awish.pro",
            "fastream.to",
            "streamhide.to",
            "seekplayer.com",
            "flaswish.com",
            "wishfast.top",
            "hlswish.com",
            "swhoi.com",
            "swdyu.com",
            "dwish.pro",
            "embedwish.com",
            "mwish.pro",
            "asnwish.com",
            "strwish.com",
            "sfastwish.com",
            "wishonly.com",
            "ajmidm.com",
            "vidhide.com",
            "vidhidepro.com",
            "vidhidepre.com",
            "filelions.to",
            "filelions.site",
            "filelions.online",
            "seekplayer.vip",
            "seekplayer.to",
            "playwish.com",
            "dwish.net",
        ],
        [re.compile("(?:wish|vidhide|filelions)", re.IGNORECASE)],
    ),
    "filemoon": ExtractorRoute(
        "filemoon",
        "FilemoonExtractor",
        [
            "filemoon.sx",
            "filemoon.to",
            "filemoon.in",
            "filemoon.top",
            "filemoon.lat",
            "filemoon.art",
            "bysekoze.com",
            "fastmoon",
            "moonplayer",
            "moonplayer.xyz",
            "mooncloud.top",
        ],
        [re.compile("(?:filemoon|bysekoze|fastmoon|moonplayer)", re.IGNORECASE)],
    ),
    "streamtape": ExtractorRoute(
        "streamtape",
        "StreamTapeExtractor",
        [
            "streamtape.com",
            "streamtape.net",
            "streamtape.xyz",
            "streamtape.to",
            "streamta.pe",
            "strcloud.link",
            "streamadblocker.xyz",
        ],
    ),
    "doodstream": ExtractorRoute(
        "doodstream",
        "DoodExtractor",
        [
            "dood.to",
            "dood.watch",
            "doodstream.com",
            "ds2play.com",
            "d0o0d.com",
            "do0od.com",
            "d000d.com",
            "dood.so",
            "dood.la",
            "dood.ws",
            "dood.sh",
            "dood.cx",
            "dood.pm",
            "dood.re",
            "playmogo.com",
            "dooodster.com",
            "doodstream.co",
            "ds2video.com",
        ],
    ),
    "mp4upload": ExtractorRoute("mp4upload", "Mp4UploadExtractor", ["mp4upload.com"]),
    "voe": ExtractorRoute(
        "voe",
        "VoeExtractor",
        [
            "voe.sx",
            "voe-network.net",
            "audaciousdefaulthouse.com",
            "metaphoricallyclattering.com",
            "tastelessparliament.com",
            "fraudulentmoulding.com",
        ],
    ),
    "kwik": ExtractorRoute("kwik", "KwikExtractor", ["kwik.cx", "kwik.si"]),
    "dailymotion": ExtractorRoute(
        "dailymotion",
        "DailymotionExtractor",
        ["dailymotion.com", "dai.ly"],
        [
            re.compile("dailymotion\\.com/(?:embed/)?video/([a-zA-Z0-9]+)", re.IGNORECASE),
            re.compile("dai\\.ly/([a-zA-Z0-9]+)", re.IGNORECASE),
            re.compile("geo\\.dailymotion\\.com/player\\.html\\?video=([a-zA-Z0-9]+)", re.IGNORECASE),
        ],
    ),
    "okru": ExtractorRoute(
        "okru",
        "OkruExtractor",
        ["ok.ru", "odnoklassniki.ru"],
        [re.compile("(?:ok\\.ru|odnoklassniki\\.ru)/(?:video|videoembed)/(\\d+)", re.IGNORECASE)],
    ),
    "mixdrop": ExtractorRoute(
        "mixdrop",
        "MixDropExtractor",
        [
            "mixdrop.co",
            "mixdrop.to",
            "mixdrop.sx",
            "mixdrop.bz",
            "mixdrop.ch",
            "mixdrop.ag",
            "mixdrop.gl",
            "mixdrop.club",
        ],
        [re.compile("mixdrop\\.[a-z]+/(?:[ef])/([a-zA-Z0-9]+)", re.IGNORECASE)],
    ),
    "misterdonghua": ExtractorRoute(
        "misterdonghua",
        "MisterDonghuaExtractor",
        ["misterdonghua.in"],
        [re.compile("misterdonghua\\.in/(?:embed/|v/|#)?([a-zA-Z0-9]+)", re.IGNORECASE)],
    ),
}
