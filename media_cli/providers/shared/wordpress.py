"""Reusable helpers for WordPress-style anime streaming sites."""

from __future__ import annotations

import base64
import html
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable

from bs4 import BeautifulSoup

from ...media.source_entries import build_direct_source, build_embed_source
from ...services.http import UA
from .schema import build_catalog, build_episode, build_title


@dataclass(frozen=True)
class Entry:
    title: str
    url: str
    meta: str = ""
    image: str = ""


@dataclass(frozen=True)
class Mirror:
    label: str
    url: str
    embed_html: str = ""


@dataclass(frozen=True)
class EpisodePage:
    title: str
    series_title: str
    series_url: str
    mirrors: list[Mirror]


def fetch_html(url: str, *, timeout: int = 8) -> str:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": UA},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def normalize_url(base_url: str, url: str) -> str:
    if url.startswith("//"):
        return "https:" + url
    return urllib.parse.urljoin(base_url.rstrip("/") + "/", url)


def clean_text(value: str) -> str:
    if value is None:
        return ""
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def attr_value(attrs: str, name: str) -> str:
    match = re.search(rf'\b{name}=["\']([^"\']+)["\']', attrs, re.I)
    return html.unescape(match.group(1)) if match else ""


def is_episode_url(url: str) -> bool:
    return bool(re.search(r"/[^/]*episode[^/]*/?$", urllib.parse.urlparse(url).path))


def is_media_asset_url(url: str) -> bool:
    path = urllib.parse.urlparse(url).path.lower()
    return path.endswith((
        ".avif", ".bmp", ".gif", ".jpeg", ".jpg", ".png", ".svg", ".webp",
    ))


def parse_cards(base_url: str, page_html: str, *, only_main: bool = False, valid_domains: list[str] = None) -> list[Entry]:
    soup = BeautifulSoup(page_html, "html.parser")
    source = soup
    if only_main:
        source = soup.select_one("div.listupd") or soup

    entries: list[Entry] = []
    seen: set[str] = set()
    for link in source.select("a[href]"):
        href_attr = link.get("href", "")
        if not href_attr:
            continue
        href = normalize_url(base_url, href_attr)
        if not href.startswith(base_url):
            if not valid_domains or not any(href.startswith(d) for d in valid_domains):
                continue
        if href in seen:
            continue
        if is_media_asset_url(href):
            continue
        title = clean_text(link.get("title", ""))
        if not title:
            headline = link.select_one("h2, h3, h4")
            title = clean_text(headline.get_text(" ", strip=True) if headline else link.get_text(" ", strip=True))
        if not title or title in {"View All", "Next"}:
            continue

        img = link.select_one("img")
        image_url = ""
        if img:
            image_url = img.get("data-src") or img.get("data-lazy-src") or img.get("src") or ""

        seen.add(href)
        entries.append(Entry(title=title, url=href, image=image_url))
    return entries


def parse_series(base_url: str, page_html: str, valid_domains: list[str] = None, fetch_fn = None) -> list[Entry]:
    soup = BeautifulSoup(page_html, "html.parser")
    section = soup.select_one("div.eplister, div.episodelist") or soup
    items: list[Entry] = []
    seen: set[str] = set()
    # Tracks whether we've already done one extra episode-page HTTP fetch this call.
    _extra_fetch_done = [False]

    # Detect the latest released episode number from div.lastend (e.g. "New Episode: Episode 22")
    last_end_node = (
        soup.select_one("div.lastend span.epcurlast")
        or soup.select_one("div.lastend .inepcx:last-child a")
        or soup.select_one("div.lastend .inepcx:last-child span.epcur")
        or soup.select_one("div.lastend a[href]:not([href='#'])")
    )
    latest_released_num = None
    if last_end_node:
        a_tag = last_end_node if last_end_node.name == "a" else (last_end_node.find_parent("a") or last_end_node.find("a"))
        href_last = a_tag.get("href", "") if a_tag else ""
        txt_last = last_end_node.get_text(" ", strip=True)
        from media_cli.domain.episodes import clean_episode_identifier
        num_str = clean_episode_identifier(href_last) or clean_episode_identifier(txt_last)
        try:
            latest_released_num = float(num_str)
        except (ValueError, TypeError):
            pass

    for item in section.select("li"):
        link = item.select_one("a[href]")
        if not link:
            continue
        href = normalize_url(base_url, link.get("href", ""))
        if href in seen:
            continue
        title_node = item.select_one("h3, .epl-title")
        label_node = item.select_one(".epl-num, span")
        title = clean_text(
            title_node.get_text(" ", strip=True)
            if title_node else link.get_text(" ", strip=True)
        )
        meta = clean_text(label_node.get_text(" ", strip=True) if label_node else "")

        from media_cli.domain.episodes import clean_episode_identifier
        ep_num_str = clean_episode_identifier(href) or clean_episode_identifier(meta) or clean_episode_identifier(title)
        try:
            ep_num = float(ep_num_str)
        except (ValueError, TypeError):
            ep_num = None

        if latest_released_num is not None and ep_num is not None and ep_num > latest_released_num:
            # Check if this episode is truly an unreleased countdown or an early release.
            # Limit to ONE extra fetch total per parse_series call to avoid N+1 slowdown.
            is_unreleased = False
            if fetch_fn and not _extra_fetch_done[0]:
                try:
                    _extra_fetch_done[0] = True
                    ep_page_html = fetch_fn(href)
                    ep_soup = BeautifulSoup(ep_page_html, "html.parser")
                    mirrors = ep_soup.select("select.mirror option[value], div.player-embed iframe, div.megavid iframe, video")
                    has_playable = any(
                        (m.get("value", "").strip() and m.get("value", "").strip() not in ("#", "/"))
                        or m.get("src", "").strip()
                        for m in mirrors
                    )
                    tick = ep_soup.select_one("a.tickcounter[data-id], .tickcounter[data-id]")
                    if tick and not has_playable:
                        is_unreleased = True
                except Exception:
                    pass
            elif fetch_fn and _extra_fetch_done[0]:
                # Already determined the boundary — treat remaining over-limit eps as unreleased.
                is_unreleased = True
            if is_unreleased:
                continue
            else:
                latest_released_num = max(latest_released_num, ep_num)

        img = item.select_one("img")
        image_url = ""
        if img:
            image_url = img.get("data-src") or img.get("data-lazy-src") or img.get("src") or ""

        if title:
            seen.add(href)
            items.append(Entry(title=title, url=href, meta=meta, image=image_url))
    return items


def parse_episode(base_url: str, page_html: str, valid_domains: list[str] = None) -> EpisodePage:
    soup = BeautifulSoup(page_html, "html.parser")
    title_node = soup.select_one("h1.entry-title, h1")
    title = clean_text(title_node.get_text(" ", strip=True) if title_node else "Unknown episode")

    series_link = None
    for link in soup.select("a[href]"):
        href = normalize_url(base_url, link.get("href", ""))
        is_internal = href.startswith(base_url) or (valid_domains and any(href.startswith(d) for d in valid_domains))
        if is_internal and not is_episode_url(href):
            text = clean_text(link.get_text(" ", strip=True))
            if text and text.lower() not in {"home", "anime", "donghua"}:
                series_link = link
                break
    series_title = clean_text(series_link.get_text(" ", strip=True)) if series_link else ""
    series_url = normalize_url(base_url, series_link.get("href", "")) if series_link else ""

    return EpisodePage(
        title=title,
        series_title=series_title,
        series_url=series_url,
        mirrors=parse_mirrors(base_url, page_html),
    )


def parse_mirrors(base_url: str, page_html: str) -> list[Mirror]:
    soup = BeautifulSoup(page_html, "html.parser")
    mirrors: list[Mirror] = []
    seen: set[str] = set()
    for option in soup.select("select.mirror option[value], option[value]"):
        value = html.unescape(option.get("value", "")).strip()
        if not value:
            continue
        if value.startswith(("http://", "https://", "//", "/")):
            url = normalize_embed_url(normalize_url(base_url, value))
            if url in seen:
                continue
            seen.add(url)
            mirrors.append(Mirror(label=clean_text(option.get_text(" ", strip=True)), url=url))
            continue
        try:
            embed = base64.b64decode(value + "=" * (-len(value) % 4)).decode(
                "utf-8",
                errors="replace",
            )
        except Exception:
            continue
        embed_soup = BeautifulSoup(embed, "html.parser")
        frame = embed_soup.select_one("iframe[src], video[src], source[src]")
        if not frame:
            continue
        url = normalize_embed_url(normalize_url(base_url, html.unescape(frame.get("src", ""))))
        if url in seen:
            continue
        seen.add(url)
        mirrors.append(Mirror(label=clean_text(option.get_text(" ", strip=True)), url=url, embed_html=embed.strip()))
    return sort_mirrors(mirrors)


def extract_embed_url(base_url: str, page_html: str) -> str:
    soup = BeautifulSoup(page_html, "html.parser")
    meta = soup.select_one('meta[itemprop="embedUrl"][content]')
    if meta:
        return normalize_embed_url(normalize_url(base_url, html.unescape(meta.get("content", ""))))

    AD_DOMAINS = ("t.co", "twitter.com", "blogspot.com", "blogger.com", "google.com", "disqus.com", "facebook.com", "histats.com", "ads")
    KNOWN_VIDEO = ("dailymotion.com", "ok.ru", "rumble.com", "vidhide", "streamtape", "mp4upload", "dood", "filelions", "megavid", "luluvdo", "yurn", "player", "embed")

    frames = soup.select("div.player-embed iframe[src], div.megavid iframe[src], div.video-content iframe[src], iframe[src]")
    selected_src = None
    for frame in frames:
        src = frame.get("src", "").strip()
        if not src:
            continue
        lower_src = src.lower()
        if any(ad in lower_src for ad in AD_DOMAINS):
            continue
        if any(v in lower_src for v in KNOWN_VIDEO):
            selected_src = src
            break
        if not selected_src:
            selected_src = src

    if selected_src:
        return normalize_embed_url(normalize_url(base_url, html.unescape(selected_src)))

    script = soup.select_one("div.player-embed script[src], div.megavid script[src]")
    if script and "dailymotion.com" in script.get("src", ""):
        video_id = script.get("data-video", "")
        if video_id:
            return f"https://www.dailymotion.com/video/{video_id}"

    return ""


def _mirror_sub_type_rank(mirror: Mirror) -> int:
    text = f"{mirror.label} {mirror.url} {mirror.embed_html}".casefold()
    if "hardsub" in text or re.search(r"\bhard[\s\-_]?subs?\b", text):
        return 0
    if "softsub" in text or re.search(r"\b(?:soft[\s\-_]?subs?|all[\s\-_]?subs?|multi[\s\-_]?subs?)\b", text):
        return 2
    return 1


def sort_mirrors(mirrors: list[Mirror]) -> list[Mirror]:
    return [
        mirror
        for _, mirror in sorted(
            enumerate(mirrors),
            key=lambda item: (
                _mirror_sub_type_rank(item[1]),
                _mirror_language_rank(item[1]),
                _mirror_host_rank(item[1]),
                item[0],
            ),
        )
    ]


def _mirror_language_rank(mirror: Mirror) -> int:
    label = mirror.label.casefold()
    if re.search(r"\b(?:eng|english)\b", label):
        return 0
    if re.search(
        r"\b(?:indo|indonesia|indonesian|raw|arabic|hindi|malay|spanish|portuguese)\b",
        label,
    ):
        return 2
    return 1


def _mirror_host_rank(mirror: Mirror) -> int:
    value = f"{mirror.label} {mirror.url}".casefold()
    if "rumble.com" in value or "rumble" in value:
        return 0
    if "dailymotion.com" in value or "dailymotion" in value:
        return 1
    if ".m3u8" in value or "odysee.com" in value or "d.tube" in value:
        return 2
    if "ok.ru" in value:
        return 5
    return 4


def normalize_embed_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower()
    if host == "geo.dailymotion.com" and "/player/" in parsed.path:
        video_id = urllib.parse.parse_qs(parsed.query).get("video", [""])[0]
        if video_id:
            return f"https://www.dailymotion.com/video/{video_id}"
    if host.endswith("dailymotion.com"):
        match = re.search(r"/embed/video/([^/?#]+)", parsed.path)
        if match:
            return f"https://www.dailymotion.com/video/{match.group(1)}"
    if host == "odysee.com":
        decoded_path = urllib.parse.unquote(parsed.path)
        prefix = "/$/embed/"
        if decoded_path.startswith(prefix):
            return f"https://odysee.com/{decoded_path.removeprefix(prefix)}"
    if host == "play.d.tube":
        video_id = urllib.parse.parse_qs(parsed.query).get("v", [""])[0]
        if video_id:
            return f"https://nas2.d.tube/videos/{dtube_to_uuid(video_id)}/master.m3u8"
    return url


def dtube_to_uuid(video_id: str) -> str:
    if re.fullmatch(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
        r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        video_id,
    ):
        return video_id
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    value = 0
    for char in video_id:
        index = alphabet.find(char)
        if index == -1:
            return video_id
        value = value * 58 + index
    hex_value = f"{value:032x}"
    return f"{hex_value[:8]}-{hex_value[8:12]}-{hex_value[12:16]}-{hex_value[16:20]}-{hex_value[20:32]}"


def episode_number(value: str, fallback: str = "") -> str:
    match_dual = re.search(r"\b(?:episode|eps?)\s*[-:]?\s*(\d+(?:\.\d+)?)\s*(\[\s*\d+(?:\.\d+)?\s*\])", value, re.I)
    if match_dual:
        return f"{match_dual.group(1)} {match_dual.group(2)}"
    match = re.search(r"\b(?:episode|eps?)\s*[-:]?\s*(\d+(?:\.\d+)?)\b", value, re.I)
    return match.group(1) if match else fallback


class WordPressAnimeProvider:
    id = ""
    name = ""
    base_url = ""
    blocked_mirror_label_pattern = None
    resolve_mirror_pages = False

    def __init__(self, fetch: Callable[[str], str] = fetch_html):
        self._fetch = fetch

    def search(self, query: str, ttype: str = "sub") -> list[dict]:
        del ttype
        query_str = str(query or "").strip()
        if not query_str:
            url = f"{self.base_url}/"
        else:
            url = f"{self.base_url}/?{urllib.parse.urlencode({'s': query_str})}"
        domains = getattr(self, 'domains', [])
        try:
            html = self._fetch(url)
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403) and not query_str:
                return []
            raise
        entries = [
            entry for entry in parse_cards(self.base_url, html, only_main=True, valid_domains=domains)
            if not is_episode_url(entry.url)
        ]
        return [self._title_from_entry(entry) for entry in entries]

    def get_title(self, provider_id: str) -> dict | None:
        title_str = provider_id.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").title()
        title = self._title_from_entry(Entry(title=title_str, url=provider_id))

        try:
            html = self._fetch(provider_id)
            if html:
                import re
                anilist_match = re.search(r'href=["\']https?://anilist\.co/anime/(\d+)["\']', html, re.IGNORECASE)
                mal_match = re.search(r'href=["\']https?://myanimelist\.net/anime/(\d+)["\']', html, re.IGNORECASE)
                if anilist_match:
                    title["aniListId"] = int(anilist_match.group(1))
                if mal_match:
                    title["malId"] = int(mal_match.group(1))

                from bs4 import BeautifulSoup
                soup = BeautifulSoup(html, "html.parser")

                desc_div = soup.find("div", {"itemprop": "description"})
                if desc_div and not title.get("description"):
                    paragraphs = []
                    for p in desc_div.find_all("p", recursive=False):
                        text = p.get_text(strip=True)
                        if text.lower() == "indonesia":
                            break
                        if text.lower() == "english":
                            continue
                        paragraphs.append(text)
                    if paragraphs:
                        title["description"] = " ".join(paragraphs)

                spe_div = soup.find("div", class_="spe")
                if spe_div:
                    for span in spe_div.find_all("span"):
                        text = span.get_text(strip=True)
                        if text.startswith("Status:"):
                            stat = text.split(":", 1)[1].strip().upper()
                            if stat in ("ONGOING", "RELEASING"):
                                title["status"] = "RELEASING"
                            elif stat in ("COMPLETED", "FINISHED"):
                                title["status"] = "FINISHED"
                        elif text.startswith("Type:"):
                            title["format"] = text.split(":", 1)[1].strip()
                        elif text.startswith("Released:"):
                            date_str = text.split(":", 1)[1].strip()
                            if date_str and not title.get("startDate"):
                                try:
                                    from datetime import datetime
                                    dt = datetime.strptime(date_str, "%b %d, %Y")
                                    title["airedStart"] = {"year": dt.year, "month": dt.month, "day": dt.day}
                                except Exception:
                                    title["airedStart"] = date_str
                        elif text.startswith("Episodes:"):
                            ep_str = text.split(":", 1)[1].strip()
                            nums = re.findall(r"\d+", ep_str)
                            if nums:
                                ep_count = int(nums[-1])
                                if not title.get("availableEpisodes"):
                                    title["availableEpisodes"] = {"sub": 0, "dub": 0, "raw": 0}
                                if ep_count > title["availableEpisodes"].get("sub", 0):
                                    title["availableEpisodes"]["sub"] = ep_count
                                title["episodeCount"] = ep_count

                # 1. Detect latest released episode from div.lastend
                last_end_node = (
                    soup.select_one("div.lastend span.epcurlast")
                    or soup.select_one("div.lastend .inepcx:last-child a")
                    or soup.select_one("div.lastend .inepcx:last-child span.epcur")
                    or soup.select_one("div.lastend a[href]:not([href='#'])")
                )
                latest_released_num = None
                if last_end_node:
                    a_tag = last_end_node if last_end_node.name == "a" else (last_end_node.find_parent("a") or last_end_node.find("a"))
                    href_last = a_tag.get("href", "") if a_tag else ""
                    txt_last = last_end_node.get_text(" ", strip=True)
                    from media_cli.domain.episodes import clean_episode_identifier
                    num_str = clean_episode_identifier(href_last) or clean_episode_identifier(txt_last)
                    try:
                        latest_released_num = float(num_str)
                        if not title.get("availableEpisodes"):
                            title["availableEpisodes"] = {"sub": 0, "dub": 0, "raw": 0}
                        title["availableEpisodes"]["sub"] = int(latest_released_num)
                    except (ValueError, TypeError):
                        pass

                # Reconcile episodeCount with latest released count
                declared_count = title.get("episodeCount")
                if title.get("status") == "FINISHED" and latest_released_num:
                    title["episodeCount"] = int(latest_released_num)
                elif declared_count and latest_released_num:
                    if declared_count < latest_released_num:
                        # Stale / merged seasons count
                        title["episodeCount"] = int(latest_released_num)

                # 2. Check for upcoming unreleased countdown episode
                unreleased_ep = None
                unreleased_url = None
                for li in soup.select("div.eplister li, div.episodelist li"):
                    a_li = li.find("a")
                    if not a_li:
                        continue
                    h_url = normalize_url(self.base_url, a_li.get("href", ""))
                    num_tag = li.select_one(".epl-num, span")
                    n_txt = num_tag.get_text(" ", strip=True) if num_tag else ""
                    from media_cli.domain.episodes import clean_episode_identifier
                    cur_num_str = clean_episode_identifier(h_url) or clean_episode_identifier(n_txt)
                    try:
                        cur_num = float(cur_num_str)
                    except (ValueError, TypeError):
                        cur_num = None

                    if latest_released_num is not None and cur_num is not None and cur_num > latest_released_num:
                        unreleased_ep = cur_num
                        unreleased_url = h_url
                        break

                if unreleased_ep is not None:
                    is_truly_unreleased = False
                    if unreleased_url:
                        try:
                            ep_page_html = self._fetch(unreleased_url)
                            ep_soup = BeautifulSoup(ep_page_html, "html.parser")
                            mirrors = ep_soup.select("select.mirror option[value], div.player-embed iframe, div.megavid iframe, video")
                            has_playable = any(
                                (m.get("value", "").strip() and m.get("value", "").strip() not in ("#", "/"))
                                or m.get("src", "").strip()
                                for m in mirrors
                            )
                            tick = ep_soup.select_one("a.tickcounter[data-id], .tickcounter[data-id]")
                            if tick and tick.get("data-id") and not has_playable:
                                data_id = tick.get("data-id")
                                w_url = f"https://www.tickcounter.com/widget/countdown/{data_id}"
                                w_html = self._fetch(w_url)
                                m_cd = re.search(r'window\.countdown\(\s*["\']([^"\']+)["\']', w_html)
                                if m_cd:
                                    from datetime import datetime, timezone
                                    dt = datetime.fromisoformat(m_cd.group(1))
                                    target_ts = int(dt.replace(tzinfo=timezone.utc).timestamp())
                                    if target_ts > time.time():
                                        is_truly_unreleased = True
                                        title["_next_airing_ep"] = int(unreleased_ep)
                                        title["_next_airing_at"] = target_ts
                                        title["_next_airing_time"] = target_ts
                                        title["status"] = "RELEASING"
                        except Exception:
                            pass

                    if not is_truly_unreleased:
                        latest_released_num = max(latest_released_num or 0, unreleased_ep)
                        if not title.get("availableEpisodes"):
                            title["availableEpisodes"] = {"sub": 0, "dub": 0, "raw": 0}
                        title["availableEpisodes"]["sub"] = int(latest_released_num)
                        title["_next_airing_ep"] = None
                        title["_next_airing_at"] = None
                        title["_next_airing_time"] = None
        except Exception:
            pass

        from .models import normalize_titles
        return normalize_titles(
            [title],
            provider_id=self.id,
            provider_name=self.name,
        )[0]

    def episode_catalog(self, provider_id: str, ttype: str = "sub") -> dict:
        del ttype
        domains = getattr(self, 'domains', [])
        entries = parse_series(self.base_url, self._fetch(provider_id), valid_domains=domains, fetch_fn=self._fetch)
        episodes = []
        for index, entry in enumerate(entries):
            dual_num = episode_number(entry.title) if entry.title else ""
            if "[" in dual_num or "(" in dual_num:
                label = dual_num
            else:
                label = entry.meta or dual_num or str(index + 1)
            episodes.append(build_episode(
                episode_id=entry.url,
                label=label,
                title=entry.title,
                url=entry.url,
                translation_type="sub",
            ))
        return build_catalog(
            provider=self.id,
            provider_id=provider_id,
            episodes={"sub": episodes, "dub": [], "raw": []},
        )


    def episode_sources(self, provider_id: str, episode: str, ttype: str = "sub") -> dict | None:
        del provider_id, ttype
        domains = getattr(self, 'domains', [])
        page = parse_episode(self.base_url, self._fetch(episode), valid_domains=domains)
        sources = []
        for mirror in page.mirrors:
            if self._skip_mirror(mirror):
                continue
            stream_url, referer = self._stream_url_for_mirror(mirror, episode)
            if not stream_url:
                continue
            stream_type = "hls" if ".m3u8" in stream_url else "external"
            sub_rank = _mirror_sub_type_rank(mirror)
            prio = 1 if sub_rank == 0 else (6 if sub_rank == 2 else 3)
            if stream_type == "hls":
                source = build_direct_source(
                    name=mirror.label or self.name,
                    stream_url=stream_url,
                    stream_type="hls",
                    resolution="Adaptive",
                    referer=referer,
                    android_safe=True,
                )
            else:
                source = build_embed_source(
                    name=mirror.label or self.name,
                    source_url=stream_url,
                    resolution="Adaptive",
                    referer=referer,
                )
            source["priority"] = prio
            sources.append(source)
        return {"episode": {"sourceUrls": sources}}

    def _stream_url_for_mirror(self, mirror: Mirror, episode_url: str) -> tuple[str, str]:
        if self.resolve_mirror_pages and mirror.url.startswith(self.base_url):
            try:
                stream_url = extract_embed_url(self.base_url, self._fetch(mirror.url))
            except Exception:
                stream_url = ""
            return stream_url, mirror.url
        return mirror.url, episode_url

    def _skip_mirror(self, mirror: Mirror) -> bool:
        pattern = self.blocked_mirror_label_pattern
        if not pattern:
            return False
        label = mirror.label or ""
        if re.search(r"\b(?:eng|english)\b", label, re.I):
            return False
        return bool(re.search(pattern, label, re.I))

    def browser_url(
        self,
        provider_id: str,
        episode: str | None = None,
        ttype: str = "sub",
        cfg: dict | None = None,
    ) -> str:
        del ttype, cfg
        return episode or provider_id or self.base_url

    def _title_from_entry(self, entry: Entry) -> dict:
        return build_title(
            provider=self.id,
            provider_name=self.name,
            provider_id=entry.url,
            name=entry.title,
            thumbnail=entry.image,
            media_type="ONA",
        )
