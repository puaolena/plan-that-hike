import os
import sys
from pathlib import Path
import pytest

# Ensure the project root is in python path
sys.path.insert(0, str(Path(__file__).parent.parent.resolve()))



@pytest.fixture(autouse=True)
def clean_env():
    """Ensure environment is controlled during tests."""
    original_env = dict(os.environ)
    yield
    # Restore env vars
    os.environ.clear()
    os.environ.update(original_env)
