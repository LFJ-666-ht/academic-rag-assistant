"""Fresh, isolated knowledge base for each test; real storage with model doubles."""

import pytest
from helpers import FakeModels

from backend.core import KnowledgeBase, Settings


@pytest.fixture
def kb(tmp_path):
    instance = KnowledgeBase(Settings(tmp_path), FakeModels())
    yield instance
    instance.close()
