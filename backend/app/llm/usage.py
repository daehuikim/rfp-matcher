from __future__ import annotations

from dataclasses import dataclass, field

# USD per 1M tokens (2025 approx — UI 추정치)
MODEL_PRICING: dict[str, tuple[float, float]] = {
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "claude-opus-4-7": (15.00, 75.00),
    "claude-sonnet-4-5": (3.00, 15.00),
}


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    key = model
    for k, (inp, out) in MODEL_PRICING.items():
        if k in model or model in k:
            key = k
            break
    inp_rate, out_rate = MODEL_PRICING.get(key, (3.0, 12.0))
    return (input_tokens * inp_rate + output_tokens * out_rate) / 1_000_000


@dataclass
class LlmCallRecord:
    purpose: str
    model: str
    provider: str
    prompt_preview: str
    input_tokens: int
    output_tokens: int
    cost_usd: float


@dataclass
class LlmUsageTracker:
    provider: str
    model: str
    calls: list[LlmCallRecord] = field(default_factory=list)
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0

    def record(
        self,
        *,
        purpose: str,
        messages: list,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        preview = ""
        if messages:
            preview = getattr(messages[-1], "content", str(messages[-1]))[:400]
        cost = estimate_cost_usd(model, input_tokens, output_tokens)
        self.calls.append(
            LlmCallRecord(
                purpose=purpose,
                model=model,
                provider=self.provider,
                prompt_preview=preview,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost,
            )
        )
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_cost_usd += cost

    def to_dict(self, *, recent_calls: int = 8) -> dict[str, object]:
        recent = self.calls[-recent_calls:]
        return {
            "provider": self.provider,
            "model": self.model,
            "total_calls": len(self.calls),
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cost_usd": round(self.total_cost_usd, 4),
            "total_cost_krw": round(self.total_cost_usd * 1350, 0),
            "recent_calls": [
                {
                    "purpose": c.purpose,
                    "model": c.model,
                    "input_tokens": c.input_tokens,
                    "output_tokens": c.output_tokens,
                    "cost_usd": round(c.cost_usd, 5),
                    "prompt_preview": c.prompt_preview,
                }
                for c in recent
            ],
        }
