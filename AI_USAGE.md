# AI Usage Disclosure

## 1. Tools Used

- **Tool:** Cursor (Claude 3.5 Sonnet)
- **Role:** AI-assisted development tool used for code scaffolding, regex drafting, syntax references, code cleanup, and basic test boilerplate generation.

## 2. My Contribution and Ownership

The overall system architecture, technical approach, database design, data-processing strategy, security decisions, and implementation logic were independently decided and directed by me.

I made all major technical decisions, evaluated the trade-offs, identified shortcomings in initial AI suggestions, and determined the final implementation approach.

AI was used only as a supporting coding assistant to speed up development, particularly for:
- Drafting initial regex patterns for phone and currency normalization.
- Generating file-path handling and basic code scaffolding.
- Cleaning and refactoring repetitive code.
- Preparing initial test boilerplate.
- Looking up SQLite syntax and implementation references.

**All critical design decisions and final implementation choices remained under my control.**

## 3. Major Technical Decisions Made by Me

The following decisions were independently analyzed, selected, and implemented by me:

1. **Strict Date Parsing:** Rejected fuzzy date parsing and implemented deterministic day-first parsing using approved `strptime` formats to prevent ambiguous dates and invalid calendar values.

2. **Append-Only Ledger Architecture:** Designed an immutable `interaction_ledger` with SQLite triggers preventing UPDATE and DELETE operations. Implemented a separate `event_lead_links` table and `resolved_ledger` view for reconciliation.

3. **Content-Hash Idempotency:** Decided to use SHA-256 content hashing instead of file-path-based identification to prevent duplicate ingestion when files are moved, copied, or accessed through different paths.

4. **PII Protection and Blind Indexing:** Designed the HMAC-SHA256 blind-indexing approach using `LEDGER_HMAC_SECRET` to support efficient phone-based matching without storing plaintext phone numbers.

These were architectural and engineering decisions made by me, not decisions delegated to AI.

## 4. Where AI Assisted

AI assistance was limited to implementation support, including:
- Drafting initial regex expressions for Indian mobile number validation and currency sanitization.
- Generating basic file-handling and normalization code scaffolding.
- Cleaning, formatting, and refactoring repetitive code.
- Preparing initial parameterized test-case boilerplate.
- Providing SQLite trigger syntax references.

Any AI-generated suggestions were reviewed, modified, or discarded based on the actual project requirements.

## 5. Testing and Verification

**Testing and verification were performed manually by me.**

I independently executed the application, inspected database records, validated outputs, and checked the expected behavior against the project requirements.

My manual verification included:

- Checking valid and invalid phone number normalization.
- Verifying strict date parsing and rejection of invalid dates.
- Testing decimal and currency sanitization.
- Validating supervisor override hierarchy.
- Checking content-hash-based duplicate detection.
- Verifying that UPDATE and DELETE operations on the interaction ledger were rejected.
- Comparing incremental ingestion against single-pass ingestion.
- Inspecting SQLite records to confirm encryption at rest.
- Verifying that the five contract reports matched byte-for-byte across sequential and combined ingestion modes.

I also inspected the database to verify that the interaction ledger remained immutable throughout execution.

## 6. Final Declaration

I used AI as a productivity and coding-support tool, not as a replacement for engineering judgment or independent implementation decisions.

The system architecture, major technical decisions, data integrity strategy, security approach, and final implementation direction were determined by me. AI assisted with selected coding tasks, file-path handling, code cleanup, syntax references, and initial test scaffolding.

**I personally performed the testing, execution, database inspection, and final verification of the application. I take responsibility for the final solution and its technical decisions.**