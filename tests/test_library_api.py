"""The package as a library: its three public names are importable from the root and are the real objects."""

from __future__ import annotations

import rb_wordpress_bulk_editor as library


def test_the_public_names_are_exported_and_are_the_real_objects() -> None:
    from rb_wordpress_bulk_editor.config import WordPressConfig
    from rb_wordpress_bulk_editor.wordpress.client import WordPressClient
    from rb_wordpress_bulk_editor.wordpress.posts import PostsEndpoint

    assert library.__all__ == ["PostsEndpoint", "WordPressClient", "WordPressConfig"]
    assert (library.WordPressClient, library.PostsEndpoint, library.WordPressConfig) == (
        WordPressClient,
        PostsEndpoint,
        WordPressConfig,
    )


def test_a_client_is_built_from_the_documented_example() -> None:
    config = library.WordPressConfig(url="https://site.example", username="me", password="app-password")
    client = library.WordPressClient(config)

    assert client.base_url.rstrip("/") == "https://site.example"
    assert library.PostsEndpoint(client).__class__ is library.PostsEndpoint
