import gc

import pytest


@pytest.fixture(autouse=True)
def _collect():
    # Collect after every test so a Qt object that crashes PySide when the
    # garbage collector frees it fails the test that made it.
    yield
    gc.collect()
