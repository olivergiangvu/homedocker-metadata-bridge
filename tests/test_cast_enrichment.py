"""Metadata Bridge: live-compatible cast schema and safe TMDB/TVDB precedence."""
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import unittest
import urllib.error
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("SH_MANIFEST_URL", "http://127.0.0.1:3232/mock-sh/manifest.json")
os.environ.setdefault("SH2_MANIFEST_URL", "http://127.0.0.1:3232/mock-tvdb/manifest.json")
spec = importlib.util.spec_from_file_location("bridge_cast", ROOT / "bridge.py")
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)

SERIES_ID = "tt6150576"


def meta(source, videos=None, cast=None, tmdb="45678", tvdb="123456"):
    result = {"id": SERIES_ID, "_tmdbId": tmdb, "_tvdbId": tvdb,
              "name": "Tokyo Stories", "poster": source + "-poster",
              "videos": copy.deepcopy(videos or [])}
    if cast is not None:
        result["app_extras"] = {"cast": copy.deepcopy(cast),
                                "seasonPosters": ["keep-this"]}
    return result


class CastTest(unittest.TestCase):
    def setUp(self):
        bridge.CACHE.data.clear()

    def test_real_aiometadata_rich_schema_and_legacy_names(self):
        raw = meta("sh", cast=[
            {"name": "Kaoru Kobayashi", "character": "Master",
             "photo": "https://image.tmdb.org/t/p/actor.jpg", "tmdbId": 123},
            {"name": "  Asako Kobayashi ", "character": "Rumi", "tmdbId": 456},
            {"name": "", "character": "Ignore"},
            {"unknown": "Ignore"},
        ])
        self.assertEqual([x["name"] for x in bridge.extract_rich_cast(raw)],
                         ["Kaoru Kobayashi", "Asako Kobayashi"])
        self.assertEqual(bridge.extract_rich_cast({"cast": ["A", "B"]}),
                         [{"name": "A"}, {"name": "B"}])

    def test_tmdb_official_credits_override_only_cast_not_episodes(self):
        sh_videos = [{"id": SERIES_ID + ":1:1", "season": 1, "episode": 1,
                      "released": "2016-10-21", "thumbnail": "tmdb"}]
        tvdb_videos = copy.deepcopy(sh_videos) + [
            {"id": SERIES_ID + ":3:1", "season": 3, "episode": 1,
             "released": "2026-10-31", "thumbnail": "tvdb"}]
        sh = meta("sh", sh_videos, [{"name": "Wrong cached credit", "tmdbId": 999}])
        tvdb = meta("tvdb", tvdb_videos, [{"name": "Extra TVDB actor", "tvdbId": 777}])
        before = copy.deepcopy(sh)
        credits = [{"name": "Kaoru Kobayashi", "character": "Master",
                    "tmdbId": 123, "photo": "https://image.tmdb.org/t/p/master.jpg"}]
        def get(p, typ, ident):
            return copy.deepcopy(sh if p == "sh" else tvdb)
        with patch.object(bridge, "get_metadata", side_effect=get), \
             patch.object(bridge, "request_tmdb_cast", return_value=credits):
            result, origin, changed = bridge.series_meta("series", SERIES_ID)
        self.assertEqual((origin, changed), ("sh", 1))
        self.assertEqual(result["app_extras"]["cast"], credits)
        self.assertEqual(result["cast"], ["Kaoru Kobayashi"])
        self.assertEqual(result["app_extras"]["seasonPosters"], ["keep-this"])
        self.assertEqual([v["id"] for v in result["videos"]],
                         [SERIES_ID + ":1:1", SERIES_ID + ":3:1"])
        self.assertEqual(result["videos"][0], sh_videos[0])
        self.assertEqual(sh, before)

    def test_sh_first_enriches_only_same_actor_and_role(self):
        sh = meta("sh", cast=[
            {"name": "Kaoru Kobayashi", "character": "Master", "tmdbId": 123},
            {"name": "Guest", "character": "Sister", "tmdbId": 555},
        ])
        tvdb = meta("tvdb", cast=[
            {"name": "Kaoru Kobayashi", "character": "Master",
             "tvdbId": 321, "photo": "https://example.com/photo.jpg"},
            {"name": "Guest", "character": "Brother",
             "photo": "https://example.com/wrong.jpg"},
            {"name": "TVDB extra", "character": "Extra"},
        ])
        result = copy.deepcopy(sh)
        with patch.object(bridge, "request_tmdb_cast", return_value=[]):
            source, count = bridge.apply_series_cast(result, sh, tvdb)
        self.assertEqual((source, count), ("sh", 2))
        self.assertEqual(result["app_extras"]["cast"][0]["photo"],
                         "https://example.com/photo.jpg")
        self.assertEqual(result["app_extras"]["cast"][0]["tvdbId"], 321)
        self.assertNotIn("photo", result["app_extras"]["cast"][1])
        self.assertEqual(result["cast"], ["Kaoru Kobayashi", "Guest"])
        self.assertEqual(result["app_extras"]["seasonPosters"], ["keep-this"])

    def test_tvdb_fallback_only_when_sh_has_no_cast(self):
        sh = meta("sh")
        tvdb = meta("tvdb", cast=[{"name": "Kaoru Kobayashi", "tvdbId": 321}])
        with patch.object(bridge, "request_tmdb_cast", return_value=[]):
            result = copy.deepcopy(sh)
            source, count = bridge.apply_series_cast(result, sh, tvdb)
        self.assertEqual((source, count), ("tvdb_fallback", 1))
        self.assertEqual(result["app_extras"]["cast"][0]["name"], "Kaoru Kobayashi")

    def test_identity_conflict_prevents_tvdb_cast_fallback(self):
        sh = meta("sh")
        tvdb = meta("tvdb", cast=[{"name": "Wrong Show Cast"}], tmdb="99999")
        with patch.object(bridge, "get_metadata",
                          side_effect=lambda p, *_: copy.deepcopy(sh if p == "sh" else tvdb)), \
             patch.object(bridge, "request_tmdb_cast", return_value=[]):
            result, _, changed = bridge.series_meta("series", SERIES_ID)
        self.assertEqual(changed, 0)
        self.assertNotIn("app_extras", result)
        self.assertNotIn("cast", result)

    def test_matching_person_ids_with_conflicting_names_never_join(self):
        self.assertFalse(bridge.cast_identity_matches(
            {"name": "Alice", "tmdbId": 1},
            {"name": "Bob", "tmdbId": 1}))
        self.assertFalse(bridge.cast_identity_matches(
            {"name": "Alice", "tmdbId": 1},
            {"name": "Alice", "tmdbId": 2}))
        self.assertFalse(bridge.cast_identity_matches(
            {"name": "Alice", "character": "A"},
            {"name": "Alice", "character": "B"}))

    def test_without_tmdb_token_no_external_call(self):
        with patch.object(bridge, "TMDB_READ_ACCESS_TOKEN", ""), \
             patch.object(bridge.urllib.request, "urlopen") as request:
            self.assertEqual(bridge.request_tmdb_cast("1396"), [])
            request.assert_not_called()

    def test_tmdb_api_cast_order_roles_and_no_token_in_url(self):
        payload = {"cast": [
            {"id": 2, "name": "Second", "order": 2, "roles": [],
             "profile_path": None},
            {"id": 1, "name": "First", "order": 0,
             "roles": [{"character": "Lead", "episode_count": 30}],
             "profile_path": "/first.jpg"},
        ]}
        response = MagicMock()
        response.status = 200
        response.read.return_value = json.dumps(payload).encode()
        response.__enter__.return_value = response
        with patch.object(bridge, "TMDB_READ_ACCESS_TOKEN", "test-only-secret"), \
             patch.object(bridge.urllib.request, "urlopen", return_value=response) as request:
            cast = bridge.request_tmdb_cast("1396")
            self.assertEqual([x["name"] for x in cast], ["First", "Second"])
            self.assertEqual(cast[0]["character"], "Lead")
            self.assertEqual(cast[0]["tmdbId"], 1)
            url = request.call_args.args[0].full_url
            self.assertEqual(url, "https://api.themoviedb.org/3/tv/1396/aggregate_credits")
            self.assertNotIn("test-only-secret", url)
            self.assertIn("Bearer ", request.call_args.args[0].headers["Authorization"])

    def test_tmdb_api_failure_falls_back_without_losing_cast(self):
        sh = meta("sh", cast=[{"name": "Primary"}])
        with patch.object(bridge, "TMDB_READ_ACCESS_TOKEN", "test-only-secret"), \
             patch.object(bridge.urllib.request, "urlopen",
                          side_effect=urllib.error.URLError("fake network down")):
            result = copy.deepcopy(sh)
            source, count = bridge.apply_series_cast(result, sh, None)
        self.assertEqual((source, count), ("sh", 1))
        self.assertEqual(result["cast"], ["Primary"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
