"""The word "Back" is reserved for the shared back control.

This is the rule the app kept breaking by hand: a link reading "Back to the
board" or "Back to Players" that was really a forward navigation to a view
the user had never been on. A convention nothing enforces is a convention
that decays, so it is checked against the template source here rather than
left to review.
"""

import re
from pathlib import Path

import pytest

import pigskin_mastermind

TEMPLATES = Path(pigskin_mastermind.__file__).parent / "templates"

#: Any anchor or button, captured as (attributes, inner markup).
_CONTROL = re.compile(r"<(?:a|button)\s([^>]*)>(.*?)</(?:a|button)>", re.DOTALL | re.I)

#: "Back" as a word, which is what a user reads as a promise about history.
#: "Welcome back" and "fall back" are prose, not navigation, so the match is
#: case-sensitive and anchored to the start of the phrase.
_SAYS_BACK = re.compile(r"\bBack\b")


def _templates():
    return sorted(TEMPLATES.rglob("*.html"))


def _visible_text(markup):
    """Strip tags and Jinja expressions, leaving what a reader sees."""
    without_jinja = re.sub(r"\{\{.*?\}\}|\{%.*?%\}", " ", markup, flags=re.DOTALL)
    return " ".join(re.sub(r"<[^>]+>", " ", without_jinja).split())


def _offending_controls(path):
    """Controls that say "Back" without being the shared back control."""
    source = path.read_text(encoding="utf-8")
    offenders = []
    for attrs, inner in _CONTROL.findall(source):
        if not _SAYS_BACK.search(_visible_text(inner)):
            continue
        if re.search(r'class="[^"]*\bnav-back\b', attrs):
            continue
        offenders.append(_visible_text(inner))
    return offenders


@pytest.mark.parametrize("path", _templates(), ids=lambda p: p.name)
def test_only_the_shared_control_says_back(path):
    offenders = _offending_controls(path)
    assert not offenders, (
        f"{path.relative_to(TEMPLATES)} labels a control 'Back' without using "
        f"nav.back_link(): {offenders}. A control that takes the user somewhere "
        f"new must be named for where it goes."
    )


def test_the_back_macro_is_the_only_definition_of_the_control():
    """One `nav-back` anchor exists; every page renders it through the macro."""
    definitions = [
        p for p in _templates() if 'class="nav-back' in p.read_text(encoding="utf-8")
    ]
    assert definitions == [TEMPLATES / "components" / "_nav.html"]


@pytest.mark.parametrize("path", _templates(), ids=lambda p: p.name)
def test_no_template_reads_the_retired_back_url_variable(path):
    """`back_url` was the unvalidated raw param; `back` is the resolved target.

    A template left on the old name renders an empty link rather than failing,
    so a half-finished migration is otherwise invisible.
    """
    assert "back_url" not in path.read_text(encoding="utf-8")


#: Markup that only ever appears in a breadcrumb trail: the explicit landmark
#: label, the `›` separator, and the chevron-right path used as a separator.
_CRUMB_MARKERS = ('aria-label="Breadcrumb"', "&rsaquo;", 'd="M9 5l7 7-7 7"')

_NAV_BLOCK = re.compile(r"<nav\b.*?</nav>", re.DOTALL | re.I)


@pytest.mark.parametrize("path", _templates(), ids=lambda p: p.name)
def test_breadcrumbs_are_only_built_by_the_macro(path):
    """Two hand-rolled trails had drifted apart — one `›`, one SVG chevron."""
    if path.name == "_nav.html":
        return
    for block in _NAV_BLOCK.findall(path.read_text(encoding="utf-8")):
        found = [m for m in _CRUMB_MARKERS if m in block]
        assert not found, (
            f"{path.relative_to(TEMPLATES)} hand-rolls a breadcrumb trail "
            f"({found}). Render it with nav.breadcrumbs() so every trail on "
            f"every page looks and behaves the same."
        )
