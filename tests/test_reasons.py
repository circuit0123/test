from app.matching.reasons import build_reason

CAPS = {"seo": "SEO", "fundraising": "Fundraising"}
TRAITS = {"technical": "Technical", "b2b": "B2B", "hardware": "Hardware"}


def reason(details, source="local", hops=None):
    return build_reason(details, source=source, hops=hops, capability_names=CAPS, trait_names=TRAITS)


def test_they_help_you():
    d = {"they_help_you": 0.8, "you_help_them": 0.1, "capability_they_help_with": "seo", "shared_traits": []}
    assert reason(d) == "Can help you with seo"


def test_mutual_help_connection_and_traits():
    d = {"they_help_you": 0.8, "you_help_them": 0.7, "capability_they_help_with": "seo",
         "capability_you_help_with": "fundraising", "shared_traits": ["technical", "b2b", "hardware"]}
    assert reason(d, hops=2) == (
        "You can help each other: they with seo, you with fundraising; "
        "you share a mutual connection; both technical and b2b"
    )


def test_you_help_them_bridge():
    d = {"they_help_you": 0.0, "you_help_them": 0.6, "capability_you_help_with": "fundraising", "shared_traits": []}
    assert reason(d, source="bridge", hops=1) == (
        "Cross-sector match. Looking for fundraising, which you offer; you're already connected"
    )


def test_unknown_capability_falls_back_to_slug():
    d = {"they_help_you": 0.9, "you_help_them": 0, "capability_they_help_with": "ux-design", "shared_traits": []}
    assert reason(d) == "Can help you with ux design"
