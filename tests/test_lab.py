"""Tests for the boundary that distinguishes a lab from arbitrary processes."""
import json
import os
from pathlib import Path
import socket
import stat
import struct
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

from lsrp import lab


class LabBoundaryTests(unittest.TestCase):
    def test_permissions_reject_symlink_unowned_and_writable_targets(self):
        for mode, uid in [(stat.S_IFLNK | 0o777, 0), (stat.S_IFREG | 0o644, 1000),
                          (stat.S_IFREG | 0o666, 0), (stat.S_IFDIR | 0o755, 0)]:
            with self.subTest(mode=oct(mode), uid=uid):
                path = MagicMock()
                path.lstat.return_value = types.SimpleNamespace(st_mode=mode, st_uid=uid)
                with self.assertRaises(lab.LabError):
                    lab._trusted(path)

    def test_root_requirement_is_checked_before_reading_or_controlling_host(self):
        with patch.object(lab.platform, "system", return_value="Linux"), \
                patch.object(lab.os, "geteuid", return_value=1000, create=True), \
                patch.object(lab, "properties") as properties, patch.object(lab, "_trusted") as trusted:
            with self.assertRaisesRegex(lab.LabError, "requires root"):
                lab.guard()
        properties.assert_not_called()
        trusted.assert_not_called()

    def test_non_linux_mutation_never_reaches_service_manager(self):
        with patch.object(lab.platform, "system", return_value="Windows"), \
                patch.object(lab, "systemctl") as systemctl:
            with self.assertRaisesRegex(lab.LabError, "Linux"):
                lab.guard()
        systemctl.assert_not_called()

    def test_control_rejects_wrong_socket_process_or_uid_before_sending_fault(self):
        pwd_module = types.SimpleNamespace(getpwnam=lambda _: types.SimpleNamespace(pw_uid=991))
        for peer in [(200, 991, 991), (100, 992, 992), (100, 0, 0)]:
            with self.subTest(peer=peer), patch.dict(sys.modules, {"pwd": pwd_module}), \
                    patch.object(lab, "identity", return_value={"pid": 100}), \
                    patch.object(lab.socket, "AF_UNIX", 1, create=True), \
                    patch.object(lab.socket, "SO_PEERCRED", 17, create=True), \
                    patch.object(lab.socket, "socket") as factory:
                connection = factory.return_value.__enter__.return_value
                connection.getsockopt.return_value = struct.pack("3i", *peer)
                with self.assertRaisesRegex(lab.LabError, "peer"):
                    lab.control("hang", 4)
                connection.sendall.assert_not_called()

    def test_validated_socket_gets_only_bounded_json_protocol(self):
        pwd_module = types.SimpleNamespace(getpwnam=lambda _: types.SimpleNamespace(pw_uid=991))
        with patch.dict(sys.modules, {"pwd": pwd_module}), \
                patch.object(lab, "identity", return_value={"pid": 100}), \
                patch.object(lab.socket, "AF_UNIX", 1, create=True), \
                patch.object(lab.socket, "SO_PEERCRED", 17, create=True), \
                patch.object(lab.socket, "socket") as factory:
            connection = factory.return_value.__enter__.return_value
            connection.getsockopt.return_value = struct.pack("3i", 100, 991, 991)
            connection.recv.return_value = b'{"ok":true,"mode":"hang"}\n'
            result = lab.control("hang", 4)
            self.assertTrue(result["ok"])
            self.assertEqual(connection.connect.call_args.args, (lab.CONTROL,))
            self.assertEqual(json.loads(connection.sendall.call_args.args[0]), {"mode": "hang", "duration": 4})

    def test_cleanup_reports_lingering_helper_and_individual_stop_errors(self):
        def ctl(*args, **kwargs):
            if args == ("stop", lab.TIMER):
                raise lab.LabError("timer stop failed")
            return ""
        with patch.object(lab, "systemctl", side_effect=ctl), \
                patch.object(lab, "properties", side_effect=lambda unit: {
                    "ActiveState": "active" if unit == lab.COLLISION else "inactive"}):
            result = lab.cleanup(restart=False)
        self.assertFalse(result["ok"])
        self.assertTrue(any("timer stop failed" in text for text in result["errors"]))
        self.assertTrue(any(lab.COLLISION in text for text in result["errors"]))

    @unittest.skipUnless(sys.platform == "linux", "requires actual Linux flock")
    def test_exclusive_lock_rejects_overlapping_operations(self):
        real_open = os.open
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory) / "lock"
            def redirect(path, flags, mode=0o777):
                self.assertEqual(path, "/run/lsrp/lock")
                return real_open(lock, flags, mode)
            with patch.object(lab, "guard"), patch.object(lab, "_trusted"), \
                    patch.object(lab.os, "open", side_effect=redirect):
                with lab.locked():
                    with self.assertRaisesRegex(lab.LabError, "Another LSRP"):
                        with lab.locked():
                            self.fail("overlapping lock was acquired")
                # Lock release is observable even after rejected overlapping acquisition.
                with lab.locked():
                    pass


if __name__ == "__main__":
    unittest.main()
