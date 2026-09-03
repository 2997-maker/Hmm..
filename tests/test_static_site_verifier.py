import importlib.util
import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "verify_static_site.py"
SPEC = importlib.util.spec_from_file_location("verify_static_site", MODULE_PATH)
verifier = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(verifier)

ORCHESTRATOR_PATH = ROOT / "tools" / "agent_orchestrator.py"
ORCHESTRATOR_SPEC = importlib.util.spec_from_file_location("agent_orchestrator_checks", ORCHESTRATOR_PATH)
orchestrator = importlib.util.module_from_spec(ORCHESTRATOR_SPEC)
assert ORCHESTRATOR_SPEC.loader is not None
ORCHESTRATOR_SPEC.loader.exec_module(orchestrator)



WORKFLOW = """name: Deploy
steps:
  - with:
      command: pages deploy dist --project-name=example
  - run: |
      cp index.html dist/index.html
      cp style.css dist/style.css
      cp script.js dist/script.js
      printf '%s\\n' index.html script.js style.css | sort > "${RUNNER_TEMP}/expected-files.txt"
"""


class StaticSiteVerifierTests(unittest.TestCase):
    def make_site(self, html='<link rel="stylesheet" href="style.css"><script src="script.js"></script>', workflow=WORKFLOW):
        directory = tempfile.TemporaryDirectory()
        root = Path(directory.name)
        (root / "index.html").write_text(html, encoding="utf-8")
        (root / "style.css").write_text("", encoding="utf-8")
        (root / "script.js").write_text("", encoding="utf-8")
        path = root / ".github/workflows"
        path.mkdir(parents=True)
        (path / "deploy-cloudflare-pages.yml").write_text(workflow, encoding="utf-8")
        self.addCleanup(directory.cleanup)
        return root

    def test_valid_site_and_allowlist_pass(self):
        self.assertEqual(verifier.verify(self.make_site()), [])

    def test_missing_local_reference_fails(self):
        root = self.make_site('<link rel="stylesheet" href="missing.css"><script src="script.js"></script>')
        self.assertEqual(verifier.verify_html_references(root), [
            "index.html references missing local asset: missing.css"
        ])

    def test_external_anchor_and_mail_references_are_not_local_assets(self):
        root = self.make_site("""            <link rel="stylesheet" href="https://fonts.example/style.css">
            <script src="//cdn.example/script.js"></script>
            <a href="#section">anchor</a><a href="mailto:test@example.com">mail</a>
        """)
        self.assertEqual(verifier.verify_html_references(root), [])

    def test_deployment_allowlist_violation_fails(self):
        workflow = WORKFLOW.replace(
            "cp script.js dist/script.js", "cp AGENTS.md dist/AGENTS.md"
        ).replace(
            "index.html script.js style.css", "AGENTS.md index.html style.css"
        )
        errors = verifier.verify_deployment_allowlist(self.make_site(workflow=workflow))
        self.assertTrue(any("deploy copy set" in error for error in errors))
        self.assertTrue(any("expected file set" in error for error in errors))

    def test_unallowlisted_dist_write_bypasses_fail(self):
        bypasses = (
            "touch dist/AGENTS.md",
            "cp ./AGENTS.md dist/AGENTS.md",
            "printf '%s\n' unwanted > dist/AGENTS.md",
            "test ! -e dist; touch dist/AGENTS.md",
        )
        for bypass in bypasses:
            with self.subTest(bypass=bypass):
                errors = verifier.verify_deployment_allowlist(
                    self.make_site(workflow=f"{WORKFLOW}{bypass}\n")
                )
                self.assertTrue(any("unallowlisted dist operation" in error for error in errors))


class CheckConfigurationTests(unittest.TestCase):
    def configured_checks(self):
        return json.loads((ROOT / ".agent/config.json").read_text(encoding="utf-8"))["checks"]

    def test_failed_configured_check_stops_the_check_set(self):
        completed = [mock.Mock(returncode=0, stdout="", stderr="") for _ in range(4)]
        completed[0].returncode = 1
        state = orchestrator.new_state("test", "text", False)
        with (
            mock.patch.object(orchestrator, "load_config", return_value={"checks": self.configured_checks()}),
            mock.patch.object(orchestrator, "run_cmd", side_effect=completed) as run_cmd,
            mock.patch.object(orchestrator, "save_state"),
        ):
            self.assertFalse(orchestrator.run_checks(state, ROOT, "unit"))
        self.assertEqual(run_cmd.call_args_list[0].args[0], ["node", "--check", "script.js"])
        self.assertEqual(state["checks"]["unit"]["lint"]["status"], "FAIL")
        self.assertEqual(state["checks"]["unit"]["test"]["status"], "PASS")

    def test_config_registers_only_the_project_quality_commands(self):
        checks = json.loads((ROOT / ".agent/config.json").read_text(encoding="utf-8"))["checks"]
        self.assertEqual(checks, {
            "lint": ["node", "--check", "script.js"],
            "typecheck": None,
            "test": ["python", "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-v"],
            "build": ["python", "tools/verify_static_site.py"],
        })


if __name__ == "__main__":
    unittest.main()
