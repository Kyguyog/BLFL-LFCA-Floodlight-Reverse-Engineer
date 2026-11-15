import unittest

from brmesh import FastConBuilder, command_power, command_rgb, command_white, raw_advertisement


class FastConVectors(unittest.TestCase):
    def setUp(self):
        self.key = bytes.fromhex("32323035")
        self.builder = FastConBuilder(0x42)

    def test_on(self):
        frame = self.builder.light(3, self.key, bytes((0x80 | 100,)))
        self.assertEqual(frame.hex(), "6db64368931ddd46962f8839f0fe09ce4196c66758fa67fd")

    def test_scan(self):
        self.builder.sequence = 0x40
        frame = self.builder.scan()
        self.assertEqual(frame.hex(), "6db64368931d0d445c3ec63e5f0f65ca0a67aa63130b71dd")

    def test_rgb(self):
        self.builder.sequence = 0x43
        frame = self.builder.light(3, self.key, command_rgb(255, 40, 10, 50.4))
        self.assertEqual(frame.hex(), "6db64368931ddd419602d839d4f6dcef4196c66758fa7639")

    def test_white(self):
        self.builder.sequence = 0x44
        frame = self.builder.light(200, self.key, command_white(127, 127, 100))
        self.assertEqual(frame.hex(), "6db64368931ddd4096ffd8f2ebfe09ce3ee9c66758fa83ad")

    def test_off(self):
        self.builder.sequence = 0x45
        frame = self.builder.light(1, self.key, command_power(False))
        self.assertEqual(frame.hex(), "6db64368931ddd439610883b14fe09ce4196c66758fa2903")

    def test_zero_color_is_off(self):
        self.assertEqual(command_rgb(0, 0, 0), b"\0")

    def test_zero_brightness_is_off(self):
        self.assertEqual(command_power(True, 0), b"\0")

    def test_raw_advertisement_order(self):
        frame = bytes(range(24))
        packet = raw_advertisement(frame)
        self.assertEqual(len(packet), 31)
        self.assertEqual(packet[:7].hex(), "0201021bfff0ff")
        self.assertEqual(packet[7:], frame)

    def test_legacy_raw_advertisement_flags(self):
        packet = raw_advertisement(bytes(24), 0x1A)
        self.assertEqual(packet[:7].hex(), "02011a1bfff0ff")


if __name__ == "__main__":
    unittest.main()
