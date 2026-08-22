"""Receipt processing pipeline.

Split into cohesive submodules after it outgrew a single module:

* ``types.py``    - exceptions, result/outcome types, filesystem helpers
* ``retry.py``    - retry-with-backoff around a single AI extraction call
* ``pipeline.py`` - ``ProcessingService`` orchestration
* ``run.py``      - ``run_with_cleanup`` guaranteed-cleanup wrapper

All previously public names are re-exported here so ``from
app.services.receipt_service import ...`` keeps working unchanged.
"""

from .pipeline import ProcessingService
from .retry import MAX_RATE_LIMIT_DELAY, _CallBudget, _extract_with_retry
from .run import run_with_cleanup
from .types import BudgetExceededError, ProcessingError, ProcessingResult, make_request_base

__all__ = [
    "BudgetExceededError",
    "MAX_RATE_LIMIT_DELAY",
    "ProcessingError",
    "ProcessingResult",
    "ProcessingService",
    "_CallBudget",
    "_extract_with_retry",
    "make_request_base",
    "run_with_cleanup",
]
