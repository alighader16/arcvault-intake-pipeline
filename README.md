# ArcVault AI Intake Pipeline

**Valsoft AI Engineer Assessment**
**Candidate**: [Ali Ghader]
**Date**: March 2026

---

## What This Is

An AI-powered pipeline that takes unstructured customer messages and automatically:
1. Classifies them (bug report, billing issue, feature request, etc.)
2. Extracts key details (invoice numbers, error codes, dollar amounts)
3. Routes them to the correct team
4. Flags urgent ones for human review

---

## Setup

### Prerequisites
- Python 3.10+ ([download here](https://www.python.org/downloads/))
- An OpenAI API key ([get one here](https://platform.openai.com/api-keys))

### Install

```bash
pip install openai watchdog
```

### Configure

Open `arcvault_workflow.py` and replace `YOUR_KEY_HERE` on line 35 with your OpenAI API key:

```python
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "YOUR_KEY_HERE")
```

Or set it as an environment variable:

```bash
# Windows PowerShell
$env:OPENAI_API_KEY = "sk-your-key-here"

# Mac/Linux
export OPENAI_API_KEY="sk-your-key-here"
```

---

## How to Run

### Mode 1: Batch Mode (processes all 5 sample messages)

```bash
python arcvault_workflow.py
```

This processes the 5 hardcoded sample messages and saves results to `output/results.json`.

#### Expected Output

```
============================================================
  ArcVault AI Intake Pipeline
  Model: gpt-4o-mini
  Mode: Batch
============================================================

============================================================
  Processing REQ-001 (Email)
============================================================
  [Step 1] Ingested ✓
  [Step 2] Classified → Bug Report | Medium | Confidence: 0.92
  [Step 3] Enriched  → User is unable to log in due to a 403 error...
  [Step 4] Routed    → Engineering
  [Step 5] Summary   → A user (jsmith) is locked out of their account...
  [Step 6] Escalation → ✓ Standard

============================================================
  Processing REQ-002 (Web Form)
============================================================
  [Step 1] Ingested ✓
  [Step 2] Classified → Feature Request | Low | Confidence: 0.97
  [Step 3] Enriched  → Customer requests a bulk export feature for audit...
  [Step 4] Routed    → Product
  [Step 5] Summary   → A compliance-focused customer is requesting a bulk...
  [Step 6] Escalation → ✓ Standard

============================================================
  Processing REQ-003 (Support Portal)
============================================================
  [Step 1] Ingested ✓
  [Step 2] Classified → Billing Issue | High | Confidence: 0.95
  [Step 3] Enriched  → Customer was charged $1,240 on invoice #8821...
  [Step 4] Routed    → Billing
  [Step 5] Summary   → The customer reports a $260 overcharge on invoice...
  [Step 6] Escalation → ⚠ ESCALATED
           → Billing discrepancy of $260 exceeds $100 threshold

============================================================
  Processing REQ-004 (Email)
============================================================
  [Step 1] Ingested ✓
  [Step 2] Classified → Technical Question | Low | Confidence: 0.88
  [Step 3] Enriched  → Customer wants to know if ArcVault supports SSO...
  [Step 4] Routed    → IT/Security
  [Step 5] Summary   → A customer is evaluating whether ArcVault supports...
  [Step 6] Escalation → ✓ Standard

============================================================
  Processing REQ-005 (Web Form)
============================================================
  [Step 1] Ingested ✓
  [Step 2] Classified → Incident/Outage | High | Confidence: 0.96
  [Step 3] Enriched  → Dashboard is non-functional for multiple users...
  [Step 4] Routed    → Engineering (Urgent)
  [Step 5] Summary   → A customer reports that the dashboard stopped...
  [Step 6] Escalation → ⚠ ESCALATED
           → Escalation keyword detected: 'multiple users affected'

============================================================
  ✓ All 5 requests processed successfully!
  ✓ Results saved to: ./output/results.json
============================================================

  ⚠ 2 record(s) escalated for human review:
    - REQ-003: Billing discrepancy of $260 exceeds $100 threshold
    - REQ-005: Escalation keyword detected: 'multiple users affected'

  Routing Summary:
    REQ-001 → Engineering
    REQ-002 → Product
    REQ-003 → Human Review — Escalation ⚠
    REQ-004 → IT/Security
    REQ-005 → Human Review — Escalation ⚠
```

---

### Mode 2: Watcher Mode (auto-processes new files)

```bash
python arcvault_workflow.py --watch
```

This monitors the `inbox/` folder. When you drop a `.txt` file in, it processes it automatically.

#### How to Test Watcher Mode

1. Start the watcher:
```bash
python arcvault_workflow.py --watch
```

2. In a separate terminal or File Explorer, copy a demo message into the inbox:
```bash
# Windows PowerShell
copy demo_messages\msg5_outage.txt inbox\
```

3. Watch the terminal — it picks up the file and processes it automatically:

#### Expected Output

```
============================================================
  ArcVault AI Intake Pipeline
  Model: gpt-4o-mini
  Mode: Watcher
============================================================
👀 Watching ./inbox/ for new .txt files...
   Drop a .txt file into the inbox folder to process it.
   Press Ctrl+C to stop.

📨 New file detected: ./inbox\msg5_outage.txt

============================================================
  Processing REQ-msg5_outage (File Drop)
============================================================
  [Step 1] Ingested ✓
  [Step 2] Classified → Incident/Outage | High | Confidence: 0.96
  [Step 3] Enriched  → Dashboard is non-functional for multiple users...
  [Step 4] Routed    → Engineering (Urgent)
  [Step 5] Summary   → A customer reports that the dashboard stopped...
  [Step 6] Escalation → ⚠ ESCALATED
           → Escalation keyword detected: 'multiple users affected'

  ✓ Result appended to ./output/results.json
```

4. Press `Ctrl+C` to stop the watcher.

---

## Project Structure

```
ArcVault/
├── arcvault_workflow.py       # Main pipeline — all 6 steps
├── prompt_documentation.md    # LLM prompts + design rationale
├── architecture_writeup.md    # System design write-up
├── README.md                  # This file
├── .gitignore                 # Keeps secrets out of GitHub
├── demo_messages/             # Sample .txt files for watcher mode
│   ├── msg1_email.txt
│   ├── msg2_webform.txt
│   ├── msg3_billing.txt
│   ├── msg4_sso.txt
│   └── msg5_outage.txt
├── inbox/                     # Drop .txt files here in watcher mode
└── output/
    └── results.json           # Generated output (all processed records)
```

---

## Pipeline Flow

```
Customer message arrives
        │
   [Step 1] Ingestion — stamp with timestamp and source
        │
   [Step 2] Classification — AI assigns category, priority, confidence
        │
   [Step 3] Enrichment — AI extracts entities, amounts, urgency
        │
   [Step 4] Routing — is confidence below 70%?
        │          ├── YES → General Support (human triages)
        │          └── NO  → match category to team
        │
   [Step 5] Summary — AI writes actionable 2-3 sentence summary
        │
   [Step 6] Escalation — check two rules:
                ├── Escalation keyword found? → Human Review
                ├── Billing discrepancy > $100? → Human Review
                └── Neither? → stays with the routed team
        │
   ✅ Saved to output/results.json
```

---

## Routing Map

| Category | Destination Team |
|---|---|
| Bug Report | Engineering |
| Feature Request | Product |
| Billing Issue | Billing |
| Technical Question | IT/Security |
| Incident/Outage | Engineering (Urgent) |
| Low confidence (< 70%) | General Support |

---

## Tools Used

| Tool | Why I Chose It |
|---|---|
| **Python 3.12** | Routing and escalation logic involve math and conditionals — cleaner in code than a visual builder like n8n. Single file is easy to review and run. |
| **OpenAI GPT-4o-mini** | Best cost/speed/accuracy ratio for classification. ~$0.15 per million tokens, under 1 second per call. |
| **watchdog** | Monitors inbox folder for new files — simulates a webhook or email trigger. |

---

## Deliverables

| # | Assessment Requirement | File |
|---|---|---|
| 4.1 | Working Workflow | `arcvault_workflow.py` + Loom recording |
| 4.2 | Structured Output | `output/results.json` |
| 4.3 | Prompt Documentation | `prompt_documentation.md` |
| 4.4 | Architecture Write-Up | `architecture_writeup.md` |
