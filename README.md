# Filee Presets

Ready-made presets and toolbars for [Filee](https://github.com/KnifeLemon/Filee), shared by its users.
Pick one in Filee's **Preset gallery** (Presets page) or on [filee.sh](https://filee.sh) and install it with one
click. Installing always adds new presets; it never replaces your own.

A preset is a saved set of conversion settings (target format and options). Presets are settings, not code: nothing
in this repository runs on your computer.

## Submit a preset

You need a GitHub account. You don't need to know Git.

1. In Filee, open **Settings > General > Export**, choose the presets (and the toolbar, if you want to share it) and
   save `filee-presets.json`.
2. Make 1 to 4 images that show what the preset does: the donut slice and its settings, or a before/after of a
   sample file. PNG or JPEG, 640 to 2560 px on the long edge, at most 2 MB each, not animated. The first image is
   the cover.
3. Open **[Submit a preset](../../issues/new?template=submit-preset.yml)** and fill in the form. **Drag
   `filee-presets.json` into the "Preset file" box and the images into the "Images" box**; GitHub uploads them and
   puts links in the text. (You can also click a box and paste, or use the attach button under it.)
4. Submit. Within a few minutes a bot replies on your issue: either a list of things to fix, or a link to the pull
   request it made from your files.
5. To change anything, **edit the issue**. The bot checks it again and updates the pull request.

A maintainer then merges the pull request, and the preset appears in the app and on filee.sh. Merging also opens a
[Discussion](../../discussions/categories/presets) for the preset: a thumbs-up there is a like, and comments go
there too.

## Rules

Allowed: conversion presets, tool presets and toolbars (donut profiles) that Filee can export, including settings
that need care, such as replacing or moving the original file, overwriting existing files, copying to a folder,
editing or removing metadata, or using a translation model. Filee's validator lists these as **warnings** (high,
medium or info). They are saved with the preset, shown in the pull request's check comment, and shown in the app
and on filee.sh before anyone installs it.

Not allowed, and rejected by the checks:

- Programs and commands: "Run a program" presets and anything else that runs a command.
- Secrets: API keys, tokens, passwords, custom endpoints with credentials.
- Personal paths and data: absolute or personal folders (`C:\Users\...`, `/home/...`, `/Users/...`), `..` in paths,
  file name patterns that could leave the output folder, and names, e-mail addresses or GPS positions written into
  files.
- Links in the title or description, spam, ads, offensive or misleading content (a description that hides what the
  warnings say counts as misleading).
- Images showing personal information: real names, faces, addresses, document contents or file paths. Images you
  didn't make yourself.

The checks work from an allow-list: a field Filee's validator doesn't know fails the check.

## How a submission is checked

1. **Rules** (decides first): the bot builds the preset folder, re-encodes the images to WebP and removes all
   metadata (EXIF, GPS, camera and software tags, color profiles), and makes the thumbnails. Filee's own validator
   (`filee presets check`) and this repository's rules then check the preset file, the fields, the images and
   duplicates.
2. **AI review** (advisory): Claude Code, with read-only access to the submission's files and nothing else,
   answers a fixed set of questions: spam, offensive or misleading? Does the description match the preset? Do the
   images show personal information? It suggests tags. It can't run anything, write anything or label the pull
   request; a separate step reads its answer and sorts the pull request into `ready` or `needs-review`.
3. **A maintainer merges.** Nothing is published before that.

## License: CC0

Everything you submit, the preset and the images, is published under
[CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) (public domain dedication): anyone can use, change and
share it, for any purpose, without asking. The gallery still shows who submitted each preset. See [LICENSE](LICENSE).

The scripts and workflows in `.github/` and `tools/` are MIT-licensed ([LICENSE-TOOLS](LICENSE-TOOLS)).

## Removal requests

To remove your own preset, or one that breaks the rules or shows something it shouldn't, open a
[blank issue](../../issues/new) titled `Remove: <preset name>` and say why. For personal information in an image,
don't repeat the information in the issue; the preset's name is enough. You can also use **Report content** on the
preset's Discussion. Maintainers remove the folder; the preset disappears from the app and the site with the next
publish. Copies people already installed stay on their computers (CC0 can't be withdrawn).

## Repository layout

```
presets/<slug>/preset.json          the exported Filee preset bundle
presets/<slug>/meta.json            title, description, author, tags, languages, ... (author and derived fields set by CI)
presets/<slug>/images/1.webp        1 to 4 images, 1 is the cover
presets/<slug>/images/1-thumb.webp  thumbnails (480 px), made by CI
index.json                          the published list, regenerated by CI on every merge
tags.json                           the tags a preset can use
```

The app and the site read `index.json` (mirrored on filee.sh). `stats` in this repository's copy are always zero;
filee.sh fills them in.

See [CONTRIBUTING.md](CONTRIBUTING.md) for pull requests by hand, the checks in detail and the maintainer setup.
