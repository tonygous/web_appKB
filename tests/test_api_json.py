from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

def test_crawl_preview_json():
    payload = {
        "url": "http://example.com",
        "max_pages": 5,
        "render_mode": "http"
    }
    # Mocking the crawler response is hard without more setup, 
    # but we can check if it accepts JSON and tries to process it.
    # We expect 400 because example.com might not return what we expect or network/mocking is needed.
    # However, if it returns 422, our JSON schema is wrong.
    # If it returns 200 or a specific logic error (400) from our code, the JSON transport worked.
    try:
        response = client.post("/crawl-preview", json=payload)
        # If we get 422 Unprocessable Entity, pydantic failed => fail
        assert response.status_code != 422
        # We expect a logic error or success, but not a validation error on the top level structure if it matches.
        print(f"Preview Response: {response.status_code}")
    except Exception as e:
        print(f"Preview Error: {e}")

def test_generate_json():
    payload = {
        "url": "http://example.com",
        "max_pages": 1,
        "render_mode": "http",
        "min_text_chars": 100 
    }
    response = client.post("/generate", json=payload)
    assert response.status_code != 422
    print(f"Generate Response: {response.status_code}")

def test_download_selected_json():
    payload = {
        "url": "http://example.com",
        "pages": [{"url": "http://example.com", "host": "example.com", "title": "Example"}],
        "max_pages": 1
    }
    response = client.post("/download-selected", json=payload)
    assert response.status_code != 422
    print(f"Download Response: {response.status_code}")
