from app.llm.providers.base import strip_thinking


def test_strips_leaked_reasoning_with_no_trailing_answer():
    # qwen3's output on a tool-call response: reasoning text ending in
    # </think> with nothing after it.
    raw = "Okay, let's see. The user wants a GPU...\n</think>\n\n"
    assert strip_thinking(raw) is None


def test_strips_leading_think_block_and_keeps_real_answer():
    raw = "Let me think about this...\nOkay I've decided.\n</think>\n\nThe RTX 4060 is a good fit."
    assert strip_thinking(raw) == "The RTX 4060 is a good fit."


def test_passes_through_content_with_no_think_block():
    assert strip_thinking("A perfectly normal answer.") == "A perfectly normal answer."


def test_handles_none():
    assert strip_thinking(None) is None


def test_handles_empty_string():
    assert strip_thinking("") is None
