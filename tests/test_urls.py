import unittest

from wta.urls import normalize_url


class NormalizeUrlTests(unittest.TestCase):
    def test_full_urls_are_kept(self):
        self.assertEqual(normalize_url("https://example.com/a?b=1"), "https://example.com/a?b=1")
        self.assertEqual(normalize_url("http://example.com"), "http://example.com")

    def test_bare_host_gets_https(self):
        self.assertEqual(normalize_url("example.com"), "https://example.com")
        self.assertEqual(normalize_url("  example.com/login  "), "https://example.com/login")

    def test_local_hosts_get_http(self):
        self.assertEqual(normalize_url("localhost:3000"), "http://localhost:3000")
        self.assertEqual(normalize_url("127.0.0.1/x"), "http://127.0.0.1/x")
        self.assertEqual(normalize_url("[::1]:8080"), "http://[::1]:8080")
        self.assertEqual(normalize_url("app.localhost"), "http://app.localhost")
        self.assertEqual(normalize_url("shop.test"), "http://shop.test")

    def test_rejects_other_schemes(self):
        for text in ("file:///C:/x.html", "javascript://alert(1)", "ftp://example.com"):
            self.assertIsNone(normalize_url(text), text)

    def test_rejects_empty_and_spaces(self):
        self.assertIsNone(normalize_url(""))
        self.assertIsNone(normalize_url("   "))
        self.assertIsNone(normalize_url("not a url"))


if __name__ == "__main__":
    unittest.main()
