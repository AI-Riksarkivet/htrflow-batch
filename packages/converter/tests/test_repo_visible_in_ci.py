"""B63 Task 20A: the dagger `test`/`checks` containers used to mount the
repo through an ``Include`` allow-list in ``.dagger/main.go`` -- a path not
named there was invisible to every test that ran in CI, and twice a test
passed only because its own fixture was missing rather than because it was
checked. ``docs/reference/campaign-yaml.md`` was never on that list. This
test reads it, so a regression back to an allow-list (one that again omits
docs/) fails here instead of passing vacuously."""

from pathlib import Path

WORKSPACE_ROOT = Path(__file__).parents[3]
CAMPAIGN_YAML_DOC = WORKSPACE_ROOT / "docs" / "reference" / "campaign-yaml.md"


def test_campaign_yaml_doc_is_visible_in_ci():
    assert CAMPAIGN_YAML_DOC.is_file(), (
        f"{CAMPAIGN_YAML_DOC} not found -- the CI container's source mount is "
        "excluding docs/ again (see .dagger/main.go's repoExclude)"
    )
    content = CAMPAIGN_YAML_DOC.read_text(encoding="utf-8")
    assert content.strip(), f"{CAMPAIGN_YAML_DOC} is empty"
