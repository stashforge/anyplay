"""Registry enumeration and routing must not import unrelated implementations."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROVIDERS = {
    "allanime", "anidbapp", "anikoto", "animegg", "animekhor", "animepahe",
    "animexin", "anipm", "anizone", "lucifer", "miruro", "movies", "reanime",
    "senshi", "torrent",
}
EXTRACTORS = {
    "dailymotion", "doodstream", "filemoon", "kwik", "misterdonghua", "mixdrop",
    "mp4upload", "okru", "odysee", "streamtape", "streamwish", "uni", "vidnest", "voe",
}


def fresh(code, tmp_path, *, providers=(), extractors=(), optional=("bs4", "curl_cffi", "m3u8")):
    denied = {f"media_cli.providers.{key}" for key in PROVIDERS - set(providers)}
    denied.update(f"media_cli.extractors.{key}" for key in EXTRACTORS - set(extractors))
    denied.update(optional)
    script = f"""
import importlib.abc, sys
attempted = []
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == root or fullname.startswith(root + '.') for root in {sorted(denied)!r}):
            attempted.append(fullname)
            raise ModuleNotFoundError('Blocked dependency: ' + fullname, name=fullname)
sys.meta_path.insert(0, Guard())
""" + code
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=ROOT,
        env={**os.environ, "HOME": str(tmp_path)}, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_provider_enumeration_and_labels_do_not_load_implementations(tmp_path):
    fresh("""
import media_cli.providers as p
assert 'anipm' in p.PROVIDERS and 'anipm' in p.PROVIDER_FACTORIES
assert len(p.PROVIDERS) > 10
assert 'anipm' in set(p.available_providers())
assert p.provider_display_name('senshi') == 'Senshi'
assert p.is_provider_active('anipm')
assert p.provider_key('anidbapp') == 'anidb'
assert p.provider_key('allanime') == 'mkissa'
assert p.get_provider_registry()['anipm']['name'] == 'AniPM'
assert not attempted, attempted
""", tmp_path)


def test_selected_provider_is_real_and_cached_without_loading_others(tmp_path):
    fresh("""
import media_cli.providers as p
provider = p.get_provider('anipm')
from media_cli.providers.anipm import AniPMProvider
assert isinstance(provider, AniPMProvider)
assert provider is p.PROVIDERS['anipm'] is p.available_providers()['anipm']
assert p.PROVIDER_FACTORIES['anipm'] is AniPMProvider
assert provider.metadata is p.PROVIDER_REGISTRY['anipm']
assert provider.domains == p.PROVIDER_REGISTRY['anipm']['domains']
assert not attempted, attempted
""", tmp_path, providers={"anipm"})


def test_aliases_and_legacy_export_share_canonical_instance(tmp_path):
    fresh("""
import media_cli.providers as p
provider = p.get_provider('allanime')
assert provider is p.get_provider('mkissa') is p.ALLANIME
assert p.PROVIDER_FACTORIES['allanime'] is p.PROVIDER_FACTORIES['mkissa']
assert not attempted, attempted
""", tmp_path, providers={"allanime"})


def test_concurrent_first_access_constructs_one_default_instance(tmp_path):
    fresh("""
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
import time
import media_cli.providers as p
factory = p.PROVIDER_FACTORIES['anipm']
original = factory.__init__
count = 0
lock = Lock()
def create(self, *args, **kwargs):
    global count
    with lock:
        count += 1
    time.sleep(.01)
    original(self, *args, **kwargs)
factory.__init__ = create
with ThreadPoolExecutor(max_workers=8) as pool:
    rows = list(pool.map(lambda _: p.get_provider('anipm'), range(16)))
assert count == 1
assert all(row is rows[0] for row in rows)
assert not attempted, attempted
""", tmp_path, providers={"anipm"})


def test_failed_selected_import_isolated_from_other_providers(tmp_path):
    fresh("""
import media_cli.providers as p
try:
    p.get_provider('animepahe')
except ModuleNotFoundError:
    pass
else:
    raise AssertionError('Missing selected provider should not silently fall back')
assert p.get_provider('anipm').id == 'anipm'
assert attempted == ['media_cli.providers.animepahe'], attempted
""", tmp_path, providers={"anipm"}, optional=())


def test_importing_extractor_registry_and_unknown_url_are_lightweight(tmp_path):
    fresh("""
import media_cli.extractors as e
assert len(e.EXTRACTORS) == 14
assert e.find_extractor('https://unknown.example/e/123') is None
assert e.find_extractor('') is None
assert not attempted, attempted
assert 'media_cli.extractors.base' not in sys.modules
""", tmp_path)


@pytest.mark.parametrize(("url", "module", "class_name"), [
    ("https://dai.ly/x123", "dailymotion", "DailymotionExtractor"),
    ("https://odysee.com/@channel:1/video:4", "odysee", "OdyseeExtractor"),
    ("https://ok.ru/videoembed/123", "okru", "OkruExtractor"),
    ("https://watchanime.uns.bio/#123", "uni", "UniExtractor"),
    ("https://vidnest.io/e/123", "vidnest", "VidnestExtractor"),
    ("https://bysekoze.com/e/123", "filemoon", "FilemoonExtractor"),
    ("https://awish.pro/e/123", "streamwish", "StreamWishExtractor"),
    ("https://streamtape.com/e/123", "streamtape", "StreamTapeExtractor"),
    ("https://dood.watch/e/123", "doodstream", "DoodExtractor"),
    ("https://mp4upload.com/embed-123.html", "mp4upload", "Mp4UploadExtractor"),
    ("https://voe.sx/e/123", "voe", "VoeExtractor"),
    ("https://kwik.si/e/123", "kwik", "KwikExtractor"),
    ("https://mixdrop.co/e/123", "mixdrop", "MixDropExtractor"),
    ("https://misterdonghua.in/embed/123", "misterdonghua", "MisterDonghuaExtractor"),
])
def test_url_selection_loads_only_matching_extractor(tmp_path, url, module, class_name):
    fresh(f"""
import media_cli.extractors as e
extractor = e.find_extractor({url!r})
assert type(extractor).__name__ == {class_name!r}
assert extractor is e.find_extractor({url!r})
assert getattr(e, {class_name!r}) is type(extractor)
assert extractor.can_handle({url!r})
assert not attempted, attempted
""", tmp_path, extractors={module}, optional=())


def test_direct_submodule_import_does_not_load_other_extractors(tmp_path):
    fresh("""
from media_cli.extractors.dailymotion import DailymotionExtractor
assert DailymotionExtractor().can_handle('https://dai.ly/x123')
assert not attempted, attempted
""", tmp_path, extractors={"dailymotion"}, optional=("bs4",))


def test_application_startup_does_not_import_any_provider_implementation(tmp_path):
    fresh("""
import media_cli.cli.application
assert not attempted, attempted
""", tmp_path, optional=())


def test_disabled_aliases_and_fallback_keys_do_not_construct_providers(tmp_path):
    fresh("""
import media_cli.providers as p
p.disabled_providers_from_config = lambda: {'mkissa', 'anipm'}
assert not p.is_provider_active('allanime')
assert 'mkissa' not in p.available_providers()
assert 'anipm' not in p.available_providers()
assert p.provider_key('allanime') == 'miruro'
assert p.provider_key('unknown') == 'miruro'
assert p.discover_provider_factories() is p.PROVIDER_FACTORIES
assert not attempted, attempted
""", tmp_path)


def test_callback_instances_keep_existing_non_session_lifecycle(tmp_path):
    fresh("""
import media_cli.providers as p
request = lambda *args, **kwargs: None
first = p.get_provider('anipm', request)
second = p.get_provider('anipm', request)
assert first is not second
assert first._request_json is request
assert first.metadata is p.PROVIDER_REGISTRY['anipm']
assert not attempted, attempted
""", tmp_path, providers={'anipm'})


def test_extractor_pattern_overlap_preserves_registration_precedence(tmp_path):
    fresh("""
import media_cli.extractors as e
from media_cli.extractors.routing import ROUTES
url = 'https://kwik.si/e/filemoon'
selected = e.find_extractor(url)
assert type(selected).__name__ == 'FilemoonExtractor'
assert selected.domains is ROUTES['filemoon'].domains
assert selected.patterns is ROUTES['filemoon'].patterns
assert not attempted, attempted
""", tmp_path, extractors={'filemoon'}, optional=())


def test_extractor_sequence_index_slice_and_export_compatibility(tmp_path):
    fresh("""
import media_cli.extractors as e
assert type(e.EXTRACTORS[0]).__name__ == 'UniExtractor'
assert type(e.EXTRACTORS[-1]).__name__ == 'MisterDonghuaExtractor'
assert e.EXTRACTORS[:1] == [e.EXTRACTORS[0]]
assert e.BaseExtractor is type(e.EXTRACTORS[0]).__bases__[0]
assert e.JsUnpacker.__name__ == 'JsUnpacker'
assert not attempted, attempted
""", tmp_path, extractors={'uni', 'misterdonghua'}, optional=())


def test_manifest_entry_modules_expose_canonical_factory():
    from media_cli.providers import PROVIDER_FACTORIES, PROVIDER_REGISTRY

    for key, metadata in PROVIDER_REGISTRY.items():
        if metadata.get('source'):
            assert key not in PROVIDER_FACTORIES
        else:
            assert metadata.get('module'), key
            assert PROVIDER_FACTORIES[key].id == key


def test_concurrent_extractor_access_constructs_one_instance(tmp_path):
    fresh("""
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
import time
import media_cli.extractors as e
factory = e.DailymotionExtractor
count = 0
lock = Lock()
def create(self):
    global count
    with lock:
        count += 1
    time.sleep(.01)
factory.__init__ = create
with ThreadPoolExecutor(max_workers=8) as pool:
    rows = list(pool.map(lambda _: e.find_extractor('https://dai.ly/x123'), range(16)))
assert count == 1 and all(row is rows[0] for row in rows)
assert not attempted, attempted
""", tmp_path, extractors={'dailymotion'}, optional=())


def test_failed_selected_extractor_does_not_break_other_routes(tmp_path):
    fresh("""
import media_cli.extractors as e
try:
    e.find_extractor('https://bysekoze.com/e/123')
except ModuleNotFoundError:
    pass
else:
    raise AssertionError('Selected import failure must propagate')
assert e.find_extractor('https://unknown.example/e/123') is None
assert type(e.find_extractor('https://dai.ly/x123')).__name__ == 'DailymotionExtractor'
assert attempted == ['media_cli.extractors.filemoon'], attempted
""", tmp_path, extractors={'dailymotion'}, optional=())


def test_explicit_custom_package_discovery_still_works(tmp_path):
    fresh("""
from pathlib import Path
import os
import media_cli.providers as p
root = Path(os.environ['HOME'])
package = root / 'custom_providers'
package.mkdir()
(package / '__init__.py').write_text('')
(package / 'example.py').write_text("class Custom:\\n    id = 'custom'\\n    aliases = ['custom_alias']\\nPROVIDER_CLASS = Custom\\n")
sys.path.insert(0, str(root))
factories = p.discover_provider_factories([str(package)], 'custom_providers')
assert factories['custom'] is factories['custom_alias']
assert not attempted, attempted
""", tmp_path)
