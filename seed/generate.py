"""Generate realistic fake seed data around a city centre.

    uv run python -m seed.generate              # uses CITY_* from .env
    uv run python -m seed.generate --seed 7 --out seed/data

Writes to the output folder:
  startups.json          crawler-shaped startup records (the future crawler contract)
  startup_profiles.json  what founders add after claiming: stage, traits, needs, team
  members.json           members with traits, needs and offers
  connections.json       undirected member-member ties
  bridge_cases.json      deliberate cross-sector need->offer pairs (for tests/eval)
  circuit_cases.json     deliberate 3-person exchange loops (for Phase 7)

Output is deterministic for a given --seed and city, so ids stay stable and tests
and hand-labelled eval pairs can refer to them.
"""

import argparse
import json
import math
import random
import uuid
from pathlib import Path

from app.config import get_settings

NAMESPACE = uuid.UUID("6f1c2d3e-4b5a-4c6d-8e7f-9a0b1c2d3e4f")
REFERENCE_DIR = Path(__file__).parent / "reference"

N_STARTUPS = 150
ROLE_COUNTS = {"student": 150, "founder": 70, "mentor": 45, "investor": 25, "partner_admin": 10}

FIRST_NAMES = """Aarav Aditi Aisha Akash Amara Ananya Arjun Bhavna Chen Daniel Deepa Divya Elena Farhan
Fatima Gaurav Hana Harish Ishaan Isha Jaya Joseph Kabir Kavya Kiran Lakshmi Leela Maya Meera Mohan
Nadia Naveen Neha Nikhil Omar Pooja Priya Rahul Ravi Riya Rohan Sahil Sana Sanjay Sara Shreya Siddharth
Sneha Tanvi Tara Uma Varun Vikram Yash Zara Zoya""".split()
LAST_NAMES = """Agarwal Bhat Chowdhury DSouza Fernandes Ghosh Gupta Hegde Iyer Jain Joshi Kapoor Khan Kulkarni
Kumar Menon Mehta Mishra Nair Pillai Rao Reddy Sastry Sen Shah Sharma Singh Thomas Varghese Verma""".split()

NAME_PREFIXES = """Nimbus Kite Lumen Orbit Pebble Quanta Ripple Saffron Tidal Vertex Zephyr Banyan Cobalt
Dhruva Ember Fable Granite Helix Indigo Juniper Kestrel Lotus Monsoon Nova Onyx Prism Quill Rangoli Sutra
Terra Umbra Vayu Willow Xenon Yonder Zenith Arc Bolt Cedar Delta""".split()
NAME_SUFFIXES = "Labs Works Health Bio Pay Learn Grow Grid Mobility Logic AI Systems Cart Farm Energy".split()

ROADS = """Station Road, Market Street, Lake View Road, Old Mill Road, Temple Street, Ring Road,
Tech Park Avenue, Church Street, Canal Road, Hill Top Lane, Garden Road, Main Road""".replace("\n", " ").split(", ")
AREAS = ["Old Town", "Tech Park", "University Quarter", "Riverside", "North Hills", "Industrial Estate",
         "Cantonment", "Lakeside", "Market District", "East Gate"]
COLLEGES = ["Government Engineering College", "City Polytechnic", "St. Joseph's College of Commerce",
            "District Institute of Technology", "Sri Venkateshwara College", "Municipal Arts & Science College"]

# Per sector: what its startups typically need, roles they hire, and their tech.
SECTORS = {
    "fintech": dict(needs=["regulatory-compliance", "cybersecurity", "b2b-sales", "fundraising", "backend-development"],
                    roles=["Backend Engineer", "Risk Analyst", "Product Manager"],
                    stack=["Python", "Go", "PostgreSQL", "Kafka", "React"],
                    expertise="fintech-domain"),
    "healthtech": dict(needs=["regulatory-compliance", "healthcare-domain", "mobile-development", "data-privacy", "growth-marketing"],
                       roles=["Mobile Developer", "Clinical Ops Associate", "Data Analyst"],
                       stack=["Kotlin", "Swift", "Node.js", "PostgreSQL", "FHIR"],
                       expertise="healthcare-domain"),
    "biotech": dict(needs=["clinical-trials", "ip-patents", "grant-writing", "fundraising", "biotech-lab"],
                    roles=["Research Associate", "Lab Technician", "Bioinformatics Intern"],
                    stack=["Python", "R", "Benchling", "AWS"],
                    expertise="biotech-lab"),
    "edtech": dict(needs=["content-marketing", "education-domain", "frontend-development", "ux-design", "growth-marketing"],
                   roles=["Content Writer", "Frontend Developer", "Community Associate"],
                   stack=["React", "Next.js", "Firebase", "Python"],
                   expertise="education-domain"),
    "agritech": dict(needs=["agriculture-domain", "supply-chain", "mobile-development", "government-relations", "fundraising"],
                     roles=["Field Operations Associate", "Android Developer", "Supply Chain Analyst"],
                     stack=["Kotlin", "Python", "Django", "IoT"],
                     expertise="agriculture-domain"),
    "climate": dict(needs=["climate-energy", "embedded-hardware", "grant-writing", "partnerships", "financial-modelling"],
                    roles=["Hardware Engineer", "Energy Analyst", "Embedded Intern"],
                    stack=["C++", "Python", "MQTT", "Grafana"],
                    expertise="climate-energy"),
    "deeptech": dict(needs=["machine-learning", "ip-patents", "embedded-hardware", "technical-architecture", "venture-capital"],
                     roles=["ML Engineer", "Research Engineer", "Robotics Intern"],
                     stack=["Python", "PyTorch", "CUDA", "ROS", "C++"],
                     expertise="computer-vision"),
    "saas": dict(needs=["b2b-sales", "customer-success", "pricing-strategy", "devops-cloud", "seo"],
                 roles=["Full Stack Developer", "Sales Development Rep", "Customer Success Associate"],
                 stack=["TypeScript", "React", "Node.js", "PostgreSQL", "AWS"],
                 expertise="product-management"),
    "ecommerce": dict(needs=["growth-marketing", "supply-chain", "social-media", "brand-design", "retail-ecommerce"],
                      roles=["Growth Marketer", "Category Associate", "Frontend Developer"],
                      stack=["Shopify", "React", "Python", "Google Analytics"],
                      expertise="retail-ecommerce"),
    "logistics": dict(needs=["operations", "data-engineering", "mobile-development", "partnerships", "supply-chain"],
                      roles=["Operations Associate", "Data Engineer", "Android Developer"],
                      stack=["Java", "Spring", "PostgreSQL", "Kafka", "Kotlin"],
                      expertise="supply-chain"),
    "mobility": dict(needs=["mobility-domain", "embedded-hardware", "government-relations", "fundraising", "operations"],
                     roles=["Fleet Operations Associate", "Embedded Engineer", "Backend Engineer"],
                     stack=["C", "Python", "Go", "PostgreSQL"],
                     expertise="mobility-domain"),
    "gaming": dict(needs=["community-building", "ui-visual-design", "social-media", "backend-development", "growth-marketing"],
                   roles=["Game Designer", "Unity Developer", "Community Manager"],
                   stack=["Unity", "C#", "Node.js", "Redis"],
                   expertise="community-building"),
}
STAGES = ["idea", "pre-seed", "seed", "series-a", "series-b"]
STUDENT_SKILLS = ["frontend-development", "backend-development", "mobile-development", "ux-design",
                  "data-engineering", "machine-learning", "content-marketing", "social-media", "qa-testing",
                  "market-research", "user-testing", "ui-visual-design"]
MENTOR_SKILLS = ["marketing-strategy", "growth-marketing", "b2b-sales", "product-management", "technical-architecture",
                 "hr-recruiting", "legal-incorporation", "financial-modelling", "pitch-coaching", "leadership-coaching",
                 "public-relations", "devops-cloud", "machine-learning", "ux-design", "operations", "partnerships",
                 "regulatory-compliance", "ip-patents", "accounting-tax", "data-privacy", "brand-design", "seo"]
INVESTOR_SKILLS = ["angel-investment", "venture-capital", "fundraising", "financial-modelling", "pitch-coaching"]
INVESTOR_NEEDS = ["market-research", "technical-architecture", "healthcare-domain", "climate-energy", "fintech-domain"]
MENTOR_NEEDS = ["user-testing", "community-building", "international-expansion", "market-research", "content-marketing"]

NEED_TEMPLATES = [
    "Looking for help with {cap}: {context}.",
    "We need someone experienced in {cap} because {context}.",
    "Seeking guidance on {cap} as {context}.",
]
OFFER_TEMPLATES = [
    "I can help with {cap}: {context}.",
    "Happy to offer {cap} support, {context}.",
    "Offering hands-on {cap} help: {context}.",
]
NEED_CONTEXT = {
    "student": ["I am a final-year student hoping to break into startups",
                "I want real-world experience beyond my college projects",
                "I am from a smaller college and have few industry contacts"],
    "founder": ["we are preparing for our next stage of growth", "our small team is stretched thin",
                "we are launching a new product line this quarter"],
    "startup": ["we are scaling our {sector} product to new cities", "our {sector} pilot is turning into paying customers",
                "we plan to raise our next round within six months"],
    "mentor": ["I am building a side community for operators", "I want feedback on a new workshop I run"],
    "investor": ["I am doing diligence on several {sector} deals", "I want sharper sector insight for my portfolio"],
    "partner_admin": ["our incubator is running a new cohort", "we support founders across the city"],
}
OFFER_CONTEXT = {
    "student": ["I have built several projects and contributed to open source",
                "I completed an internship and can commit ten hours a week",
                "I learn fast and have shipped apps at hackathons"],
    "founder": ["learned the hard way while building our {sector} startup", "we solved this at our own {sector} company"],
    "mentor": ["ten years of experience in {sector} companies", "I have advised over twenty early-stage startups",
               "I led this function at a high-growth {sector} company"],
    "investor": ["I write cheques at pre-seed and seed", "I have backed fifteen {sector} startups"],
    "partner_admin": ["through our incubator programme", "with our network of city partners"],
}


def stable_id(*parts: object) -> str:
    return str(uuid.uuid5(NAMESPACE, ":".join(str(p) for p in parts)))


class Generator:
    def __init__(self, seed: int, lat: float, lng: float, city: str) -> None:
        self.rng = random.Random(seed)
        self.lat, self.lng, self.city = lat, lng, city
        self.caps = {c["slug"]: c for c in json.loads((REFERENCE_DIR / "capabilities.json").read_text())}
        self.trait_slugs = [t["slug"] for t in json.loads((REFERENCE_DIR / "traits.json").read_text())]
        # A few startup hubs near the centre, and colleges further out.
        self.hubs = [self.offset(self.rng.uniform(0, 6), self.rng.uniform(0, 360)) for _ in range(4)]
        self.colleges = {name: self.offset(self.rng.uniform(8, 16), self.rng.uniform(0, 360)) for name in COLLEGES}
        self.startups: list[dict] = []
        self.profiles: dict[str, dict] = {}
        self.members: list[dict] = []
        self.connections: dict[tuple[str, str], dict] = {}
        self.bridge_cases: list[dict] = []
        self.circuit_cases: list[dict] = []
        self.member_sector: dict[str, str] = {}

    # ---------- geometry ----------
    def offset(self, km: float, bearing_deg: float, origin: tuple[float, float] | None = None) -> tuple[float, float]:
        lat0, lng0 = origin or (self.lat, self.lng)
        b = math.radians(bearing_deg)
        dlat = km * math.cos(b) / 111.32
        dlng = km * math.sin(b) / (111.32 * math.cos(math.radians(lat0)))
        return round(lat0 + dlat, 6), round(lng0 + dlng, 6)

    def near(self, origin: tuple[float, float], sigma_km: float) -> tuple[float, float]:
        return self.offset(abs(self.rng.gauss(0, sigma_km)), self.rng.uniform(0, 360), origin)

    def startup_location(self) -> tuple[float, float]:
        if self.rng.random() < 0.75:
            return self.near(self.rng.choice(self.hubs), 1.5)
        return self.offset(self.rng.uniform(0, 15), self.rng.uniform(0, 360))

    # ---------- text ----------
    def need(self, owner_key: str, idx: int, cap: str, role: str, sector: str) -> dict:
        context = self.rng.choice(NEED_CONTEXT[role]).format(sector=sector)
        text = self.rng.choice(NEED_TEMPLATES).format(cap=self.caps[cap]["name"].lower(), context=context)
        return {"id": stable_id("need", owner_key, idx), "capability": cap, "text": text}

    def offer(self, owner_key: str, idx: int, cap: str, role: str, sector: str) -> dict:
        context = self.rng.choice(OFFER_CONTEXT[role]).format(sector=sector)
        text = self.rng.choice(OFFER_TEMPLATES).format(cap=self.caps[cap]["name"].lower(), context=context)
        return {"id": stable_id("offer", owner_key, idx), "capability": cap, "text": text}

    def person_name(self) -> str:
        return f"{self.rng.choice(FIRST_NAMES)} {self.rng.choice(LAST_NAMES)}"

    def traits(self, base: list[str], extra: int) -> list[str]:
        chosen = list(dict.fromkeys(base))
        pool = [t for t in self.trait_slugs if t not in chosen]
        chosen += self.rng.sample(pool, extra)
        return chosen

    # ---------- startups ----------
    def make_startups(self) -> None:
        names = [f"{p} {s}" for p in NAME_PREFIXES for s in NAME_SUFFIXES]
        self.rng.shuffle(names)
        sectors = list(SECTORS)
        for i in range(N_STARTUPS):
            name = names[i]
            sector = sectors[i % len(sectors)]
            self.add_startup(name, sector)

    def add_startup(self, name: str, sector: str, description: str | None = None) -> dict:
        info = SECTORS[sector]
        lat, lng = self.startup_location()
        hiring = self.rng.random() < 0.55
        domain = name.lower().replace(" ", "") + ".example"  # .example is reserved: never a real site
        record = {
            "name": name,
            "website_domain": domain,
            "description": description or f"{name} is a {sector} startup building tools for customers in {self.city}.",
            "sector": sector,
            "address": f"{self.rng.randint(1, 250)} {self.rng.choice(ROADS)}, {self.rng.choice(AREAS)}, {self.city}",
            "lat": lat,
            "lng": lng,
            "hiring": hiring,
            "open_roles": self.rng.sample(info["roles"], self.rng.randint(1, 2)) if hiring else [],
            "tech_stack": self.rng.sample(info["stack"], self.rng.randint(2, min(4, len(info["stack"])))),
            "source": "seed",
        }
        self.startups.append(record)
        self.profiles[domain] = {
            "website_domain": domain,
            "stage": self.rng.choice(STAGES),
            "traits": self.traits(["b2b" if sector in ("saas", "logistics", "fintech") else "b2c"], self.rng.randint(1, 2)),
            "needs": [],
            "team": [],
        }
        return record

    # ---------- members ----------
    def add_member(self, role: str, idx: int, **overrides) -> dict:
        mid = stable_id("member", role, idx)
        verification = {"student": [1, 1, 2, 2, 3], "founder": [2, 3, 3], "mentor": [3, 3, 4],
                        "investor": [3, 4, 4], "partner_admin": [4]}[role]
        member = {
            "id": mid,
            "role": role,
            "display_name": self.person_name(),
            "bio": None,
            "lat": None,
            "lng": None,
            "city": self.city,
            "verification_level": self.rng.choice(verification),
            "open_intro_slots": self.rng.randint(1, 5),
            "circuits_opt_in": self.rng.random() < 0.4,
            "open_to_cross_sector": self.rng.random() < 0.8,
            "traits": [],
            "needs": [],
            "offers": [],
        }
        member.update(overrides)
        self.members.append(member)
        return member

    def make_students(self) -> None:
        for i in range(ROLE_COUNTS["student"]):
            college = self.rng.choice(COLLEGES)
            lat, lng = self.near(self.colleges[college], 1.0)
            skills = self.rng.sample(STUDENT_SKILLS, self.rng.randint(1, 2))
            m = self.add_member("student", i, lat=lat, lng=lng,
                                bio=f"Student at {college}, interested in {self.caps[skills[0]]['name'].lower()}.")
            m["traits"] = self.traits(["tier-2-3-college"] if self.rng.random() < 0.7 else [], self.rng.randint(1, 2))
            sector = self.rng.choice(list(SECTORS))
            self.member_sector[m["id"]] = sector
            m["offers"] = [self.offer(m["id"], j, c, "student", sector) for j, c in enumerate(skills)]
            needs = ["internships"] + self.rng.sample(["mentorship", "career-guidance"], self.rng.randint(0, 1))
            m["needs"] = [self.need(m["id"], j, c, "student", sector) for j, c in enumerate(needs)]
            m["_college"] = college

    def make_founders(self) -> None:
        # The first startups get founders (some get two); the rest stay unclaimed,
        # which is what the Phase 2 claiming flow needs.
        n = ROLE_COUNTS["founder"]
        teams = list(range(55)) + self.rng.sample(range(55), n - 55)
        for i, s_idx in enumerate(teams):
            startup = self.startups[s_idx]
            sector = startup["sector"]
            lat, lng = self.near((startup["lat"], startup["lng"]), 2.0)
            m = self.add_member("founder", i, lat=lat, lng=lng, bio=f"Building {startup['name']} ({sector}).")
            self.member_sector[m["id"]] = sector
            m["traits"] = self.traits([self.rng.choice(["first-time-founder", "repeat-founder"]),
                                       self.rng.choice(["technical", "non-technical"])], 1)
            expertise = SECTORS[sector]["expertise"]
            skill = self.rng.choice([c for c in MENTOR_SKILLS if c != expertise])
            m["offers"] = [self.offer(m["id"], 0, expertise, "founder", sector),
                           self.offer(m["id"], 1, skill, "founder", sector)]
            personal = self.rng.choice(["mentorship", "leadership-coaching", "pitch-coaching"])
            m["needs"] = [self.need(m["id"], 0, personal, "founder", sector)]
            profile = self.profiles[startup["website_domain"]]
            title = "CEO & Co-founder" if not profile["team"] else self.rng.choice(["CTO & Co-founder", "COO & Co-founder"])
            profile["team"].append({"member_id": m["id"], "title": title})

        for s_idx in range(55):
            startup = self.startups[s_idx]
            profile = self.profiles[startup["website_domain"]]
            caps = self.rng.sample(SECTORS[startup["sector"]]["needs"], self.rng.randint(1, 3))
            profile["needs"] = [self.need(startup["website_domain"], j, c, "startup", startup["sector"])
                                for j, c in enumerate(caps)]

    def make_experienced(self, role: str, skills: list[str], needs: list[str], traits: list[str]) -> None:
        for i in range(ROLE_COUNTS[role]):
            sector = self.rng.choice(list(SECTORS))
            lat, lng = self.offset(self.rng.uniform(0, 12), self.rng.uniform(0, 360))
            m = self.add_member(role, i, lat=lat, lng=lng)
            self.member_sector[m["id"]] = sector
            offer_caps = self.rng.sample(skills, 2) + ([SECTORS[sector]["expertise"]] if role == "mentor" else [])
            offer_caps = list(dict.fromkeys(offer_caps))
            m["offers"] = [self.offer(m["id"], j, c, role, sector) for j, c in enumerate(offer_caps)]
            need_caps = self.rng.sample(needs, self.rng.randint(0, 1) if role == "mentor" else 1)
            m["needs"] = [self.need(m["id"], j, c, role, sector) for j, c in enumerate(need_caps)]
            m["traits"] = self.traits(traits, self.rng.randint(1, 2))
            label = {"mentor": "Mentor", "investor": "Investor", "partner_admin": "Programme lead"}[role]
            m["bio"] = f"{label} with a background in {sector}."

    def make_members(self) -> None:
        self.make_students()
        self.make_founders()
        self.make_experienced("mentor", MENTOR_SKILLS, MENTOR_NEEDS, [])
        self.make_experienced("investor", INVESTOR_SKILLS, INVESTOR_NEEDS, [])
        self.make_experienced("partner_admin", ["community-building", "government-relations", "grant-writing"],
                              ["market-research"], ["public-speaker"])

    # ---------- deliberate test cases ----------
    def make_bridge_cases(self) -> None:
        """Startups in one sector needing something best offered by someone from another sector."""
        cases = [
            ("Helix Genomics", "biotech", "marketing-strategy",
             "We need a marketing strategy to explain our gene-sequencing kits to hospital buyers who are not scientists.",
             "deeptech", "Marketing strategist who has positioned complex deep-tech and AI products for non-technical buyers; I turn hard science into clear go-to-market messaging."),
            ("Monsoon Fields", "agritech", "machine-learning",
             "We need machine learning to predict crop yield and credit risk for smallholder farmers from sparse data.",
             "fintech", "Data scientist who built machine learning credit-risk and fraud models on sparse, noisy data at a lending company."),
            ("Saffron Care", "healthtech", "regulatory-compliance",
             "We need help with regulatory compliance and licensing before our patient-data platform can go live.",
             "fintech", "Compliance lead who took a payments company through licensing, audits and data-protection regulatory compliance."),
            ("Tidewater Energy", "climate", "b2b-sales",
             "We need to build a B2B sales process to sell energy monitoring to factories and commercial buildings.",
             "saas", "Sales leader who built B2B sales teams and enterprise sales playbooks at two SaaS companies."),
            ("Rangoli Academy", "edtech", "community-building",
             "We need to grow an engaged learner community so students keep coming back between courses.",
             "gaming", "Community manager who grew a gaming community to 200k engaged players with events, Discord and creator programmes."),
            ("Vayuvega Motors", "mobility", "supply-chain",
             "We need supply chain expertise to source battery packs and spare parts reliably for our EV fleet.",
             "ecommerce", "Supply chain operator who ran sourcing, warehousing and last-mile logistics for an e-commerce marketplace."),
        ]
        for i, (name, sector, cap, need_text, helper_sector, offer_text) in enumerate(cases):
            startup = self.add_startup(name, sector)
            domain = startup["website_domain"]
            lat, lng = self.near((startup["lat"], startup["lng"]), 1.0)
            founder = self.add_member("founder", 100 + i, lat=lat, lng=lng, bio=f"Building {name} ({sector}).",
                                      open_to_cross_sector=True, verification_level=3)
            founder["traits"] = self.traits(["first-time-founder"], 1)
            self.member_sector[founder["id"]] = sector
            founder["offers"] = [self.offer(founder["id"], 0, SECTORS[sector]["expertise"], "founder", sector)]
            self.profiles[domain]["team"].append({"member_id": founder["id"], "title": "CEO & Co-founder"})
            need = {"id": stable_id("need", domain, "bridge"), "capability": cap, "text": need_text}
            self.profiles[domain]["needs"].append(need)
            # The helper is far away and in another sector: only bridge search finds them.
            hlat, hlng = self.offset(self.rng.uniform(10, 14), self.rng.uniform(0, 360))
            helper = self.add_member("mentor", 100 + i, lat=hlat, lng=hlng, bio=f"Mentor with a background in {helper_sector}.",
                                     open_to_cross_sector=True, verification_level=3)
            helper["traits"] = self.traits(["deep-tech"] if helper_sector == "deeptech" else [], 1)
            self.member_sector[helper["id"]] = helper_sector
            offer = {"id": stable_id("offer", helper["id"], "bridge"), "capability": cap, "text": offer_text}
            helper["offers"] = [offer]
            self.bridge_cases.append({
                "startup_domain": domain, "startup_sector": sector, "founder_id": founder["id"],
                "need_id": need["id"], "helper_id": helper["id"], "helper_sector": helper_sector,
                "offer_id": offer["id"], "capability": cap,
            })

    def make_circuit_cases(self) -> None:
        """Three members who could each help the next one round a loop: A -> B -> C -> A."""
        loops = [
            [("founder", "ux-design", "Product designer-founder; I run UX research and design sprints for early apps."),
             ("mentor", "fundraising", "I help founders prepare fundraising decks and investor pipelines."),
             ("investor", "market-research", "I can run market research and customer interviews for your sector.")],
            [("mentor", "legal-incorporation", "Startup lawyer: company incorporation, founder agreements, ESOP setup."),
             ("founder", "growth-marketing", "I scaled our app with growth marketing experiments across paid and organic."),
             ("mentor", "technical-architecture", "Former CTO; I review technical architecture and scaling plans.")],
        ]
        for li, loop in enumerate(loops):
            ids = []
            for pi, (role, _, _) in enumerate(loop):
                lat, lng = self.offset(self.rng.uniform(0, 8), self.rng.uniform(0, 360))
                m = self.add_member(role, 200 + li * 10 + pi, lat=lat, lng=lng, circuits_opt_in=True,
                                    open_intro_slots=3, verification_level=3, bio=f"Circuit-friendly {role}.")
                m["traits"] = self.traits([], 2)
                self.member_sector[m["id"]] = self.rng.choice(list(SECTORS))
                ids.append(m)
            legs = []
            for pi, (role, cap, offer_text) in enumerate(loop):
                giver, receiver = ids[pi], ids[(pi + 1) % 3]
                offer = {"id": stable_id("offer", giver["id"], "circuit"), "capability": cap, "text": offer_text}
                need_text = f"I need help with {self.caps[cap]['name'].lower()} for my current project."
                need = {"id": stable_id("need", receiver["id"], "circuit"), "capability": cap, "text": need_text}
                giver["offers"].append(offer)
                receiver["needs"].append(need)
                legs.append({"giver_id": giver["id"], "receiver_id": receiver["id"], "capability": cap})
            self.circuit_cases.append({"members": [m["id"] for m in ids], "legs": legs})

    # ---------- connections ----------
    def connect(self, a: str, b: str, strength: float, source: str) -> None:
        if a == b:
            return
        lo, hi = sorted([a, b], key=uuid.UUID)
        key = (lo, hi)
        if key not in self.connections or self.connections[key]["strength"] < strength:
            self.connections[key] = {"a": lo, "b": hi, "strength": round(strength, 2), "source": source}

    def make_connections(self) -> None:
        for profile in self.profiles.values():
            team = [t["member_id"] for t in profile["team"]]
            for i in range(len(team)):
                for j in range(i + 1, len(team)):
                    self.connect(team[i], team[j], 0.95, "mutual")
        by_role: dict[str, list[dict]] = {}
        for m in self.members:
            by_role.setdefault(m["role"], []).append(m)
        experienced = by_role["mentor"] + by_role["investor"] + by_role["partner_admin"]
        for f in by_role["founder"]:
            same = [e for e in experienced if self.member_sector[e["id"]] == self.member_sector[f["id"]]]
            for e in self.rng.sample(same, min(len(same), 2)):
                self.connect(f["id"], e["id"], self.rng.uniform(0.5, 0.8), "mutual")
            for e in self.rng.sample(experienced, 1):
                self.connect(f["id"], e["id"], self.rng.uniform(0.2, 0.5), "event")
        students = by_role["student"]
        for s in students:
            classmates = [o for o in students if o.get("_college") == s.get("_college") and o is not s]
            for o in self.rng.sample(classmates, min(len(classmates), 2)):
                self.connect(s["id"], o["id"], self.rng.uniform(0.3, 0.6), "event")
            if self.rng.random() < 0.3:
                self.connect(s["id"], self.rng.choice(by_role["founder"])["id"], self.rng.uniform(0.2, 0.4), "event")
        for _ in range(60):
            a, b = self.rng.sample(experienced, 2)
            self.connect(a["id"], b["id"], self.rng.uniform(0.3, 0.7), "mutual")

    # ---------- output ----------
    def run(self) -> dict[str, object]:
        self.make_startups()
        self.make_members()
        self.make_bridge_cases()
        self.make_circuit_cases()
        self.make_connections()
        for m in self.members:
            m.pop("_college", None)
        return {
            "startups.json": self.startups,
            "startup_profiles.json": list(self.profiles.values()),
            "members.json": self.members,
            "connections.json": list(self.connections.values()),
            "bridge_cases.json": self.bridge_cases,
            "circuit_cases.json": self.circuit_cases,
        }


def generate(seed: int, lat: float, lng: float, city: str) -> dict[str, object]:
    return Generator(seed, lat, lng, city).run()


def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=Path, default=Path(__file__).parent / "data")
    parser.add_argument("--lat", type=float, default=settings.city_center_lat)
    parser.add_argument("--lng", type=float, default=settings.city_center_lng)
    parser.add_argument("--city", default=settings.city_name)
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    for filename, records in generate(args.seed, args.lat, args.lng, args.city).items():
        (args.out / filename).write_text(json.dumps(records, indent=2, ensure_ascii=False) + "\n")
        print(f"wrote {len(records):4d} records to {args.out / filename}")  # type: ignore[arg-type]


if __name__ == "__main__":
    main()
