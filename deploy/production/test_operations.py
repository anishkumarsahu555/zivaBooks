"""Failure-path tests for host operations; no Docker or production data needed."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

spec = importlib.util.spec_from_file_location("operations", Path(__file__).with_name("operations.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class BackupSafetyTests(unittest.TestCase):
    def operation(self):
        ops = module.Operations.__new__(module.Operations)
        ops.running_writers = Mock(return_value=["web", "worker"])
        ops.stop = Mock()
        ops.start = Mock()
        ops.snapshot = Mock()
        ops.offsite = Mock()
        ops.remember_writers = Mock()
        ops.clear_writers = Mock()
        return ops

    def test_backup_failure_resumes_only_previous_writers(self):
        ops = self.operation()
        ops.running_writers.return_value = ["web"]
        ops.snapshot.side_effect = RuntimeError("disk full")
        with self.assertRaises(RuntimeError):
            ops.backup()
        ops.start.assert_called_once_with(["web"])
        ops.offsite.assert_not_called()

    def test_offsite_failure_does_not_leave_app_stopped(self):
        ops = self.operation()
        ops.offsite.side_effect = RuntimeError("network failure")
        with self.assertRaises(RuntimeError):
            ops.backup()
        ops.start.assert_called_once_with(["web", "worker"])

    def test_upload_occurs_after_resume(self):
        ops = self.operation()
        order = Mock()
        for name in ("stop", "snapshot", "start", "offsite"):
            order.attach_mock(getattr(ops, name), name)
        ops.backup()
        self.assertEqual([c[0] for c in order.mock_calls], ["stop", "snapshot", "start", "offsite"])

    def test_stopped_services_stay_stopped(self):
        ops = self.operation()
        ops.running_writers.return_value = []
        ops.backup()
        ops.start.assert_called_once_with([])

    def test_start_empty_does_not_call_compose_start_all(self):
        ops = module.Operations.__new__(module.Operations)
        ops.dc = Mock()
        ops.start([])
        ops.dc.assert_not_called()

    def test_interrupted_backup_recovers_recorded_services(self):
        with tempfile.TemporaryDirectory() as directory:
            ops = module.Operations.__new__(module.Operations)
            ops.root = Path(directory)
            ops.start = Mock()
            ops.remember_writers(["web"])
            ops.resume_backup()
            ops.start.assert_called_once_with(["web"])
            self.assertFalse((ops.root / "backup-paused.json").exists())

    def test_failed_recovery_preserves_marker_for_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            ops = module.Operations.__new__(module.Operations)
            ops.root = Path(directory)
            ops.start = Mock(side_effect=RuntimeError("Docker unavailable"))
            ops.remember_writers(["web", "worker"])
            with self.assertRaises(RuntimeError):
                ops.resume_backup()
            self.assertTrue((ops.root / "backup-paused.json").exists())


if __name__ == "__main__":
    unittest.main()
