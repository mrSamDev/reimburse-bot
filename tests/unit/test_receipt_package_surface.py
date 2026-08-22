"""Guard: the public surface of ``app.services.receipt_service`` must stay
importable from the package after the module -> package refactor.

Every name here is relied on by ``app/bot/bot.py``, ``app/main.py``, or the
test suite; if any goes missing the package is broken even if the suite's
behavioural tests happen to pass.
"""

import importlib


def test_receipt_service_public_surface_importable() -> None:
    mod = importlib.import_module("app.services.receipt_service")
    expected = (
        "ProcessingError",
        "BudgetExceededError",
        "ProcessingResult",
        "ProcessingService",
        "run_with_cleanup",
        "make_request_base",
        "MAX_RATE_LIMIT_DELAY",
        "_extract_with_retry",
        "_CallBudget",
    )
    for name in expected:
        assert hasattr(mod, name), f"missing public name: {name}"
