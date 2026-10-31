"""Offline tests for redacted per-episode AIOStreams candidate audit."""
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "stream_probe", ROOT / "scripts" / "probe_stream_candidates.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class ProbeCandidatesTest(unittest.TestCase):
    def test_manifest_to_stream_url_preserves_config_query(self):
        s = probe.stream_url_from_manifest(
            "https://example.invalid/secret-uuid/manifest.json?private=token", "tt123:3:1")
        self.assertEqual(s, "https://example.invalid/secret-uuid/stream/series/tt123:3:1.json?private=token")

    def test_invalid_manifest_and_video_id_rejected(self):
        with self.assertRaises(ValueError):
            probe.stream_url_from_manifest("file:///token/manifest.json", "tt123:3:1")
        with self.assertRaises(ValueError):
            probe.stream_url_from_manifest("https://example.invalid/manifest.json", "../../secret")

    def test_episode_label_and_locators_are_redacted(self):
        streams = [
            {"name": "Provider A", "title": "SecretFilm.S03E01.1080p", "url": "https://key.invalid/private-token"},
            {"name": "Provider B", "title": "SecretFilm.S03E02.1080p", "infoHash": "secret-hash", "fileIdx": 0},
            {"name": "Provider C", "title": "SecretFilm 3x01", "url": "https://key.invalid/private-token"},
            {"name": "Provider D", "title": "Series pack", "externalUrl": "https://private.invalid"},
        ]
        report, locs = probe.summarize(streams, 3, 1)
        self.assertEqual(report["candidate_count"], 4)
        self.assertEqual(report["explicit_matching_episode_labels"], 2)
        self.assertEqual(report["explicit_wrong_episode_labels"], 1)
        self.assertEqual(report["no_parseable_episode_label"], 1)
        self.assertEqual(report["distinct_locator_count"], 3)
        self.assertEqual(len(locs), 3)
        txt = json.dumps(report)
        self.assertNotIn("private", txt)
        self.assertNotIn("secret-hash", txt)
        self.assertNotIn("SecretFilm", txt)

    def test_pack_multi_episode_hints_ambiguous(self):
        report, _ = probe.summarize(
            [{"title": "Show.S03E01.S03E02.PACK", "url": "https://x.invalid/1"}], 3, 1)
        self.assertEqual(report["ambiguous_multi_episode_labels"], 1)

    def test_same_locator_hash_across_episodes(self):
        a = probe.summarize([{"url": "https://x.invalid/secret"}], 3, 1)[1]
        b = probe.summarize([{"url": "https://x.invalid/secret"}], 3, 2)[1]
        self.assertEqual(len(a & b), 1)


if __name__ == "__main__":
    unittest.main()