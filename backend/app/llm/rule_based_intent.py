"""Rule-based intent extraction, used when the LLM is unavailable or
returns output that does not validate against BuildRequest.

Deliberately conservative: it only fills fields it can read from explicit
keywords or patterns and leaves everything else as the existing
conversational state had it. It cannot infer a budget; if no turn has
stated one, the caller asks for it.

classify_message_rule_based() decides the message kind from a list of PC
terms, so off-topic messages are still caught without the LLM.
"""

import re

from app.schemas import BuildRequest, MessageKind, Resolution, UseCase

_BUDGET_PATTERN = re.compile(
    r"(?:₱|php\s*)?([\d,]+(?:\.\d+)?)\s*(k)?(?:\s*(?:php|pesos))?(p)?", re.IGNORECASE
)
_RESOLUTION_KEYWORDS = {
    "1080p": Resolution.r1080p, "1080": Resolution.r1080p, "fhd": Resolution.r1080p,
    "1440p": Resolution.r1440p, "1440": Resolution.r1440p, "qhd": Resolution.r1440p, "2k": Resolution.r1440p,
    "4k": Resolution.r4k, "2160p": Resolution.r4k, "uhd": Resolution.r4k,
}
_INCREASE_KEYWORDS = ("increase", "raise", "bump up", "bump")
_DECREASE_KEYWORDS = ("decrease", "lower", "reduce", "cut", "drop")
_TRAILING_DELTA_PATTERN = re.compile(
    r"(?:₱|php\s*)?([\d,]+(?:\.\d+)?)\s*(k)?\s*(?:more|extra|additional)\b", re.IGNORECASE
)

# Words that put a message in PC territory. Ambiguous ones ("build", "case",
# "recommend") are left out so "build me a website" or "recommend a movie"
# don't count; a real build request nearly always has one of these too.
_PC_TERMS = re.compile(
    r"\b(pcs?|computers?|desktops?|rigs?|cpus?|processors?|gpus?|graphics|video cards?|motherboards?|mobo|"
    r"ram|memory|ssds?|hdds?|nvme|m\.2|storage|psus?|power supply|coolers?|cooling|aio|pc case|chassis|"
    r"fans?|gaming|games?|fps|1080p?|1440p?|4k|2k|ryzen|intel|amd|nvidia|geforce|rtx|gtx|radeon|core i\d|"
    r"core ultra|wi-?fi|bluetooth|budget|pesos?|php|upgrade|sockets?|ddr\d|a?rgb|editing|rendering|blender|"
    r"streaming|workstation|parts?|specs?|bottleneck|overclock\w*|compatib\w*)\b"
)
_SMALL_TALK = re.compile(
    r"^(hi|hello|hey|yo|good (morning|afternoon|evening)|thanks|thank you|ty|bye|goodbye|"
    r"what can you do|who are you|help)\b"
)
# Replies to "do you have a budget in mind?" that don't contain one.
_ANSWER_WITHOUT_BUDGET = re.compile(
    r"\b(not sure|no idea|don'?t know|dunno|you (pick|choose|decide)|anything|whatever|"
    r"yes|yeah|yep|sure|ok(ay)?|go ahead|recommend|suggest)\b"
)
# With a build on screen, "why that one?" or "anything cheaper?" is about it.
_BUILD_REFERENCE = re.compile(r"\b(this|that|these|those|it|cheaper|better|instead|alternatives?|why|swap|change)\b")
_BUILD_REQUEST_KEYWORDS = ("build", "recommend", "suggest", "put together", "what should i get", "pc for")

_BUILD_REQUEST_KEYWORDS_RE = re.compile("|".join(re.escape(kw) for kw in _BUILD_REQUEST_KEYWORDS))

_USE_CASE_KEYWORDS = {
    "gaming": UseCase.gaming, "game": UseCase.gaming,
    "programming": UseCase.programming, "coding": UseCase.programming, "development": UseCase.programming,
    "video editing": UseCase.video_editing, "editing": UseCase.video_editing,
    "3d rendering": UseCase.rendering_3d, "rendering": UseCase.rendering_3d, "blender": UseCase.rendering_3d,
    "ai": UseCase.ai_ml, "machine learning": UseCase.ai_ml, "ml": UseCase.ai_ml,
    "productivity": UseCase.productivity, "office": UseCase.productivity,
}


def _find_budget(text: str) -> float | None:
    """Picks the number in the message most likely to be the budget.

    Two cases break a naive first/last-number scan:
    - Resolutions like "1440p" look like a number followed by 'p' and are
      always excluded.
    - Shorthand like "4k" parses to 4000, a valid amount but implausibly low
      for a PC budget, so values in that range are trusted only when they are
      the sole candidate.

    Otherwise the largest remaining candidate wins, since the budget is
    usually the biggest figure in the message (RAM sizes, core counts, etc.
    are smaller).
    """
    candidates: list[float] = []
    for match in _BUDGET_PATTERN.finditer(text):
        raw, k_suffix, resolution_suffix = match.groups()
        if not raw or resolution_suffix:  # "1440p" / "1080p" are not a currency amount
            continue
        try:
            value = float(raw.replace(",", ""))
        except ValueError:
            continue
        if k_suffix:
            value *= 1000
        if value >= 500:  # filters out incidental small numbers ("2 sticks of RAM")
            candidates.append(value)

    if not candidates:
        return None
    plausible = [v for v in candidates if v >= 5000]
    return max(plausible) if plausible else max(candidates)


def _find_budget_delta(text: str) -> float | None:
    """Detects a relative budget change ("increase my budget by 10000", "add
    5k more") and returns a signed delta, or None if the message states an
    absolute amount.

    Without this, `_find_budget` would read "increase it by 10000" as an
    absolute budget of 10000 and replace a much larger existing budget.
    """
    for kw in _INCREASE_KEYWORDS:
        idx = text.find(kw)
        if idx != -1:
            amount = _find_budget(text[idx:])
            if amount is not None:
                return amount
    for kw in _DECREASE_KEYWORDS:
        idx = text.find(kw)
        if idx != -1:
            amount = _find_budget(text[idx:])
            if amount is not None:
                return -amount

    trailing = _TRAILING_DELTA_PATTERN.search(text)
    if trailing:
        raw, k_suffix = trailing.groups()
        try:
            value = float(raw.replace(",", ""))
        except ValueError:
            return None
        return value * 1000 if k_suffix else value

    return None


def classify_message_rule_based(message: str, has_build: bool, awaiting_budget: bool) -> MessageKind:
    """Fallback for MessageKind when the LLM is unavailable or omits it.

    `awaiting_budget` means the last reply asked for a budget, so "not sure"
    or "go ahead" is an answer to it rather than an off-topic message.
    """
    text = message.lower().strip()
    budget = _find_budget(text)
    # Short greetings and thanks, even ones that mention the build ("thanks
    # for the build"), so they never re-run the engine.
    if _SMALL_TALK.match(text) and budget is None and len(text.split()) <= 5:
        return MessageKind.small_talk
    about_pcs = bool(_PC_TERMS.search(text)) or "₱" in text or (budget is not None and budget >= 5000)
    if not about_pcs and has_build and _BUILD_REFERENCE.search(text) and not _BUILD_REQUEST_KEYWORDS_RE.search(text):
        return MessageKind.followup
    if not about_pcs:
        if awaiting_budget and _ANSWER_WITHOUT_BUDGET.search(text):
            return MessageKind.build_request
        return MessageKind.off_topic
    if not has_build or any(kw in text for kw in _BUILD_REQUEST_KEYWORDS):
        return MessageKind.build_request
    return MessageKind.followup


def parse_intent_rule_based(message: str, existing: BuildRequest | None = None) -> BuildRequest:
    text = message.lower()
    state = existing.model_copy(deep=True) if existing else None

    budget = _find_budget(text)
    use_cases = [uc for kw, uc in _USE_CASE_KEYWORDS.items() if kw in text]
    resolution = next((res for kw, res in _RESOLUTION_KEYWORDS.items() if kw in text), None)

    cpu_brand = "AMD" if "amd" in text and "ryzen" in text or ("amd" in text and "cpu" in text) else (
        "Intel" if "intel" in text else None
    )
    gpu_brand = "NVIDIA" if any(k in text for k in ("nvidia", "geforce", "rtx")) else (
        "AMD" if any(k in text for k in ("radeon", "rx ")) else None
    )
    wifi_required = "wifi" in text or "wi-fi" in text
    small_form_factor = any(k in text for k in ("small form factor", "sff", "mini-itx", "mini itx", "compact"))
    quiet = "quiet" in text or "silent" in text
    low_power = "low power" in text or "efficient" in text or "power efficient" in text

    games = []
    # No reliable rule-based way to extract arbitrary game titles; left to
    # the LLM path. An empty list here just means "unchanged from `existing`".

    if state is None:
        if budget is None:
            raise ValueError("No budget could be extracted and there is no prior conversational state.")
        state = BuildRequest(budget_php=budget)
    else:
        delta = _find_budget_delta(text)
        if delta is not None:
            state.budget_php = max(state.budget_php + delta, 1)
        elif budget is not None:
            state.budget_php = budget

    if use_cases:
        state.use_case = use_cases
    if resolution:
        state.resolution = resolution
    if cpu_brand:
        state.cpu_brand = cpu_brand
    if gpu_brand:
        state.gpu_brand = gpu_brand
    if wifi_required:
        state.wifi_required = True
    if small_form_factor:
        state.small_form_factor = True
    if quiet:
        state.quiet = True
    if low_power:
        state.low_power = True

    return state
