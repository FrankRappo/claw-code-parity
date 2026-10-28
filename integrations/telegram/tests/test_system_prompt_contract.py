import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
CLAW_PROMPT = (
    REPOSITORY_ROOT
    / "integrations/telegram/prompts/gemma4-abliterated-system-prompt.txt"
)
SYSTEM_PROMPT_DOC = REPOSITORY_ROOT / "SYSTEM_PROMPT.md"


class SystemPromptContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.prompt = CLAW_PROMPT.read_text(encoding="utf-8")
        cls.documentation = SYSTEM_PROMPT_DOC.read_text(encoding="utf-8")

    def test_claw_prompt_requires_direct_answers_without_paternalism(self):
        required_text = (
            "DIRECT ANSWERS — NO PATERNALISM",
            "Answer the task first, in the user's language.",
            "Do not speculate about the user's wellbeing, motives, or competence",
            "Do not act as a substitute guardian, therapist, or judge",
            "Do not recycle moralizing language from older assistant messages",
            "Tie necessary clarification or authority requests to a concrete action",
        )

        for text in required_text:
            with self.subTest(text=text):
                self.assertIn(text, self.prompt)

    def test_claw_prompt_retains_operational_and_real_boundary_contracts(self):
        retained_text = (
            "TodoWrite",
            "The project state under `.claw/` is durable and versioned.",
            "The output ceiling is 32000 tokens",
            "Automatic context compaction begins at 64000 persisted conversation tokens",
            "a genuinely destructive or authority-gated decision",
            "Never fabricate tool access",
        )

        for text in retained_text:
            with self.subTest(text=text):
                self.assertIn(text, self.prompt)

    def test_documentation_describes_role_specific_prompts_and_environment(self):
        self.assertIn("role-specific prompts", self.documentation)
        self.assertIn("ordinary Gemma chat prompt", self.documentation)
        self.assertIn("inherits the full service environment", self.documentation)
        self.assertNotIn(
            "passes only the two explicit prompt variables into the sanitized Claw child environment",
            self.documentation,
        )


if __name__ == "__main__":
    unittest.main()
