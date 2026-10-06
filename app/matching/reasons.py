"""One-line, template-based explanations for a match. No LLM needed.

Built only from the stored score components, so the same inputs always give
the same sentence, and every claim in it is something the scorer actually used.
"""

from typing import Any

STRONG = 0.5  # a direction counts as "real help" at or above this fit


def build_reason(
    details: dict[str, Any], *, source: str, hops: int | None,
    capability_names: dict[str, str], trait_names: dict[str, str],
) -> str:
    def cap(slug: str | None) -> str:
        return capability_names.get(slug or "", (slug or "").replace("-", " ")).lower()

    parts: list[str] = []
    they, you = details.get("they_help_you", 0), details.get("you_help_them", 0)
    cap_in, cap_out = details.get("capability_they_help_with"), details.get("capability_you_help_with")

    if they >= STRONG and you >= STRONG:
        parts.append(f"You can help each other: they with {cap(cap_in)}, you with {cap(cap_out)}")
    elif they >= you and cap_in:
        parts.append(f"Can help you with {cap(cap_in)}")
    elif cap_out:
        parts.append(f"Looking for {cap(cap_out)}, which you offer")

    if hops == 1:
        parts.append("you're already connected")
    elif hops == 2:
        parts.append("you share a mutual connection")

    shared = [trait_names.get(t, t).lower() for t in details.get("shared_traits", [])][:2]
    if shared:
        parts.append("both " + " and ".join(shared))

    sentence = "; ".join(parts) if parts else "Complementary needs and offers"
    if source == "bridge":
        sentence = "Cross-sector match. " + sentence
    return sentence[0].upper() + sentence[1:]
