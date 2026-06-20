"""
Static model tradeoff lookup table.
Specs are factual and published — no claims about which model will succeed at a specific task.
"""

MODELS = [
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
        "notes": "Fastest and cheapest. Good for simple, repetitive tasks.",
    },
    {
        "id": "gpt-4o-mini",
        "provider": "OpenAI",
        "input_cost_per_1m": 0.15,
        "output_cost_per_1m": 0.60,
        "context_window_k": 128,
        "notes": "Very cheap. Good for high-volume, low-complexity steps.",
    },
    {
        "id": "gemini-2.0-flash",
        "provider": "Google",
        "input_cost_per_1m": 0.10,
        "output_cost_per_1m": 0.40,
        "context_window_k": 1000,
        "notes": "Cheapest with large context. Fast throughput.",
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
