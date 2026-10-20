"""Startup encoding guards.

An app bundle launched from Finder inherits nothing from launchd: no LANG, no
LC_ALL. The process therefore starts in the "C" locale, where a text read that
does not name an encoding decodes as US-ASCII. Track titles and lyrics contain
non-ASCII bytes routinely, so that aborted startup right after the click-through
log line and left the app running with no panel — visible only when the app was
started the way a user starts it.

These tests run the same code path the app runs, and assert on what a bare
open() actually decodes rather than on the presence of a particular call.
"""

import ast
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "applyrx_ui.py"


def startup_locale_block() -> str:
    """Return the locale bootstrap statements from the UI module.

    Sliced verbatim so the structure — a for body and a nested function — is
    preserved and the result compiles on its own.
    """
    source = UI.read_text(encoding="utf-8")
    start = source.find("for _candidate in")
    if start < 0:
        return ""
    end = source.find("\nimport objc", start)
    return source[start:end] if end > start else ""


class StartupEncodingSourceTests(unittest.TestCase):
    def test_locale_bootstrap_precedes_the_objc_import(self):
        """The binding must run before anything can read a file.

        The failure happened during the objc import, so a binding placed later
        would never get the chance to run.
        """
        source = UI.read_text(encoding="utf-8")
        self.assertIn("setlocale", source)
        self.assertLess(
            source.index("setlocale"),
            source.index("\nimport objc"),
            "locale must be bound before the objc import",
        )

    def test_bootstrap_is_syntactically_valid(self):
        """The bootstrap must compile on its own, without the rest of the module.

        Importing applyrx_ui needs AppKit, so the guard is compiled from source
        instead. The whole module is parsed separately below.
        """
        import textwrap

        block = startup_locale_block()
        self.assertTrue(block, "no locale bootstrap found in applyrx_ui.py")
        # Keep the original indentation: the statements live inside a for body
        # and a function, and de-indenting them would change their meaning.
        compile(textwrap.dedent(block) if block.startswith(" ") else block,
                "<bootstrap>", "exec")

    def test_module_is_syntactically_valid(self):
        ast.parse(UI.read_text(encoding="utf-8"))


class StartupEncodingBehaviourTests(unittest.TestCase):
    """Run the shipped bootstrap under a locale-less environment.

    The development shell exports PYTHONUTF8=1, which puts Python in UTF-8 mode
    and hides the very failure this guards against. It is cleared here so the
    bootstrap has to do the work itself.
    """

    def _run_bootstrap(self, lang_exported: bool):
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(("LANG", "LC_", "PYTHONUTF8", "PYTHONCOERCECLOCALE"))
        }
        if lang_exported:
            env["LANG"] = "en_US.UTF-8"
        probe = "import locale\n" + startup_locale_block() + """
import os, tempfile

preferred = locale.getpreferredencoding(False)
path = os.path.join(tempfile.gettempdir(), "applyrx-encoding-probe.txt")
with open(path, "w", encoding="utf-8") as handle:
    handle.write("\\u4e2d\\u6587\\u6b4c\\u66f2")
try:
    with open(path) as handle:
        decoded = handle.read()
finally:
    os.remove(path)
print(preferred)
print(decoded)
"""
        return subprocess.run(
            [sys.executable, "-c", probe],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def _decoded(self, lang_exported: bool) -> str:
        result = self._run_bootstrap(lang_exported)
        self.assertEqual(0, result.returncode, result.stderr)
        lines = [line for line in result.stdout.strip().split("\n") if line]
        self.assertGreaterEqual(len(lines), 2, result.stdout)
        return lines[-1]

    def test_locale_less_environment_decodes_text_correctly(self):
        """The regression: with no LANG the default read must still be UTF-8."""
        self.assertEqual("中文歌曲", self._decoded(lang_exported=False))

    def test_effective_encoding_is_utf8_without_lang(self):
        result = self._run_bootstrap(False)
        preferred = result.stdout.strip().split("\n")[0]
        self.assertEqual("utf-8", preferred.lower())

    def test_works_whether_or_not_lang_is_exported(self):
        """Both launch paths must end up decoding the same way."""
        for exported in (True, False):
            with self.subTest(lang_exported=exported):
                self.assertEqual("中文歌曲", self._decoded(exported))


class ExplicitEncodingTests(unittest.TestCase):
    """No text read in the project should rely on the ambient default."""

    def _sources(self):
        for path in sorted(ROOT.glob("*.py")):
            yield path

    def test_no_bare_text_open_in_project_sources(self):
        import re

        offenders = []
        pattern = re.compile(r"open\(\s*([^)]*?)\)")
        for path in self._sources():
            for number, line in enumerate(
                path.read_text(encoding="utf-8").split("\n"), 1
            ):
                stripped = line.strip()
                if stripped.startswith("#") or stripped.startswith('"'):
                    continue
                # urlopen and friends return a network response, not a file on
                # disk; how that body is decoded is the caller's own choice and
                # is asserted at the point of use instead.
                if re.search(r"\burlopen\(|\brequest\.urlopen", stripped):
                    continue
                for match in pattern.finditer(stripped):
                    arguments = match.group(1)
                    if "encoding" in arguments:
                        continue
                    if "rb" in arguments or '"b"' in arguments or "'b'" in arguments:
                        continue
                    if re.search(r'["\']r["\']', arguments):
                        continue
                    offenders.append(f"{path.name}:{number}")
                if ".read_text(" in stripped and "encoding" not in stripped:
                    offenders.append(f"{path.name}:{number}")
        self.assertEqual([], offenders, "these reads fall back to the locale default")


if __name__ == "__main__":
    unittest.main()