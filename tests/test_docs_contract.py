import contextlib
import importlib.util
import io
import json
import pathlib
import re
import sys
import unittest


ROOT = pathlib.Path(__file__).parents[1]

SOLVER_PATH = ROOT / "scripts" / "blindbox_solver.py"
solver_spec = importlib.util.spec_from_file_location(
    "blindbox_solver_docs", SOLVER_PATH
)
solver = importlib.util.module_from_spec(solver_spec)
sys.modules[solver_spec.name] = solver
assert solver_spec.loader is not None
solver_spec.loader.exec_module(solver)

SNAPSHOT_PATH = ROOT / "scripts" / "qiandao_market_snapshot.py"
snapshot_spec = importlib.util.spec_from_file_location(
    "qiandao_market_snapshot_docs", SNAPSHOT_PATH
)
snapshot = importlib.util.module_from_spec(snapshot_spec)
sys.modules[snapshot_spec.name] = snapshot
assert snapshot_spec.loader is not None
snapshot_spec.loader.exec_module(snapshot)


def documented_commands():
    docs = [ROOT / "SKILL.md", ROOT / "README.md"]
    docs += sorted((ROOT / "references").glob("*.md"))
    block_re = re.compile(r"```bash\n(.*?)```", re.S)
    for doc in docs:
        text = doc.read_text(encoding="utf-8")
        for block in block_re.findall(text):
            joined = block.replace("\\\n", " ")
            for line in joined.splitlines():
                line = line.strip()
                if line.startswith("python3 scripts/"):
                    yield doc.name, line


# Placeholders that appear inside documented commands. Numeric placeholders
# must map to numbers; path placeholders must map to a runnable fixture.
BARE_PLACEHOLDERS = {
    "<CNY>": "1",
    "<regulars>": "1",
    "<state.json>": str(ROOT / "examples" / "minimal-demo.json"),
}


def argv_of(command):
    # Quoted placeholders contain spaces; collapse them before tokenizing.
    command = re.sub(r'"<[^"]*>"', '"demo"', command)
    parts = command.split()
    assert parts[0] == "python3" and parts[1].startswith("scripts/")
    argv = []
    for token in parts[2:]:
        if token.startswith("<") and token.endswith(">"):
            if token not in BARE_PLACEHOLDERS:
                raise AssertionError(
                    f"unknown placeholder {token!r} in documented command; "
                    "extend BARE_PLACEHOLDERS in test_docs_contract.py"
                )
            token = BARE_PLACEHOLDERS[token]
        # Docs assume the skill root as cwd; resolve relative paths from there
        # so the commands stay runnable from any working directory.
        if "/" in token and not token.startswith("-"):
            candidate = ROOT / token
            if candidate.is_file():
                token = str(candidate)
        argv.append(token)
    return argv


class DocsContractTests(unittest.TestCase):
    def test_documented_solver_commands_run_cleanly(self):
        ran = 0
        for doc, command in documented_commands():
            if "blindbox_solver.py" not in command:
                continue
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = solver.main(argv_of(command))
            self.assertEqual(
                exit_code, 0, f"{doc}: {command}"
            )
            json.loads(stdout.getvalue())
            ran += 1
        self.assertGreaterEqual(
            ran, 5, "documented solver commands vanished from the skill docs"
        )

    def test_documented_snapshot_commands_parse(self):
        checked = 0
        for doc, command in documented_commands():
            if "qiandao_market_snapshot.py" not in command:
                continue
            try:
                snapshot.parse_args(argv_of(command))
            except SystemExit as exc:
                self.fail(f"{doc}: {command}\nparse_args rejected it: {exc}")
            checked += 1
        self.assertGreaterEqual(
            checked, 2, "documented snapshot commands vanished from the skill docs"
        )

    def test_state_schema_first_json_block_is_a_runnable_state(self):
        text = (ROOT / "references" / "state-schema.md").read_text(
            encoding="utf-8"
        )
        match = re.search(r"```json\n(.*?)```", text, re.S)
        self.assertIsNotNone(match, "state-schema.md lost its json example")
        solver._normalize_state(json.loads(match.group(1)))

    def test_every_example_state_normalizes(self):
        examples = sorted((ROOT / "examples").glob("*.json"))
        self.assertGreaterEqual(len(examples), 5)
        for path in examples:
            with self.subTest(example=path.name):
                solver._normalize_state(
                    json.loads(path.read_text(encoding="utf-8"))
                )


if __name__ == "__main__":
    unittest.main()
