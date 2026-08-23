"""Guard: ReimbursementBot's public surface must survive the bot.py -> mixin
+ JobProcessor refactor.

main.py registers handlers as bound callables (CommandHandler("start",
bot.start_command)) and tests invoke many of them directly on the instance,
so a missing method means a broken refactor even if logic tests still pass.
"""

from app.bot import bot as bot_module
from app.bot.bot import MAX_CAPTION_CHARS, ReimbursementBot, _clamp_caption


def test_bot_name_surface_preserved() -> None:
    # test_caption.py imports these from app.bot.bot; they must be re-exported.
    assert bot_module.MAX_CAPTION_CHARS == 1024
    assert callable(bot_module._clamp_caption)
    assert MAX_CAPTION_CHARS == 1024
    assert callable(_clamp_caption)


def test_bot_method_surface_preserved() -> None:
    for method in (
        "start_command",
        "help_command",
        "status_command",
        "clear_command",
        "cancel_command",
        "generate_command",
        "message_handler",
        "start_workers",
        "stop_workers",
        "notify_queued_lost",
    ):
        assert callable(getattr(ReimbursementBot, method, None)), f"missing {method}"
