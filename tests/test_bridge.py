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
            obj = {"meta": {"id": SERIES_ID, "name": "Test Show", "ids": {"imdb": SERIES_ID, "tmdb": "1396", "tvdb": "81189"},
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
