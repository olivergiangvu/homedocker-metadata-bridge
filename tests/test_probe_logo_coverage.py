import importlib.util
import pathlib
import unittest

SRC = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "probe_logo_coverage.py"
spec = importlib.util.spec_from_file_location("logo_probe", SRC)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class LogoCoverageProbeTests(unittest.TestCase):
    def test_classification_and_no_arbitrary_network(self):
        self.assertFalse(probe.is_valid_art(""))
        self.assertFalse(probe.is_valid_art("https://cdn.example/no_image.png"))
        self.assertEqual(probe.art_vendor("https://image.tmdb.org/t/p/original/logo.svg"), "TMDB")
        self.assertEqual(probe.art_vendor("https://artworks.thetvdb.com/abc.png"), "TVDB")
        self.assertEqual(probe.art_vendor("https://assets.fanart.tv/artwork.png"), "Fanart")
        self.assertEqual(probe.head_art("http://127.0.0.1:3232/private"), "NOT_TESTED_HOST")
        self.assertEqual(probe.head_art("https://example.invalid/poster.png"), "NOT_TESTED_HOST")

    def test_detects_verified_bridge_gain(self):
        sh = {"logo": None}
        sh2 = {"logo": "https://assets.fanart.tv/x.png"}
        merged = {"logo": sh2["logo"]}
        self.assertEqual(probe.choose_source(sh, sh2, merged), "SH2")
        self.assertTrue(probe.is_valid_art(merged["logo"]))

    def test_unknown_art_source_preserved_without_network_fetch(self):
        own = "https://imageproxy.example.invalid/a.png"
        self.assertEqual(probe.art_vendor(own), "Other/Proxy")
        self.assertEqual(probe.head_art(own), "NOT_TESTED_HOST")

if __name__ == "__main__":
    unittest.main()
