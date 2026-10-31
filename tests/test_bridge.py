import copy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
SERIES_ID = "tt1234567"
MOVIE_ID = "tt7777777"

class Mock(BaseHTTPRequestHandler):
    def log_message(self, *_): pass
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        prov = "sh" if path.startswith("/sh/") else "tvdb"
        sub = path.split("/", 2)[2]
        episodes = [{"id": SERIES_ID + ":1:1", "season": 1, "episode": 1, "title": "One", "thumbnail": "sh-one", "overview": "sh-description"},
                    {"id": SERIES_ID + ":1:2", "season": 1, "episode": 2, "title": "Two", "thumbnail": "" if prov == "sh" else "tvdb-two", "overview": "" if prov == "sh" else "tvdb-desc"}]
        if prov == "tvdb":
            episodes[0]["thumbnail"] = "tvdb-one"
        if sub == "manifest.json":
            obj = {"id": "mock."+prov, "types": ["movie", "series", "anime"], "resources": ["meta", "catalog", "watch_state"],
                   "catalogs": [{"id": "search", "type": "series", "name": "Series Search", "extra": [{"name": "search", "isRequired": True}]}]}
        elif sub == f"meta/series/{SERIES_ID}.json":
            obj = {"meta": {"id": SERIES_ID, "name": "Test Show", "imdb_id": SERIES_ID, "_imdbId": SERIES_ID, "_tmdbId": "1396", "_tvdbId": "81189",
                            "poster": prov + "-poster", "videos": episodes}}
        elif sub == "meta/movie/" + MOVIE_ID + ".json" and prov == "sh":
            obj = {"meta": {"id": MOVIE_ID, "name": "Movie", "trace": "SH-MOVIE-UNCHANGED"}}
        elif sub == "meta/anime/kitsu:123.json" and prov == "sh":
            obj = {"meta": {"id": "kitsu:123", "name": "Anime", "trace": "SH-ANIME-UNCHANGED"}}
        elif sub == "meta/series/tvdb:222.json" and prov == "tvdb":
            obj = {"meta": {"id": "tvdb:222", "name": "TVDB only", "videos": [{"id": "tvdb:222:1:1", "season": 1, "episode": 1}]}}
        elif sub.startswith("catalog/series/search/"):
            obj = {"metas": [{"id": SERIES_ID if prov == "sh" else "tvdb:222", "name": "Show " + prov, "type": "series"}]}
        else:
            self.send_response(404); self.end_headers(); return
        data = json.dumps(obj).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(data)))
        self.end_headers(); self.wfile.write(data)

class BridgeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mock = ThreadingHTTPServer(("127.0.0.1", 0), Mock)
        threading.Thread(target=cls.mock.serve_forever, daemon=True).start()
        port = cls.mock.server_address[1]
        os.environ.update({"SH_MANIFEST_URL": f"http://127.0.0.1:{port}/sh/manifest.json",
                           "SH2_MANIFEST_URL": f"http://127.0.0.1:{port}/tvdb/manifest.json"})
        spec = importlib.util.spec_from_file_location("bridge", ROOT / "bridge.py")
        cls.bridge = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.bridge)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), cls.bridge.Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = "http://127.0.0.1:" + str(cls.server.server_address[1])
        cls.overrides = tempfile.TemporaryDirectory()
        cls.bridge.OVERRIDES_FILE = Path(cls.overrides.name) / "overrides.json"
        cls.bridge.OVERRIDES_FILE.write_text('{"prefer_tvdb_meta_ids": []}')

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.mock.shutdown(); cls.overrides.cleanup()

    def get(self, path):
        with urllib.request.urlopen(self.url + path) as resp:
            return json.load(resp)

    def test_manifest_exposes_no_watch_state(self):
        m = self.get("/manifest.json")
        self.assertFalse(any(isinstance(x, str) and x == "watch_state" or isinstance(x, dict) and x.get("name") == "watch_state" for x in m["resources"]))
        self.assertIn("series", m["types"])

    def test_movie_anime_unchanged(self):
        self.assertEqual(self.get("/meta/movie/" + MOVIE_ID + ".json")["meta"]["trace"], "SH-MOVIE-UNCHANGED")
        self.assertEqual(self.get("/meta/anime/kitsu:123.json")["meta"]["trace"], "SH-ANIME-UNCHANGED")

    def test_series_preserves_ids_and_uses_fallback_fields(self):
        m = self.get("/meta/series/" + SERIES_ID + ".json")["meta"]
        self.assertEqual(m["poster"], "sh-poster")
        self.assertEqual(m["videos"][0]["thumbnail"], "sh-one")
        self.assertEqual(m["videos"][1]["thumbnail"], "tvdb-two")
        self.assertEqual(m["videos"][1]["id"], SERIES_ID + ":1:2")

    def test_series_tvdb_only(self):
        m = self.get("/meta/series/tvdb:222.json")["meta"]
        self.assertEqual(m["id"], "tvdb:222")
        self.assertEqual(m["videos"][0]["id"], "tvdb:222:1:1")

    def test_search_union(self):
        metas = self.get("/catalog/series/unified-series-search/search=midnight%20diner.json")["metas"]
        self.assertEqual(len(metas), 2)
        self.assertEqual({x["id"] for x in metas}, {SERIES_ID, "tvdb:222"})

    def test_conflicting_identity_prevents_series_overlay(self):
        same = {"ids": {"imdb": SERIES_ID, "tmdb": "1396"}}
        conflict = {"ids": {"imdb": SERIES_ID, "tmdb": "99999"}}
        self.assertFalse(self.bridge.strongly_same_series(same, conflict))
        self.assertTrue(self.bridge.strongly_same_series(same, same))

    def test_episode_matching_refuses_unverified_season_jump(self):
        one = {"id": "tt1234567:1:1", "season": 1, "episode": 1,
               "title": "First Night"}
        other = {"id": "tt1234567:3:1", "season": 3, "episode": 1,
                 "title": "First Night"}
        self.assertFalse(self.bridge.strong_episode_match(one, other, False))
        self.assertTrue(self.bridge.strong_episode_match(one, dict(one), True))

    def test_preflight_probe_helpers_keep_identifiers_private(self):
        spec = importlib.util.spec_from_file_location(
            "upstream_probe", ROOT / "scripts" / "probe_upstreams.py")
        probe = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(probe)
        sh = self.bridge.get_metadata("sh", "series", SERIES_ID)
        summary = probe.metadata_summary(sh, self.bridge)
        self.assertEqual(summary["identity_keys"], ["imdb", "tmdb", "tvdb"])
        self.assertEqual(summary["episode_count"], 2)
        self.assertNotIn(SERIES_ID, json.dumps(summary))
        sm = self.bridge.request_json("sh", "manifest.json")
        tm = self.bridge.request_json("tvdb", "manifest.json")
        self.assertEqual(probe.searchable_catalog_count(sm), 1)
        sh_ids = probe.provider_search_ids(self.bridge, "sh", sm, "test")
        tvdb_ids = probe.provider_search_ids(self.bridge, "tvdb", tm, "test")
        self.assertEqual(len(sh_ids), 1)
        self.assertEqual(len(tvdb_ids - sh_ids), 1)

    def test_aiometadata_v382_id_fields(self):
        native = {"id": SERIES_ID, "imdb_id": SERIES_ID,
                  "_tmdbId": 1396, "_tvdbId": "81189"}
        self.assertEqual(self.bridge.ids_of(native),
                         {"imdb": SERIES_ID, "tmdb": "1396", "tvdb": "81189"})
        tvdb = {"id": "tvdb:81189", "_imdbId": SERIES_ID,
                "_tmdbId": "1396"}
        self.assertTrue(self.bridge.strongly_same_series(native, tvdb))
        self.assertEqual(self.bridge.extra_ids(native),
                         [SERIES_ID, "tmdb:1396", "tvdb:81189"])

    def test_aiometadata_id_conflict_rejected(self):
        inconsistent = {"id": SERIES_ID, "_imdbId": "tt9999999",
                        "_tmdbId": "1396"}
        self.assertEqual(self.bridge.ids_of(inconsistent), {})
        self.assertFalse(self.bridge.strongly_same_series(
            inconsistent, {"id": SERIES_ID, "_tmdbId": "1396"}))

    def test_integrated_overlay_with_native_aiometadata_fields(self):
        # The mock response uses the actual v3.8.2 ID vocabulary rather than
        # an artificial `meta.ids` field.
        sh = self.bridge.get_metadata("sh", "series", SERIES_ID)
        tvdb = self.bridge.get_metadata("tvdb", "series", SERIES_ID)
        self.assertTrue(self.bridge.strongly_same_series(sh, tvdb))
        m = self.get("/meta/series/" + SERIES_ID + ".json")["meta"]
        self.assertEqual(m["videos"][1]["thumbnail"], "tvdb-two")
        self.assertEqual(m["videos"][1]["id"], SERIES_ID + ":1:2")

    def test_episode_cohort_summary_30_vs_20_protects_ids(self):
        spec = importlib.util.spec_from_file_location(
            "upstream_probe", ROOT / "scripts" / "probe_upstreams.py")
        probe = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(probe)
        # Simulate the observed 20 TMDB episodes + 10 TVDB-only season 3.
        sh = {"videos": []}
        tvdb = {"videos": []}
        for season in (1, 2, 3):
            for episode in range(1, 11):
                item_id = f"{SERIES_ID}:{season}:{episode}"
                item = {"id": item_id, "season": season, "episode": episode,
                        "released": f"2026-10-{episode:02d}",
                        "thumbnail": "image", "overview": "description"}
                if season < 3:
                    sh["videos"].append(copy.deepcopy(item))
                if season == 3 and episode <= 8:
                    item["overview"] = ""
                tvdb["videos"].append(item)
        report = probe.episode_cohort_summary(sh, tvdb, self.bridge)
        self.assertEqual(report["shared_video_ids"], 20)
        self.assertEqual(report["shared_same_season_episode"], 20)
        self.assertEqual(report["shared_different_season_episode"], 0)
        self.assertEqual(report["tvdb_only_by_season"], {
            "3": {"episodes": 10, "thumbnails": 10, "overviews": 2}})
        self.assertEqual(report["duplicate_video_ids"], {"sh": 0, "tvdb": 0})
        self.assertNotIn(SERIES_ID, json.dumps(report))

    def test_episode_cohort_summary_detects_shared_id_conflicts(self):
        spec = importlib.util.spec_from_file_location(
            "upstream_probe", ROOT / "scripts" / "probe_upstreams.py")
        probe = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(probe)
        id0 = f"{SERIES_ID}:1:1"
        sh = {"videos": [{"id": id0, "season": 1, "episode": 1,
                           "released": "2025-01-01", "overview": "SH"}]}
        tvdb = {"videos": [{"id": id0, "season": 3, "episode": 7,
                             "released": "2026-01-01", "overview": ""}]}
        report = probe.episode_cohort_summary(sh, tvdb, self.bridge)
        self.assertEqual(report["shared_different_season_episode"], 1)
        self.assertEqual(report["shared_conflicting_air_dates"], 1)
        self.assertEqual(report["shared_aligned_tvdb_missing_overview_with_sh_available"], 0)

    def test_unified_series_20_sh_plus_10_tvdb_preserves_episodes(self):
        sh = {"id": SERIES_ID, "_tmdbId": "1396", "_tvdbId": "81189",
              "name": "Tokyo Stories", "poster": "tmdb-poster", "videos": []}
        tvdb = {"id": SERIES_ID, "_tmdbId": "1396", "_tvdbId": "81189",
                "name": "Tokyo Stories", "poster": "tvdb-poster", "videos": []}
        for season in (1, 2, 3):
            for episode in range(1, 11):
                video_id = f"{SERIES_ID}:{season}:{episode}"
                t = {"id": video_id, "season": season, "episode": episode,
                     "title": f"Episode {episode}", "released": "2026-10-20",
                     "thumbnail": "tvdb-image", "overview": "" if season == 3 else "tvdb-description"}
                tvdb["videos"].append(t)
                if season < 3:
                    sh["videos"].append({**t, "released": "2016-01-01",
                                         "thumbnail": "tmdb-image", "overview": "tmdb-description"})
        def getter(provider, media_type, media_id):
            return copy.deepcopy(sh if provider == "sh" else tvdb)
        with patch.object(self.bridge, "get_metadata", side_effect=getter):
            merged, provider, changed = self.bridge.series_meta("series", SERIES_ID)
        self.assertEqual(provider, "sh")
        self.assertEqual(len(merged["videos"]), 30)
        self.assertEqual(changed, 10)
        self.assertEqual(merged["poster"], "tmdb-poster")
        self.assertEqual(merged["videos"][0]["id"], f"{SERIES_ID}:1:1")
        self.assertEqual(merged["videos"][0]["thumbnail"], "tmdb-image")
        self.assertEqual(merged["videos"][0]["released"], "2016-01-01")
        self.assertEqual(merged["videos"][-1]["id"], f"{SERIES_ID}:3:10")
        self.assertEqual(merged["videos"][-1]["thumbnail"], "tvdb-image")
        self.assertEqual(merged["videos"][-1]["overview"], "")

    def test_unified_series_rejects_conflicting_shared_episode_id(self):
        sh = {"id": SERIES_ID, "_tmdbId": "1396", "videos": [
            {"id": SERIES_ID+":1:1", "season": 1, "episode": 1}]}
        tvdb = {"id": SERIES_ID, "_tmdbId": "1396", "videos": [
            {"id": SERIES_ID+":1:1", "season": 3, "episode": 5},
            {"id": SERIES_ID+":3:2", "season": 3, "episode": 2}]}
        self.assertEqual(self.bridge.union_episode_videos(sh, tvdb), 0)
        self.assertEqual(len(sh["videos"]), 1)

    def test_search_dedup_uses_verified_identity_not_name(self):
        # 2016 Tokyo Stories appears under different provider ID namespaces.
        # A 2009 Midnight Diner remains a separate work despite its shared name.
        sh_item = {"id": "tmdb:1396", "type": "series", "name": "Midnight Diner: Tokyo Stories", "poster": "tmdb-card"}
        tvdb_item = {"id": "tvdb:81189", "type": "series", "name": "Midnight Diner", "poster": "tvdb-card"}
        old_item = {"id": "tvdb:2009", "type": "series", "name": "Midnight Diner", "poster": "old-card"}
        meta = {
            ("sh", "tmdb:1396"): {"id": SERIES_ID, "_tmdbId": "1396", "_tvdbId": "81189",
                                   "name": "Midnight Diner: Tokyo Stories", "poster": "tmdb-full"},
            ("tvdb", "tvdb:81189"): {"id": "tvdb:81189", "_imdbId": SERIES_ID,
                                      "_tmdbId": "1396", "name": "Midnight Diner"},
            ("tvdb", "tvdb:2009"): {"id": "tvdb:2009", "_imdbId": "tt2009000",
                                     "name": "Midnight Diner"},
        }
        with patch.object(self.bridge, "get_metadata", side_effect=lambda p,t,i: meta.get((p,i))):
            out = self.bridge.canonical_search_entries([
                ("tvdb", tvdb_item), ("sh", sh_item), ("tvdb", old_item)], "series")
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["id"], SERIES_ID)
        self.assertEqual(out[0]["poster"], "tmdb-full")
        self.assertEqual(out[0]["name"], "Midnight Diner: Tokyo Stories")
        self.assertEqual(out[1]["id"], "tt2009000")

    def test_search_same_raw_id_retains_sh_priority_when_tvdb_first(self):
        tvdb_preview = {"id": SERIES_ID, "type": "series", "name": "TVDB name", "poster": "tvdb-img"}
        sh_preview = {"id": SERIES_ID, "type": "series", "name": "TMDB name", "poster": "tmdb-img"}
        def getter(provider, media_type, media_id):
            return {"id": SERIES_ID, "_tmdbId": "1396", "_tvdbId": "81189",
                    "poster": provider + "-full", "name": provider + "-full-name"}
        with patch.object(self.bridge, "get_metadata", side_effect=getter):
            result = self.bridge.canonical_search_entries([
                ("tvdb", tvdb_preview), ("sh", sh_preview)], "series")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], SERIES_ID)
        self.assertEqual(result[0]["poster"], "sh-full")
        self.assertEqual(result[0]["name"], "sh-full-name")

    def test_search_tvdb_only_stays_resolvable_without_tmdb(self):
        item = {"id": "tvdb:222", "type": "series", "name": "Exclusive"}
        def getter(provider, media_type, media_id):
            if provider == "sh": return None
            if media_id == "tvdb:222":
                return {"id": "tvdb:222", "name": "Exclusive", "videos": []}
            return None
        with patch.object(self.bridge, "get_metadata", side_effect=getter):
            result = self.bridge.canonical_search_entries([("tvdb", item)], "series")
            detail, origin, _ = self.bridge.series_meta("series", result[0]["id"])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], "tvdb:222")
        self.assertEqual(origin, "tvdb")
        self.assertEqual(detail["name"], "Exclusive")

    def test_anime_series_under_series_type_stays_exact_sh_passthrough(self):
        sh = {"id": "kitsu:333", "name": "Anime title", "_metaProvider": "kitsu",
              "poster": "anime-poster", "videos": [
                  {"id": "kitsu:333:1", "season": 1, "episode": 1}]}
        with patch.object(self.bridge, "get_metadata", side_effect=lambda p,t,i: copy.deepcopy(sh) if p=="sh" else self.fail("TVDB anime must not be queried")):
            result, origin, added = self.bridge.series_meta("series", "kitsu:333")
        self.assertEqual(result, sh)
        self.assertEqual(origin, "sh")
        self.assertEqual(added, 0)

    def test_missing_series_artwork_falls_back_to_tvdb(self):
        sh = {"id": SERIES_ID, "_tvdbId": "81189", "poster": "https://example.invalid/missing_thumbnail.png", "videos": []}
        tvdb = {"id": SERIES_ID, "_tvdbId": "81189", "poster": "https://example.invalid/real-poster.jpg", "videos": []}
        with patch.object(self.bridge, "get_metadata", side_effect=lambda p,t,i: copy.deepcopy(sh if p=="sh" else tvdb)):
            detail, origin, _ = self.bridge.series_meta("series", SERIES_ID)
        self.assertEqual(origin, "sh")
        self.assertEqual(detail["poster"], "https://example.invalid/real-poster.jpg")

    def test_prefer_tvdb_requires_optin(self):
        self.bridge.OVERRIDES_FILE.write_text(json.dumps({"prefer_tvdb_meta_ids": [SERIES_ID]}))
        try:
            m = self.get("/meta/series/" + SERIES_ID + ".json")["meta"]
            self.assertEqual(m["poster"], "sh-poster")
            self.assertEqual(m["videos"][0]["thumbnail"], "sh-one")
            self.assertEqual(m["videos"][1]["thumbnail"], "tvdb-two")
        finally:
            self.bridge.OVERRIDES_FILE.write_text('{"prefer_tvdb_meta_ids": []}')

if __name__ == "__main__": unittest.main(verbosity=2)
