from __future__ import annotations

"""Typed shapes the model fills in.

No model in this system writes prose that reaches the deliverable. It fills one
of these, every field is optional, and an unfilled field becomes a recorded gap
rather than an invented value.
"""

DOC_TYPES = [
    "master_agreement",
    "amendment",
    "order_form",
    "sow",
    "invoice",
    "credit_note",
    "unknown",
]

# Terms the register tracks. Adding one is a data change here plus a renderer
# label -- not a rewrite. (Configuration over code.)
TERM_KEYS = [
    "parties",
    "effective_date",
    "initial_term_months",
    "renewal_type",
    "renewal_notice_days",
    "unit_price",
    "billing_frequency",
    "payment_terms_days",
    "liability_cap",
    "sla_uptime_pct",
    "sla_credit_pct",
    "termination_for_convenience_days",
    "assignment_restricted",
    "governing_law",
]

CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "doc_type": {"type": "string", "enum": DOC_TYPES},
        "confidence": {"type": "number", "description": "0.0 to 1.0"},
        "document_date": {"type": "string", "description": "ISO date, or empty string"},
        "amends_reference": {
            "type": "string",
            "description": "Title or number of the agreement this amends, or empty string",
        },
        "reasoning": {"type": "string"},
    },
    "required": ["doc_type", "confidence"],
}

FACT_SCHEMA = {
    "type": "object",
    "properties": {
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "enum": TERM_KEYS},
                    "value": {"type": "string", "description": "Normalised scalar value"},
                    "unit": {"type": "string"},
                    "effective_from": {"type": "string", "description": "ISO date or empty"},
                    "quote": {
                        "type": "string",
                        "description": (
                            "Text copied EXACTLY from the source that supports this "
                            "value. It is checked character by character against the "
                            "document; if it does not appear verbatim the fact is discarded."
                        ),
                    },
                    "confidence": {"type": "number"},
                },
                "required": ["key", "value", "quote"],
            },
        }
    },
    "required": ["facts"],
}

INVOICE_SCHEMA = {
    "type": "object",
    "properties": {
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "invoice_number": {"type": "string"},
                    "service_date": {"type": "string", "description": "ISO date or empty"},
                    "description": {"type": "string"},
                    "quantity": {"type": "string"},
                    "unit_price": {"type": "string"},
                    "total": {"type": "string"},
                    "quote": {
                        "type": "string",
                        "description": "Exact line text copied from the invoice.",
                    },
                },
                "required": ["unit_price", "quote"],
            },
        }
    },
    "required": ["lines"],
}

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["pass", "fail", "unsupported"]},
        "confidence": {"type": "number"},
        "detail": {"type": "string"},
        "quote": {
            "type": "string",
            "description": "Exact supporting text from the source. Empty if unsupported.",
        },
    },
    "required": ["verdict", "confidence", "detail"],
}
