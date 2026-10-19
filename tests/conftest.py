"""Pytest configuration and test fixtures."""


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "integration: marks integration tests that require external services"
    )
