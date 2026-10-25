import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fy_proto


class FramingTests(unittest.TestCase):
    def test_encode_is_compact_one_line(self):
        data = fy_proto.encode({"op": "ping", "id": 1, "text": "a\nb"})
        self.assertEqual(data.count(b"\n"), 1)
        self.assertTrue(data.endswith(b"\n"))
        self.assertNotIn(b" ", data)
        self.assertEqual(fy_proto.decode(data), {"op": "ping", "id": 1, "text": "a\nb"})

    def test_non_ascii_round_trip(self):
        obj = {"s": "caf\u00e9 \u4e2d \U0001f600"}
        self.assertEqual(fy_proto.decode(fy_proto.encode(obj)), obj)

    def test_encode_rejects_unserialisable(self):
        for bad in ({"x": object()}, {"x": float("nan")}, {"x": {1, 2}}):
            with self.assertRaises(fy_proto.ProtocolError):
                fy_proto.encode(bad)
        loop = {}
        loop["self"] = loop
        with self.assertRaises(fy_proto.ProtocolError):
            fy_proto.encode(loop)

    def test_encode_line_cap(self):
        fy_proto.encode({"s": "x" * (fy_proto.LINE_MAX - 100)})
        with self.assertRaises(fy_proto.ProtocolError):
            fy_proto.encode({"s": "x" * fy_proto.LINE_MAX})

    def test_decode_rejects_bad_lines(self):
        for bad in (b"", b"\n", b"not json\n", b"[1,2]\n", b'"s"\n', b"1\n", b"null\n", b'{"a":\n', b"\xff\xfe\n"):
            with self.assertRaises(fy_proto.ProtocolError, msg=repr(bad)):
                fy_proto.decode(bad)

    def test_decode_rejects_non_finite_constants(self):
        for bad in (b'{"a":NaN}', b'{"a":Infinity}', b'{"a":-Infinity}', b'{"a":[1,NaN]}', b'{"op":"call","arg":{"x":{"y":Infinity}}}'):
            with self.assertRaises(fy_proto.ProtocolError, msg=repr(bad)):
                fy_proto.decode(bad)
        self.assertEqual(fy_proto.decode(b'{"a":1e308,"b":-0.0}'), {"a": 1e308, "b": -0.0})

    def test_decode_line_cap(self):
        big = b'{"s":"' + b"x" * fy_proto.LINE_MAX + b'"}\n'
        with self.assertRaises(fy_proto.ProtocolError):
            fy_proto.decode(big)

    def test_decode_accepts_crlf_and_missing_newline(self):
        self.assertEqual(fy_proto.decode(b'{"a":1}\r\n'), {"a": 1})
        self.assertEqual(fy_proto.decode(b'{"a":1}'), {"a": 1})

    def test_decode_deep_nesting_is_a_protocol_error(self):
        with self.assertRaises(fy_proto.ProtocolError):
            fy_proto.decode(b"[" * 100000 + b"]" * 100000 + b"\n")

    def test_caps_are_the_contract_values(self):
        self.assertEqual(fy_proto.LINE_MAX, 1048576)
        self.assertEqual(fy_proto.PAYLOAD_MAX, 786432)
        self.assertEqual(fy_proto.EVENT_MAX, 16384)


class ErrorTests(unittest.TestCase):
    def test_message_truncated_and_shape(self):
        e = fy_proto.error("exception", "m" * 1000, stage="startup", trace="t" * 2000)
        self.assertEqual(e["code"], "exception")
        self.assertEqual(len(e["message"]), 300)
        self.assertEqual(e["stage"], "startup")
        self.assertEqual(len(e["trace"]), 600)

    def test_non_string_message(self):
        self.assertEqual(fy_proto.error("x", 5)["message"], "5")


class EventTests(unittest.TestCase):
    def test_names(self):
        for good in ("a", "progress", "a.b-c_d", "x" * 40, "0"):
            self.assertTrue(fy_proto.event_ok(good, None), good)
        for bad in ("", "x" * 41, "Upper", "a b", "a\n", "a/b", "caf\u00e9", None, 5, b"a"):
            self.assertFalse(fy_proto.event_ok(bad, None), repr(bad))

    def test_name_regex_is_anchored(self):
        self.assertTrue(fy_proto.EVENT_NAME.fullmatch("ok"))
        self.assertFalse(fy_proto.EVENT_NAME.fullmatch("ok\n"))
        self.assertIsNone(fy_proto.EVENT_NAME.match("ok\n"))

    def test_payload_cap(self):
        self.assertTrue(fy_proto.event_ok("e", {"s": "x" * 1000}))
        self.assertFalse(fy_proto.event_ok("e", "x" * fy_proto.EVENT_MAX))
        self.assertTrue(fy_proto.event_ok("e", "x" * (fy_proto.EVENT_MAX - 2)))

    def test_payload_must_serialise(self):
        self.assertFalse(fy_proto.event_ok("e", object()))
        self.assertFalse(fy_proto.event_ok("e", float("inf")))


class SourceTests(unittest.TestCase):
    def test_no_comments_polling_or_third_party_names(self):
        root = Path(__file__).resolve().parents[1]
        for name in ("fy_proto.py", "fy_worker.py", "fy_supervisor.py"):
            text = (root / name).read_text()
            self.assertNotIn("asyncio.sleep", text, name)
            self.assertNotIn("while True: await", text, name)
            for line in text.splitlines():
                self.assertFalse(line.lstrip().startswith("#"), (name, line))
            self.assertNotIn('"""', text, name)


class StrictJsonTests(unittest.TestCase):
    def test_nan_and_infinity_are_refused_on_every_json_path(self):
        for bad in (b'{"a":NaN}', b'{"a":Infinity}', b'{"a":-Infinity}'):
            with self.assertRaises(fy_proto.ProtocolError):
                fy_proto.decode(bad)
            with self.assertRaises(ValueError):
                fy_proto.loads(bad)


if __name__ == "__main__":
    unittest.main()
