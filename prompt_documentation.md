# Prompt Documentation — ArcVault Intake Pipeline

## My Process

I wrote the initial prompts myself based on what I thought the model needed to know, then used GPT to help me refine the wording and tighten the output formatting. The core logic and rules are mine — the AI helped me clean up the structure so the model would return consistent JSON every time. Below are the final versions with my reasoning.

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

Return ONLY valid JSON. No explanation, no markdown fences, no extra text.
```

### Why I Wrote It This Way

The first version of this prompt was much simpler — I just said "classify this message into a category and priority." The problem was the model kept inventing its own categories (like "Account Issue" or "Urgent Bug") and the priority was inconsistent. So I locked it down with exact lists it has to pick from.

The category definitions were the part I spent the most time on. A 403 login error could easily be a "Bug Report" or a "Technical Question" depending on how you look at it. I added "was working and is now broken" to Bug Report specifically to handle that edge case — if it was working before, it's a bug, not a question.

The confidence score is self-reported by the model, which isn't ideal — the model tends to be overconfident. But it still gives us a useful signal: anything below 0.7 is different enough from the rest that it's worth sending to a human for triage. If I had more time, I'd run this against a labeled test set and see where the model actually starts getting things wrong, then calibrate the threshold properly.

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

Return ONLY valid JSON. No explanation, no markdown fences, no extra text.
```

### Why I Wrote It This Way

I originally tried to do classification and enrichment in one prompt. It didn't work well — the model would get lazy about entity extraction when it was also trying to classify, or it would focus so much on pulling out details that the category would come back wrong. Splitting them into two calls fixed that immediately.

I feed the classification result into this prompt as context ("this is a Billing Issue") because it helps the model know what to look for. When it knows it's dealing with a billing issue, it pays more attention to dollar amounts and invoice numbers instead of looking for error codes.

The "mentioned_amounts" field exists for a very specific reason — the escalation rule needs raw numbers to calculate billing discrepancies. If I just had the model write "there's a $260 difference," my code can't do math on a sentence. By extracting amounts as a list of numbers, the code can do `max - min` and check if it's over $100.

The "identifiers" field is intentionally flexible (not a fixed schema) because different message types have completely different identifiers. A bug report has error codes, a billing issue has invoice numbers, a technical question might reference a third-party tool like Okta. Trying to force all of these into one rigid structure would either miss things or make the model hallucinate fields that don't exist.

---

## Prompt 3: Summary Generation

### The Prompt

```
You are writing a 2-3 sentence summary of a customer request for the internal team that will handle it.
The summary should be actionable — tell the receiving team what happened and what they need to do.

Classification: {category} | Priority: {priority}
Routing to: {queue}

Customer message: [message here]

Extracted entities: [entities here]

Write only the summary, nothing else. No JSON, no labels — just 2-3 plain sentences.
```

### Why I Wrote It This Way

This is the only prompt that returns plain text instead of JSON. I put it last in the pipeline because by this point we already have all the context — category, priority, routing destination, extracted entities — so the summary can be rich and specific.

The key word in this prompt is "actionable." My first version produced summaries like "The customer has a billing concern regarding invoice #8821." That's useless — it just restates the problem without telling anyone what to do. When I added "tell the receiving team what they need to do," the output changed to things like "Billing should verify the contract terms and issue a correction." That's something someone can actually act on without reading the original message.

I also pass the routing destination into the prompt so the summary addresses the right team. If it's going to Engineering, the summary talks about investigating the issue. If it's going to Billing, it talks about verifying contracts and issuing credits. The summary feels tailored because it is.

With more time, I'd create different summary templates per team — Engineering summaries would emphasize reproduction steps and error codes, Billing summaries would focus on amounts and contract references, Product summaries would highlight user impact and business value.

---

## General Notes

**Temperature 0.1**: I use low temperature because I need consistency. Running the same message twice should give the same classification. I didn't go all the way to 0 because that sometimes causes weird repetition issues, so 0.1 is the sweet spot.

**Three calls vs one**: Yes it's slower and costs more per message. But each call does one job well instead of one call doing three jobs badly. In production I'd run classification and enrichment in parallel since they don't actually depend on each other — that would cut total time by about a third.

**Model choice (GPT-4o-mini)**: Best balance of cost, speed, and accuracy for structured tasks. About $0.15 per million input tokens, responds in under a second, and handles JSON formatting without issues. GPT-4o would work too but it's overkill — like using a sports car to get groceries.
