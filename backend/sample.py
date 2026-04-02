from datetime import datetime, timezone
from typing import Any, Dict


def get_sample_email() -> Dict[str, Any]:
    return {
        "subject": "Bill from Brosnahan Insurance Agency",
        "from": "numinatest2@gmail.com",
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "html": "",
        "text": (
            "Hello,\n"
            "Please record the following bill from Brosnahan Insurance Agency:\n"
            "Items Purchased:\n"
            "- Liability Insurance (12 months @ $130 per month) - $1,560.00\n"
            "- Commercial Property Insurance - $900.00\n"
            "- Workers' Compensation Insurance - $500.00\n"
            "Subtotal: $2,960.00\n"
            "Tax (10%): $296.00\n"
            "Total: $3,256.00\n"
            "Payment Method: Credit Card\n"
            "Bill Date: April 15, 2026\n"
            "Due Date: April 25, 2026\n"
            "Category: Insurance\n"
            "Vendor: Brosnahan Insurance Agency\n"
            "Regards,\n"
            "Finance Team"
        ),
    }

