"""ObsCo — Observability Copilot.

A standalone Q&A assistant about observability tools. Lives entirely in this
package and never imports from other accelerators (ayosa, observascore, etc.).
"""

from accelerators.obsco.service import answer_question

__all__ = ["answer_question"]
