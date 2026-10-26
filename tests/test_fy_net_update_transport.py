import ast
import socket
import time
import types
import unittest
import urllib.request
from pathlib import Path
from unittest import mock


class PinnedUpdateTransportSocketLifetime(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[1] / "framey.py"
        source = ast.parse(path.read_text())
        self.namespace = {"time": time, "socket": socket, "urllib": urllib, "PINS": {}, "FETCH_S": 2}
        for name in ("pinned_https", "fetch"):
            node = next(node for node in source.body if isinstance(node, ast.FunctionDef) and node.name == name)
            exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), self.namespace)

    def fetch(self, response, **kwargs):
        client, server = socket.socketpair()
        self.addCleanup(client.close)
        self.addCleanup(server.close)
        server.sendall(response)
        server.shutdown(socket.SHUT_WR)
        addresses = ["93.184.216.34"]
        connect = mock.Mock(return_value=client)
        network = types.SimpleNamespace(_resolve=mock.Mock(return_value=addresses), _connect=connect)
        self.namespace["use"] = lambda name: network
        with mock.patch.object(urllib.request, "getproxies", return_value={}):
            result = self.namespace["fetch"]("https://metadata.example/releases/latest", **kwargs)
        self.assertEqual(connect.call_args.args[:4], ("https", "metadata.example", 443, addresses))
        self.assertEqual(client.fileno(), -1)
        return result

    def test_content_length_completion_does_not_reuse_the_closed_raw_socket(self):
        self.assertEqual(self.fetch(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}"), b"{}")

    def test_empty_content_length_response_completes(self):
        self.assertEqual(self.fetch(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"), b"")

    def test_content_length_completion_preserves_progress_and_the_size_limit(self):
        payload = b"x" * 65537
        response = b"HTTP/1.1 200 OK\r\nContent-Length: 65537\r\nConnection: close\r\n\r\n" + payload
        progress = []
        self.assertEqual(self.fetch(response, limit=len(payload), progress=lambda n, total: progress.append((n, total))), payload)
        self.assertEqual(progress[-1], (len(payload), len(payload)))
        self.assertGreaterEqual(len(progress), 2)
        with self.assertRaisesRegex(ValueError, "download too large"):
            self.fetch(response, limit=len(payload) - 1)

    def test_declared_content_length_that_closes_early_is_refused(self):
        response = b"HTTP/1.1 200 OK\r\nContent-Length: 10\r\nConnection: close\r\n\r\nabc"
        with self.assertRaisesRegex(ValueError, "download incomplete"):
            self.fetch(response)

    def test_expired_total_deadline_is_still_refused(self):
        values = iter((0, 0, 3))
        self.namespace["time"] = types.SimpleNamespace(monotonic=lambda: next(values))
        response = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}"
        with self.assertRaisesRegex(TimeoutError, "download too slow"):
            self.fetch(response)

    def test_close_delimited_response_completes(self):
        self.assertEqual(self.fetch(b"HTTP/1.1 200 OK\r\nConnection: close\r\n\r\nhello"), b"hello")

    def test_chunked_response_completes(self):
        response = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n5\r\nhello\r\n0\r\n\r\n"
        self.assertEqual(self.fetch(response), b"hello")
