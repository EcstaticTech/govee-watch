import unittest

from govee_watch.protocol import (
    PACKET_LENGTH,
    build_brightness_packet,
    build_color_packet,
    build_power_packet,
    parse_hex_color,
)


def _xor_valid(packet: bytes) -> bool:
    acc = 0
    for b in packet[:-1]:
        acc ^= b
    return (acc & 0xFF) == packet[-1]


class TestProtocol(unittest.TestCase):
    def test_power_packets(self) -> None:
        on_pkt = build_power_packet(True)
        off_pkt = build_power_packet(False)
        self.assertEqual(len(on_pkt), PACKET_LENGTH)
        self.assertEqual(len(off_pkt), PACKET_LENGTH)
        self.assertEqual(on_pkt[:3], bytes([0x33, 0x01, 0x01]))
        self.assertEqual(off_pkt[:3], bytes([0x33, 0x01, 0x00]))
        self.assertTrue(_xor_valid(on_pkt))
        self.assertTrue(_xor_valid(off_pkt))

    def test_brightness_packet_and_profile_override(self) -> None:
        full_pkt = build_brightness_packet(100)
        self.assertEqual(len(full_pkt), PACKET_LENGTH)
        self.assertEqual(full_pkt[:3], bytes([0x33, 0x04, 0xFF]))
        self.assertTrue(_xor_valid(full_pkt))

        scaled_pkt = build_brightness_packet(50, profile={"brightness_max": 100})
        self.assertEqual(scaled_pkt[:3], bytes([0x33, 0x04, 50]))
        self.assertTrue(_xor_valid(scaled_pkt))

        with self.assertRaises(ValueError):
            build_brightness_packet(101)

    def test_color_packet_and_hex_parsing(self) -> None:
        r, g, b = parse_hex_color("#FF8010")
        self.assertEqual((r, g, b), (255, 128, 16))
        pkt = build_color_packet(r, g, b)
        self.assertEqual(len(pkt), PACKET_LENGTH)
        self.assertEqual(pkt[:6], bytes([0x33, 0x05, 0x02, 255, 128, 16]))
        self.assertTrue(_xor_valid(pkt))

        custom = build_color_packet(1, 2, 3, profile={"color_mode": 0x0D})
        self.assertEqual(custom[:6], bytes([0x33, 0x05, 0x0D, 1, 2, 3]))
        self.assertTrue(_xor_valid(custom))

        with self.assertRaises(ValueError):
            parse_hex_color("GGGGGG")

        with self.assertRaises(ValueError):
            build_color_packet(256, 0, 0)


if __name__ == "__main__":
    unittest.main()
