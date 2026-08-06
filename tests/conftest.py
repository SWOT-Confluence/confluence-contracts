"""Shared pytest fixtures for the contract-model test suite.

The :func:`valid_contract` fixture yields a fresh, fully valid contract dictionary
for every test. Invalid-input tests start from this known-good dict and mutate exactly
one field, so each assertion isolates a single failure mode.
"""

import pytest


def build_valid_contract() -> dict:
    """Build a fresh, fully valid contract dictionary.

    Returns:
        A deep, mutation-safe dict that validates cleanly against ``Contract``.
    """
    return {
        "version": "1.0.0",
        "source": {
            "repo": "momma",
            "github_username": "octocat",
            "branch": "main",
            "commit": "0123456789abcdef0123456789abcdef01234567",
            "image_tag": "momma:latest",
        },
        "module": {
            "name": "momma",
            "produces": [
                {
                    "filepath": "flpe/momma/{reach_id}_momma.nc",
                    "dimensions": ["nt"],
                    "variables": {
                        "stage": {
                            "dtype": "f8",
                            "dimensions": ["nt"],
                            "required": True,
                            "attrs": {
                                "long_name": "water surface elevation",
                                "comment": "MOMMA-estimated stage",
                                "units": "m",
                                "valid_min": -1000.0,
                                "valid_max": 1000.0,
                                "coverage_content_type": "physicalMeasurement",
                            },
                        }
                    },
                }
            ],
            "consumes": [
                {
                    "filepath": "sword/{continent}_sword.nc",
                    "variables": ["reach_id"],
                }
            ],
        },
    }


@pytest.fixture
def valid_contract() -> dict:
    """Provide a known-good contract dict that each test may mutate freely.

    Returns:
        A fresh contract dictionary, rebuilt per test so mutations do not leak.
    """
    return build_valid_contract()
