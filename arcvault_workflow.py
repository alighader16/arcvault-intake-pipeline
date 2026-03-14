"""
ArcVault AI-Powered Intake & Triage Pipeline
=============================================
Processes unstructured customer requests through 6 steps:
  1. Ingestion      — Accept raw message
  2. Classification  — Categorize via LLM
  3. Enrichment     — Extract entities via LLM
  4. Routing        — Map to destination queue
  5. Summary        — Generate human-readable summary via LLM
  6. Escalation     — Flag for human review if needed

Requirements:
    pip install openai watchdog

Usage:
    # Process all 5 sample inputs at once:
    python arcvault_workflow.py

    # Watch inbox folder for new .txt files:
    python arcvault_workflow.py --watch
"""

import os
import json
import time
import re
from datetime import datetime, timezone
from openai import OpenAI

# ============================================================================
# CONFIGURATION — Change these values as needed
# ============================================================================

# Your OpenAI API key — set it here OR as an environment variable
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "YOUR_KEY_HERE")

# Model — GPT-4o-mini is cheap (~$0.15 per million input tokens), fast, and
# accurate enough for classification tasks. We don't need GPT-4o for this.
MODEL = "gpt-4o-mini"

# Folders
INBOX_DIR = "./inbox"
OUTPUT_DIR = "./output"
RESULTS_FILE = os.path.join(OUTPUT_DIR, "results.json")

# Escalation settings
ESCALATION_KEYWORDS = [
    "outage",
    "down for all users",
    "down for multiple users",
    "multiple users affected",
    "all users affected",
]
BILLING_DISCREPANCY_THRESHOLD = 100  # dollars — flag if overcharge exceeds this

# Queue mapping — each category goes to a specific team
QUEUE_MAP = {
    "Bug Report":          "Engineering",
    "Feature Request":     "Product",
    "Billing Issue":       "Billing",
    "Technical Question":  "IT/Security",
    "Incident/Outage":     "Engineering (Urgent)",
}
FALLBACK_QUEUE = "General Support"              # Used when confidence is low
ESCALATION_QUEUE = "Human Review — Escalation"  # Used when escalation triggers


# ============================================================================
# SAMPLE INPUTS — The 5 test messages from the assessment
# ============================================================================

SAMPLE_REQUESTS = [
    {
        "id": "REQ-001",
        "source": "Email",
        "raw_message": (
            "Hi, I tried logging in this morning and keep getting a 403 error. "
            "My account is arcvault.io/user/jsmith. This started after your "
            "update last Tuesday."
        ),
    },
    {
        "id": "REQ-002",
        "source": "Web Form",
        "raw_message": (
            "We'd love to see a bulk export feature for our audit logs. We're "
            "a compliance-heavy org and this would save us hours every month."
        ),
    },
    {
        "id": "REQ-003",
        "source": "Support Portal",
        "raw_message": (
            "Invoice #8821 shows a charge of $1,240 but our contract rate is "
            "$980/month. Can someone look into this?"
        ),
    },
    {
        "id": "REQ-004",
        "source": "Email",
        "raw_message": (
            "I'm not sure if this is the right place to ask, but is there a "
            "way to set up SSO with Okta? We're evaluating switching our auth "
            "provider."
        ),
    },
    {
        "id": "REQ-005",
        "source": "Web Form",
        "raw_message": (
            "Your dashboard stopped loading for us around 2pm EST. Checked our "
            "end — it's definitely on yours. Multiple users affected."
        ),
    },
]


# ============================================================================
# PROMPTS — These are the instructions we send to the AI model
# ============================================================================

CLASSIFICATION_PROMPT = """\
You are a support ticket classifier for ArcVault, a B2B SaaS company.

Analyze the customer message below and return a JSON object with exactly these fields:
- "category": one of ["Bug Report", "Feature Request", "Billing Issue", "Technical Question", "Incident/Outage"]
- "priority": one of ["Low", "Medium", "High"]
- "confidence_score": a number between 0.0 and 1.0 representing how certain you are

Rules for priority:
- High: service outages affecting multiple users, billing overcharges, security issues
- Medium: bugs blocking a single user, urgent feature needs, login failures
- Low: general questions, nice-to-have feature requests, informational inquiries

Rules for category:
- Bug Report: something that was working and is now broken for a specific user
- Feature Request: a request for new functionality that does not exist yet
- Billing Issue: anything related to invoices, charges, payments, or pricing
- Technical Question: a question about how to configure or use a feature
- Incident/Outage: a service-wide or multi-user disruption

Return ONLY valid JSON. No explanation, no markdown fences, no extra text.

Customer message:
\"\"\"
{message}
\"\"\"
"""

ENRICHMENT_PROMPT = """\
You are an entity extraction system for ArcVault customer support.

Given the customer message and its classification, extract structured data.
Return a JSON object with exactly these fields:
- "core_issue": a single sentence summarizing the problem or request
- "identifiers": an object containing any relevant IDs found (account_id, invoice_number, error_code, url, product_area, etc.). Use null for fields not present.
- "urgency_signal": one of ["immediate", "time-sensitive", "routine"] based on message tone and content
- "mentioned_amounts": any dollar amounts mentioned (as a list of numbers), or empty list
- "temporal_references": any time/date references mentioned (as a list of strings), or empty list

Return ONLY valid JSON. No explanation, no markdown fences, no extra text.

Classification: {classification}

Customer message:
\"\"\"
{message}
\"\"\"
"""

SUMMARY_PROMPT = """\
You are writing a 2-3 sentence summary of a customer request for the internal team that will handle it.
The summary should be actionable — tell the receiving team what happened and what they need to do.

Classification: {category} | Priority: {priority}
Routing to: {queue}

Customer message:
\"\"\"
{message}
\"\"\"

Extracted entities:
{entities}

Write only the summary, nothing else. No JSON, no labels — just 2-3 plain sentences.
"""


# ============================================================================
# LLM INTERACTION — How we talk to OpenAI
# ============================================================================

# Create the OpenAI client — this handles all communication with the API
client = OpenAI(api_key=OPENAI_API_KEY)


def call_llm(prompt: str, max_retries: int = 2) -> str:
    """
    Send a prompt to GPT-4o-mini and get the response text back.

    What this does:
    - Sends your prompt to OpenAI's API
    - Uses temperature=0.1 for consistent, repeatable results
    - Retries up to 2 times if something goes wrong (network issues, etc.)

    Args:
        prompt: The text to send to the model
        max_retries: How many times to retry on failure

    Returns:
        The model's response as a string
    """
    for attempt in range(max_retries + 1):
        try:
            response = client.chat.completions.create(
                model=MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,   # Low = more consistent/deterministic
                max_tokens=1024,   # Max length of response
            )
            # The response has a specific structure:
            # response.choices[0].message.content is where the text lives
            return response.choices[0].message.content.strip()

        except Exception as e:
            if attempt < max_retries:
                # Wait before retrying (1s, then 2s — exponential backoff)
                wait_time = 2 ** attempt
                print(f"    ⚠ API call failed, retrying in {wait_time}s... ({e})")
                time.sleep(wait_time)
                continue
            raise RuntimeError(
                f"LLM call failed after {max_retries + 1} attempts: {e}"
            )


def parse_json_response(text: str) -> dict:
    """
    Parse JSON from the model's response.

    Sometimes the model wraps JSON in ```json ... ``` markdown fences
    even when we tell it not to. This function strips those fences
    before parsing.

    Args:
        text: Raw text from the model

    Returns:
        Parsed Python dictionary
    """
    # Remove markdown code fences if present
    cleaned = re.sub(r"^```(?:json)?\s*", "", text.strip())
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return json.loads(cleaned)


# ============================================================================
# PIPELINE STEPS — Each function is one step of the workflow
# ============================================================================

def step1_ingest(request: dict) -> dict:
    """
    STEP 1 — INGESTION
    Accept the raw message and tag it with metadata (timestamp).

    This is the entry point. In production, this would be triggered by
    a webhook, email listener, or form submission. Here we just take
    the message and add a timestamp.
    """
    return {
        "id": request["id"],
        "source": request["source"],
        "raw_message": request["raw_message"],
        "ingested_at": datetime.now(timezone.utc).isoformat(),
    }


def step2_classify(record: dict) -> dict:
    """
    STEP 2 — CLASSIFICATION
    Send the message to the LLM and get back:
      - category (Bug Report, Feature Request, etc.)
      - priority (Low, Medium, High)
      - confidence_score (0.0 to 1.0)

    This is LLM Call #1 of 3.
    """
    prompt = CLASSIFICATION_PROMPT.format(message=record["raw_message"])
    raw_response = call_llm(prompt)
    classification = parse_json_response(raw_response)

    record["category"] = classification["category"]
    record["priority"] = classification["priority"]
    record["confidence_score"] = round(float(classification["confidence_score"]), 2)

    return record


def step3_enrich(record: dict) -> dict:
    """
    STEP 3 — ENRICHMENT
    Extract structured entities from the message:
      - core_issue: one-sentence summary
      - identifiers: account IDs, invoice numbers, error codes, etc.
      - urgency_signal: immediate / time-sensitive / routine
      - mentioned_amounts: dollar values
      - temporal_references: dates and times mentioned

    This is LLM Call #2 of 3.
    We pass the classification from Step 2 as context so the model
    knows what type of message it's looking at.
    """
    classification_context = json.dumps({
        "category": record["category"],
        "priority": record["priority"],
    })
    prompt = ENRICHMENT_PROMPT.format(
        message=record["raw_message"],
        classification=classification_context,
    )

    raw_response = call_llm(prompt)
    enrichment = parse_json_response(raw_response)

    record["core_issue"] = enrichment.get("core_issue", "")
    record["identifiers"] = enrichment.get("identifiers", {})
    record["urgency_signal"] = enrichment.get("urgency_signal", "routine")
    record["mentioned_amounts"] = enrichment.get("mentioned_amounts", [])
    record["temporal_references"] = enrichment.get("temporal_references", [])

    return record


def step4_route(record: dict) -> dict:
    """
    STEP 4 — ROUTING DECISION
    Map the classification to a destination queue.

    Logic:
    - If confidence < 70% → send to General Support (fallback)
    - Otherwise → look up the category in QUEUE_MAP

    Why 70%? In testing, classifications below 0.7 were often wrong.
    It's safer to send uncertain tickets to a generalist queue than
    to the wrong specialist team.
    """
    category = record.get("category", "")
    confidence = record.get("confidence_score", 0)

    if confidence < 0.7:
        record["routed_to"] = FALLBACK_QUEUE
        record["routing_note"] = (
            f"Low confidence ({confidence}) — routed to fallback queue"
        )
    else:
        record["routed_to"] = QUEUE_MAP.get(category, FALLBACK_QUEUE)
        record["routing_note"] = f"Matched category '{category}' to queue"

    return record


def step5_generate_summary(record: dict) -> dict:
    """
    STEP 5 — STRUCTURED OUTPUT (Summary)
    Generate a 2-3 sentence human-readable summary for the receiving team.

    This is LLM Call #3 of 3.
    We pass all previous data (category, priority, queue, entities)
    so the summary is as helpful as possible.
    """
    prompt = SUMMARY_PROMPT.format(
        category=record["category"],
        priority=record["priority"],
        queue=record["routed_to"],
        message=record["raw_message"],
        entities=json.dumps(record.get("identifiers", {}), indent=2),
    )

    record["summary"] = call_llm(prompt)
    return record


def step6_escalation_check(record: dict) -> dict:
    """
    STEP 6 — HUMAN ESCALATION FLAG
    Check if this record needs human review. Two rules:

    Note: Low confidence is already handled in Step 4 — those messages
    go to General Support, NOT to escalation. Escalation is reserved
    for genuinely urgent situations:

    Rule 1: Escalation keywords detected
      → Words like "outage" or "multiple users affected" mean
        this could be a major incident regardless of confidence.

    Rule 2: Billing discrepancy over $100
      → If two dollar amounts are mentioned and the difference
        is > $100, it's likely an overcharge complaint.

    If ANY rule triggers, the record gets rerouted to the
    escalation queue and the reasons are logged.
    """
    reasons = []
    message_lower = record["raw_message"].lower()

    # Rule 1: Escalation keywords
    for keyword in ESCALATION_KEYWORDS:
        if keyword.lower() in message_lower:
            reasons.append(f"Escalation keyword detected: '{keyword}'")

    # Rule 2: Billing discrepancy
    amounts = record.get("mentioned_amounts", [])
    if len(amounts) >= 2:
        discrepancy = abs(max(amounts) - min(amounts))
        if discrepancy > BILLING_DISCREPANCY_THRESHOLD:
            reasons.append(
                f"Billing discrepancy of ${discrepancy} "
                f"exceeds ${BILLING_DISCREPANCY_THRESHOLD} threshold"
            )

    # Set the escalation flag and override routing if needed
    record["escalation_flag"] = len(reasons) > 0
    record["escalation_reasons"] = reasons

    if record["escalation_flag"]:
        record["routed_to"] = ESCALATION_QUEUE
        record["routing_note"] = f"Escalated — {'; '.join(reasons)}"

    # Mark as fully processed
    record["processed_at"] = datetime.now(timezone.utc).isoformat()

    return record


# ============================================================================
# ORCHESTRATOR — Runs all 6 steps in sequence
# ============================================================================

def process_request(request: dict) -> dict:
    """
    Run a single request through the full 6-step pipeline.
    Prints progress to the terminal so you can see what's happening.
    """
    print(f"\n{'='*60}")
    print(f"  Processing {request['id']} ({request['source']})")
    print(f"{'='*60}")

    record = step1_ingest(request)
    print(f"  [Step 1] Ingested ✓")

    record = step2_classify(record)
    print(f"  [Step 2] Classified → {record['category']} | "
          f"{record['priority']} | Confidence: {record['confidence_score']}")

    record = step3_enrich(record)
    print(f"  [Step 3] Enriched  → {record['core_issue'][:60]}...")

    record = step4_route(record)
    print(f"  [Step 4] Routed    → {record['routed_to']}")

    record = step5_generate_summary(record)
    print(f"  [Step 5] Summary   → {record['summary'][:60]}...")

    record = step6_escalation_check(record)
    status = "⚠ ESCALATED" if record["escalation_flag"] else "✓ Standard"
    print(f"  [Step 6] Escalation → {status}")
    if record["escalation_flag"]:
        for r in record["escalation_reasons"]:
            print(f"           → {r}")

    return record


def run_batch(requests: list) -> list:
    """
    Process a list of requests and save results to a JSON file.
    This is the main way to run the pipeline.
    """
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    results = []
    for req in requests:
        result = process_request(req)
        results.append(result)

    # Write all results to a JSON file
    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    # Print final summary
    print(f"\n{'='*60}")
    print(f"  ✓ All {len(results)} requests processed successfully!")
    print(f"  ✓ Results saved to: {RESULTS_FILE}")
    print(f"{'='*60}")

    # Show which ones were escalated
    escalated = [r for r in results if r["escalation_flag"]]
    if escalated:
        print(f"\n  ⚠ {len(escalated)} record(s) escalated for human review:")
        for r in escalated:
            print(f"    - {r['id']}: {', '.join(r['escalation_reasons'])}")

    # Show routing summary
    print(f"\n  Routing Summary:")
    for r in results:
        flag = " ⚠" if r["escalation_flag"] else ""
        print(f"    {r['id']} → {r['routed_to']}{flag}")

    return results


# ============================================================================
# FILE WATCHER — Alternative trigger that monitors a folder
# ============================================================================

def start_watcher():
    """
    Watch the ./inbox/ folder for new .txt files.
    When a file appears, read it and run it through the pipeline.

    This simulates a real trigger mechanism (like a webhook or email listener).
    To use: run the script with --watch, then drop .txt files into ./inbox/
    """
    try:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler
    except ImportError:
        print("'watchdog' not installed. Run: pip install watchdog")
        print("Falling back to batch mode...\n")
        return run_batch(SAMPLE_REQUESTS)

    class InboxHandler(FileSystemEventHandler):
        def on_created(self, event):
            if event.src_path.endswith(".txt"):
                print(f"\n📨 New file detected: {event.src_path}")
                time.sleep(0.5)  # Brief pause to let the file finish writing
                with open(event.src_path, "r", encoding="utf-8") as f:
                    content = f.read()

                filename = os.path.basename(event.src_path)
                request = {
                    "id": f"REQ-{filename.replace('.txt', '')}",
                    "source": "File Drop",
                    "raw_message": content,
                }

                result = process_request(request)

                # Append to results file
                existing = []
                if os.path.exists(RESULTS_FILE):
                    with open(RESULTS_FILE, "r", encoding="utf-8") as rf:
                        existing = json.load(rf)
                existing.append(result)
                os.makedirs(OUTPUT_DIR, exist_ok=True)
                with open(RESULTS_FILE, "w", encoding="utf-8") as wf:
                    json.dump(existing, wf, indent=2, ensure_ascii=False)
                print(f"\n  ✓ Result appended to {RESULTS_FILE}")

    os.makedirs(INBOX_DIR, exist_ok=True)
    observer = Observer()
    observer.schedule(InboxHandler(), INBOX_DIR, recursive=False)
    observer.start()
    print(f"👀 Watching {INBOX_DIR}/ for new .txt files...")
    print(f"   Drop a .txt file into the inbox folder to process it.")
    print(f"   Press Ctrl+C to stop.\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        print("\n\nWatcher stopped.")
    observer.join()


# ============================================================================
# MAIN — Entry point when you run the script
# ============================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="ArcVault AI Intake & Triage Pipeline"
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Watch the inbox folder for new files instead of batch mode",
    )
    args = parser.parse_args()

    print("\n" + "="*60)
    print("  ArcVault AI Intake Pipeline")
    print(f"  Model: {MODEL}")
    print(f"  Mode: {'Watcher' if args.watch else 'Batch'}")
    print("="*60)

    if args.watch:
        start_watcher()
    else:
        run_batch(SAMPLE_REQUESTS)
