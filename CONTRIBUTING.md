# Contributing

The easy way to share a preset is the **[Submit a preset](../../issues/new?template=submit-preset.yml)** form; see
the [README](README.md). This page is for pull requests by hand, the checks in detail, and maintainers.

## Pull requests by hand

A preset pull request adds or changes exactly one `presets/<slug>/` folder and nothing else:

- `preset.json`: the file Filee's **Settings > General > Export** writes. At most 20 presets and 1 toolbar,
  at most 256 KB.
- `meta.json`: the fields in the table below. Leave `created` and `updated` out and set `discussion` to `null`;
  CI sets them after the merge. `kinds`, `formats` and `minVersion` must be what Filee's validator derives from
  `preset.json` (the check says the right values if they differ).
- `images/1.webp` ... `images/4.webp` and a `images/N-thumb.webp` (480 px long edge) for each. WebP, 640 to 2560 px
  on the long edge, at most 2 MB, no metadata. The form does this conversion for you; by hand, strip all metadata.

| Field | Rule |
| --- | --- |
| `id` | the folder name: 3-48 characters, `a-z`, `0-9`, `-` |
| `title` | 3-60 characters, one line, no links |
| `description` | 10-600 characters, plain text, no links or HTML |
| `author` | your GitHub login (CI checks it can't change later) |
| `tags` | 0-6 ids from [tags.json](tags.json) |
| `languages` | 1-6 of the codes Filee's UI uses (`en`, `ko`, `ja`, `zh-CN`, ...) |
| `kinds`, `formats`, `minVersion` | what `filee presets check` derives |
| `created`, `updated`, `discussion` | set by CI on main (leave out / `null`) |
| `license` | `CC0-1.0` |

Pull requests from forks get the rules check right away (result in the job summary). The AI review and the PR
comment run when a maintainer comments `/review`.

New tags: suggest them in the pull request or an issue; a maintainer adds them to `tags.json` (and to the form's
Tags list in `.github/ISSUE_TEMPLATE/submit-preset.yml`) first.

## The checks

| Workflow | When | What |
| --- | --- | --- |
| `submission.yml` | a `submission` issue is opened or edited | builds the folder from the issue's attachments and opens or updates the pull request |
| `check.yml` | a pull request touches `presets/`, or a maintainer comments `/review` | rules + Filee's validator; AI review; comment and label |
| `publish.yml` | a push to main | validates everything again, fills the CI fields, creates Discussions, regenerates `index.json` |
| `tools.yml` | changes to `tools/` or `.github/` | unit tests of the tools |

Labels: `submission` (form issues and their pull requests), `ready` (rules passed with no warnings and the AI review
found nothing; a maintainer can merge), `needs-review` (a maintainer must look), `needs-changes` (the submitter must
fix something).

How untrusted content is handled:

- The issue body is read from the event payload file, never put into a shell command.
- Only `https://github.com/user-attachments/...` links in the Preset file and Images fields are downloaded. Every
  redirect must stay on GitHub's attachment storage, no credentials are sent, and sizes are capped while
  downloading. Files are parsed as JSON or decoded as images, never run.
- Images are decoded with Pillow (pinned in `tools/requirements.txt`) and written again as new WebP files, so no
  metadata survives.
- Pull request files are checked out as data; the tools always come from main. Links (symlinks) and unexpected
  files fail the check.
- The AI review gets the submission as data inside a fixed instruction and must answer in a fixed JSON shape, which
  is checked field by field. It can only choose between `ready` and `needs-review`. Nobody and nothing merges
  automatically.
- Text quoted back into comments is stripped of mentions, links and markup.

Run the tool tests locally with Python 3.12:

```
python -m pip install -r tools/requirements.txt
python -m unittest discover -s tools/tests
```

## Maintainers

One-time setup of this repository.

**1. The bot (a GitHub App).** Pull requests opened with the built-in `GITHUB_TOKEN` don't start other workflows,
so the submission pull requests, comments, labels, Discussions and the publish commit come from a GitHub App.

- Create it under **Settings > Developer settings > GitHub Apps > New GitHub App**: name `filee-presets-bot`,
  homepage `https://github.com/KnifeLemon/Filee-Presets`, **Webhook: off**.
- Repository permissions: **Contents: read & write**, **Pull requests: read & write**, **Issues: read & write**,
  **Discussions: read & write**, Metadata: read (automatic). Nothing else, no account permissions, no events.
- "Where can this app be installed?": **Only on this account**. Install it with **Only select repositories >
  Filee-Presets**.
- Generate a private key (a `.pem` file).
- In Filee-Presets **Settings > Secrets and variables > Actions > Secrets**, add `PRESETS_BOT_APP_ID` (the App ID or
  Client ID from the app's page) and `PRESETS_BOT_PRIVATE_KEY` (the whole `.pem` content). Then delete the local
  `.pem` file.

**2. The AI review.** Add the secret `ANTHROPIC_API_KEY`. Without it the review is skipped with a notice and every
pull request goes to `needs-review`. Variable `REVIEW_MODEL` picks the model (default `claude-haiku-5-5`).

**3. Variables** (Settings > Secrets and variables > Actions > Variables), all optional:

| Variable | Default | Use |
| --- | --- | --- |
| `FILEE_REF` | `feat/preset-gallery` | branch or tag of KnifeLemon/Filee whose `filee presets check` the checks build. Set it to `main` once the validator is merged there. |
| `REVIEW_MODEL` | `claude-haiku-5-5` | model of the AI review |
| `DISCUSSION_CATEGORY` | `Presets` | Discussions category for the preset posts |

**4. Discussions category.** Settings > General > Features: Discussions on. Then in the Discussions tab, edit the
categories and add **Presets** (format: Announcement, so only maintainers and the bot start posts; everyone can
comment and react). The API can't create categories.

**5. Labels**: `submission`, `ready`, `needs-review`, `needs-changes`.

**6. Actions settings** (Settings > Actions > General): Workflow permissions **Read repository contents**; leave
"Allow GitHub Actions to create and approve pull requests" off (the bot does that, not `GITHUB_TOKEN`). Fork pull
request workflows: require approval for first-time contributors.

**7. Protect main** (Settings > Rules > Rulesets > New branch ruleset, target `main`):

- Require a pull request before merging (1 approval is optional for a single maintainer), require status checks:
  **rules** (from "Check preset"), block force pushes, restrict deletions.
- **Bypass list: add the `filee-presets-bot` app** (always allow), so `publish.yml` can push its
  `[gallery-publish]` commit straight to main. No other bypass is needed.
