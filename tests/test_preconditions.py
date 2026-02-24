import unittest
from unittest.mock import patch, MagicMock
import tempfile
import os
from agent.security.preconditions import PreconditionChecker, PreconditionViolation

class TestPreconditions(unittest.TestCase):

    @patch("agent.security.preconditions.subprocess.run")
    def test_git_head_capture(self, mock_run):
        # Setup mock
        mock_result = MagicMock()
        mock_result.stdout = "abcdef123456\n"
        mock_run.return_value = mock_result
        
        # Test
        head = PreconditionChecker.get_git_head()
        self.assertEqual(head, "abcdef123456")

    @patch("agent.security.preconditions.PreconditionChecker.get_git_head")
    def test_git_consistency_check(self, mock_get_head):
        # Scenario 1: Match
        mock_get_head.return_value = "hash1"
        violation = PreconditionChecker.check_git_consistency("hash1")
        self.assertIsNone(violation)
        
        # Scenario 2: Drift
        mock_get_head.return_value = "hash2"
        violation = PreconditionChecker.check_git_consistency("hash1")
        self.assertIsNotNone(violation)
        self.assertEqual(violation.check_type, "GIT_HEAD")
        self.assertIn("Drift detected", violation.details)

    def test_file_checksum_logic(self):
        # Create temp file
        with tempfile.NamedTemporaryFile(delete=False, mode="w") as f:
            f.write("Hello World")
            path = f.name
            
        try:
            # 1. Capture
            checksum1 = PreconditionChecker.get_file_checksum(path)
            self.assertIsNotNone(checksum1)
            
            # 2. Verify Match
            violations = PreconditionChecker.check_file_consistency({path: checksum1})
            self.assertEqual(len(violations), 0)
            
            # 3. Modify File (Drift)
            with open(path, "w") as f:
                f.write("Hello World CHANGED")
                
            # 4. Verify Mismatch
            violations = PreconditionChecker.check_file_consistency({path: checksum1})
            self.assertEqual(len(violations), 1)
            self.assertEqual(violations[0].check_type, "FILE_CHECKSUM")
            
            # 5. Delete File
            os.remove(path)
            violations = PreconditionChecker.check_file_consistency({path: checksum1})
            self.assertEqual(len(violations), 1)
            self.assertEqual(violations[0].check_type, "FILE_MISSING")
            
        finally:
            if os.path.exists(path):
                os.remove(path)

if __name__ == "__main__":
    unittest.main()
