"""Unit tests for Mkissa / AllAnime modern crypto and provider adapter."""

import base64
import json
import pytest

from media_cli.media.decryption import (
    DEFAULT_BUILD_ID,
    DEFAULT_MASK,
    DEFAULT_SEEDS,
    boot_token,
    build_aa_req,
    decrypt_source_url,
    decrypt_tobeparsed,
    derive_key,
    derive_mask,
    get_mkissa_material,
)
from media_cli.providers import PROVIDER_REGISTRY, get_provider
from media_cli.providers.allanime import (
    ALLANIME_LANE,
    ALLANIME_QUERY_HASH,
    AllAnimeProvider,
)


def test_derive_mask_and_boot_token():
    mask = derive_mask(DEFAULT_BUILD_ID, DEFAULT_SEEDS)
    assert mask is not None
    assert mask == DEFAULT_MASK

    token = boot_token(mask, DEFAULT_BUILD_ID, 2960, "mkissa", "mkissa.to", "k7")
    expected_token = "6acd74ca7ebc9ecca262881d1ee80ff8a8c0f3c7d6d877a544e5cb6e8b090e1a"
    assert token == expected_token


def test_derive_key_and_roundtrip_aareq():
    mask = DEFAULT_MASK
    part_b = base64.b64decode("1a5H9vWfNHrbAJxUevZQadwo7oz3UptJHnBvxEesHIM=")
    aes_key = derive_key(mask, part_b)
    assert len(aes_key) == 32

    # Build aaReq
    aa_req = build_aa_req(aes_key, 2960, DEFAULT_BUILD_ID, ALLANIME_QUERY_HASH, ALLANIME_LANE)
    assert aa_req
    blob = base64.b64decode(aa_req)
    assert blob[0] == 1
    iv = blob[1:13]
    ciphertext = blob[13:]

    # Decrypt aaReq
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    decrypted = AESGCM(aes_key).decrypt(iv, ciphertext, None)
    payload = json.loads(decrypted.decode("utf-8"))
    assert payload["v"] == 1
    assert payload["epoch"] == 2960
    assert payload["buildId"] == DEFAULT_BUILD_ID
    assert payload["qh"] == ALLANIME_QUERY_HASH
    assert payload["k"] == ALLANIME_LANE


def test_decrypt_tobeparsed():
    key = bytes.fromhex("64642f3d5c51401e26207d3649a6753fb4264069563b547270cb98cb051bd04a")
    plain = json.dumps({"sourceUrls": [{"sourceUrl": "--test", "sourceName": "Default"}]})

    # Encrypt a test tobeparsed envelope
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    iv = b"\x00" * 12
    ciphertext = AESGCM(key).encrypt(iv, plain.encode("utf-8"), None)
    encoded = base64.b64encode(b"\x01" + iv + ciphertext).decode("ascii")

    decrypted = decrypt_tobeparsed(encoded, key=key)
    assert decrypted is not None
    data = json.loads(decrypted)
    assert data["sourceUrls"][0]["sourceName"] == "Default"


def test_decrypt_source_url():
    # XOR key index 3 has mask 56
    test_url = "https://allanime.day/clock?id=test123"
    enc = "--" + "".join(f"{ord(c) ^ 56:02x}" for c in test_url)
    dec = decrypt_source_url(enc)
    assert dec == test_url


def test_mkissa_provider_active_and_metadata():
    assert "mkissa" in PROVIDER_REGISTRY
    assert PROVIDER_REGISTRY["mkissa"]["status"] == "active"

    provider = get_provider("mkissa")
    assert provider is not None
    assert provider.id == "mkissa"
    assert provider.name == "Mkissa"
    assert "allanime" in provider.aliases


def test_mkissa_search_normalization(monkeypatch):
    provider = AllAnimeProvider()

    def mock_request_json(url, data=None, extra_hdrs=None):
        return {
            "data": {
                "shows": {
                    "edges": [
                        {
                            "_id": "abc123show",
                            "name": "Frieren",
                            "englishName": "Frieren: Beyond Journey's End",
                            "availableEpisodes": {"sub": 28, "dub": 28},
                        }
                    ]
                }
            }
        }

    provider._request_json = mock_request_json
    results = provider.search("frieren")
    assert len(results) == 1
    assert results[0]["name"] == "Frieren"
    assert results[0]["_provider"] == "mkissa"
    assert results[0]["_provider_id"] == "abc123show"


def test_uni_and_vidnest_extractors():
    from media_cli.extractors import find_extractor
    from media_cli.extractors.uni import UniExtractor
    from media_cli.extractors.vidnest import VidnestExtractor

    ext_uni = find_extractor("https://watchanime.uns.bio/#pj9aqw")
    assert isinstance(ext_uni, UniExtractor)

    ext_vidnest = find_extractor("https://vidnest.io/e/m6vvyba4iw3s")
    assert isinstance(ext_vidnest, VidnestExtractor)

    ext_filemoon = find_extractor("https://bysekoze.com/e/z2gb57opvmim")
    from media_cli.extractors.filemoon import FilemoonExtractor
    assert isinstance(ext_filemoon, FilemoonExtractor)

