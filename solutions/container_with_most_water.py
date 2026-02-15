def max_area(height):
    """
    Find the maximum area of water that can be stored between two lines.
    
    Uses the two-pointer technique to solve the container with most water problem
    in O(n) time complexity.
    
    Args:
        height (List[int]): List of heights representing vertical lines
        
    Returns:
        int: Maximum area of water that can be stored
        
    Examples:
        >>> max_area([1,8,6,2,5,4,8,3,7])
        49
        >>> max_area([1,1])
        1
        >>> max_area([4,3,2,1,4])
        16
        >>> max_area([1,2,1])
        2
    """
    # Handle edge cases
    if not height or len(height) < 2:
        return 0
    
    left = 0  # Left pointer starts at beginning
    right = len(height) - 1  # Right pointer starts at end
    max_water = 0  # Track maximum water area found
    
    # Continue until pointers meet
    while left < right:
        # Calculate width between pointers
        width = right - left
        
        # Calculate height (limited by shorter line)
        container_height = min(height[left], height[right])
        
        # Calculate current area
        current_area = width * container_height
        
        # Update maximum if current area is larger
        max_water = max(max_water, current_area)
        
        # Move pointer pointing to shorter line inward
        # This is the key optimization - moving the taller line
        # inward can only decrease the area since width decreases
        if height[left] < height[right]:
            left += 1
        else:
            right -= 1
    
    return max_water


# Test the function with example cases
if __name__ == "__main__":
    # Test case 1: Example from problem description
    test1 = [1, 8, 6, 2, 5, 4, 8, 3, 7]
    result1 = max_area(test1)
    print(f"Input: {test1}")
    print(f"Output: {result1}")
    print(f"Expected: 49\n")
    
    # Test case 2: Minimum array size
    test2 = [1, 1]
    result2 = max_area(test2)
    print(f"Input: {test2}")
    print(f"Output: {result2}")
    print(f"Expected: 1\n")
    
    # Test case 3: Another example
    test3 = [4, 3, 2, 1, 4]
    result3 = max_area(test3)
    print(f"Input: {test3}")
    print(f"Output: {result3}")
    print(f"Expected: 16\n")
    
    # Test case 4: Small array
    test4 = [1, 2, 1]
    result4 = max_area(test4)
    print(f"Input: {test4}")
    print(f"Output: {result4}")
    print(f"Expected: 2\n")
