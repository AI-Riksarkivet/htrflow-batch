#!/usr/bin/env sh
# Build or serve the documentation site from a staged copy of docs/ WITHOUT
# the project's history: the stories (the backlog's product view, mirrored to
# Azure DevOps), specs and plans, audits, the decision log and the run logs.
# They stay in git at their paths, which code, READMEs and stories link to,
# but the site documents the system as it is. The generator has no exclude
# option, so the staging is the exclusion. The slide decks are staged out for
# a different reason: they are Marp sources, not site pages, and they build
# with scripts/slides.sh into the same ./site. Output lands in ./site.
set -eu
cmd=${1:-build}
[ $# -gt 0 ] && shift
stage=.docs-site
rm -rf "$stage" site
mkdir -p "$stage"
cp -R docs "$stage/docs"
for history in features superpowers audits slides \
  how-it-works/decision-log.md development/e2e-indexed-jobs.md development/test-log.md; do
  rm -rf "$stage/docs/$history"
done
cp zensical.toml "$stage/zensical.toml"
# The site documents the system in general: no project ids, dates, versions,
# hardware or one site's hosts (scripts/docs_lint.py, scripts/docs-lint.allow).
python3 scripts/docs_lint.py "$stage/docs" README.md
cd "$stage"
${ZENSICAL:-uvx zensical} "$cmd" "$@"
[ "$cmd" = build ] && mv site ../site
