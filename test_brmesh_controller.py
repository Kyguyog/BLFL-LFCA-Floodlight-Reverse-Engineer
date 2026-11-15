import struct
import unittest
from unittest.mock import patch

from brmesh_controller.brmesh_protocol import HciAdvertiser


class FakeHciSocket:
    def __init__(self):
        self.commands = []
        self.last_opcode = None
        self.closed = False
        self.filters = []

    def setsockopt(self, level, option, value):
        self.filters.append((level, option, value))

    def sendall(self, packet):
        self.last_opcode = struct.unpack_from("<H", packet, 1)[0]
        self.commands.append(packet)

    def recv(self, size):
        return b"\x04\x0e\x04\x01" + struct.pack("<H", self.last_opcode) + b"\x00"

    def close(self):
        self.closed = True


class HciAdvertiserTests(unittest.TestCase):
    @patch("brmesh_controller.brmesh_protocol.time.sleep")
    def test_exact_legacy_advertisement(self, sleep):
        radio = FakeHciSocket()
        advertiser = HciAdvertiser(1.0, 0)
        advertiser._open = lambda: radio

        advertiser.send(bytes(range(24)))

        opcodes = [struct.unpack_from("<H", packet, 1)[0] for packet in radio.commands]
        self.assertEqual(opcodes, [0x200A, 0x2006, 0x2008, 0x200A, 0x200A])
        parameters = radio.commands[1][4:]
        self.assertEqual(parameters[:5], b"\x20\x00\x30\x00\x03")
        data = radio.commands[2][4:]
        self.assertEqual(data[0], 31)
        self.assertEqual(data[1:8].hex(), "0201021bfff0ff")
        self.assertEqual(data[8:], bytes(range(24)))
        self.assertEqual(radio.commands[3][4:], b"\x01")
        self.assertEqual(radio.commands[4][4:], b"\x00")
        self.assertTrue(all(len(value) == 16 for _, _, value in radio.filters))
        self.assertTrue(radio.closed)
        sleep.assert_called_once_with(1.0)


if __name__ == "__main__":
    unittest.main()
