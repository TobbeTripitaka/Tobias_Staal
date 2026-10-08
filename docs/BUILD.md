# How this repository works

One record, four outputs. Every paper, job, talk, award and skill is **one row** in
[`data/records.csv`](../data/records.csv). A build script turns that record into:

| Output | File | What it shows |
|---|---|---|
| Homepage | [`index.html`](../index.html) | Selected items, short author lists (`T Stål, AM Reading`), DOI links |
| Full CV | [`cv/Tobias_Staal_CV_long.pdf`](../cv/Tobias_Staal_CV_long.pdf) | All CV sections, up to 10 authors, DOIs |
| One-page CV | [`cv/Tobias_Staal_CV_short.pdf`](../cv/Tobias_Staal_CV_short.pdf) | Highlights only, strictly one A4 page |
| BibTeX | [`publications/references.bib`](../publications/references.bib) | Every citable item, full names, abstracts |
| Publication list | [`publication_list.pdf`](../publication_list.pdf) | The .bib file, formatted, by category, with abstracts |

Because all outputs read the same row, a correction made once (a DOI, a title, an author)
appears everywhere. Display differs (short vs full author names), content does not.

```
data/
  records.csv        THE record. One row per item. Edit in Excel, Numbers, LibreOffice or a text editor.
  new_entries.yaml   Inbox. Type new items here in YAML (handy for DOIs); the build moves them into the CSV.
  template.yaml      Every field explained, with examples to copy into the inbox.
  settings.yaml      Profile, links, homepage text, section order, labels, and which outputs to build.
  doi_cache/         Downloaded DOI metadata (so builds work offline and are reproducible).
templates/           Look and layout: index.html, cv_long.tex, cv_short.tex, publication_list.tex, cv_common.tex
tools/build.py       The build script (Python 3, needs PyYAML; XeLaTeX for PDFs)
build.ipynb          Optional notebook: the same steps, one cell at a time
update.sh            Build, then git commit + push
.github/workflows/   The same build on GitHub's servers (GitHub Actions)
cv/tex/              LaTeX class, fonts, photo and the generated .tex files
```

## Everyday use

**Add a paper with a DOI.** Put this in `data/new_entries.yaml` and run `./update.sh`:

```yaml
entries:
  - section: article
    doi: 10.1029/2024GL110098
```

Title, authors, journal, volume, issue, pages, publisher and abstract are fetched
automatically. Add `cv_short: yes` (or `include: {cv_short: yes}`) to put it on the one-page CV.

**Add anything else** (a job, talk, award, art project ...): copy the matching block from
`data/template.yaml` into `new_entries.yaml`, fill in what you know, leave the rest blank.

**Edit or delete something:** open `data/records.csv` and change the row; delete the row to
remove the item everywhere. You can also type an `id` in the inbox with only the fields you
want to change.

**Choose where an item appears:** the columns `homepage`, `cv_long`, `cv_short`, `reflist`
take `yes`, `no`, or blank. Blank means the section default (listed in `template.yaml`;
e.g. conference abstracts default to the reference list only).

**Choose what gets built:** the `build:` switches at the top of `data/settings.yaml`, or
`python3 tools/build.py --only homepage cv_short`.

## The rules

1. **Your values win.** DOI metadata only fills empty cells; it never overwrites what you typed.
   Once fetched, the values are written into the CSV, so you can see and correct them.
2. **`-` means empty on purpose.** Type `-` in a cell to keep it blank even if the DOI has a value
   (e.g. a very long abstract).
3. **Titles are kept as published** (American or British spelling as in the journal). Text you write
   yourself (descriptions, headings) is in Australian English.
4. **Authors** are stored in order as `Family, Given; Family, Given`, with full given names where known.
   Corporate authors go in braces: `{Global Heat Flow Data Assessment Group}`. Your name (any spelling listed under
   `my_names` in `settings.yaml`) is shown in bold and always kept when long lists are shortened:
   `NJ Abram, A Purich, …, T Stål et al.`
5. **The build stops** with a clear message if an entry is broken (missing title, unknown section,
   duplicate id ...), if a DOI cannot be resolved and nothing is cached, or if the one-page CV spills
   onto a second page. Nothing half-finished gets published.
6. In free-text cells, `*stars*` give italics and ` | ` starts a new line.

## The CSV columns

| Column | Used for |
|---|---|
| `id` | Unique key; also the BibTeX key. Generated for new items (e.g. `staal2024geology`). |
| `section` | `article`, `submitted`, `chapter`, `report`, `outreach`, `dataset`, `software`, `abstract`, `thesis`, `position`, `education`, `conference`, `work`, `award`, `service`, `art`, `skill` |
| `status` | `published`, `accepted`, `in press`, `in review`, `submitted`, `preprint` |
| `year`, `end` | Year (start); `end` for ranges, e.g. `Present` |
| `title` | Paper title, job title, degree, event, award, skill category |
| `authors` | Authors (papers) or artists/collaborators (art) |
| `venue`, `volume`, `issue`, `pages`, `publisher` | Bibliographic details (`pages` can be an article number) |
| `doi`, `url` | Links |
| `organisation`, `location` | Employer, university, committee |
| `description` | Longer text for the homepage and full CV |
| `short` | One-line text for the one-page CV |
| `homepage`, `cv_long`, `cv_short`, `reflist` | `yes` / `no` / blank = default |
| `bibtype` | Optional BibTeX type override |
| `abstract` | Abstract (reference list and .bib only) |
| `note` | Extra note shown in the reference list (e.g. `PhD thesis`) |

## Running the build

```bash
./update.sh "Add new paper"          # build + commit + push
python3 tools/build.py               # build only
python3 tools/build.py --no-pdf      # skip LaTeX (fast; HTML and .bib only)
python3 tools/build.py --check       # import and validate only
python3 tools/build.py --refresh-doi # download DOI metadata again (still never overwrites your values)
```

The steps always run in this order: import inbox → fill from DOIs → validate → write HTML/.bib/.tex → compile PDFs.

**Requirements on your own computer:** Python 3 with PyYAML (`pip install pyyaml`) and a TeX
distribution with XeLaTeX (MacTeX on macOS; `texlive-xetex texlive-latex-extra texlive-fonts-extra`
on Debian/Ubuntu). The PDFs use the fonts in `cv/tex/fonts` plus Source Sans Pro and FontAwesome from TeX Live.

## GitHub Actions (no local install needed)

`.github/workflows/build.yml` runs the same build on GitHub's servers whenever `data/`,
`templates/` or `tools/` change on `main`, and commits the new HTML and PDFs back. That means you
can add a paper from a browser: open `data/new_entries.yaml` on github.com, click the pencil, add
the DOI, commit. A few minutes later the homepage, CVs and publication list are updated
(progress under the **Actions** tab). You can also start it by hand: **Actions → Build CV and
homepage → Run workflow**. The homepage itself is served by GitHub Pages from `main`.

## Changing the look

* Colours, fonts and layout of the homepage: `templates/index.html` (CSS at the top).
* CV and publication list: `templates/*.tex`; shared list macros and colours in `templates/cv_common.tex`;
  the base class is `cv/tex/cvClass.cls` (Awesome-CV based).
* Section order and headings: `layout:` and `labels:` in `data/settings.yaml`.
* Number of authors shown before `et al.`: `max_authors:` in `data/settings.yaml`.

Placeholders in templates look like `<<name>>`; the build fills them in.
