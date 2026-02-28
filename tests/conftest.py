"""
Shared fixtures for WordPress Bulk Editor tests.
"""

import pytest
from pathlib import Path


@pytest.fixture
def tmp_media_folder(tmp_path):
    """Temporary media folder with sample files."""
    media = tmp_path / "media"
    media.mkdir()
    (media / "photo.jpg").write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")
    (media / "doc.pdf").write_bytes(b"%PDF-1.4 fake")
    return media


@pytest.fixture
def tmp_excel_file(tmp_path):
    """Temporary .xlsx file (empty but valid path)."""
    f = tmp_path / "data.xlsx"
    f.write_bytes(b"")
    return f


@pytest.fixture
def sample_wp_post_response():
    """Sample WordPress REST API post response."""
    return {
        "id": 42,
        "title": {"rendered": "Hello World"},
        "content": {"rendered": "<p>Body text</p>"},
        "excerpt": {"rendered": "Short excerpt"},
        "slug": "hello-world",
        "status": "publish",
        "link": "https://example.com/hello-world",
        "categories": [3, 5],
        "tags": [10],
        "featured_media": 99,
        "date": "2024-01-15T10:00:00",
        "modified": "2024-01-16T12:00:00",
    }


@pytest.fixture
def taxonomy_cache():
    """Sample taxonomy ID→name cache."""
    return {
        3: "News",
        5: "Tutorials",
        10: "python",
        11: "django",
    }
