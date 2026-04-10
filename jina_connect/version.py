# Build version information
# This file is auto-updated during deployment/CI pipeline

# Semantic version
VERSION = "1.0.0"

# Build number - increment on each deployment
# Format: YYYYMMDD.BUILD_NUMBER or just incremental number
BUILD_NUMBER = "1"

# Git commit hash (short) - can be set by CI/CD
GIT_COMMIT = "unknown"

# Build timestamp - can be set by CI/CD
BUILD_DATE = "2026-02-06"


def get_version_string():
    """Returns version string for display (e.g., 'v1.0.0 (Build #1)')"""
    return f"v{VERSION} (Build #{BUILD_NUMBER})"


def get_full_version():
    """Returns detailed version info"""
    return {
        "version": VERSION,
        "build_number": BUILD_NUMBER,
        "git_commit": GIT_COMMIT,
        "build_date": BUILD_DATE,
    }
