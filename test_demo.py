import pytest
from demo_server import app

@pytest.fixture
def client():
    """Create a test client for the Flask app."""
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client

def test_home_route_returns_blue_button(client):
    """Test that the root route returns a blue Submit button."""
    # When: Making a GET request to the root route
    response = client.get('/')
    
    # Then: The response should be successful
    assert response.status_code == 200
    
    # Then: The response should contain the expected button HTML
    expected_button = "<button style='background:blue; color:white;'>Submit</button>"
    assert expected_button in response.get_data(as_text=True)

def test_home_route_content_type(client):
    """Test that the root route returns HTML content."""
    # When: Making a GET request to the root route
    response = client.get('/')
    
    # Then: The response should have HTML content type
    assert 'text/html' in response.content_type

def test_app_exists():
    """Test that the Flask app is created successfully."""
    assert app is not None

def test_app_is_in_testing_mode():
    """Test that the app is configured for testing."""
    assert app.config['TESTING'] is True
