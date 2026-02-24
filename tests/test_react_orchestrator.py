import unittest
from unittest.mock import MagicMock, patch
from agent.core.react_orchestrator import ReActOrchestrator
from agent.state import TaskIntent

class TestReActOrchestrator(unittest.TestCase):
    def setUp(self):
        self.mock_provider = MagicMock()
        self.mock_executor = MagicMock()
        self.mock_executor._repo_path = "/mock/repo"
        self.orchestrator = ReActOrchestrator(self.mock_provider, self.mock_executor)

    def test_orchestrate_finish_immediately(self):
        """Test that the loop finishes if the LLM says so."""
        # Mock LLM response for decide_step
        self.mock_provider.complete_with_tools.return_value = MagicMock(
            function_name="decide_step",
            arguments={
                "thought": "Task is already done.",
                "action": "finish",
                "action_input": {}
            }
        )
        
        success = self.orchestrator.orchestrate("Do nothing", "develop")
        self.assertTrue(success)
        self.mock_provider.complete_with_tools.assert_called_once()

    def test_orchestrate_multiple_steps(self):
        """Test a two-step loop: read_file -> finish."""
        # 1st call: read_file
        res1 = MagicMock(
            function_name="decide_step",
            arguments={
                "thought": "I need to read the file.",
                "action": "read_file",
                "action_input": {"path": "test.txt"}
            }
        )
        # 2nd call: finish
        res2 = MagicMock(
            function_name="decide_step",
            arguments={
                "thought": "File looks good.",
                "action": "finish",
                "action_input": {}
            }
        )
        
        self.mock_provider.complete_with_tools.side_effect = [res1, res2]
        
        # Mock read_file behavior (since _execute_action uses open())
        with patch("builtins.open", unittest.mock.mock_open(read_data="hello")):
            with patch("os.path.exists", return_value=True):
                success = self.orchestrator.orchestrate("Read test.txt", "develop")
        
        self.assertTrue(success)
        self.assertEqual(len(self.orchestrator._history), 1) # Only records history for non-finish steps? 
        # Actually my implementation says:
        # self._history.append(step) is at the END of the loop, but if it returns True it might skip.
        # Let's check orchestrate logic.

if __name__ == "__main__":
    unittest.main()
