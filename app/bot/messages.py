"""User-facing message templates."""

from __future__ import annotations

UNAUTHORIZED = "Sorry, you are not authorized to use this bot."

HELP = (
    "Reimbursement Bot\n\n"
    "Commands:\n"
    "/start - Start the bot\n"
    "/status - Show how many receipts are staged\n"
    "/clear - Clear all staged receipts\n"
    "/generate - Create your reimbursement report\n"
    "/cancel - Cancel the current flow\n\n"
    "Send a receipt photo or image document to stage it. Your receipts stay in "
    "Telegram until you run /generate with your password."
)

STARTED = "Ready. Send me receipt photos, or use /help for commands."

PASSWORD_PROMPT = "Enter your reimbursement password."

WRONG_PASSWORD = "Incorrect password. The password flow has been cancelled."

CANCELLED = "Cancelled."

NO_RECEIPTS = "No receipts have been uploaded."

RECEIPT_STORED = "Receipt staged ({n}/{max}). Send more or run /generate."

DUPLICATE_RECEIPT = "That receipt is already staged."

MAX_RECEIPTS_REACHED = "Maximum of {max} receipts reached."

UNSUPPORTED_DOCUMENT = "Please send a photo or a JPEG/PNG/WEBP image document."

UPLOAD_DURING_PROCESSING = "A report is already being generated. Please wait."

BUSY = "A reimbursement report is already being generated. Please wait."

STATUS = "Receipts staged: {n}"

SESSION_CLEARED = "Session cleared."

PROCESSING_STARTED = "Processing {n} receipts…"

REPORT_READY = (
    "Your reimbursement report is ready.\n\n"
    "Receipts: {receipts}\n"
    "Processed: {processed}\n"
    "Review required: {review}\n"
    "{totals}"
)

NO_PASSWORD_CONFIGURED = (
    "The bot has no password configured. Refusing to generate."
)

ERROR_MESSAGE = (
    "Something went wrong while processing your receipts.\n\n"
    "Request ID: {request_id}"
)
