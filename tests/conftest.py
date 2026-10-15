"""Test bootstrap.

Replaces app.core.config with dummy settings *before* any app module is imported,
so tests never read a .env file and never need real Neo4j / Gemini credentials.
Nothing here opens a network connection: the Neo4j driver connects lazily and
graph tests drive the transaction functions with a fake tx.
"""
import sys
import types

from app.core.settings_schema import Settings

_config = types.ModuleType("app.core.config")
# model_construct applies field defaults without reading the environment or .env
_config.settings = Settings.model_construct(
    neo4j_uri="neo4j://localhost:7687",
    neo4j_username="test",
    neo4j_password="test",
    gemini_api_key="test",
)
sys.modules["app.core.config"] = _config


class FakeResult:
    def __init__(self, records):
        self._records = records

    def single(self):
        return self._records[0] if self._records else None

    def __iter__(self):
        return iter(self._records)


class FakeTx:
    """Records every query; replies with the queued record lists in order."""
    def __init__(self, replies=None):
        self.calls = []
        self._replies = list(replies or [])

    def run(self, query, **params):
        self.calls.append((query, params))
        return FakeResult(self._replies.pop(0) if self._replies else [])
