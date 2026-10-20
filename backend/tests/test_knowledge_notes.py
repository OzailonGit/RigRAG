"""Tests the knowledge notes file, note matching, retrieval without vectors,
and citation handling. None of these need the database or Qdrant."""

import json

import pytest

from app.llm.citations import NoteSet, cited_refs, drop_unserved_citations, sources, sources_markdown
from app.llm.explain import explain_build
from app.llm.providers.base import LLMProvider, LLMResponse
from app.rag.knowledge.notes import Note, applies_to_part, load_notes, notes_for_parts
from app.rag.knowledge.retrieval import NoteRetriever, RetrievedNote


def _note(note_id: str, applies_to: list[dict], text: str = "Some sourced fact.", topic: str = "quirk") -> Note:
    return Note(id=note_id, title=note_id.replace("-", " "), topic=topic, text=text,
                applies_to=tuple(applies_to), checked="2026-10-20",
                sources=({"publisher": "Maker", "title": "Spec page", "url": "https://example.com/spec",
                          "kind": "manufacturer"},))


ARC_A750 = {"category": "gpu", "manufacturer": "ASRock", "model": "Challenger Arc A750", "chip": "Intel Arc A750"}
RTX_5060_TI_8 = {"category": "gpu", "manufacturer": "MSI", "model": "Ventus 2X", "chip": "GeForce RTX 5060 Ti",
                 "vram_gb": 8}
RTX_5060_TI_16 = {**RTX_5060_TI_8, "vram_gb": 16}
I5_14600KF = {"category": "cpu", "manufacturer": "Intel", "model": "Core i5-14600KF", "socket": "LGA1700"}
A620_BOARD = {"category": "motherboard", "manufacturer": "ASRock", "model": "A620M-HDV", "socket": "AM5",
              "chipset": "A620"}


# --- The notes file ---

def test_every_note_is_sourced_and_well_formed():
    notes = load_notes()
    assert notes
    for note in notes:
        assert note.sources, note.id
        for source in note.sources:
            assert source["url"].startswith("https://"), note.id
            # user_reviews: owner-review notes, when data/raw/amazon/ has been built locally
            assert source["kind"] in ("manufacturer", "review", "press", "user_reviews"), note.id
        assert note.applies_to and all("category" in c for c in note.applies_to), note.id
        assert "\u2014" not in note.text and "\u2014" not in note.title, note.id


def test_every_note_matches_its_own_example_parts():
    # A misspelled chip or chipset would make a note silently unreachable.
    by_id = {note.id: note for note in load_notes()}
    assert applies_to_part(by_id["arc-a-series-resizable-bar"], ARC_A750)
    assert applies_to_part(by_id["raptor-lake-vmin-shift"], I5_14600KF)
    assert applies_to_part(by_id["a620-b840-no-cpu-overclocking"], A620_BOARD)


# --- Matching ---

def test_chip_names_match_across_vendor_prefixes():
    note = _note("arc", [{"category": "gpu", "chip": ["Arc A750"]}])
    assert applies_to_part(note, ARC_A750)  # catalog says "Intel Arc A750"


def test_every_field_of_a_condition_must_match():
    note = _note("8gb", [{"category": "gpu", "chip": ["GeForce RTX 5060 Ti"], "vram_gb": 8}])
    assert applies_to_part(note, RTX_5060_TI_8)
    assert not applies_to_part(note, RTX_5060_TI_16)


def test_category_is_a_hard_filter():
    note = _note("am5", [{"category": "motherboard", "socket": "AM5"}])
    am5_cpu = {"category": "cpu", "model": "Ryzen 5 9600X", "socket": "AM5"}
    assert not applies_to_part(note, am5_cpu)
    assert applies_to_part(note, A620_BOARD)


def test_intel_f_chips_match_the_base_model():
    note = _note("14600k", [{"category": "cpu", "model": ["Core i5-14600K"]}])
    assert applies_to_part(note, I5_14600KF)


def test_a_part_missing_the_field_doesnt_match():
    note = _note("vram", [{"category": "gpu", "vram_gb": 8}])
    assert not applies_to_part(note, ARC_A750)


def test_notes_for_parts_lists_the_parts_each_note_covers():
    notes = (_note("arc", [{"category": "gpu", "chip": ["Arc A750"]}]),
             _note("lga1700", [{"category": "cpu", "socket": "LGA1700"}]),
             _note("unrelated", [{"category": "case", "model": "Nothing"}]))
    matched = notes_for_parts([ARC_A750, I5_14600KF], notes)
    assert set(matched) == {"arc", "lga1700"}
    assert matched["arc"] == [ARC_A750]


def test_load_notes_rejects_a_note_without_sources(tmp_path):
    path = tmp_path / "notes.json"
    path.write_text(json.dumps({"notes": [{"id": "x", "title": "x", "topic": "quirk", "text": "x",
                                           "applies_to": [{"category": "cpu"}], "sources": [], "checked": "2026-01-01"}]}))
    with pytest.raises(ValueError, match="no source"):
        load_notes(path)


# --- Retrieval without vectors ---

def test_retrieval_never_returns_a_note_for_parts_not_in_play():
    notes = (_note("arc-power", [{"category": "gpu", "chip": ["Arc A750"]}], text="Power supply needs."),
             _note("rtx-power", [{"category": "gpu", "chip": ["GeForce RTX 5080"]}], text="Power supply needs."))
    found = NoteRetriever(notes, use_vectors=False).for_parts([ARC_A750], "what power supply do I need")
    assert [r.note.id for r in found] == ["arc-power"]
    assert found[0].parts == ["ASRock Challenger Arc A750"]


def test_word_ranking_puts_the_relevant_note_first():
    notes = (_note("physx", [{"category": "gpu"}], text="Older games lose GPU PhysX effects."),
             _note("noise", [{"category": "gpu"}], text="The fan is rated at 30 dBA."))
    found = NoteRetriever(notes, use_vectors=False).for_parts([ARC_A750], "how loud is the fan", limit=1)
    assert [r.note.id for r in found] == ["noise"]


# --- Citations ---

def _served(*ids: str) -> NoteSet:
    notes = NoteSet()
    for note_id in ids:
        notes.add(RetrievedNote(_note(note_id, [{"category": "gpu"}]), ["Some GPU"], 1.0))
    return notes


def test_wide_brackets_are_normalized_and_unserved_citations_dropped():
    notes = _served("a", "b")
    reply = drop_unserved_citations("Enable it in the BIOS【1】. Also true [2, 7]. Invented [9].", notes)
    assert reply == "Enable it in the BIOS[1]. Also true [2]. Invented."
    assert notes.unserved_citations == 2
    assert cited_refs(reply) == [1, 2]


def test_markdown_links_arent_citations():
    assert cited_refs("See [1](https://example.com) and [2].") == [2]


def test_sources_follow_citation_order_and_skip_uncited_notes():
    notes = _served("first", "second", "third")
    cited = sources("Something [3]. Something else [1].", notes)
    assert [(c["ref"], c["note_id"]) for c in cited] == [(3, "third"), (1, "first")]
    markdown = sources_markdown(cited)
    assert "**Sources**" in markdown and "[Maker](https://example.com/spec)" in markdown
    assert sources_markdown([]) == ""


def test_serving_a_note_twice_keeps_one_number():
    notes = NoteSet()
    note = _note("a", [{"category": "gpu"}])
    assert notes.add(RetrievedNote(note, ["GPU one"], 1.0)) == 1
    assert notes.add(RetrievedNote(note, ["GPU two"], 0.5)) == 1
    assert notes.served[0].parts == ["GPU one", "GPU two"]


class _RecordingLLM(LLMProvider):
    def __init__(self, reply: str):
        self.reply = reply
        self.messages = None

    def chat(self, messages, tools=None, json_mode=False) -> LLMResponse:
        self.messages = messages
        return LLMResponse(content=self.reply)


def test_explanation_is_given_the_notes_and_loses_invented_citations(monkeypatch):
    import app.llm.explain as explain

    monkeypatch.setattr(explain, "summarize_build_result", lambda result: {"primary": "a build"})
    llm = _RecordingLLM("Turn on Resizable BAR [1]. And [4].")
    notes = _served("arc")
    reply = explain_build(llm, result=None, request=_request(), notes=notes)

    payload = json.loads(llm.messages[1]["content"])
    assert payload["notes"][0]["ref"] == 1 and payload["notes"][0]["applies_to"] == ["Some GPU"]
    assert "Cite it by putting its ref" in llm.messages[0]["content"]
    assert reply == "Turn on Resizable BAR [1]. And."


def _request():
    from app.schemas import BuildRequest

    return BuildRequest(budget_php=50000)


def test_owner_review_notes_match_one_part_and_stay_out_of_open_search():
    review = Note(id="amazon-reviews-1", title="Amazon owner reviews of the ASRock A750", topic="owner_reviews",
                  text="120 reviews, averaging 4.2 of 5 stars.", checked="2026-10-20",
                  applies_to=({"category": "gpu", "id": "1111-aaaa"},),
                  sources=({"publisher": "Amazon customer reviews", "title": "x", "url": "https://www.amazon.com/dp/B0",
                            "kind": "user_reviews"},))
    sourced = _note("arc", [{"category": "gpu", "chip": ["Arc A750"]}], text="Reviews say enable Resizable BAR.")
    retriever = NoteRetriever((review, sourced), use_vectors=False)
    part = {**ARC_A750, "id": "1111-AAAA"}

    assert [r.note.id for r in retriever.for_parts([part], "reviews", topics={"owner_reviews"})] == ["amazon-reviews-1"]
    assert [r.note.id for r in retriever.for_parts([ARC_A750], "reviews")] == ["arc"]  # another card: no review note
    assert [r.note.id for r in retriever.search("reviews")] == ["arc"]


# --- Review passages (search_reviews) ---

class _FakeReviews:
    def __init__(self, passages):
        self.passages = passages
        self.calls = []

    def search(self, query, part_ids, limit=8):
        from app.rag.knowledge.reviews import ReviewPassage

        self.calls.append((query, part_ids, limit))
        return [ReviewPassage(text=t, rating=2.0, date="2022-03-01", verified_purchase=True, helpful_votes=0, score=0.8)
                for t in self.passages[:limit]]


def _review_executor(monkeypatch, reviews, notes_by_part):
    import app.llm.tools as tools

    parts = {"cooler-1": {"id": "cooler-1", "category": "cooler", "manufacturer": "NZXT", "model": "Kraken X73"},
             "gpu-1": {"id": "gpu-1", "category": "gpu", "manufacturer": "MSI", "model": "RTX 5080"}}
    monkeypatch.setattr(tools, "note_by_id", lambda note_id: notes_by_part.get(note_id))
    executor = tools.ToolExecutor(db=None, build_part_ids=list(parts), review_retriever=reviews)
    monkeypatch.setattr(executor, "_tool_get_part_details", lambda pid: parts[pid])
    return executor


def test_search_reviews_returns_passages_with_the_parts_review_ref(monkeypatch):
    note = Note(id="amazon-reviews-cooler-1", title="Amazon owner reviews of the NZXT Kraken X73", topic="owner_reviews",
                text="1,718 reviews, averaging 4.4 of 5 stars.", applies_to=({"category": "cooler", "id": "cooler-1"},),
                sources=({"publisher": "Amazon customer reviews", "title": "x", "url": "https://www.amazon.com/dp/B1",
                          "kind": "user_reviews"},), checked="2026-10-20")
    reviews = _FakeReviews(["The pump started clicking after a month.", "Quiet pump, no leaks."])
    executor = _review_executor(monkeypatch, reviews, {"amazon-reviews-cooler-1": note})

    result = executor.execute("search_reviews", {"query": "pump clicking"})

    cooler, gpu = sorted(result["results"], key=lambda r: "ref" not in r)
    assert cooler["ref"] == 1 and cooler["passages_returned"] == 2
    assert cooler["review_summary"].startswith("1,718 reviews")
    assert "No Amazon owner reviews" in gpu["message"]  # a 2025 card: no data, said plainly
    assert reviews.calls == [("pump clicking", ["cooler-1"], 8)]  # only the reviewed part is searched
    assert executor.notes.served[0].note.id == "amazon-reviews-cooler-1"
    assert executor.review_passages == ["The pump started clicking after a month.", "Quiet pump, no leaks."]


def test_search_reviews_reports_an_unreachable_index(monkeypatch):
    from app.rag.knowledge.reviews import ReviewUnavailable

    class Down:
        def search(self, *args, **kwargs):
            raise ReviewUnavailable("connection refused")

    note = _note("amazon-reviews-cooler-1", [{"category": "cooler", "id": "cooler-1"}], topic="owner_reviews")
    executor = _review_executor(monkeypatch, Down(), {"amazon-reviews-cooler-1": note})
    assert executor.execute("search_reviews", {"query": "noise", "product_ids": ["cooler-1"]}) == {
        "error": "Owner review search is unavailable right now."}


def test_copied_review_text_is_caught():
    from app.rag.evaluation.notes_eval import _copied

    passage = "After about three months the pump started making a loud clicking noise every time the PC idled."
    assert _copied("One owner said the pump started making a loud clicking noise every time it idled [1].", [passage])
    assert not _copied("A few owners mention the pump clicking at idle after a few months [1].", [passage])
