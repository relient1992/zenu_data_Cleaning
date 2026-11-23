"""Chat tests run without an AI so they are fast and repeatable (the AI step has its own tests)."""
from daris import assistant


class OfflineLLM:
    available = False
    is_cloud = False
    protect = False
    label = "Offline"
    last_error = "offline (tests)"


assistant.get_llm = lambda *a, **k: OfflineLLM()
