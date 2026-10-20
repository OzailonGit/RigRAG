"""Runs the knowledge-note evaluation set (notes_cases.py): whether the right
notes are retrieved, whether answers cite them, and whether answers stick
to what the notes and part data say.

    python -m app.rag.evaluation.notes_eval                  # retrieval and answers (calls the LLM)
    python -m app.rag.evaluation.notes_eval --retrieval-only # retrieval only, no LLM
    python -m app.rag.evaluation.notes_eval --judge          # also ask the LLM if each cited sentence is supported

Checks per case:
- retrieved: every expected note was given to the model.
- cited: the answer cites every expected note.
- invented citations: citations to numbers never served (removed from the
  reply, but counted here).
- unsupported numbers: a number in a citing sentence that's in neither the
  cited notes nor the build's part data ("rated at 1600 RPM [2]" when the
  note says 1550).
- uncited claims: a sentence about a topic only notes cover (BIOS updates,
  noise ratings, PhysX, ...) that cites nothing and isn't in the part data.
- abstain cases: the answer says it has no sourced information (or no owner
  reviews) and cites nothing.
- owner-review cases: the answer cites the part's Amazon reviews, the
  passages retrieved are relevant (share containing one of the case's
  terms), and the answer doesn't copy a passage (8 or more words in a row).
  Counts from passages ("3 of the 8") are allowed numbers.

The deterministic checks miss a sentence that cites a note but says
something the note doesn't ("the kit is on AMD's compatibility list [4]").
--judge catches some of those, but the judge is the same kind of model
being judged and can be wrong itself, so its verdicts are reported
separately and don't fail a case.
"""

import argparse
import re
import sys
import time
from dataclasses import dataclass, field

import httpx
from sqlalchemy.orm import Session

from app.database.session import SessionLocal
from app.json_utils import to_prompt_json
from app.llm.build_summary import summarize_build_result
from app.llm.chat_orchestrator import (
    _FOLLOWUP_NOTES, _build_notes, _notes_for, _part_dicts, handle_chat_message,
)
from app.llm.citations import CITATION, NoteSet, cited_refs
from app.rag.knowledge.notes import note_by_id, owner_review_note_id
from app.rag.knowledge.reviews import ReviewUnavailable, get_review_retriever
from app.llm.explain import explain_build
from app.llm.providers.base import LLMProvider
from app.llm.providers.factory import get_llm_provider
from app.models.case import Case
from app.models.cooler import CpuCooler
from app.models.cpu import Cpu
from app.models.gpu import Gpu
from app.models.motherboard import Motherboard
from app.models.product import Category, Product
from app.models.psu import Psu
from app.rag.evaluation.notes_cases import NOTE_CASES, NoteCase
from app.recommendation.engine import BuildResult, RecommendationEngine
from app.services.chat_sessions import ChatSession

_SPECS = {"cpu": Cpu, "gpu": Gpu, "motherboard": Motherboard, "cooler": CpuCooler, "psu": Psu, "case": Case}

# Topics only notes cover. A sentence mentioning one must cite a note, unless
# the part data itself mentions it.
KNOWLEDGE_TERMS = (
    "resizable bar", "smart access memory", "microcode", "vmin", "instabil", "degrad", "physx", "fsr",
    "dba", "decibel", "noise", "loud", "quiet", "warranty", "bios update", "v-cache", "parked", "parking",
    "expo", "2029", "overclock",
)
ABSTAIN = re.compile(r"\b(?:don't|do not|doesn't|does not|no)\b[^.]{0,40}\b(?:sourced|(?:owner |amazon )*reviews)\b"
                     r"|\bno sourced\b", re.I)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?!\[)|\n+")
# A number, including one attached to a unit ("750W", "8GB"), so it can be
# found in a note that writes it that way.
_NUMBER = re.compile(r"(?<![\d.,])(\d[\d,]*(?:\.\d+)?)(?!\d)")
COPY_WORDS = 8
# Free-tier providers limit tokens per minute; a rate-limited case waits and
# runs again rather than failing the evaluation.
RATE_LIMIT_WAIT_SECONDS = 65
RATE_LIMIT_ATTEMPTS = 6


@dataclass
class CaseResult:
    case: NoteCase
    served: list[str] = field(default_factory=list)
    cited: list[str] = field(default_factory=list)
    reply: str = ""
    failures: list[str] = field(default_factory=list)
    unsupported_numbers: list[str] = field(default_factory=list)
    uncited_claims: list[str] = field(default_factory=list)
    invented_citations: int = 0
    judged: list[tuple[str, bool]] = field(default_factory=list)
    passages: list[str] = field(default_factory=list)
    relevant_passages: int = 0
    copied: list[str] = field(default_factory=list)


def _pin(db: Session, role: str, fields: dict):
    spec = _SPECS[role]
    query = (db.query(Product).join(spec, spec.product_id == Product.id)
             .filter(Product.category == Category(role), Product.price_php.is_not(None)))
    for name, value in fields.items():
        query = query.filter((Product.model if name == "model" else getattr(spec, name)) == value)
    product = query.order_by(Product.price_php).first()
    if product is None:
        raise LookupError(f"No priced {role} matches {fields}")
    return product.id


def _build(db: Session, case: NoteCase) -> tuple:
    pins = {role: _pin(db, role, fields) for role, fields in case.pins.items()}
    request = case.request.model_copy(update={"pinned_parts": pins})
    result = RecommendationEngine.recommend(db, request)
    if result is None:
        raise LookupError("The engine found no build; raise the case's budget")
    return request, result


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "").rstrip(".") for n in _NUMBER.findall(text)}


def _plain(text: str) -> str:
    """Curly quotes made straight, so "don’t" reads as "don't"."""
    return text.translate(str.maketrans("\u2018\u2019\u201c\u201d", "''\"\""))


def _copied(reply: str, passages: list[str]) -> list[str]:
    """Runs of COPY_WORDS or more words the reply shares with a passage."""
    words = lambda text: re.findall(r"[a-z0-9']+", _plain(text).lower())
    reply_words = words(reply)
    reply_runs = {" ".join(reply_words[i:i + COPY_WORDS]) for i in range(len(reply_words) - COPY_WORDS + 1)}
    copied = []
    for passage in passages:
        passage_words = words(passage)
        for i in range(len(passage_words) - COPY_WORDS + 1):
            run = " ".join(passage_words[i:i + COPY_WORDS])
            if run in reply_runs:
                copied.append(run)
                break
    return copied


def _relevant(passage: str, terms: tuple[str, ...]) -> bool:
    lowered = passage.lower()
    return any(term in lowered for term in terms)


def _sentences(reply: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_END.split(reply) if s and s.strip()]


def _check_answer(outcome: CaseResult, reply: str, notes: NoteSet, result: BuildResult) -> None:
    part_data = to_prompt_json(summarize_build_result(result)).lower()
    allowed_numbers = _numbers(part_data) | _numbers(outcome.case.question or "")
    # Counts out of the passages returned, and anything a passage says.
    allowed_numbers |= {str(n) for n in range(len(outcome.passages) + 1)}
    allowed_numbers |= _numbers(" ".join(outcome.passages))
    by_ref = {ref: notes.by_ref(ref) for ref in range(1, len(notes.served) + 1)}
    for sentence in _sentences(reply):
        refs = [int(n) for m in CITATION.finditer(sentence) for n in m.group(1).split(",")]
        if refs:
            cited_text = " ".join(by_ref[r].note.text for r in refs if by_ref.get(r))
            bare = CITATION.sub("", sentence)
            missing = _numbers(bare) - _numbers(cited_text) - allowed_numbers
            outcome.unsupported_numbers += [f"{n} in: {sentence}" for n in sorted(missing)]
        elif not ABSTAIN.search(sentence):
            lowered = sentence.lower()
            if any(term in lowered and term not in part_data for term in KNOWLEDGE_TERMS):
                outcome.uncited_claims.append(sentence)


_JUDGE_SYSTEM = """You check whether a sentence from a PC-build answer is supported by the note it cites.
Reply with exactly one word, SUPPORTED or UNSUPPORTED. A sentence is SUPPORTED only if every claim in it that
isn't a plain part specification is stated by the note. Rephrasing is fine; adding a conclusion the note doesn't
draw, or making its claim stronger, is UNSUPPORTED."""


def _judge(llm: LLMProvider, outcome: CaseResult, reply: str, notes: NoteSet, pause: float) -> None:
    for sentence in _sentences(reply):
        refs = [int(n) for m in CITATION.finditer(sentence) for n in m.group(1).split(",")]
        cited = [notes.by_ref(r) for r in refs if notes.by_ref(r)]
        if not cited:
            continue
        time.sleep(pause)
        note_text = "\n".join(f"- {r.note.title}: {r.note.text}" for r in cited)
        verdict = llm.chat([
            {"role": "system", "content": _JUDGE_SYSTEM},
            {"role": "user", "content": f"Note:\n{note_text}\n\nSentence: {CITATION.sub('', sentence).strip()}"},
        ]).content or ""
        outcome.judged.append((sentence, verdict.strip().upper().startswith("SUPPORTED")))


class _NoReviewData(LookupError):
    """The local Amazon data isn't built, so owner-review cases can't run."""


def _review_ids(request) -> dict[str, str]:
    return {role: str(pid) for role, pid in request.pinned_parts.items()}


def run_case(db: Session, llm: LLMProvider | None, case: NoteCase, judge: bool, pause: float) -> CaseResult:
    outcome = CaseResult(case)
    request, result = _build(db, case)
    expected = list(case.expect)
    if case.owner_reviews_for:
        part_id = _review_ids(request)[case.owner_reviews_for]
        if note_by_id(owner_review_note_id(part_id)) is None:
            raise _NoReviewData("no owner-review note for the pinned part; build data/raw/amazon/ first")
        expected.append(owner_review_note_id(part_id))
    if case.question is None:
        message = "Build me a PC"
        notes = _build_notes(result, request, message)
    else:
        notes = _notes_for(_part_dicts(result), case.question, _FOLLOWUP_NOTES)
    outcome.served = [r.note.id for r in notes.served]

    if llm is None and case.owner_reviews_for:
        found = get_review_retriever().search(case.question, [_review_ids(request)[case.owner_reviews_for]])
        outcome.passages = [p.text for p in found]

    if llm is not None:
        if case.question is None:
            reply = explain_build(llm, result, request, notes=notes)
        else:
            session = ChatSession()
            session.state, session.last_result = request, result
            turn = handle_chat_message(db, llm, session, case.question)
            reply, notes = _plain(turn.reply), turn.notes or NoteSet()
            outcome.passages = turn.review_passages
            # The follow-up may have searched for more notes.
            outcome.served = [r.note.id for r in notes.served]
        reply = _plain(reply)
        outcome.reply = reply
        outcome.copied = _copied(reply, outcome.passages)
        outcome.cited = [notes.by_ref(r).note.id for r in cited_refs(reply) if notes.by_ref(r)]
        outcome.invented_citations = notes.unserved_citations
        _check_answer(outcome, reply, notes, result)
        if judge:
            _judge(llm, outcome, reply, notes, pause)

    if case.relevant_terms:
        outcome.relevant_passages = sum(_relevant(p, case.relevant_terms) for p in outcome.passages)
        if not outcome.passages:
            outcome.failures.append("no review passages retrieved")
    for note_id in expected:
        if note_id not in outcome.served and llm is None and case.owner_reviews_for and note_id.startswith("amazon-"):
            continue  # owner reviews reach the model through search_reviews, which needs the LLM to call it
        if note_id not in outcome.served:
            outcome.failures.append(f"{note_id} not retrieved")
        elif llm is not None and note_id not in outcome.cited:
            outcome.failures.append(f"{note_id} retrieved but not cited")
    if llm is not None:
        if case.abstain and (outcome.cited or not ABSTAIN.search(outcome.reply)):
            outcome.failures.append("answered without a covering note instead of saying so")
        if outcome.invented_citations:
            outcome.failures.append(f"{outcome.invented_citations} citation(s) to notes never served")
        if outcome.unsupported_numbers:
            outcome.failures.append(f"{len(outcome.unsupported_numbers)} unsupported number(s)")
        if outcome.uncited_claims:
            outcome.failures.append(f"{len(outcome.uncited_claims)} uncited claim(s)")
        if outcome.copied:
            outcome.failures.append(f"copied review text from {len(outcome.copied)} passage(s)")
    return outcome


def _print_case(o: CaseResult, answers: bool, verbose: bool) -> None:
    print(f"[{'PASS' if not o.failures else 'FAIL'}] {o.case.name}")
    print(f"    served: {', '.join(o.served) or 'none'}")
    if answers:
        print(f"    cited:  {', '.join(o.cited) or 'none'}")
    for failure in o.failures:
        print(f"    - {failure}")
    if o.case.relevant_terms:
        print(f"    review passages: {o.relevant_passages}/{len(o.passages)} relevant")
    for detail in o.unsupported_numbers + [f"uncited: {c}" for c in o.uncited_claims] + [f"copied: {c}" for c in o.copied]:
        print(f"      {detail}")
    for sentence, supported in o.judged:
        if not supported:
            print(f"      judge, unsupported: {sentence}")
    if verbose and o.reply:
        print("    reply: " + o.reply.replace("\n", "\n           "))


def _run_with_rate_limit_retries(db: Session, llm: LLMProvider | None, case: NoteCase, judge: bool,
                                 pause: float) -> CaseResult:
    for attempt in range(1, RATE_LIMIT_ATTEMPTS + 1):
        try:
            return run_case(db, llm, case, judge, pause)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 429 or attempt == RATE_LIMIT_ATTEMPTS:
                raise
            wait = RATE_LIMIT_WAIT_SECONDS * (1 + attempt // 3)
            print(f"    (rate-limited, waiting {wait}s: {exc.response.text[:160]})", flush=True)
            time.sleep(wait)
    raise AssertionError("unreachable")


def _summarize(outcomes: list[CaseResult], answers: bool) -> None:
    expected = [(o, n) for o in outcomes for n in o.case.expect]
    reviewed = [o for o in outcomes if o.case.relevant_terms and o.passages]
    if reviewed:
        relevant = sum(o.relevant_passages for o in reviewed)
        total = sum(len(o.passages) for o in reviewed)
        print(f"Review passage relevance: {relevant}/{total} retrieved passages mention what was asked")
    print(f"\n{len(outcomes)} cases, {sum(1 for o in outcomes if not o.failures)} pass")
    print(f"Retrieval recall: {sum(n in o.served for o, n in expected)}/{len(expected)} expected notes given to the model")
    if not answers:
        return
    abstains = [o for o in outcomes if o.case.abstain]
    print(f"Citation recall:  {sum(n in o.cited for o, n in expected)}/{len(expected)} expected notes cited")
    print(f"Abstention:       {sum('answered without' not in ' '.join(o.failures) for o in abstains)}/{len(abstains)} "
          "uncovered questions answered with \"no sourced information\"")
    print(f"Invented citations: {sum(o.invented_citations for o in outcomes)}")
    print(f"Unsupported numbers in cited sentences: {sum(len(o.unsupported_numbers) for o in outcomes)}")
    print(f"Uncited claims on note-only topics: {sum(len(o.uncited_claims) for o in outcomes)}")
    owner_cases = [o for o in outcomes if o.case.owner_reviews_for]
    if owner_cases:
        cited = sum(not any("amazon-reviews" in f for f in o.failures) for o in owner_cases)
        print(f"Owner-review answers citing the part's reviews: {cited}/{len(owner_cases)}")
    print(f"Answers copying review text: {sum(bool(o.copied) for o in outcomes)}")
    judged = [ok for o in outcomes for _, ok in o.judged]
    if judged:
        print(f"Judge: {sum(judged)}/{len(judged)} cited sentences supported by their notes")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--retrieval-only", action="store_true", help="check retrieval without calling the LLM")
    parser.add_argument("--judge", action="store_true", help="ask the LLM whether each cited sentence is supported")
    parser.add_argument("--pause", type=float, default=10.0, help="seconds between LLM calls, for rate limits")
    parser.add_argument("--verbose", action="store_true", help="print every reply")
    args = parser.parse_args(argv)

    llm = None if args.retrieval_only else get_llm_provider()
    db = SessionLocal()
    outcomes = []
    try:
        for i, case in enumerate(NOTE_CASES):
            if llm is not None and i:
                time.sleep(args.pause)
            try:
                outcomes.append(_run_with_rate_limit_retries(db, llm, case, args.judge, args.pause))
            except (_NoReviewData, ReviewUnavailable) as exc:
                print(f"[SKIP] {case.name}: {exc}")
                continue
            _print_case(outcomes[-1], answers=llm is not None, verbose=args.verbose)
            sys.stdout.flush()
    finally:
        db.close()
    _summarize(outcomes, answers=llm is not None)
    sys.exit(0 if all(not o.failures for o in outcomes) else 1)


if __name__ == "__main__":
    main()
