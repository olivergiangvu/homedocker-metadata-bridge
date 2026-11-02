import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import urllib.error

PATH = Path(__file__).resolve().parents[1] / "scripts" / "audit_sh2_fanart_logo_ab.py"
spec = importlib.util.spec_from_file_location("fanart_ab_audit", PATH)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class Response(io.BytesIO):
    def __enter__(self):
        return self
    def __exit__(self, *_):
        self.close()


class FakeOpener:
    def __init__(self, payload=None, status=None, key_kind="project"):
        self.payload = payload or {}
        self.status = status
        self.key_kind = key_kind
    def open(self, req, timeout=None):
        assert req.full_url.startswith("https://webservice.fanart.tv/v3.2/")
        header = "Client-key" if self.key_kind == "personal" else "Api-key"
        assert req.get_header(header) == "secret-test-key"
        other = "Api-key" if self.key_kind == "personal" else "Client-key"
        assert req.get_header(other) is None
        assert "secret-test-key" not in req.full_url
        if self.status:
            raise urllib.error.HTTPError(req.full_url, self.status, "test", {}, None)
        return Response(json.dumps(self.payload).encode())


class FanartLogoABAuditTests(unittest.TestCase):
    def test_movie_hd_logo_matches_current_aiometadata_selector(self):
        with patch.object(audit.urllib.request, "build_opener", return_value=FakeOpener({
            "hdmovielogo": [{"url": "https://assets.fanart.tv/hd.png"}]})):
            self.assertEqual(audit.get_fanart("movie", "123", "secret-test-key"), "SUPPORTED_LOGO_AVAILABLE")

    def test_tv_hd_logo_matches_current_aiometadata_selector(self):
        with patch.object(audit.urllib.request, "build_opener", return_value=FakeOpener({
            "hdtvlogo": [{"url": "https://assets.fanart.tv/hd.png"}]})):
            self.assertEqual(audit.get_fanart("series", "123", "secret-test-key"), "SUPPORTED_LOGO_AVAILABLE")

    def test_legacy_only_is_distinguished_from_supported_hd(self):
        for kind, key in (("movie", "movielogo"), ("series", "clearlogo")):
            with self.subTest(kind=kind):
                with patch.object(audit.urllib.request, "build_opener", return_value=FakeOpener({
                    key: [{"url": "https://assets.fanart.tv/legacy.png"}]})):
                    self.assertEqual(audit.get_fanart(kind, "123", "secret-test-key"), "ONLY_LEGACY_LOGO_AVAILABLE")

    def test_personal_api_key_in_memory_uses_client_key_header(self):
        with patch.object(audit.urllib.request, "build_opener", return_value=FakeOpener({
            "hdtvlogo":[{"url":"https://assets.fanart.tv/logo.png"}]
        }, key_kind="personal")):
            self.assertEqual(audit.get_fanart("series", "123", "secret-test-key",
                                               key_kind="personal"), "SUPPORTED_LOGO_AVAILABLE")

    def test_stdin_personal_key_mode_bypasses_tty_and_never_logs_secret(self):
        import contextlib
        transcript = io.StringIO()
        cohort = json.dumps({
            "counts":{"missing_logo":1},
            "candidates":[{"type":"movie","id":"123"}]
        })
        with patch.object(audit.sys,"argv",["audit","--personal-key-stdin"]), \
             patch.object(audit.sys,"stdin",io.StringIO("secret-test-key\\n")), \
             patch.object(audit,"cmd",return_value=cohort), \
             patch.object(audit,"get_fanart",return_value="SUPPORTED_LOGO_AVAILABLE") as getter, \
             contextlib.redirect_stdout(transcript):
            self.assertEqual(audit.main(), 0)
        getter.assert_called_once_with(
            "movie","123","secret-test-key",key_kind="personal")
        self.assertIn("POTENTIAL_ADD_ON_SH2_FANART 1",transcript.getvalue())
        self.assertNotIn("secret-test-key",transcript.getvalue())

    def test_empty_or_unknown_or_invalid_and_api_errors(self):
        with patch.object(audit.urllib.request, "build_opener", return_value=FakeOpener()):
            self.assertEqual(audit.get_fanart("series", "123", "secret-test-key"), "NO_LOGO")
        self.assertEqual(audit.get_fanart("movie", "tt123", "secret-test-key"), "INVALID_ID")
        for status, expected in ((401,"AUTH_ERROR"),(404,"NOT_FOUND"),(429,"RATE_LIMITED"),(500,"HTTP_OTHER")):
            with self.subTest(status=status):
                with patch.object(audit.urllib.request, "build_opener", return_value=FakeOpener(status=status)):
                    self.assertEqual(audit.get_fanart("movie", "123", "secret-test-key"), expected)

    def test_key_presence_detected_without_printing_value(self):
        with patch.object(audit, "cmd", side_effect=[
            "aiometadata-sh2\naiometadata-sh",
            json.dumps([{"Config":{"Env":["FANART_API_PROJECT_KEY=secret-test-key","OTHER=unrelated"]}}])
        ]):
            key, origin, scanned = audit.inspect_key()
        self.assertEqual(key, "secret-test-key")
        self.assertEqual(scanned, 1)
        self.assertNotIn(key, origin)

if __name__ == "__main__":
    unittest.main()
