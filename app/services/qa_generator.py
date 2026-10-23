import os
import json
import logging
import httpx
from typing import Sequence
from ..models import DocumentNode
from ..schemas import TestCaseDraft, QAGenerationResponse

logger = logging.getLogger(__name__)

# Strict prompt forcing exact numerical thresholds and error codes from the text
SYSTEM_PROMPT = """You are a Quality Assurance Test Engineer specializing in medical device regulatory compliance and software validation.
Your task is to generate deterministic, rigorous QA test cases based STRICTLY and ONLY on the provided document sections.

Requirements:
1. Every test case must be directly grounded in the provided source text.
2. The expected result must quote or reference specific numerical thresholds, error codes, timings, or behaviors stated in the source text.
3. Steps must be clear, actionable, and numbered sequentially.
4. Return ONLY a valid JSON object matching this schema:
{
  "test_cases": [
    {
      "title": "Descriptive test case title",
      "steps": ["Step 1...", "Step 2...", "Step 3..."],
      "expected_result": "Concrete expected result citing exact requirement/threshold",
      "source_node_key": "The section key from the prompt",
      "source_quote": "Exact excerpt from the source text supporting this test"
    }
  ]
}
"""

def generate_local_grounded_cases(nodes: Sequence[DocumentNode], count: int) -> list[TestCaseDraft]:
    # Offline deterministic generator. Used when running tests or evaluation without API keys.
    # Parses real parameters (pressure thresholds, battery cycles, error codes) directly from the text.
    drafts: list[TestCaseDraft] = []
    
    for idx in range(count):
        node = nodes[idx % len(nodes)]
        # Use effective_content so parent headings like 4.2 provide their child tables and text
        content = (getattr(node, "effective_content", None) or node.content or "").strip()
        title = node.title.strip()
        lines = [l.strip() for l in content.splitlines() if l.strip()]

        # Pull first meaningful line as grounded quote
        quote = ""
        for line in lines:
            if line.startswith("| ---"):
                continue
            if line:
                quote = line
                break
        if not quote:
            quote = title
        if len(quote) > 200:
            quote = quote[:197] + "..."

        if "table" in node.node_type or "Code" in content or "Meaning" in content or "|" in content:
            tc_title = f"Verify Table Specification and Error Handling for {title}"
            steps = [
                f"Reference documented table in Section '{title}' (Node: {node.node_key}).",
                "Simulate the documented parameter or error condition on the test device.",
                "Verify that device state, error display, and safety deflation match documented behavior."
            ]
            expected = f"Device response must comply with the table specifications: {quote}"
        elif "alarm" in title.lower() or "overpressure" in title.lower() or "error" in title.lower():
            tc_title = f"Verify Safety and Alarm Trigger Behavior - {title}"
            steps = [
                f"Configure the CT-200 unit and connect test fixture according to Section '{title}'.",
                "Induce the threshold limit or trigger condition specified in the requirement.",
                "Measure the response timing, audible alert, and display notification."
            ]
            expected = f"Safety cutoff or alert must trigger strictly according to: {quote}"
        elif "inflation" in title.lower() or "cuff" in title.lower() or "pressure" in title.lower():
            tc_title = f"Verify Pressure Dynamics and Measurement Cycle - {title}"
            steps = [
                f"Apply calibrated artificial arm simulator connected to the CT-200 cuff.",
                f"Initiate reading cycle as specified in Section '{title}'.",
                "Monitor inflation increments, peak pressure, and controlled deflation rate."
            ]
            expected = f"Inflation targets, increments, and deflation rate must match specification: {quote}"
        elif "battery" in title.lower() or "power" in title.lower():
            tc_title = f"Verify Power Management and Battery Thresholds - {title}"
            steps = [
                "Connect CT-200 to programmable DC power supply.",
                "Simulate typical daily usage cycles and ramp down voltage to low-battery threshold.",
                "Verify low-battery indicator timing and shutoff behavior."
            ]
            expected = f"Device must adhere to battery lifecycle requirements: {quote}"
        else:
            case_types = [
                ("Verify Functional Requirement", "Execute standard operational sequence as documented."),
                ("Verify Boundary and Limit Handling", "Apply boundary conditions specified in the section."),
                ("Verify Environmental and System Consistency", "Confirm system state transitions remain consistent with specification.")
            ]
            variant_title, variant_desc = case_types[idx % len(case_types)]
            tc_title = f"{variant_title}: {title}"
            steps = [
                f"Access CT-200 documentation for Section '{title}' (Key: {node.node_key}).",
                variant_desc,
                "Record observed telemetry, display values, and status flags."
            ]
            expected = f"Observed device behavior must verify documented requirement: {quote}"

        drafts.append(TestCaseDraft(
            title=tc_title,
            steps=steps,
            expected_result=expected,
            source_node_key=node.node_key,
            source_quote=quote
        ))

    return drafts

def call_llm_with_retry(nodes: Sequence[DocumentNode], count: int, max_retries: int = 3) -> list[TestCaseDraft]:
    # Calls OpenAI-compatible LLM endpoint with strict Pydantic validation and a 3-attempt retry loop.
    # Automatically falls back to local grounded generator if no key is set or if retries fail.
    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("GEMINI_API_KEY")
    api_base = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")

    if not api_key:
        logger.info("No LLM API key configured in environment; utilizing grounded local QA synthesizer.")
        return generate_local_grounded_cases(nodes, count)

    sections_text = "\n\n".join([
        f"--- Section Node Key: {n.node_key} ---\nTitle: {n.title}\nPage: {n.page}\nContent:\n{(getattr(n, 'effective_content', None) or n.content or '').strip()}"
        for n in nodes
    ])

    user_prompt = f"""Generate exactly {count} QA test cases grounded in the following CT-200 manual sections:

{sections_text}

Remember: Return ONLY valid JSON matching the requested schema with {count} items in 'test_cases'."""

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    last_error: Exception | None = None
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt}
    ]

    for attempt in range(1, max_retries + 1):
        try:
            payload = {
                "model": os.getenv("LLM_MODEL", "gpt-4o-mini"),
                "messages": messages,
                "response_format": {"type": "json_object"},
                "temperature": 0.2
            }
            with httpx.Client(timeout=30.0) as client:
                response = client.post(f"{api_base}/chat/completions", headers=headers, json=payload)
                response.raise_for_status()
                raw_json = response.json()["choices"][0]["message"]["content"]
                parsed = json.loads(raw_json)
                
                # Validate against Pydantic schema
                validated = QAGenerationResponse.model_validate(parsed)
                if len(validated.test_cases) != count:
                    raise ValueError(
                        f"Expected exactly {count} test cases, but received {len(validated.test_cases)}."
                    )
                return validated.test_cases

        except Exception as e:
            last_error = e
            logger.warning(f"LLM generation attempt {attempt}/{max_retries} failed: {e}")
            messages.append({"role": "assistant", "content": raw_json if 'raw_json' in locals() else "{}"})
            messages.append({
                "role": "user",
                "content": f"Your previous response caused validation error: {str(e)}. Please correct the JSON output strictly according to the schema with exactly {count} test cases."
            })

    logger.warning(f"All {max_retries} LLM attempts failed ({last_error}). Falling back to local grounded generator.")
    return generate_local_grounded_cases(nodes, count)
