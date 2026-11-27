"""WordPress mass editing as a library: the REST client, one post type's endpoint, and the config they take.

The CLI (`wp-bulk`) is one caller; a script is another:

    from rb_wordpress_bulk_editor import PostsEndpoint, WordPressClient, WordPressConfig

    async with WordPressClient(WordPressConfig(url=..., username=..., password=...)) as client:
        await PostsEndpoint(client).update(123, {"title": "..."})
"""

from __future__ import annotations

from .config import WordPressConfig
from .wordpress.client import WordPressClient
from .wordpress.posts import PostsEndpoint

__all__ = ["PostsEndpoint", "WordPressClient", "WordPressConfig"]
