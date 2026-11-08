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
# IMDb/TMDB dual-ID routes mirror the two real movie failure shapes;
# synthetic artwork URLs are not fetched during tests.
MOVIE_LOGO_FIXTURES = {
    "tt9876001": ("tt9876001", "1571955"),
    "tmdb:1571955": ("tt9876001", "1571955"),
    "tt9876002": ("tt9876002", "1507696"),
    "tmdb:1507696": ("tt9876002", "1507696"),
}

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
            obj = {"id": "mock."+prov, "types": ["movie", "series", "anime", "collection"], "resources": ["meta", "catalog", "watch_state"],
                   "catalogs": [
                       {"id": "search", "type": "series", "name": "Series Search",
                        "extra": [{"name": "search", "isRequired": True}]},
                       {"id": "movie-trending", "type": "movie", "name": "My Movies",
                        "extra": [{"name": "genre", "options": ["Horror", "Drama"]},
                                  {"name": "skip"}], "description": "configured by AIOMetadata"},
                       {"id": "series-trending", "type": "series", "name": "My Shows",
                        "extra": [{"name": "skip"}]},
                       {"id": "anime-cat", "type": "anime.movie", "name": "Anime Movies"},
                       {"id": "aiom.collection.demo", "type": "collection",
                        "name": "Directors", "extra": [{"name":"genre",
                        "isRequired": True, "options": ["None"]}]},
                       *([{"id": "tvdb-exclusive", "type": "movie", "name": "SH2 Exclusive"}]
                         if prov == "tvdb" else [])
                   ]}
        elif sub == f"meta/series/{SERIES_ID}.json":
            obj = {"meta": {"id": SERIES_ID, "name": "Test Show", "imdb_id": SERIES_ID, "_imdbId": SERIES_ID, "_tmdbId": "1396", "_tvdbId": "81189",
                            "poster": prov + "-poster",
                            "logo": "https://image.tmdb.org/t/p/w500/show-logo.png" if prov == "tvdb" else None,
                            "videos": episodes}}
        elif sub.startswith("meta/movie/") and sub.removeprefix("meta/movie/").removesuffix(".json") in MOVIE_LOGO_FIXTURES:
            movie_id = sub.removeprefix("meta/movie/").removesuffix(".json")
            imdb_id, tmdb_id = MOVIE_LOGO_FIXTURES[movie_id]
            obj = {"meta": {
                "id": movie_id, "name": "New release", "imdb_id": imdb_id,
                "_tmdbId": tmdb_id, "poster": "sh-poster",
                "background": "sh-backdrop", "trace": "SH",
                "videos": [{"id": movie_id + "-play"}],
                "logo": None if prov == "sh" else "https://image.tmdb.org/t/p/w500/fake-logo.png",
            }, "source": "SH-ONLY"}
        elif sub.startswith("meta/collection/aiom.collection:demo:"):
            # AIOMetadata builder's native full-meta format: member sources
            # use original SH catalog IDs (not the namespaced Bridge IDs).
            folder = sub.removeprefix("meta/collection/").removesuffix(".json")
            obj = {"meta": {
                "id": folder, "type": "collection", "name": "Director's Work",
                "poster": "https://example.invalid/director-poster.jpg",
                "collection": {
                    "items": [{"id": "aiom.collection:demo:child", "type": "collection",
                               "name": "Nested Folder", "collection": {}}],
                    "sources": [
                        {"type": "movie", "catalogId": "movie-trending",
                         "genre": "Horror"},
                        {"type": "series", "catalogId": "series-trending",
                         "addonId": "mock.sh"},
                        {"type": "movie", "catalogId": "external-catalog",
                         "addonId": "third-party.addon"},
                        {"type": "movie", "catalogId": "missing-in-manifest"},
                    ],
                },
            }, "metadata_source": "SH"}
        elif sub == "meta/movie/" + MOVIE_ID + ".json" and prov == "sh":
            obj = {"meta": {"id": MOVIE_ID, "name": "Movie", "trace": "SH-MOVIE-UNCHANGED"}}
        elif sub == "meta/anime/kitsu:123.json" and prov == "sh":
            obj = {"meta": {"id": "kitsu:123", "name": "Anime", "trace": "SH-ANIME-UNCHANGED"}}
        elif sub == "meta/series/tvdb:222.json" and prov == "tvdb":
            obj = {"meta": {"id": "tvdb:222", "name": "TVDB only", "videos": [{"id": "tvdb:222:1:1", "season": 1, "episode": 1}]}}
        elif sub.startswith("catalog/movie/movie-trending"):
            obj = {"metas": [
                {"id": "tt9876001", "name": "Missing logo", "type": "movie",
                 "poster": "preview-poster", "background": "preview-background"},
                {"id": "tt9876002", "name": "Existing logo", "type": "movie",
                 "logo": "https://image.tmdb.org/t/p/w500/preview-logo.png"},
                {"id": "kitsu:991", "name": "Anime movie", "type": "movie"}
            ], "catalog_context": sub, "provider": prov}
        elif sub.startswith("catalog/series/series-trending"):
            obj = {"metas": [
                {"id": SERIES_ID, "type": "series", "name": "Show without preview logo"},
                {"id": "mal:991", "type": "series", "name": "Anime ignored"}
            ], "catalog_context": sub, "provider": prov}
        elif sub.startswith("catalog/collection/aiom.collection.demo"):
            obj = {"metas": [{"id": "aiom.collection:demo:director",
                              "type":"collection", "name":"Director's Work",
                              "collection": {}}]}
        elif sub == "catalog/anime.movie/anime-cat.json":
            obj = {"metas": [{"id": "kitsu:991", "name": "Anime untouched",
                              "type": "anime.movie", "poster": "source"}]}
        elif sub.startswith("catalog/movie/tvdb-exclusive") and prov == "tvdb":
            obj = {"metas": [{"id": "tt9876001", "name": "SH2 only catalog", "type": "movie"}]}
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


    def test_collection_meta_rebinds_source_catalog_ids_and_keeps_members(self):
        path = "/meta/collection/aiom.collection:demo:director.json"
        source = self.bridge.request_json(
            "sh", "meta/collection/aiom.collection:demo:director.json")
        with patch.object(self.bridge, "CATALOG_PASSTHROUGH_ENABLED", True):
            served = self.get(path)
        self.assertEqual(served["meta"]["id"], source["meta"]["id"])
        self.assertEqual(served["meta"]["type"], "collection")
        self.assertEqual(served["meta"]["poster"], source["meta"]["poster"])
        self.assertEqual(served["meta"]["collection"]["items"],
                         source["meta"]["collection"]["items"])
        self.assertEqual(served["metadata_source"], "SH")
        original = source["meta"]["collection"]["sources"]
        target = served["meta"]["collection"]["sources"]
        self.assertEqual(len(target), len(original))
        self.assertEqual(target[0], {
            "type":"movie", "catalogId":
            self.bridge.proxied_catalog_id("sh","movie-trending"),
            "genre":"Horror"})
        self.assertEqual(target[1], {
            "type":"series", "catalogId":
            self.bridge.proxied_catalog_id("sh","series-trending")})
        self.assertEqual(target[2], original[2])  # third-party untouched
        self.assertEqual(target[3], original[3])  # invalid manifest untouched
        self.assertEqual(
            self.bridge.request_json("sh","meta/collection/aiom.collection:demo:director.json"),
            source, "read-only upstream cache must remain immutable")
        # Only the two matched catalog references may differ.
        normalized = copy.deepcopy(served)
        normalized["meta"]["collection"]["sources"] = copy.deepcopy(original)
        self.assertEqual(normalized, source)

    def test_collection_without_catalog_feature_preserves_original_payload(self):
        path = "/meta/collection/aiom.collection:demo:director.json"
        before = self.bridge.request_json(
            "sh", "meta/collection/aiom.collection:demo:director.json")
        with patch.object(self.bridge, "CATALOG_PASSTHROUGH_ENABLED", False):
            self.assertEqual(self.get(path), before)

    def test_collection_nested_folder_and_manifest_routes(self):
        bridged = self.bridge.proxied_catalog_id("sh", "aiom.collection.demo")
        with patch.object(self.bridge,"CATALOG_PASSTHROUGH_ENABLED", True):
            members = self.get("/catalog/collection/" + bridged + "/genre=None.json")
            child = self.get("/meta/collection/aiom.collection:demo:child.json")
        self.assertEqual(members["metas"][0]["id"], "aiom.collection:demo:director")
        self.assertEqual(child["meta"]["id"], "aiom.collection:demo:child")
        self.assertEqual(
            child["meta"]["collection"]["sources"][0]["catalogId"],
            self.bridge.proxied_catalog_id("sh","movie-trending"))

    def test_clearlogo_coverage_audit_selection_and_source_classes(self):
        import sys
        audit_path = ROOT / "scripts" / "audit_catalog_logo_coverage.py"
        spec = importlib.util.spec_from_file_location("logo_audit", audit_path)
        audit = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"bridge": self.bridge}):
            spec.loader.exec_module(audit)
        # Search-required catalogs are not probed without a real search value.
        self.assertIsNone(audit.catalog_sample({
            "id": "search", "type": "movie",
            "extra": [{"name":"search", "isRequired": True}]}, True))
        self.assertIsNone(audit.catalog_sample({
            "id": "genre", "type": "movie",
            "extra": [{"name":"genre", "isRequired": True, "options":["Horror"]}]}, False))
        self.assertEqual(audit.catalog_sample({
            "id": "genre", "type": "movie",
            "extra": [{"name":"genre", "isRequired": True, "options":["Horror"]},
                      {"name":"skip"}]}, True)["extras"], ["genre=Horror"])
        interleaved = audit.alternated([
            {"id":"a","type":"movie"},{"id":"b","type":"movie"},
            {"id":"c","type":"series"},{"id":"d","type":"series"}])
        self.assertEqual([x["id"] for x in interleaved], ["a","c","b","d"])
        row = {"source":"sh", "type":"movie", "id":"tt9876001"}
        self.assertEqual(audit.find_logo(row), "verified_other_source")
        self.assertEqual(audit.find_logo({**row, "id":"kitsu:991"}), "anime_excluded")
        with patch.object(self.bridge, "get_metadata", return_value=None):
            self.assertEqual(audit.find_logo(row), "own_metadata_unavailable")
        with patch.object(self.bridge, "get_metadata", return_value={
            "id":row["id"],"_imdbId":row["id"],
            "logo":"https://example.invalid/owned-logo.png"}):
            self.assertEqual(audit.find_logo(row), "same_source")

    def test_catalog_mirror_manifest_preserves_sources_order_filters_and_no_watch_state(self):
        with patch.object(self.bridge, "CATALOG_PASSTHROUGH_ENABLED", True):
            response = self.get("/manifest.json")
        original_sh = self.bridge.request_json("sh", "manifest.json")["catalogs"]
        original_sh2 = self.bridge.request_json("tvdb", "manifest.json")["catalogs"]
        self.assertEqual(len(response["catalogs"]), len(original_sh) + len(original_sh2))
        for i, (provider, catalog) in enumerate(
                [("sh", x) for x in original_sh] + [("tvdb", x) for x in original_sh2]):
            actual = copy.deepcopy(response["catalogs"][i])
            self.assertEqual(actual.pop("id"), self.bridge.proxied_catalog_id(provider, catalog["id"]))
            original = copy.deepcopy(catalog)
            original.pop("id")
            self.assertEqual(actual, original)
        self.assertTrue(any(c["name"] == "SH2 Exclusive" for c in response["catalogs"]))
        self.assertFalse("watch_state" in str(response["resources"]))
        ids = [(x["type"], x["id"]) for x in response["catalogs"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_catalog_passthrough_preserves_movie_page_except_missing_logo(self):
        ident = self.bridge.proxied_catalog_id("sh", "movie-trending")
        path = "/catalog/movie/" + ident + "/genre=Horror&skip=20.json"
        with patch.object(self.bridge, "CATALOG_PASSTHROUGH_ENABLED", True):
            actual = self.get(path)
        source = self.bridge.request_json("sh", "catalog/movie/movie-trending/genre=Horror&skip=20.json")
        self.assertEqual(len(actual["metas"]), len(source["metas"]))
        self.assertEqual(actual["metas"][0]["logo"],
                         "https://image.tmdb.org/t/p/w500/fake-logo.png")
        self.assertEqual(actual["metas"][1]["logo"], source["metas"][1]["logo"])
        self.assertEqual(actual["catalog_context"], source["catalog_context"])
        self.assertEqual(actual["provider"], source["provider"])
        transformed = copy.deepcopy(actual)
        transformed["metas"][0].pop("logo")
        self.assertEqual(transformed, source)

    def test_catalog_passthrough_tvdb_source_and_series_logo(self):
        for provider in ("sh", "tvdb"):
            cid = self.bridge.proxied_catalog_id(provider, "series-trending")
            with patch.object(self.bridge, "CATALOG_PASSTHROUGH_ENABLED", True):
                actual = self.get("/catalog/series/" + cid + ".json")
            self.assertEqual(actual["metas"][0]["logo"],
                             "https://image.tmdb.org/t/p/w500/show-logo.png")
            self.assertNotIn("logo", actual["metas"][1])
        exclusive = self.bridge.proxied_catalog_id("tvdb", "tvdb-exclusive")
        with patch.object(self.bridge, "CATALOG_PASSTHROUGH_ENABLED", True):
            rows = self.get("/catalog/movie/" + exclusive + ".json")["metas"]
        self.assertEqual(rows[0]["logo"], "https://image.tmdb.org/t/p/w500/fake-logo.png")

    def test_catalog_dotted_anime_media_type_passthrough(self):
        proxy = self.bridge.proxied_catalog_id("sh", "anime-cat")
        with patch.object(self.bridge, "CATALOG_PASSTHROUGH_ENABLED", True):
            mirrored = self.get("/catalog/anime.movie/" + proxy + ".json")
        upstream = self.bridge.request_json("sh", "catalog/anime.movie/anime-cat.json")
        self.assertEqual(mirrored, upstream)
        self.assertNotIn("logo", mirrored["metas"][0])

    def test_catalog_enrich_bound_and_disabled_passthrough(self):
        orig = self.bridge.request_json("sh", "catalog/movie/movie-trending.json")
        with patch.object(self.bridge, "CATALOG_ENRICH_LIMIT", 0):
            result = self.bridge.passthrough_catalog("sh", "movie", "movie-trending")
        self.assertEqual(result, orig)
        with patch.object(self.bridge, "CATALOG_ENRICH_LIMIT", 1):
            result = self.bridge.passthrough_catalog("sh", "movie", "movie-trending")
        self.assertEqual(result["metas"][0]["logo"], "https://image.tmdb.org/t/p/w500/fake-logo.png")
        self.assertNotIn("logo", result["metas"][2])

    def test_catalog_rejects_unknown_ids_bad_extras_and_path_traversal(self):
        cid = self.bridge.proxied_catalog_id("sh", "movie-trending")
        with patch.object(self.bridge, "CATALOG_PASSTHROUGH_ENABLED", True):
            for path in (
                "/catalog/movie/" + self.bridge.proxied_catalog_id("sh", "unknown") + ".json",
                "/catalog/series/" + cid + ".json",
                "/catalog/movie/" + cid + "/genre=Horror%0AInjected.json",
            ):
                with self.subTest(path=path):
                    with self.assertRaises(urllib.error.HTTPError) as ctx:
                        self.get(path)
                    self.assertEqual(ctx.exception.code, 404)
        self.assertFalse(self.bridge.safe_catalog_extra("genre=Horror%0AInjected"))
        self.assertTrue(self.bridge.safe_catalog_extra("search=New%20World&skip=20"))

    def test_movie_anime_unchanged(self):
        self.assertEqual(self.get("/meta/movie/" + MOVIE_ID + ".json")["meta"]["trace"], "SH-MOVIE-UNCHANGED")
        self.assertEqual(self.get("/meta/anime/kitsu:123.json")["meta"]["trace"], "SH-ANIME-UNCHANGED")


    def test_movie_logo_enrichment_real_http_dual_id_routes(self):
        for movie_id in MOVIE_LOGO_FIXTURES:
            with self.subTest(movie_id=movie_id):
                payload = self.get("/meta/movie/" + movie_id + ".json")
                self.assertEqual(payload["meta"]["logo"],
                    "https://image.tmdb.org/t/p/w500/fake-logo.png")
                self.assertEqual(payload["meta"]["poster"], "sh-poster")
                self.assertEqual(payload["meta"]["background"], "sh-backdrop")
                self.assertEqual(payload["meta"]["trace"], "SH")
                self.assertEqual(payload["source"], "SH-ONLY")
                self.assertEqual(payload["meta"]["videos"],
                    [{"id": movie_id + "-play"}])
                # The read-only SH response cached in Bridge must remain unchanged.
                sh = self.bridge.get_metadata("sh", "movie", movie_id)
                self.assertIsNone(sh["logo"])

    def test_movie_logo_full_passthrough_when_sh_has_logo(self):
        sh = {"meta": {
            "id": MOVIE_ID, "_tmdbId": "1571955",
            "logo": "https://image.tmdb.org/t/p/original/sh-logo.png",
            "poster": "sh-poster", "videos": [{"id": "play"}]},
            "extra": {"untouched": True}}
        before = copy.deepcopy(sh)
        with patch.object(self.bridge, "probe_other",
                          side_effect=AssertionError("SH2 must not be called")):
            self.assertEqual(self.bridge.enrich_movie_logo(sh, MOVIE_ID), before)
        self.assertEqual(sh, before)

    def test_movie_logo_rejects_conflicting_or_weak_identity(self):
        sh = {"meta": {
            "id": MOVIE_ID, "_imdbId": MOVIE_ID,
            "_tmdbId": "1571955", "logo": "",
            "poster": "original", "trace": "SH"}}
        bad = {"id": MOVIE_ID, "_imdbId": MOVIE_ID,
               "_tmdbId": "9999999", "logo": "https://example.invalid/bad.png"}
        with patch.object(self.bridge, "get_metadata", return_value=bad):
            self.assertEqual(self.bridge.enrich_movie_logo(sh, MOVIE_ID), sh)
        weak = {"meta": {"id": "unknown", "logo": None, "trace": "SH"}}
        with patch.object(self.bridge, "probe_other",
                          side_effect=AssertionError("No strong SH ID")):
            self.assertEqual(self.bridge.enrich_movie_logo(weak, "unknown"), weak)

    def test_movie_logo_rejects_missing_or_placeholder_sh2(self):
        sh = {"meta": {"id": MOVIE_ID, "_tmdbId": "1571955",
                        "logo": "", "trace": "SH"}}
        for invalid in ("", None, "https://example.invalid/missing_thumbnail.png"):
            with self.subTest(invalid=invalid):
                tvdb = {"id": MOVIE_ID, "_tmdbId": "1571955", "logo": invalid}
                with patch.object(self.bridge, "get_metadata", return_value=tvdb):
                    self.assertEqual(self.bridge.enrich_movie_logo(sh, MOVIE_ID), sh)

    def test_movie_logo_does_not_replace_absent_sh_or_anime(self):
        self.assertIsNone(self.bridge.enrich_movie_logo(None, MOVIE_ID))
        self.assertEqual(self.bridge.enrich_movie_logo({"other": "x"}, MOVIE_ID),
                         {"other": "x"})
        anime = {"meta": {"id": "mal:991", "_metaProvider": "mal",
                           "logo": None, "videos": [{"id": "anime-play"}]}}
        with patch.object(self.bridge, "probe_other",
                          side_effect=AssertionError("Do not enrich anime")):
            self.assertEqual(self.bridge.enrich_movie_logo(anime, "mal:991"), anime)

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


    def test_chinese_catalog_english_movie_with_imdb_and_sh_payload(self):
        foreign = "tch:movie:146:tt0190332"
        original = {"meta": {
            "id": "tt0190332", "_imdbId": "tt0190332", "_tmdbId": "146",
            "name": "Crouching Tiger, Hidden Dragon", "videos": [],
            "behaviorHints": {"keep": "existing"},
        }, "source": "SH"}
        with patch.object(self.bridge, "CHINESE_CATALOG_ENGLISH_META_ENABLED", True), \
             patch.object(self.bridge, "get_metadata", return_value=original["meta"]), \
             patch.object(self.bridge, "request_json", return_value=original), \
             patch.object(self.bridge, "enrich_movie_logo", side_effect=lambda p, mid: p):
            obj = self.get("/meta/movie/" + foreign + ".json")
        self.assertEqual(obj["meta"]["id"], foreign)
        self.assertEqual(obj["meta"]["name"], "Crouching Tiger, Hidden Dragon")
        self.assertEqual(obj["meta"]["behaviorHints"]["defaultVideoId"], "tt0190332")
        self.assertEqual(obj["meta"]["behaviorHints"]["keep"], "existing")
        self.assertEqual(obj["source"], "SH")
        self.assertEqual(original["meta"]["id"], "tt0190332")  # no mutation

    def test_chinese_catalog_english_movie_without_imdb_uses_tmdb_playback(self):
        foreign = "tch:movie:146"
        orig = {"meta": {"id": "tmdb:146", "_tmdbId": "146", "name": "English movie"}}
        with patch.object(self.bridge, "get_metadata", return_value=orig["meta"]), \
             patch.object(self.bridge, "request_json", return_value=orig), \
             patch.object(self.bridge, "enrich_movie_logo", side_effect=lambda p, mid: p):
            out = self.bridge.chinese_catalog_english_meta("movie", foreign)
        self.assertEqual(out["meta"]["id"], foreign)
        self.assertEqual(out["meta"]["behaviorHints"]["defaultVideoId"], "tmdb:146")

    def test_chinese_catalog_series_retains_original_episode_ids(self):
        foreign = "tch:series:223911:tt27432264"
        series = {"id": "tt27432264", "_imdbId": "tt27432264", "_tmdbId": "223911",
                  "name": "English series", "videos": [
                      {"id": "tt27432264:1:1", "season": 1, "episode": 1, "title": "Pilot"},
                      {"id": "tt27432264:1:2", "season": 1, "episode": 2, "title": "Two"}]}
        with patch.object(self.bridge, "CHINESE_CATALOG_ENGLISH_META_ENABLED", True), \
             patch.object(self.bridge, "get_metadata", return_value=series), \
             patch.object(self.bridge, "series_meta", return_value=(series, "sh", 0)):
            obj = self.get("/meta/series/" + foreign + ".json")
        self.assertEqual(obj["meta"]["id"], foreign)
        self.assertEqual(obj["meta"]["name"], "English series")
        self.assertEqual([v["id"] for v in obj["meta"]["videos"]],
                         ["tt27432264:1:1", "tt27432264:1:2"])
        self.assertEqual(series["id"], "tt27432264")

    def test_chinese_catalog_series_without_imdb_retains_tmdb_episode_ids(self):
        orig = {"id": "tmdb:223911", "_tmdbId": "223911",
                "name": "English series", "videos": [
                    {"id": "tmdb:223911:1:1", "season": 1, "episode": 1}]}
        with patch.object(self.bridge, "get_metadata", return_value=orig), \
             patch.object(self.bridge, "series_meta", return_value=(orig, "sh", 0)):
            out = self.bridge.chinese_catalog_english_meta("series", "tch:series:223911")
        self.assertEqual(out["meta"]["id"], "tch:series:223911")
        self.assertEqual(out["meta"]["videos"][0]["id"], "tmdb:223911:1:1")

    def test_chinese_catalog_conflicting_identity_fails_closed(self):
        a = {"id": "tt0190332", "_imdbId": "tt0190332", "_tmdbId": "146",
             "name": "True movie"}
        with patch.object(self.bridge, "get_metadata", return_value=a), \
             patch.object(self.bridge, "request_json") as payload:
            out = self.bridge.chinese_catalog_english_meta(
                "movie", "tch:movie:999:tt0190332")
        self.assertIsNone(out)
        payload.assert_not_called()

    def test_chinese_catalog_rejects_wrong_type_and_unsafe_ids(self):
        with patch.object(self.bridge, "get_metadata") as sh:
            for media_type, media_id in (
                ("movie", "tch:series:223911:tt27432264"),
                ("movie", "tch:movie:0:tt0190332"),
                ("movie", "tch:movie:146:ttanything"),
                ("series", "tch:series:1/../../etc"),
                ("anime", "tch:movie:146:tt0190332"),
            ):
                self.assertIsNone(self.bridge.chinese_catalog_english_meta(media_type, media_id))
            sh.assert_not_called()

    def test_chinese_catalog_meta_resource_prefix_priority(self):
        with patch.object(self.bridge, "CHINESE_CATALOG_ENGLISH_META_ENABLED", True):
            resources = self.get("/manifest.json")["resources"]
            self.assertEqual(resources[0], {
                "name": "meta",
                "types": ["movie", "series"],
                "idPrefixes": ["tch:"],
            })
            self.assertTrue(any(
                isinstance(r, dict) and r.get("name") == "meta"
                and not r.get("idPrefixes") for r in resources
            ))
            self.assertFalse(any(
                r == "watch_state" or
                (isinstance(r, dict) and r.get("name") == "watch_state")
                for r in resources
            ))


    def test_year_hint_trusts_matching_sh_release_fields(self):
        year = self.bridge.trusted_series_year
        self.assertEqual(year({"released": "2026-09-07T00:00:00Z",
                               "releaseInfo": "2026–", "year": 2026}), 2026)
        self.assertEqual(year({"released": "2026-09-07", "year": "2026"}), 2026)
        self.assertIsNone(year({"released": "2022-10-01", "releaseInfo": "2026–"}))
        self.assertIsNone(year({"released": "2026", "releaseInfo": "unknown"}))
        self.assertIsNone(year({"released": "not-a-date"}))
        self.assertIsNone(year({"released": "2026-10-01", "year": 2035}))
        self.assertIsNone(year({"year": 2099}))
        self.assertIsNone(year({"year": True}))
        self.assertIsNone(year({}))

    def test_year_hint_english_title_is_display_only(self):
        foreign = "tch:series:331912"
        series = {"id": "tmdb:331912", "_tmdbId": "331912",
                  "name": "Cause of Death", "released": "2026-09-07T00:00:00Z",
                  "releaseInfo": "2026–", "videos": [
                      {"id": "tmdb:331912:1:4", "season": 1, "episode": 4,
                       "title": "Episode 4", "released": "2026-09-10T00:00:00Z"}]}
        with patch.object(self.bridge, "CHINESE_CATALOG_TITLE_YEAR_ENABLED", True), \
             patch.object(self.bridge, "CHINESE_CATALOG_ENGLISH_META_ENABLED", True), \
             patch.object(self.bridge, "get_metadata", return_value=series), \
             patch.object(self.bridge, "series_meta", return_value=(series, "sh", 0)):
            payload = self.get("/meta/series/" + foreign + ".json")
            manifest = self.get("/manifest.json")
        self.assertEqual(payload["meta"]["name"], "Cause of Death (2026)")
        self.assertEqual(payload["meta"]["id"], foreign)
        self.assertEqual(payload["meta"]["videos"][0]["id"], "tmdb:331912:1:4")
        self.assertEqual(payload["meta"]["videos"][0]["released"], "2026-09-10T00:00:00Z")
        self.assertEqual(series["name"], "Cause of Death")
        self.assertEqual(manifest["version"], "0.4.1-rc1")
        self.assertFalse(any(r == "watch_state" for r in manifest["resources"]))

    def test_year_hint_off_or_conflicting_is_unchanged(self):
        series = {"id": "tmdb:331912", "_tmdbId": "331912",
                  "name": "Cause of Death",
                  "released": "2026-09-07T00:00:00Z",
                  "releaseInfo": "2026–", "videos": []}
        with patch.object(self.bridge, "CHINESE_CATALOG_TITLE_YEAR_ENABLED", False), \
             patch.object(self.bridge, "get_metadata", return_value=series), \
             patch.object(self.bridge, "series_meta", return_value=(series, "sh", 0)):
            payload = self.bridge.chinese_catalog_english_meta("series", "tch:series:331912")
        self.assertEqual(payload["meta"]["name"], "Cause of Death")

        self.assertEqual(self.bridge.english_series_year_label({
            "name": "Cause of Death (2026)", "releaseInfo": "2026–"},
            {"releaseInfo": "2026–"}), "Cause of Death (2026)")
        self.assertEqual(self.bridge.english_series_year_label({
            "name": "Cause of Death (2022)", "releaseInfo": "2026–"},
            {"releaseInfo": "2026–"}), "Cause of Death (2022)")
        self.assertEqual(self.bridge.english_series_year_label({
            "name": "Cause of Death", "releaseInfo": "2026–",
            "released": "2022-01-01"},
            {"releaseInfo": "2026–"}), "Cause of Death")
        self.assertEqual(self.bridge.english_series_year_label({
            "name": "Cause of Death"}, {"releaseInfo": "2026–"}), "Cause of Death")
        # SH and final SH2 structure disagree: never imprint the wrong year.
        self.assertEqual(self.bridge.english_series_year_label({
            "name": "Cause of Death", "releaseInfo": "2022–"},
            {"released": "2026-09-07"}), "Cause of Death")
        # Even perfect-looking final metadata isn't enough without SH release year.
        self.assertEqual(self.bridge.english_series_year_label({
            "name": "Cause of Death", "releaseInfo": "2026–"},
            {"name": "Cause of Death"}), "Cause of Death")

    def test_year_hint_never_alters_movies_or_canonical_meta(self):
        movie = {"meta": {"id": "tt0190332", "_imdbId": "tt0190332",
                          "_tmdbId": "146", "name": "Crouching Tiger, Hidden Dragon",
                          "released": "2000-07-06T00:00:00Z"}}
        with patch.object(self.bridge, "CHINESE_CATALOG_TITLE_YEAR_ENABLED", True), \
             patch.object(self.bridge, "get_metadata", return_value=movie["meta"]), \
             patch.object(self.bridge, "request_json", return_value=movie), \
             patch.object(self.bridge, "enrich_movie_logo", side_effect=lambda p, mid: p):
            obj = self.bridge.chinese_catalog_english_meta("movie", "tch:movie:146:tt0190332")
        self.assertEqual(obj["meta"]["name"], "Crouching Tiger, Hidden Dragon")
        self.assertEqual(obj["meta"]["behaviorHints"]["defaultVideoId"], "tt0190332")

    def test_chinese_catalog_feature_disabled_by_default(self):
        with patch.object(self.bridge, "CHINESE_CATALOG_ENGLISH_META_ENABLED", False), \
             patch.object(self.bridge, "chinese_catalog_english_meta") as resolver:
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.get("/meta/movie/tch:movie:146:tt0190332.json")
            self.assertEqual(caught.exception.code, 404)
            resolver.assert_not_called()
        self.assertFalse(any(
            (r == "watch_state" or isinstance(r, dict) and r.get("name") == "watch_state")
            for r in self.get("/manifest.json")["resources"]))

if __name__ == "__main__": unittest.main(verbosity=2)
