import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SELECTOR = ROOT / "scripts" / "select_python.sh"


class PythonSelectionTests(unittest.TestCase):
    def run_selector(self, env):
        return subprocess.run(
            ["bash", str(SELECTOR)],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_rejects_python_below_310_with_required_message(self):
        env = dict(os.environ)
        env["APPLYRX_PYTHON"] = "/usr/bin/python3"
        result = self.run_selector(env)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("Applyrx build requires Python 3.10 or newer", result.stderr)
        self.assertIn("system Python will not be modified", result.stderr)

    def test_selects_an_explicit_compatible_interpreter(self):
        with tempfile.TemporaryDirectory() as directory:
            interpreter = Path(directory) / "python3.12"
            interpreter.write_text(
                "#!/bin/sh\n"
                'if [ "$1" = "-c" ]; then exit 0; fi\n'
                'if [ "$1" = "--version" ]; then echo "Python 3.12.0"; exit 0; fi\n'
                "exit 1\n",
                encoding="utf-8",
            )
            interpreter.chmod(0o755)
            env = dict(os.environ)
            env["APPLYRX_PYTHON"] = str(interpreter)
            result = self.run_selector(env)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(str(interpreter), result.stdout.strip())


if __name__ == "__main__":
    unittest.main()
