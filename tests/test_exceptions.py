"""
Tests for src/exceptions.py — inheritance chain validation.
"""

from rb_wordpress_bulk_editor.exceptions import (
    WPBulkEditorError,
    ConfigurationError,
    ExcelError,
    ExcelStructureError,
    ExcelValidationError,
    ProfileSheetMissingError,
    WordPressError,
    AuthenticationError,
    NotFoundError,
    ServerError,
    NetworkError,
    ValidationError,
    MediaUploadError,
)


class TestExceptionHierarchy:
    def test_all_inherit_from_base(self):
        subclasses = [
            ConfigurationError,
            ExcelError,
            ExcelStructureError,
            ExcelValidationError,
            ProfileSheetMissingError,
            WordPressError,
            AuthenticationError,
            NotFoundError,
            ServerError,
            NetworkError,
            ValidationError,
            MediaUploadError,
        ]
        for cls in subclasses:
            assert issubclass(cls, WPBulkEditorError), f"{cls.__name__} not subclass of WPBulkEditorError"

    def test_excel_subclasses(self):
        for cls in (ExcelStructureError, ExcelValidationError, ProfileSheetMissingError):
            assert issubclass(cls, ExcelError)

    def test_wordpress_subclasses(self):
        for cls in (AuthenticationError, NotFoundError, ServerError, NetworkError, MediaUploadError):
            assert issubclass(cls, WordPressError)

    def test_catch_base_catches_all(self):
        try:
            raise AuthenticationError("test")
        except WPBulkEditorError:
            pass  # should be caught

    def test_message_preserved(self):
        err = NetworkError("connection refused")
        assert str(err) == "connection refused"
