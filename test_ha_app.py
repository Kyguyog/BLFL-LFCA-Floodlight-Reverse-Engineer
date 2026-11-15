import importlib.util
import pathlib
import subprocess
import sys
import unittest
from unittest.mock import patch


MODULE_PATH = pathlib.Path(__file__).parent / "brmesh_controller" / "brmesh_protocol.py"
SPEC = importlib.util.spec_from_file_location("addon_brmesh_protocol", MODULE_PATH)
protocol = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = protocol
SPEC.loader.exec_module(protocol)


class HomeAssistantProtocolTests(unittest.TestCase):
    def test_addon_encoder_matches_proven_vector(self):
        builder = protocol.FastConBuilder(0x45)
        frame = builder.light(1, bytes.fromhex("32323035"), protocol.command_power(False))
        self.assertEqual(frame.hex(), "6db64368931ddd439610883b14fe09ce4196c66758fa2903")

    def test_addon_raw_advertisement_layout(self):
        packet = protocol.raw_advertisement(bytes(range(24)))
        self.assertEqual(len(packet), 31)
        self.assertEqual(packet[:7].hex(), "0201021bfff0ff")

    def test_key_validation(self):
        self.assertEqual(protocol.parse_key("12:34:56:78"), bytes.fromhex("12345678"))
        with self.assertRaises(ValueError):
            protocol.parse_key("1234")

    @patch.object(protocol.time, "sleep")
    @patch.object(protocol.subprocess, "run")
    def test_advertiser_does_not_remove_before_add(self, run, sleep):
        run.return_value = subprocess.CompletedProcess([], 0, "", "")
        advertiser = protocol.BtmgmtAdvertiser(1.0, 0)
        advertiser.send(bytes(24))

        commands = [item.args[0] for item in run.call_args_list]
        self.assertEqual(commands[0][3], "add-ext-adv-params")
        self.assertEqual(commands[1][3], "add-ext-adv-data")
        self.assertEqual(commands[2][3:], ["rm-adv", "1"])


if __name__ == "__main__":
    unittest.main()
