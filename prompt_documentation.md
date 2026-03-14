# Prompt Documentation — ArcVault Intake Pipeline

## Overview

The pipeline uses three LLM calls per request. Each prompt is designed to do one thing well, keeping outputs focused and parseable. All prompts enforce JSON-only output to avoid parsing failures from conversational preamble.

---

## Prompt 1: Classification

### The Prompt

```
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

Return ONLY valid JSON. No explanation, no markdown fences.
```

### Design Rationale

I structured this prompt with explicit enum constraints for both category and priority to eliminate ambiguity — the model can only pick from the defined set, which makes downstream routing deterministic. The priority rules are ordered by severity (High first) so the model encounters the most important criteria before lower ones, reducing the chance it defaults to "Medium" for everything.

I included brief distinguishing definitions for each category because several overlap in practice. For example, a 403 error could be classified as a "Bug Report" or a "Technical Question" depending on framing — the "was working and is now broken" criterion for Bug Report resolves that ambiguity. The confidence score is self-reported by the model, which is imperfect but serves as a useful routing signal — messages with confidence below 70% are routed to General Support for human triage rather than risking a misroute to the wrong specialist team. With more time, I would calibrate confidence by running the classifier against a labeled test set and adjusting the 0.7 threshold based on observed precision/recall.

---

## Prompt 2: Enrichment

### The Prompt

```
You are an entity extraction system for ArcVault customer support.

Given the customer message and its classification, extract structured data.
Return a JSON object with exactly these fields:
- "core_issue": a single sentence summarizing the problem or request
- "identifiers": an object containing any relevant IDs found (account_id, invoice_number, error_code, url, product_area, etc.). Use null for fields not present.
- "urgency_signal": one of ["immediate", "time-sensitive", "routine"] based on message tone and content
- "mentioned_amounts": any dollar amounts mentioned (as a list of numbers), or empty list
- "temporal_references": any time/date references mentioned (as a list of strings), or empty list

Return ONLY valid JSON. No explanation, no markdown fences.
```

### Design Rationale

This prompt receives the classification output as context so the model can use the category to guide extraction — for example, knowing something is a "Billing Issue" cues the model to look harder for invoice numbers and dollar amounts. I separated enrichment from classification into its own LLM call because combining them into a single mega-prompt degraded accuracy in testing: the model would rush through extraction to get to the classification, or vice versa.

The "identifiers" field uses a flexible object rather than a fixed schema because the set of relevant IDs varies wildly by message type (error codes for bugs, invoice numbers for billing, product areas for feature requests). Null values for absent fields keep the schema consistent without hallucinating data. The "mentioned_amounts" field exists specifically to feed the billing discrepancy escalation rule — extracting raw numbers lets the routing logic do math without parsing sentences. With more time, I would add a second-pass validation step that checks extracted entities against known patterns (e.g., invoice numbers matching a regex).

---

## Prompt 3: Summary Generation

### The Prompt

```
You are writing a 2-3 sentence summary of a customer request for the internal team that will handle it.
The summary should be actionable — tell the receiving team what happened and what they need to do.

Classification: {category} | Priority: {priority}
Routing to: {queue}

Customer message: ...

Extracted entities: ...

Write only the summary, nothing else. No JSON, no labels — just 2-3 plain sentences.
```

### Design Rationale

This is the only prompt that produces free text rather than JSON. I made it the final LLM call because it can incorporate all upstream data (classification, routing destination, extracted entities) to produce a context-rich summary. The instruction "tell the receiving team what happened and what they need to do" is deliberate — it forces the model to produce actionable output rather than a passive restatement of the message.

I include the routing destination in the prompt context so the summary can address the correct team ("Engineering should investigate..." vs. "Billing should verify..."). The 2-3 sentence constraint prevents the model from over-explaining. With more time, I would template these summaries per queue — Engineering summaries would emphasize reproduction steps and error codes, while Billing summaries would emphasize amounts and contract references.

---

## General Prompt Design Decisions

**Temperature 0.1**: I use very low temperature across all calls. Classification and extraction need determinism — running the same input twice should produce the same output. A slight non-zero value avoids degenerate repetition patterns that sometimes occur at temperature 0.

**Separate calls vs. single call**: I chose three separate LLM calls instead of one combined prompt. This costs more in latency (~3x) but produces significantly better results because each call has a focused task. In production, I would parallelize the enrichment and classification calls since they don't depend on each other, then run the summary call last.

**Model choice (GPT-4o-mini)**: Fast, cheap (~$0.15/M input tokens), and accurate for structured output tasks. It handles JSON formatting reliably without needing function calling or structured output mode. For production, I would benchmark against Claude Haiku and Llama 3.3 70B (via Groq) on a labeled dataset to find the best cost/accuracy tradeoff.
