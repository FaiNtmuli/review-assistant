from langgraph.graph import END
from langgraph.types import Send

from review_assistant.config import Settings
from review_assistant.graph import build_graph
from review_assistant.nodes.parse import fan_out_files
from review_assistant.nodes.verify import route_after_verify
from review_assistant.nodes.human import route_after_human_gate
from review_assistant.schemas import CommentVerdict, FileChange

EXPECTED_NODES = {
    "ingest",
    "parse_diff",
    "classify",
    "review_file",
    "verify",
    "revise",
    "aggregate",
    "human_gate",
    "finalize",
}


def test_graph_contains_designed_nodes():
    compiled = build_graph()
    assert EXPECTED_NODES <= set(compiled.get_graph().nodes)


def test_graph_has_two_loops_back_to_upstream():
    mermaid = build_graph().get_graph().draw_mermaid()
    assert "revise" in mermaid and "verify" in mermaid
    assert "human_gate" in mermaid


def test_fan_out_sends_one_task_per_file():
    state = {
        "files": {
            "a.py": FileChange(path="a.py"),
            "b.py": FileChange(path="b.py"),
        },
        "settings": Settings().for_state(),
    }
    result = fan_out_files(state)
    assert isinstance(result, list)
    assert len(result) == 2
    assert all(isinstance(item, Send) for item in result)
    assert {item.arg["file"]["path"] for item in result} == {"a.py", "b.py"}


def test_fan_out_falls_back_to_aggregate_without_files():
    assert fan_out_files({"files": {}, "settings": {}}) == "aggregate"


def test_verify_routes_to_revise_while_rounds_remain():
    state = {
        "settings": Settings(max_rounds=3).for_state(),
        "revision_round": 1,
        "verdicts": {"a.py": [CommentVerdict(comment_id="x", file_path="a.py", passed=False)]},
    }
    assert route_after_verify(state) == "revise"


def test_verify_routes_to_aggregate_after_the_last_round():
    state = {
        "settings": Settings(max_rounds=3).for_state(),
        "revision_round": 3,
        "verdicts": {"a.py": [CommentVerdict(comment_id="x", file_path="a.py", passed=False)]},
    }
    assert route_after_verify(state) == "aggregate"


def test_verify_routes_to_aggregate_when_everything_passed():
    state = {
        "settings": Settings(max_rounds=3).for_state(),
        "revision_round": 0,
        "verdicts": {"a.py": [CommentVerdict(comment_id="x", file_path="a.py", passed=True)]},
    }
    assert route_after_verify(state) == "aggregate"


def test_human_gate_routes():
    assert route_after_human_gate({"decision": "approve"}) == "finalize"
    assert route_after_human_gate({"decision": "revise"}) == "revise"
    assert route_after_human_gate({"decision": "abort"}) == END
