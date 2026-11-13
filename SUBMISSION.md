# Technical Assignment Submission

## 1. Candidate Details
- **Name:** Ravi Teja
- **Assignment:** A – Interaction Ledger & Reporting Pipeline
- **Time Spent:** Approximately 4.5 hours

## 2. How to Run

```bash
# 1. Environment setup
python -m venv .venv

# Windows
.venv\Scripts\activate

# Linux/macOS
source .venv/bin/activate

pip install -r requirements.txt

# 2. Ingest data
python -m solution ingest data/drop_1
python -m solution ingest data/drop_2

# 3. Generate reports
python -m solution report --out reports/

# 4. Run tests
python -m pytest
```

## 3. System Design

The solution uses a modular SQLite-based pipeline with an immutable, append-only Interaction Ledger.

**Core workflow:**
1. **Ingestion:** Validates and normalizes lead CSVs and JSONL events.
2. **Data Security:** Uses AES-based encryption for PII and HMAC-SHA256 for secure phone matching.
3. **Reconciliation:** Links events to leads through a separate mapping table without modifying ledger records.
4. **Reporting:** Generates five CSV/JSON reports covering batch summaries, daily performance, lead details, rejections, and data quality.

## 4. Key Technical Decisions

| Decision | Implementation |
|---|---|
| PII protection | Fernet encryption and HMAC-SHA256 blind indexing |
| Ledger integrity | SQLite triggers prevent UPDATE and DELETE operations |
| Event reconciliation | Separate `event_lead_links` table and `resolved_ledger` view |
| Idempotency | SHA-256 content hashing prevents duplicate ingestion |
| Date handling | Strict day-first parsing with calendar validation |
| Disposition resolution | Latest supervisor override takes priority |

These decisions ensure data integrity, secure processing, reliable reconciliation, and consistent reporting.

## 5. Failure Handling

The pipeline handles:
- Events arriving before their corresponding leads.
- Duplicate files and duplicate event delivery.
- Invalid dates, malformed JSON, and missing identifiers.
- Different timestamp formats, including UTC and IST.
- Supervisor overrides and phone-less event reconciliation.

**Limitation:** Distributed multi-writer concurrency across separate nodes is not supported by SQLite. PostgreSQL would be the next step for distributed deployment.

## 6. Testing and Verification

I manually executed and verified the solution against critical scenarios, including:

- Phone, date, and currency normalization.
- PII encryption and plaintext exposure checks.
- Ledger immutability and database trigger enforcement.
- Content-hash-based duplicate detection.
- Cross-drop event reconciliation.
- Supervisor override handling.
- Incremental versus single-pass report consistency.

The project also includes 19 automated pytest test cases covering these scenarios.

## 7. Results

| Dataset | Processing Time |
|---|---:|
| ~2,000 leads and 9,000 events | Under 2 seconds |
| ~140,000 leads and 600,000 events | Approximately 35 seconds |

The reports were verified for consistency across incremental and combined ingestion.

## 8. Future Improvements

- PostgreSQL support for distributed processing.
- Automated database schema migrations.
- SQL-based reporting optimizations for larger datasets.

## 9. AI Usage Disclosure

AI (Cursor – Claude 3.5 Sonnet) was used as a supporting development assistant for initial code scaffolding, regex suggestions, file-path handling, syntax references, and code cleanup.

The major architectural decisions, database design, security approach, reconciliation strategy, and final implementation direction were determined by me. I reviewed and modified AI-generated suggestions wherever necessary.

Testing, execution, database inspection, and final verification were performed manually by me.

**Final Responsibility:** I take ownership of the technical decisions, implementation, and submitted solution.