# Changelog

All notable changes to WordPress Bulk Editor will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-01-10

### Added
- Initial release of WordPress Bulk Editor
- CLI interface with `sync`, `validate`, `fetch`, and `options` commands
- Excel to WordPress synchronization
- Support for posts, pages, and custom post types
- Support for categories, tags, and custom taxonomies
- ACF fields support with `acf_` prefix
- Media upload support (local files and URLs)
- SEO fields support (auto-passed to WordPress REST API)
- Smart taxonomy handling with auto-creation
- Asynchronous processing for performance
- Comprehensive logging with loguru
- JSON statistics output
- Dry-run mode for safe testing
- Write-back of IDs, dates, and links to Excel
- Application Password authentication
- Configurable concurrency for parallel requests
- Retry logic for failed requests
- Excel profile sheet for credentials
- Multiple sheet processing
- Field auto-detection and mapping

### Features
- **Excel Reader**: Multi-sheet support with automatic type classification
- **Excel Writer**: Write-back WordPress IDs and metadata
- **WordPress Client**: Async HTTP client with Basic Auth
- **Posts Endpoint**: CRUD operations for all content types
- **Taxonomies Endpoint**: Smart term resolution and creation
- **Media Endpoint**: Upload local files or download from URLs
- **Content Mapper**: Universal field mapping Excel → WordPress API
- **Field Detector**: Auto-detection of ACF, taxonomies, system fields
- **Content Sync**: Orchestrated sync with DELETE → UPDATE → ADD order
- **Taxonomy Cache**: Pre-loading for performance
- **Logger**: Structured logging with summary statistics

### Documentation
- Comprehensive README.md with examples
- QUICKSTART.md for fast onboarding
- DEVELOPMENT.md for developers
- .env.example for configuration
- Detailed inline documentation

### Configuration
- Pydantic-based configuration validation
- Environment variable support
- Configurable sync options (concurrency, timeout, batch size)
- Configurable logging (level, rotation, retention)

### CLI Commands
- `validate` - Validate Excel structure without sync
- `sync` - Synchronize Excel → WordPress
  - `--file` - Excel file path
  - `--sheets` - Filter specific sheets
  - `--dry-run` - Preview without changes
  - `--verbose` - Detailed logging
  - `--concurrency` - Parallel request limit
  - `--skip-media` - Skip media uploads
  - `--no-update-excel` - Don't write back to Excel
- `fetch` - Get data from WordPress → Excel
  - `--file` - Excel file path
  - `--sheets` - Filter specific sheets
  - `--status` - Filter by status (publish, draft, any)
  - `--limit` - Maximum posts per sheet
- `options` - Update WordPress site settings
  - `--file` - Excel file path with options sheet
  - `--dry-run` - Preview without sending

## [1.1.0] - 2026-01-10

### Added
- **Content action**: New `action=content` for updating only the content field from markdown/html files
  - Files stored in `data/content/` directory
  - Supports `.md`, `.markdown`, `.html`, `.htm` formats
  - Automatic markdown → HTML conversion
  - HTML → Gutenberg blocks conversion using `html_to_gutenberg.py`
  - New field: `content_path` for specifying content file
  - ContentProcessor utility for file processing
- **Export command**: New CLI command to export all site content to files
  - Discovers all post types via WordPress REST API
  - Exports content to organized folders: `{post_type}/{id}.html`
  - Creates CSV index with: post_type, id, title, file_path, seo_title, status, date
  - Supports credentials from Excel file or CLI parameters
  - ContentExporter utility for orchestrating export

### Fixed
- **Fetch command**: Categories and tags now show names instead of IDs
  - Added taxonomy cache loading in ResponseMapper
  - ID→name conversion for all taxonomies
- **Sync command**: Fixed 'updateed' statistics bug
  - Corrected action statistics increment logic
  - Changed from string formatting to explicit if/elif statements
- **Sync command**: Fixed `link` and `modified` fields not updating
  - Added additional GET request after update to fetch fresh data
  - Ensures all metadata is properly written back to Excel

### Changed
- Moved `html_to_gutenberg.py` to `src/utils/` directory
- Added `content_path` to SYSTEM_COLUMNS constant
- Updated VALID_ACTIONS to include "content"
- Added `content_updated` to sync statistics
- Updated requirements.txt with markdown and beautifulsoup4 dependencies

### Documentation
- Added comprehensive documentation for `action=content` feature
- Added documentation for `export` command with examples
- Updated README.md with new action types and fields
- Updated TODO.md with completion status for all tasks
- Updated CHANGELOG.md with version history

## [Unreleased]

### Planned Features
- `stats` command - View sync statistics from logs
- Taxonomy sheet synchronization (currently skipped)
- Progress bars for large operations
- Batch operations optimization
- Unit and integration tests
- CI/CD pipeline
- Web UI for configuration
- Multi-language support

---

## Version History

- **1.0.0** - Initial release with core synchronization features
