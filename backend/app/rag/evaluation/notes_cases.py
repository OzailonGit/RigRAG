"""Knowledge-note evaluation set: builds around a part a note covers, with
the explanation or a follow-up question that note should answer.

Each case pins the part the note is about (the cheapest priced match), so
the case doesn't depend on which parts the engine happens to prefer.
`expect` lists the notes the answer must be given and must cite. An
`abstain` case asks something no note covers; the answer should say it
has no sourced information and cite nothing, rather than answer from the
model's own knowledge.

An `owner_reviews_for` case asks what owners experienced with the part in
that role, which needs the review passages (search_reviews): the answer
must cite that part's Amazon reviews. `relevant_terms` judge the passages
retrieved: one counts as relevant when it contains any of them. These
cases need the local Amazon data (data/raw/amazon/ and the review_passages
collection) and are skipped without it.
"""

from dataclasses import dataclass, field

from app.schemas import BuildRequest, Resolution, UseCase


@dataclass
class NoteCase:
    name: str
    request: BuildRequest
    # role -> fields of the part to pin, e.g. {"gpu": {"chip": "Arc A750"}}
    pins: dict[str, dict] = field(default_factory=dict)
    question: str | None = None  # None: the build's own explanation
    expect: tuple[str, ...] = ()
    abstain: bool = False
    owner_reviews_for: str | None = None
    relevant_terms: tuple[str, ...] = ()


def _gaming(budget: float, resolution: Resolution | None = Resolution.r1440p) -> BuildRequest:
    return BuildRequest(budget_php=budget, use_case=[UseCase.gaming], resolution=resolution)


NOTE_CASES: list[NoteCase] = [
    # --- Explanations: the note should come up unprompted ---
    NoteCase("Arc A750 build explanation", _gaming(40000, Resolution.r1080p),
             pins={"gpu": {"chip": "Arc A750"}}, expect=("arc-a-series-resizable-bar",)),
    NoteCase("i5-14600KF build explanation", _gaming(70000),
             pins={"cpu": {"model": "Core i5-14600KF"}}, expect=("raptor-lake-vmin-shift",)),
    NoteCase("Core Ultra 7 265KF build explanation", _gaming(85000),
             pins={"cpu": {"model": "Core Ultra 7 265KF"}}, expect=("core-ultra-200s-updates",)),
    # --- Follow-ups: the question targets the note ---
    NoteCase("FSR 4 on an RX 7900 XT", _gaming(120000), pins={"gpu": {"chip": "Radeon RX 7900 XT"}},
             question="Does this graphics card support FSR 4?", expect=("rx-7000-fsr-4-1",)),
    NoteCase("8GB VRAM at 1440p", _gaming(60000), pins={"gpu": {"chip": "GeForce RTX 5060 Ti", "vram_gb": 8}},
             question="Is 8GB of VRAM enough for 1440p gaming?", expect=("rtx-5060-ti-8gb-vram",)),
    NoteCase("RX 9070 XT power supply", _gaming(100000), pins={"gpu": {"chip": "Radeon RX 9070 XT"}},
             question="What size power supply does this graphics card need?", expect=("rx-9070-xt-power",)),
    NoteCase("9950X3D setup for games", _gaming(150000), pins={"cpu": {"model": "Ryzen 9 9950X3D"}},
             question="Do I need to set anything up so games run well on this CPU?",
             expect=("ryzen-9950x3d-one-cache-die",)),
    NoteCase("PhysX on an RTX 5080", _gaming(150000), pins={"gpu": {"chip": "GeForce RTX 5080"}},
             question="Can I play Borderlands 2 with PhysX turned on with this card?",
             expect=("rtx-50-no-32bit-physx",)),
    NoteCase("CPU overclocking on A620", BuildRequest(budget_php=50000, use_case=[UseCase.general]),
             pins={"cpu": {"model": "Ryzen 5 9600X"}, "motherboard": {"chipset": "A620"}},
             question="Can I overclock the CPU on this motherboard?", expect=("a620-b840-no-cpu-overclocking",)),
    NoteCase("Air cooler noise", _gaming(70000), pins={"cooler": {"model": "Assassin X 120 Refined SE"}},
             question="Is the CPU cooler loud?", expect=("thermalright-assassin-x-120-se-noise",)),
    NoteCase("AM5 upgrade path", _gaming(80000), pins={"cpu": {"model": "Ryzen 7 9800X3D"}},
             question="Will I be able to upgrade the CPU later without changing the motherboard?",
             expect=("am5-support-through-2029",)),
    # --- Owner experiences: answered from review passages ---
    NoteCase("AIO pump noise and leaks", _gaming(130000), pins={"cooler": {"model": "Kraken X73 RGB 360mm"}},
             question="Do owners report pump noise or leaks with this cooler?", owner_reviews_for="cooler",
             relevant_terms=("pump", "leak", "noise", "loud", "buzz", "rattl", "grind", "click", "whin", "hum")),
    NoteCase("Air cooler installation", _gaming(60000), pins={"cooler": {"model": "Hyper 212 EVO V2"}},
             question="Do owners find this cooler hard to install?", owner_reviews_for="cooler",
             relevant_terms=("install", "mount", "bracket", "backplate", "screw")),
    NoteCase("GPU coil whine", _gaming(120000), pins={"gpu": {"model": "Red Dragon OC Radeon RX 6800 XT 16GB"}},
             question="Do owners mention coil whine with this graphics card?", owner_reviews_for="gpu",
             relevant_terms=("coil", "whine", "buzz", "squeal")),
    NoteCase("Motherboard BIOS update", BuildRequest(budget_php=45000, use_case=[UseCase.general]),
             pins={"motherboard": {"model": "B550M DS3H AC"}},
             question="Did owners need to update the BIOS to get this motherboard working?",
             owner_reviews_for="motherboard", relevant_terms=("bios", "flash", "firmware")),
    NoteCase("PSU failures", _gaming(50000), pins={"psu": {"model": "750 N1 750W"}},
             question="Do owners say this power supply fails or dies?", owner_reviews_for="psu",
             relevant_terms=("die", "dead", "fail", "stopped", "smoke", "broke", "rma", "pop")),
    NoteCase("Case arriving damaged", _gaming(60000), pins={"case": {"model": "iCUE 4000D RGB AIRFLOW"}},
             question="Did this case arrive damaged for owners?", owner_reviews_for="case",
             relevant_terms=("damage", "broke", "missing", "shipping", "scratch", "dent", "crack", "shatter")),
    # --- Nothing covers these: the answer should say so ---
    NoteCase("Owner reviews of a 2025 GPU, none exist", _gaming(150000), pins={"gpu": {"chip": "GeForce RTX 5080"}},
             question="Do owners complain about coil whine on this graphics card?", abstain=True),
    NoteCase("PSU MTBF, no data", _gaming(70000), question="What is the MTBF rating of this power supply?",
             abstain=True),
]
