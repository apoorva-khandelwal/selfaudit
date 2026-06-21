"""
Static model tradeoff lookup table.
Specs are factual and published — no claims about which model will succeed at a specific task.
"""

MODELS = [
    # ── Anthropic ──────────────────────────────────────────────────────────────
    {
        "id": "claude-opus-4-8",
        "provider": "Anthropic",
        "input_cost_per_1m": 5.00,
        "output_cost_per_1m": 25.00,
        "context_window_k": 1000,
        "notes": "Most capable. Best for complex reasoning.",
    },
    {
        "id": "claude-sonnet-4-6",
        "provider": "Anthropic",
        "input_cost_per_1m": 3.00,
        "output_cost_per_1m": 15.00,
        "context_window_k": 1000,
        "notes": "Strong balance of quality and cost.",
    },
    {
        "id": "claude-haiku-4-5",
        "provider": "Anthropic",
        "input_cost_per_1m": 1.00,
        "output_cost_per_1m": 5.00,
        "context_window_k": 200,
        "notes": "Fastest and cheapest Anthropic model. Good for simple, repetitive tasks.",
    },
    # ── OpenAI ─────────────────────────────────────────────────────────────────
    {
        "id": "gpt-4o",
        "provider": "OpenAI",
        "input_cost_per_1m": 2.50,
        "output_cost_per_1m": 10.00,
        "context_window_k": 128,
        "notes": "OpenAI flagship. Strong reasoning and tool use.",
    },
    {
        "id": "gpt-4o-mini",
        "provider": "OpenAI",
        "input_cost_per_1m": 0.15,
        "output_cost_per_1m": 0.60,
        "context_window_k": 128,
        "notes": "Very cheap OpenAI model. Good for high-volume, low-complexity steps.",
    },
    {
        "id": "gpt-4.1",
        "provider": "OpenAI",
        "input_cost_per_1m": 2.00,
        "output_cost_per_1m": 8.00,
        "context_window_k": 1000,
        "notes": "Latest GPT-4 generation. Large context, strong coding.",
    },
    {
        "id": "gpt-4.1-mini",
        "provider": "OpenAI",
        "input_cost_per_1m": 0.40,
        "output_cost_per_1m": 1.60,
        "context_window_k": 1000,
        "notes": "Cheap GPT-4.1 variant. Good balance for agentic loops.",
    },
    {
        "id": "gpt-4.1-nano",
        "provider": "OpenAI",
        "input_cost_per_1m": 0.10,
        "output_cost_per_1m": 0.40,
        "context_window_k": 1000,
        "notes": "Cheapest OpenAI model. Best for classification and simple extraction.",
    },
    {
        "id": "o4-mini",
        "provider": "OpenAI",
        "input_cost_per_1m": 1.10,
        "output_cost_per_1m": 4.40,
        "context_window_k": 200,
        "notes": "Reasoning model. Cheaper than o3 for math and code tasks.",
    },
    # ── Google ─────────────────────────────────────────────────────────────────
    {
        "id": "gemini-2.5-pro",
        "provider": "Google",
        "input_cost_per_1m": 1.25,
        "output_cost_per_1m": 10.00,
        "context_window_k": 1000,
        "notes": "Google flagship. Strong reasoning, very large context.",
    },
    {
        "id": "gemini-2.5-flash",
        "provider": "Google",
        "input_cost_per_1m": 0.30,
        "output_cost_per_1m": 2.50,
        "context_window_k": 1000,
        "notes": "Fast and cheap Google model. Good for high-throughput pipelines.",
    },
    {
        "id": "gemini-2.0-flash",
        "provider": "Google",
        "input_cost_per_1m": 0.10,
        "output_cost_per_1m": 0.40,
        "context_window_k": 1000,
        "notes": "Cheapest with large context. Fast throughput.",
    },
    # ── Meta (via API providers) ───────────────────────────────────────────────
    {
        "id": "llama-3.3-70b",
        "provider": "Meta/Groq",
        "input_cost_per_1m": 0.59,
        "output_cost_per_1m": 0.79,
        "context_window_k": 128,
        "notes": "Open-weight. Very cheap on Groq. Good for structured extraction.",
    },
    {
        "id": "llama-3.1-8b",
        "provider": "Meta/Groq",
        "input_cost_per_1m": 0.05,
        "output_cost_per_1m": 0.08,
        "context_window_k": 128,
        "notes": "Smallest useful open model. Near-zero cost for simple tasks.",
    },
    # ── Mistral ────────────────────────────────────────────────────────────────
    {
        "id": "mistral-small-3.1",
        "provider": "Mistral",
        "input_cost_per_1m": 0.10,
        "output_cost_per_1m": 0.30,
        "context_window_k": 128,
        "notes": "Cheap Mistral model. Good for classification and simple generation.",
    },
]


def get_cheaper_alternatives(current_model_id: str, budget_per_1m: float) -> list:
    """Return models cheaper than budget_per_1m input cost, excluding the current model."""
    return [
        m for m in MODELS
        if m["id"] != current_model_id and m["input_cost_per_1m"] < budget_per_1m
    ]


def format_tradeoffs(alternatives: list) -> str:
    if not alternatives:
        return "No cheaper alternatives in the lookup table."
    lines = ["Cheaper alternatives (published specs only — not a guarantee of success):"]
    for m in alternatives:
        lines.append(
            f"  • {m['id']} ({m['provider']}) — "
            f"${m['input_cost_per_1m']:.2f}/$1M in, "
            f"${m['output_cost_per_1m']:.2f}/$1M out, "
            f"{m['context_window_k']}K ctx — {m['notes']}"
        )
    return "\n".join(lines)
