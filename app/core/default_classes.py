"""Predefined document classes seeded into every account.

`description` is intentionally rich — it feeds the extraction-time classifier, so
each line should read like a short instruction about what belongs in the class.
Accounts may add their own custom classes on top of these system ones.
"""

from __future__ import annotations

from typing import NamedTuple


class ClassSeed(NamedTuple):
    slug: str
    name: str
    description: str


DEFAULT_CLASSES: tuple[ClassSeed, ...] = (
    ClassSeed("invoice", "Invoice", "A bill requesting payment for goods or services, with line items, totals, and a due date."),
    ClassSeed("receipt", "Receipt", "Proof of a completed payment or purchase, showing amount paid, date, and merchant."),
    ClassSeed("contract", "Contract", "A legally binding agreement between parties, with terms, obligations, and signatures."),
    ClassSeed("id_document", "ID Document", "Government or official identification: passport, driver's license, national ID card."),
    ClassSeed("bank_statement", "Bank Statement", "A periodic summary of account transactions, balances, and fees from a bank."),
    ClassSeed("tax_document", "Tax Document", "Tax forms, returns, or assessments such as W-2, 1099, or a filed return."),
    ClassSeed("payslip", "Payslip", "An employer's record of wages for a pay period, with gross pay, deductions, and net pay."),
    ClassSeed("utility_bill", "Utility Bill", "A bill for electricity, water, gas, internet, or phone service with usage and charges."),
    ClassSeed("insurance", "Insurance", "An insurance policy, certificate, or claim covering health, auto, home, or life."),
    ClassSeed("medical_record", "Medical Record", "Clinical or health documents: lab results, prescriptions, discharge summaries, reports."),
    ClassSeed("report", "Report", "An analytical or informational document presenting findings, metrics, or research."),
    ClassSeed("letter", "Letter", "Formal or personal correspondence addressed to a recipient."),
    ClassSeed("resume", "Resume / CV", "A summary of a person's work experience, education, and skills."),
    ClassSeed("warranty", "Warranty", "A guarantee covering repair or replacement of a product for a stated period."),
)
