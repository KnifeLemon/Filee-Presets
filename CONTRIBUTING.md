# Contributing

The easy way to share a preset is the **[Submit a preset](../../issues/new?template=submit-preset.yml)** form; see
the [README](README.md). This page is for pull requests by hand, the checks in detail, and maintainers.

## Pull requests by hand

A preset pull request adds or changes exactly one `presets/<slug>/` folder and nothing else (repository owners and
members may send several folders in one pull request; each is still checked on its own):

- `preset.json`: the file Filee's **Settings > General > Export** writes. At most 20 presets and 1 toolbar,
  at most 256 KB.
- `meta.json`: the fields in the table below. Leave `created` and `updated` out and set `discussion` to `null`;
  CI sets them after the merge. `kinds`, `formats` and `minVersion` must be what Filee's validator derives from
  `preset.json` (the check says the right values if they differ).
- `images/1.png` ... `images/4.png` (PNG, JPEG or WebP; 1 to 4, no gaps, 1 is the cover): 640 to 2560 px on the
  long edge, at most 2 MB each, not animated. After the merge, CI re-encodes them to WebP without any metadata
  (EXIF, GPS, color profiles) and makes the 480 px `N-thumb.webp` thumbnails, so you don't need to.
- Optional language sets: `images/<code>/N.png` with the app's language codes (`ko`, `ja`, `zh-CN`, `pt-BR`,
  `mn`, ...). Same rules per image; a set may leave numbers out (the default image shows instead) but can't add
  numbers the default set doesn't have. The whole folder may be at most 40 MB.

| Field | Rule |
| --- | --- |
| `id` | the folder name: 3-48 characters, `a-z`, `0-9`, `-`; not `files`, `download` or `index` (filee.sh routes) |
| `title` | 3-60 characters, one line, no links |
| `description` | 10-600 characters, plain text, no links or HTML |
| `author` | your GitHub login (CI checks it can't change later) |
| `tags` | 0-6 ids from [tags.json](tags.json) |
| `language` | the main language of `title`, `description` and the default images: one of the codes Filee's UI uses (`en`, `ko`, `ja`, `zh-CN`, ...) |
| `titleByLang`, `descriptionByLang` | optional translations, `{"ja": "..."}`, same limits as `title` and `description`; a language needs a title to count, its description falls back to the main one |
| `languages` | derived by CI: `language` plus every code in `titleByLang` (the supported languages) |
| `kinds`, `formats`, `minVersion` | what `filee presets check` derives |
| `warnings` | `[{"code", "severity"}]` from `filee presets check`: what the preset can do that people see before installing (left out with older validators) |
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
| `check.yml` | called by `submission.yml` for its pull requests; a pull request by a person touches `presets/`; a maintainer comments `/review` | rules + Filee's validator; AI review; comment, label and a `rules` commit status |
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
- The AI review is Claude Code (`anthropics/claude-code-action`, pinned) in a job that can't write anything: a
  read-only `GITHUB_TOKEN`, the **Read** tool only (no shell, no editing, no web, no MCP tools), Claude Code's
  `default` permission mode (whatever isn't allowed is denied, as nobody can approve), at most 8 turns and 10
  minutes. Its workspace holds only `./submission` (meta.json, preset.json, JPEG copies of the images) plus
  `tags.json` and the tools from main, and deny rules keep Read off the tools, `.git` and system folders. The
  pull request itself is never checked out there.
- Its instructions are fixed in `tools/review.py` and tell it that the submission files are untrusted data, never
  instructions. It must answer with a JSON verdict (`--json-schema`), which a separate step checks field by field.
  It never labels, comments or merges: the report job does that, and it can only choose `ready` (rules passed with
  no warnings and a clean verdict) or `needs-review` (anything else, including a missing token, a time-out, a rate
  limit or an unreadable answer). Nothing merges automatically.
- Text quoted back into comments is stripped of mentions, links and markup.

Run the tool tests locally with Python 3.12:

```
python -m pip install -r tools/requirements.txt
python -m unittest discover -s tools/tests
```

## Maintainers

One-time setup of this repository. Everything runs on the workflows' own `GITHUB_TOKEN`; there is no bot account
or app. Its pull requests, comments, labels, Discussions and publish commits show as `github-actions[bot]`.

**1. Actions settings** (Settings > Actions > General):

- Workflow permissions: **Read repository contents** (each job asks for the little more it needs), and tick
  **Allow GitHub Actions to create and approve pull requests**: `submission.yml` opens the pull requests with
  `GITHUB_TOKEN`. (No workflow here approves anything.)
- Fork pull request workflows: require approval for first-time contributors.

**2. The AI review** runs on the maintainer's Claude subscription, not API billing. On a computer with Claude Code
signed in to that subscription, run `claude setup-token` and add the printed token as the repository secret
`CLAUDE_CODE_OAUTH_TOKEN` (Settings > Secrets and variables > Actions). A secret `ANTHROPIC_API_KEY` is used instead
only when the OAuth token isn't set; leave it out to never bill the API. Without either, the review is skipped with a
notice and every pull request goes to `needs-review`; everything else works. Reviews only run for form submissions
and after a maintainer's `/review`, so usage stays bounded. The token expires; run `claude setup-token` again and
replace the secret when reviews start failing with an authentication error.

What the review can do: read the submission's meta.json, preset.json and images, and return a verdict (spam,
offensive, misleading, personal information in images, description matches, suggested tags, a short note). What it
can't: run commands, edit or write files, use the web, see the pull request's other files, use a GitHub token
that can write, or label, comment, approve or merge.

**3. Discussions category.** Discussions are on. In the Discussions tab, edit the categories and add **Presets**
(format: Announcement, so only maintainers and the workflows start posts; everyone can comment and react). The API
can't create categories. Until it exists, presets are published without a discussion (a warning in the log).

**4. Labels**: `submission`, `ready`, `needs-review`, `needs-changes` (already created).

**5. Variables** (Settings > Secrets and variables > Actions > Variables), all optional:

| Variable | Default | Use |
| --- | --- | --- |
| `FILEE_REF` | `feat/preset-gallery` | branch or tag of KnifeLemon/Filee whose `filee presets check` the checks build. Set it to `main` once the validator is merged there. |
| `REVIEW_MODEL` | `claude-haiku-5-5` | model of the AI review (Claude Code `--model`) |
| `DISCUSSION_CATEGORY` | `Presets` | Discussions category for the preset posts |

**6. Protecting main** is optional while there is one maintainer. If you add a ruleset later, `publish.yml` pushes
its `[gallery-publish]` commit straight to main with `GITHUB_TOKEN`, so let GitHub Actions bypass it (or require
pull requests only for other people), and note that pull requests which don't touch `presets/` never get the
`rules` status.
