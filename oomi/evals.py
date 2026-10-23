"""Memory recall benchmark: `oomi eval memory`.

30 target facts about a fictional user, 15 near-miss distractors, and two queries per target:
  * keyword — shares at least one content word with the fact (how an agent usually searches);
  * paraphrase — same meaning, different words (the hard case for lexical search).
Reports recall@1/3/5 for each kind. Lexical (BM25) needs no extra service; add a Voyage key to
measure hybrid retrieval as well.
"""

from __future__ import annotations

from .memory import Memory

TARGETS = [
    ("Prefers pytest over unittest for Python tests", "which python test framework should I use", "how do I like my automated checks written"),
    ("Uses uv instead of pip to manage Python environments", "install python packages with pip or uv", "what tool sets up my virtualenvs"),
    ("Main laptop is a 14-inch MacBook Pro with an M3 Pro chip", "what MacBook do I have", "how powerful is my computer"),
    ("Deploys web services to Render, not Heroku", "where do I deploy web services", "which hosting provider for the backend"),
    ("Sister Anna lives in Lisbon", "where does Anna live", "which city is my sibling in"),
    ("Thesis is about battery degradation in electric scooters", "what is my thesis topic", "what is my final year research on"),
    ("Vegetarian and doesn't eat fish either", "is this dinner recipe vegetarian", "can I cook salmon for myself"),
    ("Prefers British English spelling in documents", "British or American spelling", "should I write colour or color"),
    ("Git commit messages follow Conventional Commits (feat:, fix:)", "commit message format", "how should I describe changes when saving to version control"),
    ("The splatter game backend runs on Node 20 with Socket.IO", "splatter game backend stack", "what realtime library does the multiplayer paint shooter use"),
    ("Timezone is Europe/London", "what is my timezone", "what time is it where I live"),
    ("Car is a 2004 Volvo V70 estate", "what car do I drive", "my vehicle's make and model"),
    ("Works part-time at a bike repair shop on Saturdays", "do I work on Saturday", "when am I busy at the weekend"),
    ("Postgres database for the inventory app is hosted on Supabase", "inventory app database", "where is the stock tracker's data stored"),
    ("Prefers short answers without long preambles", "preferred answer length", "how verbose should replies be"),
    ("Code editor is Neovim with the LazyVim config", "which code editor do I use", "my programming setup for writing software"),
    ("Team standup is at 10am on weekdays", "when is standup", "daily team meeting time"),
    ("Home internet is Hyperoptic fibre at 500 Mbps", "home internet speed", "how fast is my broadband"),
    ("Learning Japanese, around JLPT N4 level", "my Japanese level", "which foreign language am I studying"),
    ("The company Slack workspace is acme-labs", "slack workspace name", "where does the team chat"),
    ("Prefers dark mode in all generated UIs", "dark mode or light mode UI", "what colour scheme for the dashboard I'm building"),
    ("Home server is a Raspberry Pi 5 running Home Assistant", "what runs home assistant", "what's my smart home hub hardware"),
    ("Flight to Tokyo on 12 March", "when is the Tokyo flight", "when do I travel to Japan"),
    ("Partner's name is Jordan", "what is my partner's name", "who am I in a relationship with"),
    ("API keys live in 1Password and are never committed in .env files", "where are API keys stored", "how to handle secrets for the new project"),
    ("Usual coffee order is a flat white with oat milk", "my coffee order", "what should I get at the café"),
    ("Supports Arsenal football club", "which football club do I support", "which team do I follow in the Premier League"),
    ("Uses OrbStack instead of Docker Desktop on the Mac", "docker desktop on mac", "how do I run containers"),
    ("Landlord is Mr Patel and rent is due on the 1st", "when is rent due", "who owns my flat"),
    ("Running a half marathon in October with a target time of 1:50", "half marathon target time", "what race am I training for"),
]

DISTRACTORS = [
    "Tried Heroku once in 2023 but found it expensive",
    "Python 3.12 is the default interpreter",
    "Anna's birthday is in June",
    "Considering buying an electric scooter for commuting",
    "Used VS Code before switching editors",
    "Prefers metric units",
    "Brother lives in Manchester",
    "The inventory app frontend is built with SvelteKit",
    "Weekly planning meeting on Mondays at 2pm",
    "Studied French at school",
    "Drinks tea in the afternoon",
    "Previous car was a Ford Fiesta",
    "Uses Tailwind CSS for styling web apps",
    "Wants detailed explanations for maths topics",
    "Supabase free tier pauses projects after a week of inactivity",
]


def run_memory_eval(embedder=None, expander=None, k: int = 5) -> dict:
    mem = Memory(":memory:", embedder=embedder)
    ids = []
    for text, _, _ in TARGETS:
        fid, _ = mem.add(text, source="eval")
        ids.append(fid)
    for text in DISTRACTORS:
        mem.add(text, source="eval")

    results: dict = {}
    for kind, col in (("keyword", 1), ("paraphrase", 2)):
        hits = {1: 0, 3: 0, 5: 0}
        misses = []
        for fid, row in zip(ids, TARGETS):
            got = [f.id for f in mem.search(row[col], k=k, touch=False, expand=expander)]
            rank = got.index(fid) + 1 if fid in got else None
            for cutoff in hits:
                if rank is not None and rank <= cutoff:
                    hits[cutoff] += 1
            if rank is None:
                misses.append(row[col])
        n = len(TARGETS)
        results[kind] = {f"recall@{c}": hits[c] / n for c in hits}
        results[kind]["misses"] = misses
    mode = "hybrid (BM25 + embeddings)" if embedder else "lexical (BM25, Porter stemming)"
    results["mode"] = mode + (" + LLM query expansion" if expander else "")
    results["facts"] = len(TARGETS) + len(DISTRACTORS)
    return results
