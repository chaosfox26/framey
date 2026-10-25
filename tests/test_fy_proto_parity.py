import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fy_proto


class NumericBoundaryTests(unittest.TestCase):
    def test_decode_rejects_exponents_that_overflow_to_infinity(self):
        for number in (b"1e309", b"-1e309", b"1E9999", b"-1.8e308"):
            for template in (b'{"value":%s}', b'{"arg":{"items":[0,%s]}}'):
                wire = template % number
                with self.subTest(wire=wire):
                    with self.assertRaises(fy_proto.ProtocolError):
                        fy_proto.decode(wire)

    def test_shared_json_loader_rejects_exponent_overflow(self):
        for wire in ('1e309', '{"values":[-1e309]}', b'{"value":1E9999}'):
            with self.subTest(wire=wire):
                with self.assertRaises(ValueError):
                    fy_proto.loads(wire)

    def test_finite_extremes_and_number_strings_remain_valid(self):
        obj = fy_proto.decode(
            b'{"largest":1.7976931348623157e308,"smallest":5e-324,'
            b'"zero":1e-9999,"negative_zero":-1e-9999,'
            b'"text":"1e309","flag":true}'
        )
        self.assertTrue(math.isfinite(obj["largest"]))
        self.assertGreater(obj["smallest"], 0)
        self.assertEqual(obj["zero"], 0.0)
        self.assertEqual(math.copysign(1, obj["negative_zero"]), -1)
        self.assertEqual(obj["text"], "1e309")
        self.assertIs(obj["flag"], True)
        self.assertEqual(fy_proto.decode(fy_proto.encode(obj)), obj)


class PayloadBoundaryTests(unittest.TestCase):
    def test_event_limit_counts_escaped_unicode_bytes(self):
        count = (fy_proto.EVENT_MAX - 2) // 12
        payload = "\U0001f600" * count
        self.assertTrue(fy_proto.event_ok("progress", payload))
        self.assertFalse(fy_proto.event_ok("progress", payload + "\U0001f600"))
        self.assertLess(len(payload + "\U0001f600"), fy_proto.EVENT_MAX)

    def test_multiple_json_records_cannot_decode_as_one_message(self):
        for wire in (b'{"op":"ping"}\n{"op":"call"}\n', b'{"a":1} {"b":2}'):
            with self.subTest(wire=wire):
                with self.assertRaises(fy_proto.ProtocolError):
                    fy_proto.decode(wire)


if __name__ == "__main__":
    unittest.main()
