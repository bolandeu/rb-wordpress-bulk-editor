# Contributing to WordPress Bulk Editor

Thank you for considering contributing to WordPress Bulk Editor! This document provides guidelines and instructions for contributing.

## Code of Conduct

Be respectful and constructive in all interactions.

## How to Contribute

### Reporting Bugs

Before creating a bug report, please check existing issues to avoid duplicates.

**When creating a bug report, include:**
- Clear, descriptive title
- Steps to reproduce the issue
- Expected behavior vs actual behavior
- Excel file structure (without sensitive data)
- WordPress version and REST API details
- Python version and OS
- Relevant log files from `logs/` directory
- Screenshots if applicable

### Suggesting Features

Feature suggestions are welcome! Please include:
- Clear description of the feature
- Use cases and benefits
- Possible implementation approach
- Any relevant examples or mockups

### Pull Requests

1. **Fork the repository**
2. **Create a feature branch**
   ```bash
   git checkout -b feature/your-feature-name
   ```
3. **Make your changes**
   - Follow the existing code style
   - Add tests for new features
   - Update documentation as needed
   - Add entry to CHANGELOG.md

4. **Test your changes**
   ```bash
   # Run tests
   pytest

   # Check code style
   black src/
   flake8 src/
   mypy src/
   ```

5. **Commit your changes**
   ```bash
   git add .
   git commit -m "feat: add feature description"
   ```

   Use conventional commit messages:
   - `feat:` - New feature
   - `fix:` - Bug fix
   - `docs:` - Documentation changes
   - `style:` - Code style changes
   - `refactor:` - Code refactoring
   - `test:` - Test changes
   - `chore:` - Build/tool changes

6. **Push to your fork**
   ```bash
   git push origin feature/your-feature-name
   ```

7. **Create a Pull Request**
   - Provide clear description of changes
   - Reference related issues
   - Include screenshots/logs if relevant

## Development Setup

### Prerequisites
- Python 3.10+
- Git
- WordPress test site (optional, for integration tests)

### Setup Development Environment

```bash
# Clone the repository
git clone https://github.com/yourusername/wordpress-bulk-editor.git
cd wordpress-bulk-editor

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install in development mode
pip install -e .

# Install development dependencies
pip install -r requirements-dev.txt
```

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=src --cov-report=html

# Run specific test file
pytest tests/unit/test_field_detector.py

# Run with verbose output
pytest -v -s
```

### Code Style

We use:
- **Black** for code formatting
- **Flake8** for linting
- **MyPy** for type checking
- **isort** for import sorting

```bash
# Format code
black src/

# Check linting
flake8 src/

# Type checking
mypy src/

# Sort imports
isort src/
```

### Project Structure

```
src/
├── cli.py           # CLI entry point
├── config.py        # Configuration models
├── constants.py     # Project constants
├── exceptions.py    # Custom exceptions
├── excel/           # Excel file handling
├── wordpress/       # WordPress API integration
├── sync/            # Synchronization logic
├── mappers/         # Data transformation
└── utils/           # Utility functions
```

## Development Guidelines

### Code Style
- Follow PEP 8
- Use type hints for all function signatures
- Maximum line length: 100 characters
- Use descriptive variable names
- Add docstrings to all public functions/classes

### Documentation
- Update README.md for user-facing changes
- Update DEVELOPMENT.md for architectural changes
- Add docstrings following Google style:
  ```python
  def function_name(param1: str, param2: int) -> bool:
      """
      Short description.

      Args:
          param1: Description of param1
          param2: Description of param2

      Returns:
          Description of return value

      Raises:
          ValueError: When value is invalid
      """
  ```

### Testing
- Write tests for new features
- Maintain or improve code coverage
- Use pytest fixtures for common setups
- Mock external API calls
- Test edge cases and error conditions

### Commit Messages
Follow [Conventional Commits](https://www.conventionalcommits.org/):
```
<type>(<scope>): <subject>

<body>

<footer>
```

Example:
```
feat(sync): add support for custom post types

Added ability to sync custom post types by detecting
sheet names that don't match standard types.

Closes #123
```

### Pull Request Process

1. Update CHANGELOG.md with changes
2. Update documentation if needed
3. Ensure all tests pass
4. Ensure code passes linting
5. Get approval from maintainer
6. Maintainer will merge

### Branch Naming

- `feature/feature-name` - New features
- `fix/bug-description` - Bug fixes
- `docs/description` - Documentation changes
- `refactor/description` - Code refactoring
- `test/description` - Test additions/changes

## Areas for Contribution

### High Priority
- [ ] Integration tests with real WordPress instance
- [ ] `fetch` command implementation (WordPress → Excel)
- [ ] `options` command for site settings
- [ ] Taxonomy sheet synchronization
- [ ] Progress bars for long operations

### Medium Priority
- [ ] Unit tests for all modules
- [ ] Performance optimizations
- [ ] Better error messages
- [ ] Logging improvements
- [ ] CLI improvements (--help text, etc.)

### Nice to Have
- [ ] GitHub Actions CI/CD
- [ ] Docker support
- [ ] Web UI for configuration
- [ ] Plugin marketplace integration
- [ ] Backup/restore functionality

## Questions?

Feel free to open an issue with the `question` label.

## License

By contributing, you agree that your contributions will be licensed under the MIT License.

Thank you for contributing! 🎉
