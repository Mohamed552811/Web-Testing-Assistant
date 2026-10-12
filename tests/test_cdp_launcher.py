import json
import os
import socket
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path, PurePosixPath
from unittest import mock

from wta import cdp, claude_launcher


class _VersionHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/json/version":
            self.send_error(404)
            return
        body = json.dumps({"Browser": "test", "User-Agent": "QtWebEngine/6.10 Chrome/140"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class CdpTests(unittest.TestCase):
    def test_free_port_is_bindable_on_localhost(self):
        port = cdp.find_free_port()
        with socket.socket() as sock:
            sock.bind((cdp.HOST, port))

    def test_enable_sets_env_and_narrows_origins(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            args = cdp.enable(9444)
            self.assertEqual(os.environ[cdp.ENV_VAR], "127.0.0.1:9444")
        self.assertEqual(args, ["--webEngineArgs", "--remote-allow-origins=http://127.0.0.1:9444"])

    def test_endpoint_info(self):
        server = HTTPServer((cdp.HOST, 0), _VersionHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            info = cdp.endpoint_info(server.server_address[1])
            self.assertEqual(info["Browser"], "test")
        finally:
            server.shutdown()
            server.server_close()
        self.assertIsNone(cdp.endpoint_info(cdp.find_free_port(), timeout=0.3))


class LauncherTests(unittest.TestCase):
    def test_mcp_config_attaches_to_cdp(self):
        config = claude_launcher.playwright_mcp_config(9555, Path("C:/out dir"))
        server = config["mcpServers"]["playwright"]
        self.assertEqual(server["command"], "npx")
        args = server["args"]
        self.assertIn(claude_launcher.PLAYWRIGHT_MCP_PACKAGE, args)
        self.assertRegex(claude_launcher.PLAYWRIGHT_MCP_PACKAGE, r"@\d+\.\d+\.\d+$")  # pinned
        self.assertEqual(args[args.index("--cdp-endpoint") + 1], "http://127.0.0.1:9555")
        self.assertEqual(args[args.index("--output-dir") + 1], "C:/out dir")

    def test_write_mcp_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = claude_launcher.write_mcp_config(Path(tmp) / "mcp" / "c.json", 9555, Path(tmp) / "out")
            self.assertEqual(json.loads(path.read_text("utf-8")), claude_launcher.playwright_mcp_config(9555, Path(tmp) / "out"))
            self.assertTrue((Path(tmp) / "out").is_dir())
            self.assertEqual([p.name for p in path.parent.iterdir()], ["c.json"])  # no temp file left

    def test_manual_command_limits_tools(self):
        args = claude_launcher.manual_command(Path("cfg.json"), "do it")
        self.assertEqual(args[:3], ["claude", "-p", "do it"])
        self.assertIn("--strict-mcp-config", args)
        self.assertIn("--tools=", args)  # no built-in tools at all
        self.assertEqual(args[args.index("--allowedTools") + 1], "mcp__playwright")
        self.assertEqual(args[args.index("--permission-mode") + 1], "dontAsk")
        self.assertFalse(any("ANTHROPIC_API_KEY" in a for a in args))

    def test_format_for_shell(self):
        args = ["claude", "-p", "it's here", "--tools=", "--mcp-config", "C:\\a b\\c.json"]
        self.assertEqual(
            claude_launcher.format_for_shell(args, windows=True),
            "claude -p 'it''s here' --tools= --mcp-config 'C:\\a b\\c.json'",
        )
        self.assertEqual(
            claude_launcher.format_for_shell(["claude", "-p", "x y"], cwd=PurePosixPath("/tmp/o d"), windows=False),
            "cd '/tmp/o d' && claude -p 'x y'",
        )
        self.assertTrue(
            claude_launcher.format_for_shell(["claude"], cwd=Path("C:/o d"), windows=True).startswith("Set-Location '")
        )

    def test_launcher_never_mentions_api_key_env(self):
        source = Path(claude_launcher.__file__).read_text("utf-8")
        self.assertNotIn("os.environ[\"ANTHROPIC_API_KEY\"]", source)
        self.assertNotIn("getenv(\"ANTHROPIC_API_KEY\")", source)


if __name__ == "__main__":
    unittest.main()
