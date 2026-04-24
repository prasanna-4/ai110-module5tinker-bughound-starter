from bughound_agent import BugHoundAgent
from llm_client import MockClient


def test_workflow_runs_in_offline_mode_and_returns_shape():
    agent = BugHoundAgent(client=None)  # heuristic-only
    code = "def f():\n    print('hi')\n    return True\n"
    result = agent.run(code)

    assert isinstance(result, dict)
    assert "issues" in result
    assert "fixed_code" in result
    assert "risk" in result
    assert "logs" in result

    assert isinstance(result["issues"], list)
    assert isinstance(result["fixed_code"], str)
    assert isinstance(result["risk"], dict)
    assert isinstance(result["logs"], list)
    assert len(result["logs"]) > 0


def test_offline_mode_detects_print_issue():
    agent = BugHoundAgent(client=None)
    code = "def f():\n    print('hi')\n    return True\n"
    result = agent.run(code)

    assert any(issue.get("type") == "Code Quality" for issue in result["issues"])


def test_offline_mode_proposes_logging_fix_for_print():
    agent = BugHoundAgent(client=None)
    code = "def f():\n    print('hi')\n    return True\n"
    result = agent.run(code)

    fixed = result["fixed_code"]
    assert "logging" in fixed
    assert "logging.info(" in fixed


def test_mock_client_forces_llm_fallback_to_heuristics_for_analysis():
    # MockClient returns non-JSON for analyzer prompts, so agent should fall back.
    agent = BugHoundAgent(client=MockClient())
    code = "def f():\n    print('hi')\n    return True\n"
    result = agent.run(code)

    assert any(issue.get("type") == "Code Quality" for issue in result["issues"])
    # Ensure we logged the fallback path
    assert any("Falling back to heuristics" in entry.get("message", "") for entry in result["logs"])


def test_malformed_llm_issues_are_filtered():
    # Guardrail: issues missing a msg or with an unrecognized severity should be
    # silently dropped so they never reach the risk assessor or UI.
    class MalformedClient:
        def complete(self, system_prompt: str, user_prompt: str) -> str:
            # Returns issues with: one valid, one missing msg, one bad severity
            return (
                '[{"type":"Bug","severity":"High","msg":"real issue"},'
                ' {"type":"Bug","severity":"Critical","msg":"bad severity"},'
                ' {"type":"Bug","severity":"Low","msg":""}]'
            )

    agent = BugHoundAgent(client=MalformedClient())
    code = "def f():\n    return 1\n"
    result = agent.run(code)

    # Only the one valid issue should survive
    assert len(result["issues"]) == 1
    assert result["issues"][0]["msg"] == "real issue"
    assert any("Filtered" in entry.get("message", "") for entry in result["logs"])
