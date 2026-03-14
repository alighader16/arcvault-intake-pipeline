# Architecture Write-Up — ArcVault AI Intake Pipeline

## System Design

The pipeline is a sequential, six-step workflow implemented as a single Python application with two operating modes: **batch mode** (processes all inputs at once) and **watcher mode** (monitors an inbox folder for new files and processes them on arrival).

### How the Pieces Connect

```
[Trigger] → [Ingestion] → [Classification] → [Enrichment] → [Routing] → [Summary] → [Escalation Check] → [Output File]
   │                           │                   │                                       │
   │                        LLM Call #1         LLM Call #2                             LLM Call #3
   │                                                                                       │
   ├── Batch: hardcoded list                                                               ▼
   └── Watcher: filesystem event                                                    results.json
```

**Trigger mechanism**: In batch mode, the five sample requests are defined as a Python list and processed sequentially. In watcher mode, the `watchdog` library monitors `./inbox/` for new `.txt` files, reads their content, and feeds them into the same pipeline. This simulates a webhook or email trigger — the key design constraint is that the workflow starts automatically without manual intervention.

**State**: All intermediate state lives in a Python dictionary that gets built up as it passes through each step. There is no database, no message queue, no external state store. The final result is appended to a single `results.json` file. This is appropriate for a prototype; production would need durable state (see below).

**LLM calls**: Three calls to OpenAI's API per request — classification, enrichment, and summary. Each is independent except that enrichment receives the classification output as context, and the summary receives everything. Temperature is set to 0.1 for determinism.

---

## Routing Logic

Routing maps the LLM-assigned category to a destination queue using a static lookup table:

| Category           | Queue                 |
|--------------------|-----------------------|
| Bug Report         | Engineering           |
| Feature Request    | Product               |
| Billing Issue      | Billing               |
| Technical Question | IT/Security           |
| Incident/Outage    | Engineering (Urgent)  |

**Why these mappings**: Bug reports and outages both go to Engineering because engineers own code fixes, but outages get a separate "Urgent" designation to signal immediate attention. Technical questions about auth/SSO go to IT/Security because those typically require infrastructure-level answers. Feature requests go to Product because they own the backlog.

**Fallback**: If the model's confidence score is below 0.70, the request routes to "General Support" instead of the category-matched queue. The rationale is that a low-confidence classification is more likely to be wrong, and sending a misclassified ticket to the wrong specialized team wastes more time than routing to a generalist queue that can manually triage.

---

## Escalation Logic

A record is flagged for human review and rerouted to "Human Review — Escalation" if any of these criteria are met:

I deliberately separated low-confidence handling from escalation. These are two different problems:

- **Low confidence** means the AI isn't sure what the message is. The right response is to send it to **General Support** — a human triages it manually. This happens in Step 4 (Routing), not Step 6.
- **Escalation** means the message is genuinely urgent or sensitive, regardless of how confident the AI is. The right response is to flag it for **Human Review** with priority attention.

Mixing these two into the same queue would flood the escalation team with messages that are just ambiguous, diluting their attention from truly urgent issues.

The two escalation rules are:

1. **Keyword match**: The message contains terms like "outage," "down for all users," or "multiple users affected." These indicate high-impact incidents that should always get human eyes regardless of classification confidence. The keyword list is deliberately conservative — false positives (unnecessary escalation) are preferable to false negatives (missed outages).

2. **Billing discrepancy over $100**: If two or more dollar amounts are extracted and the difference exceeds $100, the record escalates. This catches overcharge complaints that could have financial or legal implications. The $100 threshold is low intentionally — billing errors erode trust quickly, and it's better to over-escalate.

When escalation fires, it **overrides** the standard routing destination. A record that would have gone to Engineering goes to Human Review instead. The escalation reasons are preserved in the output so the reviewer knows why it was flagged.

---

## What I Would Do Differently at Production Scale

**Reliability**: Replace the Python script with a proper workflow orchestrator — n8n or Temporal. The current pipeline has no retry logic beyond the LLM call itself; if the script crashes between step 3 and step 4, the record is lost. A production system needs durable task queues, idempotent steps, and dead-letter handling.

**Cost**: Three LLM calls per ticket is expensive at scale. I would consolidate classification and enrichment into a single call with a well-structured output schema, reducing to two calls per ticket. For high-volume periods, batch requests and use a cheaper model (GPT-4o-mini is already cheap, but a fine-tuned smaller model could be even cheaper and faster).

**Latency**: The current pipeline is sequential — each step waits for the previous one. Classification and enrichment could run in parallel since enrichment only uses classification as a hint, not a hard dependency. With async calls, wall-clock time per ticket drops from ~3 seconds to ~2 seconds.

**Confidence calibration**: The self-reported confidence score is unreliable. In production, I would calibrate it by running the classifier against a labeled test set of 200+ tickets, measuring actual accuracy at each confidence bucket, and adjusting the 0.70 threshold to the point where precision drops below an acceptable level (e.g., 90%).

**Observability**: Add logging for every LLM call (input, output, latency, token count), track classification distribution over time (a sudden spike in "Incident/Outage" could indicate a real outage), and alert on escalation rate exceeding a threshold.

---

## Phase 2 — What I Would Add With Another Week

1. **Feedback loop**: Let human reviewers correct misclassifications and feed those corrections back into a fine-tuning dataset. After 500+ corrections, fine-tune a small model specifically for ArcVault's ticket types. This reduces cost and improves accuracy simultaneously.

2. **Duplicate detection**: Hash incoming messages and compare against recent tickets to detect duplicates or related reports. If five users report the same dashboard outage, they should be grouped into a single incident, not five separate tickets.

3. **Auto-response drafting**: For high-confidence, low-priority tickets (e.g., feature requests, simple technical questions), generate a draft response for the agent to review and send. This cuts response time from hours to minutes for routine requests.

4. **SLA tracking**: Assign SLA deadlines based on priority (High = 1 hour, Medium = 4 hours, Low = 24 hours) and trigger alerts as deadlines approach. The pipeline already has the priority; it just needs a timer.

5. **Multi-language support**: Detect language in the ingestion step and route non-English tickets to a translation step before classification. The LLM can handle classification in most languages, but entity extraction accuracy drops — a dedicated translation step preserves quality.
