"""ObsCo — Observability Copilot.

A standalone Q&A assistant about observability tools and about the
Observability Studio platform itself. Lives entirely in this package and
never imports from other accelerators (ayosa, observascore, etc.).
"""

from accelerators.obsco.service import answer_question
from accelerators.obsco.studio_knowledge import (
    STUDIO_FACTS,
    detect_studio_topics,
    get_studio_facts,
    list_studio_keys,
)

__all__ = [
    "answer_question",
    "STUDIO_FACTS",
    "detect_studio_topics",
    "get_studio_facts",
    "list_studio_keys",
]
