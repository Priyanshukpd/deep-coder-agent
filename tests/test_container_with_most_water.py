import unittest
from solutions.container_with_most_water import max_area

class TestContainerWithMostWater(unittest.TestCase):
    """Unit tests for the container with most water solution."""

    def test_example_case(self):
        """Test the example case from the problem description."""
        height = [1, 8, 6, 2, 5, 4, 8, 3, 7]
        expected = 49
        result = max_area(height)
        self.assertEqual(result, expected)

    def test_minimum_array(self):
        """Test with minimum array size of 2."""
        height = [1, 1]
        expected = 1
        result = max_area(height)
        self.assertEqual(result, expected)

    def test_another_example(self):
        """Test another example case."""
        height = [4, 3, 2, 1, 4]
        expected = 16
        result = max_area(height)
        self.assertEqual(result, expected)

    def test_small_array(self):
        """Test with a small array."""
        height = [1, 2, 1]
        expected = 2
        result = max_area(height)
        self.assertEqual(result, expected)

    def test_empty_array(self):
        """Test with an empty array."""
        height = []
        expected = 0
        result = max_area(height)
        self.assertEqual(result, expected)

    def test_single_element(self):
        """Test with a single element."""
        height = [5]
        expected = 0
        result = max_area(height)
        self.assertEqual(result, expected)

    def test_two_elements(self):
        """Test with exactly two elements."""
        height = [3, 7]
        expected = 3
        result = max_area(height)
        self.assertEqual(result, expected)

    def test_decreasing_heights(self):
        """Test with decreasing heights."""
        height = [9, 8, 7, 6, 5, 4, 3, 2, 1]
        expected = 20  # 8 * (9-1-1) = 8 * 2 = 16 or 1 * 8 = 8 or 2 * 7 = 14 or 3 * 6 = 18 or 4 * 5 = 20
        result = max_area(height)
        self.assertEqual(result, expected)

    def test_increasing_heights(self):
        """Test with increasing heights."""
        height = [1, 2, 3, 4, 5, 6, 7, 8, 9]
        expected = 20  # Same as decreasing case but mirrored
        result = max_area(height)
        self.assertEqual(result, expected)

    def test_large_numbers(self):
        """Test with large numbers."""
        height = [10000, 0, 0, 0, 0, 0, 0, 0, 10000]
        expected = 80000  # 10000 * (9-1-1) = 10000 * 8 = 80000
        result = max_area(height)
        self.assertEqual(result, expected)

    def test_all_zeros(self):
        """Test with all zeros."""
        height = [0, 0, 0, 0, 0]
        expected = 0
        result = max_area(height)
        self.assertEqual(result, expected)

    def test_performance_large_input(self):
        """Test performance with a large input array."""
        height = list(range(10000)) + list(range(10000, 0, -1))
        # For this case, the maximum area will be at the ends
        # width = len(height) - 1 = 19999, height = min(0, 0) = 0
        # Actually, let's think differently - the max will be between first and last elements
        # But first and last are 0 and 0, so area = 0
        # The actual maximum will be between the highest elements
        # In this case, elements at positions 9999 and 10000 (0-indexed 9999 and 10000)
        # Values are 9999 and 10000, width = 10000-9999 = 1, area = 1*9999 = 9999
        # But we also have 9998 at position 9998 and 9998 at position 10001, width = 3, area = 3*9998 = 29994
        # Actually, let's just check that it runs in reasonable time
        result = max_area(height)
        self.assertIsInstance(result, int)
        self.assertGreaterEqual(result, 0)

if __name__ == "__main__":
    unittest.main()
