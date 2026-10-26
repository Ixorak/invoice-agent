# Evaluation on the 10 fictional sample invoices

This file is written by `python -m invoice_agent demo`. The numbers below are measured, not estimated.
All sample invoices are **fictional** (invented companies, numbers and IBANs). A result on 10 synthetic invoices says nothing about accuracy on real invoices.

- Date: 2026-10-26 11:06 (local time)
- Command: `python -m invoice_agent demo --width 150 --export-console docs/demo-console.svg --eval-report docs/eval.md`
- Claude Code version: 2.1.284
- Model setting: `sonnet`, model reported by Claude Code: `claude-sonnet-5-5`
- Effort setting: `Claude Code default`
- Invoices: 10 (9 read from their text layer, 1 scanned and read by the AI from the PDF)

## Status and codes: 10/10 correct

| File | Expected | Got | Match | Time (s) |
|---|---|---|---|---|
| 01-papeterie-lumenta.pdf | OK | OK | yes | 4.8 |
| 02-primeurs-du-lac.pdf | OK | OK | yes | 5.9 |
| 03-nordwerk-demo.pdf | OK | OK | yes | 6.5 |
| 04-tipografia-esempio.pdf | OK | OK | yes | 4.8 |
| 05-examplify-cloud.pdf | OK | OK | yes | 5.2 |
| 06-garage-exemplaire.pdf | NEEDS_REVIEW (TOTAL_MISMATCH) | NEEDS_REVIEW (TOTAL_MISMATCH) | yes | 5.4 |
| 07-nettoyage-modele.pdf | NEEDS_REVIEW (VAT_RATE_INVALID) | NEEDS_REVIEW (VAT_RATE_INVALID) | yes | 5.0 |
| 08-papeterie-lumenta-duplicata.pdf | NEEDS_REVIEW (DUPLICATE) | NEEDS_REVIEW (DUPLICATE) | yes | 5.6 |
| 09-menuiserie-specimen-scan.pdf | OK | OK | yes | 6.1 |
| 10-conseil-fantome.pdf | NEEDS_REVIEW (MISSING_DUE_DATE, IBAN_INVALID, SUSPICIOUS_INSTRUCTION) | NEEDS_REVIEW (MISSING_DUE_DATE, IBAN_INVALID, SUSPICIOUS_INSTRUCTION) | yes | 5.6 |

## Field accuracy

| Field | Correct |
|---|---|
| Supplier name | 10/10 |
| Supplier VAT ID | 10/10 |
| Invoice number | 10/10 |
| Invoice date | 10/10 |
| Due date | 10/10 |
| Currency | 10/10 |
| Subtotal | 10/10 |
| VAT breakdown (as used by the checks) | 10/10 |
| VAT total (as used by the checks) | 10/10 |
| Total (printed) | 10/10 |
| IBAN | 10/10 |
| Language | 10/10 |
| Line count | 10/10 |
| Scanned (no text layer) | 10/10 |

How fields are compared: names and invoice numbers ignoring case and extra spaces; VAT IDs and IBANs after removing spaces and punctuation; amounts to the cent; dates exactly. "VAT breakdown" and "VAT total" are the values used by the checks: a VAT base that is not printed is taken from the lines with that rate, and when no VAT total is printed the per-rate VAT amounts are added up.

## Time per invoice

- Average: 5.5 s
- Median: 5.5 s
- Min / max: 4.8 s / 6.5 s
- Total for 10 invoices: 55 s

Measured from opening the PDF to the final status, including the Claude Code call (start-up of the CLI and the model's answer). Invoices are processed one after the other.
