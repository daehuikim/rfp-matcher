from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class StepEnv:
    keep_artifacts: bool
    mark_running: Callable[[str, str], None]
    mark_done: Callable[[str, str], None]
    mark_error: Callable[[str, str], None]
    render_cards: Callable[[list], None]
    persist_llm_cost_state: Callable[[float, list[dict]], None] | None = None
