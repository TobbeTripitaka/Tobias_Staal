#!/usr/bin/env python3
"""
Build Tobias Stål's homepage, CVs and publication list from one record.

    data/records.csv        the master record: one row per item (paper, job, skill ...)
    data/new_entries.yaml   optional inbox: new items typed in YAML, merged into the CSV
    data/settings.yaml      profile, links, text blocks, layout and build switches

Run:
    python3 tools/build.py              # import inbox, fill DOIs, write + compile all selected outputs
    python3 tools/build.py --no-pdf     # write files but skip LaTeX
    python3 tools/build.py --only homepage cv_short
    python3 tools/build.py --refresh-doi   # re-download DOI metadata (still never overwrites your values)
    python3 tools/build.py --check      # validate only

Steps (always in this order):
    1. import data/new_entries.yaml -> data/records.csv  (inbox is archived and emptied)
    2. fill blank fields from DOI metadata (Crossref / DataCite, cached in data/doi_cache/)
    3. validate every row (stops with a clear message on errors)
    4. write index.html, references.bib and the .tex files
    5. compile the PDFs with XeLaTeX (one-page check for the short CV)

Rules:
    * Values you type always win over DOI metadata. DOI data only fills empty cells.
    * Type a single "-" in a cell to keep it empty even if the DOI has a value.
    * Include flags (homepage, cv_long, cv_short, reflist): yes / no / blank (= section default).
Only dependency beyond the standard library: PyYAML (requests is optional).
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import html
import json
import re
import shutil
import subprocess
import sys
import unicodedata
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RECORDS = DATA / "records.csv"
INBOX = DATA / "new_entries.yaml"
SETTINGS = DATA / "settings.yaml"
CACHE = DATA / "doi_cache"
ARCHIVE = DATA / "imported"
TEMPLATES = ROOT / "templates"

OUT_HTML = ROOT / "index.html"
OUT_BIB = ROOT / "publications" / "references.bib"
OUT_REFTEX = ROOT / "cv" / "tex" / "publication_list.tex"
OUT_REFPDF = ROOT / "publication_list.pdf"
CV_DIR = ROOT / "cv" / "tex"
OUT_CVLONG_TEX = CV_DIR / "cv_long.tex"
OUT_CVSHORT_TEX = CV_DIR / "cv_short.tex"
OUT_CVLONG_PDF = ROOT / "cv" / "Tobias_Staal_CV_long.pdf"
OUT_CVSHORT_PDF = ROOT / "cv" / "Tobias_Staal_CV_short.pdf"

# --------------------------------------------------------------------------- #
#  Columns of records.csv (order = column order in the file)
# --------------------------------------------------------------------------- #
COLUMNS = [
    "id", "section", "status", "year", "end", "title", "authors",
    "venue", "volume", "issue", "pages", "publisher", "doi", "url",
    "organisation", "location", "description", "short",
    "homepage", "cv_long", "cv_short", "reflist",
    "bibtype", "abstract", "note",
]
OUTPUTS = ["homepage", "cv_long", "cv_short", "reflist"]

# section -> (is_citable, default bibtype, default flags homepage/cv_long/cv_short/reflist)
SECTIONS = {
    "article":    (True,  "article",       "yes yes no  yes"),
    "submitted":  (True,  "unpublished",   "yes yes no  yes"),
    "chapter":    (True,  "incollection",  "yes yes no  yes"),
    "report":     (True,  "techreport",    "yes yes no  yes"),
    "outreach":   (True,  "article",       "yes yes no  yes"),
    "dataset":    (True,  "misc",          "yes yes no  yes"),
    "software":   (True,  "misc",          "yes yes no  yes"),
    "abstract":   (True,  "inproceedings", "no  no  no  yes"),
    "thesis":     (True,  "phdthesis",     "no  no  no  yes"),
    "position":   (False, "",              "yes yes yes no"),
    "education":  (False, "",              "yes yes yes no"),
    "conference": (False, "",              "yes yes no  no"),
    "work":       (False, "",              "yes yes no  no"),
    "award":      (False, "",              "yes yes yes no"),
    "service":    (False, "",              "yes yes yes no"),
    "art":        (False, "",              "yes yes no  no"),
    "skill":      (False, "",              "yes yes no  no"),
}
YES = {"yes", "y", "true", "1", "x"}
NO = {"no", "n", "false", "0"}


class BuildError(Exception):
    pass


def log(msg: str) -> None:
    print(msg, flush=True)


# --------------------------------------------------------------------------- #
#  Reading and writing the record
# --------------------------------------------------------------------------- #
def load_settings() -> dict:
    return yaml.safe_load(SETTINGS.read_text(encoding="utf-8"))


def read_records() -> list[dict]:
    if not RECORDS.exists():
        return []
    with RECORDS.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    out = []
    for r in rows:
        row = {c: (r.get(c) or "").strip() for c in COLUMNS}
        extra = set(r) - set(COLUMNS) - {None}
        if extra:
            raise BuildError(f"records.csv has unknown column(s): {sorted(extra)}")
        if any(row.values()):
            out.append(row)
    return out


def write_records(rows: list[dict]) -> None:
    with RECORDS.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, quoting=csv.QUOTE_MINIMAL)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in COLUMNS})


def val(r: dict, key: str) -> str:
    """Cell value, with the '-' placeholder meaning 'intentionally empty'."""
    v = (r.get(key) or "").strip()
    return "" if v == "-" else v


# --------------------------------------------------------------------------- #
#  Step 1: import the YAML inbox
# --------------------------------------------------------------------------- #
INBOX_HEADER = """\
# =============================================================================
#  new_entries.yaml — inbox for new or updated items
# =============================================================================
#  Copy a block from data/template.yaml below `entries:`, fill in what you know
#  (blank fields are fine; a DOI fills the rest) and run ./update.sh.
#  The build moves the entries into data/records.csv and archives this file in
#  data/imported/. To change an existing item, use its id: non-empty values
#  here replace the values in the CSV.
# =============================================================================
entries: []
"""


def make_id(row: dict, existing: set[str]) -> str:
    authors = parse_authors(val(row, "authors"))
    fam = authors[0]["family"] if authors and authors[0]["family"] else ""
    base = fam or val(row, "organisation") or val(row, "section") or "item"
    word = next((w for w in re.findall(r"[A-Za-z]+", val(row, "title"))
                 if w.lower() not in {"a", "an", "the", "of", "on", "in", "and", "for"}), "")
    base = id_fold(f"{base}{val(row, 'year')}{word}").lower()
    base = re.sub(r"[^a-z0-9]", "", base) or "item"
    key, n = base, 2
    while key in existing:
        key, n = f"{base}{n}", n + 1
    return key


def yaml_entry_to_row(e: dict) -> dict:
    row = {c: "" for c in COLUMNS}
    inc = e.get("include") or {}
    for k, v in e.items():
        if k == "include":
            continue
        if k not in COLUMNS:
            raise BuildError(f"new_entries.yaml: unknown field '{k}' (allowed: {', '.join(COLUMNS)})")
        if k == "authors" and isinstance(v, list):
            v = "; ".join(str(a).strip() for a in v if str(a).strip())
        if isinstance(v, bool):
            v = "yes" if v else "no"
        row[k] = "" if v is None else str(v).strip()
    for k, v in inc.items():
        if k not in OUTPUTS:
            raise BuildError(f"new_entries.yaml: unknown include target '{k}' (allowed: {OUTPUTS})")
        row[k] = "" if v is None else ("yes" if v is True else "no" if v is False else str(v))
    return row


def import_inbox(rows: list[dict]) -> list[dict]:
    if not INBOX.exists():
        INBOX.write_text(INBOX_HEADER, encoding="utf-8")
        return rows
    doc = yaml.safe_load(INBOX.read_text(encoding="utf-8")) or {}
    entries = doc.get("entries") or []
    if not entries:
        return rows
    by_id = {r["id"]: r for r in rows}
    added = updated = 0
    for e in entries:
        if not isinstance(e, dict):
            raise BuildError("new_entries.yaml: every entry must start with '- ' and contain fields")
        new = yaml_entry_to_row(e)
        if not new["section"] and not new["id"]:
            raise BuildError(f"new_entries.yaml: entry without section: {e}")
        if not new["id"] and new["doi"]:  # same DOI as an existing row -> update that row
            match = [r["id"] for r in rows if r["doi"] and norm_doi(r["doi"]).lower() == norm_doi(new["doi"]).lower()]
            if match:
                new["id"] = match[0]
                log(f"  DOI {norm_doi(new['doi'])} already in records.csv as '{match[0]}' -> updating it")
        if new["id"] and new["id"] in by_id:
            target = by_id[new["id"]]
            for c in COLUMNS:
                if new[c] and new[c] != target[c]:
                    log(f"  update {new['id']}.{c}: {target[c][:40]!r} -> {new[c][:40]!r}")
                    target[c] = new[c]
            updated += 1
        else:
            if not new["id"]:
                if new["doi"]:  # give DOI metadata a chance to supply author/year for the id
                    fill_from_doi(new, fetch=True, quiet=True)
                new["id"] = make_id(new, set(by_id))
            rows.append(new)
            by_id[new["id"]] = new
            added += 1
    ARCHIVE.mkdir(exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.copy(INBOX, ARCHIVE / f"{stamp}.yaml")
    INBOX.write_text(INBOX_HEADER, encoding="utf-8")
    log(f"Imported inbox: {added} new, {updated} updated (copy kept in data/imported/{stamp}.yaml)")
    return rows


# --------------------------------------------------------------------------- #
#  Step 2: DOI metadata
# --------------------------------------------------------------------------- #
def norm_doi(doi: str) -> str:
    doi = doi.strip()
    doi = re.sub(r"^(https?://)?(dx\.)?doi\.org/", "", doi, flags=re.I)
    return re.sub(r"^doi:\s*", "", doi, flags=re.I)


def cache_path(doi: str) -> Path:
    return CACHE / (re.sub(r"[^A-Za-z0-9._-]", "_", doi.lower()) + ".json")


def fetch_csl(doi: str, fetch: bool, refresh: bool = False) -> dict | None:
    p = cache_path(doi)
    if p.exists() and not refresh:
        return json.loads(p.read_text(encoding="utf-8"))
    if not fetch:
        return None
    req = urllib.request.Request(
        "https://doi.org/" + urllib.request.quote(doi, safe="/:;()._-"),
        headers={"Accept": "application/vnd.citationstyles.csl+json",
                 "User-Agent": "Tobias_Staal-cv-build (https://github.com/TobbeTripitaka/Tobias_Staal)"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # network down, unknown DOI ...
        if p.exists():
            log(f"  ! DOI {doi}: lookup failed ({exc}); using cached copy")
            return json.loads(p.read_text(encoding="utf-8"))
        log(f"  ! DOI {doi}: lookup failed ({exc})")
        return None
    CACHE.mkdir(exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return data


CORPORATE = re.compile(r"\b(group|collaboration|consortium|team|project|network|committee)\b", re.I)


def csl_author(a: dict) -> str:
    fam, giv, lit = a.get("family"), a.get("given"), a.get("literal") or a.get("name")
    if fam and giv and CORPORATE.search(f"{giv} {fam}"):
        return "{" + f"{giv} {fam}" + "}"
    if fam:
        return f"{fam}, {giv}" if giv else fam
    if lit:
        if CORPORATE.search(lit):
            return "{" + lit + "}"
        parts = lit.split()
        return f"{parts[-1]}, {' '.join(parts[:-1])}" if len(parts) > 1 else lit
    return ""


def clean_abstract(s: str) -> str:
    s = re.sub(r"<jats:title>.*?</jats:title>", " ", s, flags=re.S)
    s = re.sub(r"<[^>]+>", " ", s)
    for _ in range(3):                      # some sources encode entities twice (&amp;#160;)
        s = html.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    return re.sub(r"^(Abstract|ABSTRACT)[:.]?\s+", "", s)


def csl_to_fields(j: dict) -> dict:
    first = lambda v: (v[0] if isinstance(v, list) and v else v if isinstance(v, str) else "") or ""
    year = ""
    for k in ("issued", "published-print", "published-online", "created"):
        dp = (j.get(k) or {}).get("date-parts") or [[None]]
        if dp and dp[0] and dp[0][0]:
            year = str(dp[0][0])
            break
    url = j.get("URL") or ""
    if "doi.org" in url:
        url = ""
    return {
        "title": html.unescape(re.sub(r"<[^>]+>", "", first(j.get("title")))).strip(),
        "authors": "; ".join(x for x in (csl_author(a) for a in j.get("author", [])) if x),
        "year": year,
        "venue": html.unescape(first(j.get("container-title"))),
        "volume": str(j.get("volume") or ""),
        "issue": str(j.get("issue") or ""),
        "pages": str(j.get("page") or j.get("article-number") or "").replace("-", "–"),
        "publisher": html.unescape(j.get("publisher") or ""),
        "url": url,
        "abstract": clean_abstract(j.get("abstract") or ""),
    }


def fill_from_doi(row: dict, fetch: bool, refresh: bool = False, quiet: bool = False) -> list[str]:
    doi = norm_doi(val(row, "doi"))
    if not doi:
        return []
    row["doi"] = doi
    j = fetch_csl(doi, fetch, refresh)
    if not j:
        return []
    filled = []
    for k, v in csl_to_fields(j).items():
        if v and not (row.get(k) or "").strip():   # never overwrite; '-' counts as a value
            row[k] = v
            filled.append(k)
    if filled and not quiet:
        log(f"  DOI {doi} -> filled {', '.join(filled)}  [{row.get('id')}]")
    return filled


# --------------------------------------------------------------------------- #
#  Step 3: validation
# --------------------------------------------------------------------------- #
def validate(rows: list[dict]) -> list[str]:
    errors, seen = [], set()
    for i, r in enumerate(rows, start=2):  # line 1 is the header
        where = f"records.csv line {i} ({r.get('id') or 'no id'})"
        if not r["id"]:
            errors.append(f"{where}: missing id")
        elif r["id"] in seen:
            errors.append(f"{where}: duplicate id '{r['id']}'")
        elif not re.fullmatch(r"[A-Za-z0-9_:.-]+", r["id"]):
            errors.append(f"{where}: id may only contain letters, digits and _ : . -")
        seen.add(r["id"])
        sec = r["section"]
        if sec not in SECTIONS:
            errors.append(f"{where}: unknown section '{sec}' (allowed: {', '.join(SECTIONS)})")
            continue
        for o in OUTPUTS:
            v = r[o].lower()
            if v and v not in YES | NO:
                errors.append(f"{where}: {o} must be yes/no/blank, not '{r[o]}'")
        if not val(r, "title"):
            errors.append(f"{where}: missing title")
        citable = SECTIONS[sec][0]
        if citable:
            if not val(r, "authors"):
                errors.append(f"{where}: missing authors (add them or a DOI)")
            if not re.fullmatch(r"\d{4}", val(r, "year")):
                errors.append(f"{where}: year must be four digits, not '{r['year']}'")
            if sec == "article" and not val(r, "venue"):
                errors.append(f"{where}: journal articles need a venue (journal name)")
        elif sec in {"position", "education", "conference", "work", "award", "service", "art"}:
            if not val(r, "year") and sec != "education":
                errors.append(f"{where}: missing year")
    return errors


def included(r: dict, output: str) -> bool:
    v = r[output].lower()
    if v in YES:
        return True
    if v in NO:
        return False
    flags = dict(zip(OUTPUTS, SECTIONS[r["section"]][2].split()))
    return flags[output] == "yes"


# --------------------------------------------------------------------------- #
#  Names and author lists
# --------------------------------------------------------------------------- #
TRANSLIT = str.maketrans({"å": "aa", "Å": "Aa", "ä": "ae", "Ä": "Ae", "ö": "oe", "Ö": "Oe",
                          "ø": "oe", "Ø": "Oe", "æ": "ae", "Æ": "Ae", "ü": "ue", "Ü": "Ue", "ß": "ss"})


def ascii_fold(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def id_fold(s: str) -> str:
    return ascii_fold(s.translate(TRANSLIT))


def parse_authors(s: str) -> list[dict]:
    out = []
    for part in [p.strip() for p in s.split(";") if p.strip()]:
        if part.startswith("{") and part.endswith("}"):
            out.append({"family": "", "given": "", "literal": part[1:-1]})
        elif "," in part:
            fam, giv = [x.strip() for x in part.split(",", 1)]
            out.append({"family": fam, "given": giv, "literal": ""})
        else:
            out.append({"family": "", "given": "", "literal": part})
    return out


def initials(given: str) -> str:
    pieces = [p for p in re.split(r"[\s.\-]+", given) if p]
    return "".join(p[0].upper() for p in pieces)


def is_me(a: dict, settings: dict) -> bool:
    names = {ascii_fold(n).lower() for n in settings["profile"]["my_names"]}
    fam = ascii_fold(a["family"]).lower()
    giv = a["given"].strip()[:1].upper()
    return fam in names and giv == "T"


def short_name(a: dict) -> str:
    if a["literal"]:
        return a["literal"]
    ini = initials(a["given"])
    return f"{ini} {a['family']}".strip()


def full_name(a: dict) -> str:
    return a["literal"] or f"{a['given']} {a['family']}".strip()


def author_tokens(authors: str, settings: dict, max_n: int | None):
    """Return [(text, is_me)] plus a flag for 'et al.'; shortened around the owner's name."""
    alist = parse_authors(authors)
    names = [(settings["profile"]["my_short_name"] if is_me(a, settings) else short_name(a),
              is_me(a, settings)) for a in alist]
    if not max_n or len(names) <= max_n:
        return names, False
    shown = names[:max_n]
    if any(m for _, m in shown):
        return shown, True
    me = [i for i, (_, m) in enumerate(names) if m]
    if not me:
        return shown, True
    k = me[0]
    return names[:max_n - 1] + [("…", False), names[k]], k < len(names) - 1


# --------------------------------------------------------------------------- #
#  Escaping
# --------------------------------------------------------------------------- #
TEX_SPECIAL = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
               "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\^{}"}


def tex(s: str) -> str:
    s = "".join(TEX_SPECIAL.get(ch, ch) for ch in s)
    s = s.replace("\u2009", " ").replace("\u00a0", "~")
    return s


def esc(s: str) -> str:
    return html.escape(s, quote=True)


def italic_markup(s: str, fmt: str) -> str:
    """Allow *italic* inside free-text cells (e.g. work titles)."""
    if fmt == "html":
        return re.sub(r"\*(.+?)\*", r"<i>\1</i>", esc(s))
    return re.sub(r"\*(.+?)\*", r"\\textit{\1}", tex(s))


def link_markup(s: str, fmt: str) -> str:
    return italic_markup(s, fmt)


# --------------------------------------------------------------------------- #
#  Citation text (shared by homepage and CVs so they never disagree)
# --------------------------------------------------------------------------- #
STATUS_TEXT = {"submitted": "Submitted", "in review": "In review", "in press": "In press",
               "accepted": "Accepted", "preprint": "Preprint", "in prep": "In preparation"}


def venue_details(r: dict, with_numbers: bool) -> str:
    bits = []
    vol, iss, pages = val(r, "volume"), val(r, "issue"), val(r, "pages")
    if with_numbers:
        if vol:
            bits.append(vol + (f"({iss})" if iss else ""))
        if pages:
            bits.append(pages)
    return ", ".join(bits)


def citation(r: dict, settings: dict, fmt: str, max_n: int | None, details: bool = True,
             link_doi: bool = True) -> str:
    e = esc if fmt == "html" else tex
    names, etal = author_tokens(val(r, "authors"), settings, max_n)
    parts = []
    for text, me in names:
        t = e(text)
        parts.append((f"<strong>{t}</strong>" if fmt == "html" else f"\\textbf{{{t}}}") if me else t)
    auth = ", ".join(parts) + (" et al." if etal else "")
    title = e(val(r, "title")).rstrip(".")
    venue = val(r, "venue") or val(r, "publisher")
    sec = r["section"]
    if sec == "dataset":
        title += " [Dataset]"
    elif sec == "software":
        title += " [Software]"
    out = f"{auth} — {title}."
    if venue:
        v = e(venue)
        out += f" <i>{v}</i>" if fmt == "html" else f" \\textit{{{v}}}"
        num = venue_details(r, details)
        out += (f", {e(num)}." if num else ".")
    status = val(r, "status").lower()
    if status and status != "published":
        out += f" {STATUS_TEXT.get(status, val(r, 'status'))}."
    doi = norm_doi(val(r, "doi"))
    if link_doi and doi:
        if fmt == "html":
            out += f' <a href="https://doi.org/{esc(doi)}" target="_blank" rel="noopener">doi:{esc(doi)}</a>'
        else:
            out += f" \\href{{https://doi.org/{doi}}}{{doi:{tex(doi)}}}"
    elif link_doi and val(r, "url"):
        u = val(r, "url")
        out += (f' <a href="{esc(u)}" target="_blank" rel="noopener">link</a>' if fmt == "html"
                else f" \\url{{{u}}}")
    return out


def years(r: dict) -> str:
    y, e = val(r, "year"), val(r, "end")
    if e and e != y:
        return f"{y} – {e}" if y else e
    return y or val(r, "end")


def sort_key(r: dict):
    def num(s):
        m = re.search(r"\d{4}", s or "")
        return int(m.group()) if m else (9999 if (s or "").lower() in {"present", "ongoing"} else 0)
    # newest first; for jobs and roles: latest start first, then latest end
    if not val(r, "year"):          # undated items (e.g. "Ongoing" studies) go last
        return (1, 0, 0)
    return (0, -num(val(r, "year")), -num(val(r, "end")))


def select(rows: list[dict], output: str, section: str) -> list[dict]:
    items = [r for r in rows if r["section"] == section and included(r, output)]
    if section == "skill":
        return items  # keep CSV order for skills
    return sorted(items, key=sort_key)  # stable: CSV order breaks ties


# --------------------------------------------------------------------------- #
#  Homepage
# --------------------------------------------------------------------------- #
def cap(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s


def lines(s: str) -> list[str]:
    return [x.strip() for x in s.split("|") if x.strip()]


def html_item(left: str, body: str) -> str:
    return (f'<li class="item"><span class="when">{left}</span>'
            f'<span class="what">{body}</span></li>')


def html_section(rows, sec, settings) -> str:
    items = select(rows, "homepage", sec)
    if not items:
        return ""
    n = settings["max_authors"]["homepage"]
    out = []
    for r in items:
        if SECTIONS[sec][0]:
            body = citation(r, settings, "html", n)
        elif sec in {"position", "work", "service"}:
            org = ", ".join(x for x in (val(r, "organisation"), val(r, "location")) if x)
            body = f"<b>{italic_markup(val(r, 'title'), 'html')}</b>" + (f", {esc(org)}" if org else "")
            desc = val(r, "short") or val(r, "description")
            if desc:
                body += f'<span class="note">{italic_markup(desc, "html")}</span>'
        elif sec == "education":
            org = ", ".join(x for x in (val(r, "organisation"), val(r, "location")) if x)
            body = f"<b>{esc(val(r, 'title'))}</b>" + (f", {esc(org)}" if org else "")
            for ln in lines(val(r, "description")):
                body += f'<span class="note">{italic_markup(ln, "html")}</span>'
        elif sec == "conference":
            head = ", ".join(x for x in (val(r, "title"), val(r, "location")) if x)
            head = esc(head)
            if val(r, "url"):
                head = f'<a href="{esc(val(r, "url"))}" target="_blank" rel="noopener">{head}</a>'
            body = head + "".join(f'<span class="note">– {italic_markup(ln, "html")}</span>'
                                  for ln in lines(val(r, "description")))
        elif sec == "art":
            names, _ = author_tokens(val(r, "authors"), settings, None)
            who = ", ".join(esc(t) for t, _ in names)
            body = (f"{who} — " if who else "") + f"<i>{esc(val(r, 'title'))}</i>"
            if val(r, "venue"):
                body += f", {esc(val(r, 'venue'))}"
            if val(r, "location"):
                body += f" ({esc(val(r, 'location'))})"
            body += "."
            if val(r, "description"):
                body += f'<span class="note">{italic_markup(val(r, "description"), "html")}</span>'
        elif sec == "skill":
            out.append(html_item(esc(val(r, "title")), italic_markup(val(r, "description"), "html")))
            continue
        else:
            body = italic_markup(val(r, "title"), "html")
        out.append(html_item(esc(years(r)), body))
    label = settings["labels"][sec]
    return (f'<section class="block" id="{sec}">\n<h3>{esc(label)}</h3>\n'
            f'<ul class="list">\n' + "\n".join(out) + "\n</ul>\n</section>")


def build_homepage(rows: list[dict], settings: dict) -> None:
    p = settings["profile"]
    blocks = []
    for b in settings.get("homepage_blocks", []):
        s = '<section class="intro">'
        if b.get("heading"):
            s += f"<h2>{esc(b['heading'])}</h2>"
        if b.get("image"):
            s += (f'<figure><img src="{esc(b["image"])}" alt="{esc(b.get("alt", ""))}" loading="lazy">'
                  + (f'<figcaption>{esc(b.get("credit", ""))}</figcaption>' if b.get("credit") else "")
                  + "</figure>")
        s += f"<p>{esc(b.get('text', ''))}</p></section>"
        blocks.append(s)
    links = "\n".join(
        f'<li><span class="lbl">{esc(l["label"])}</span>'
        f'<a href="{esc(l["url"])}" target="_blank" rel="noopener">{esc(l["text"])}</a></li>'
        for l in settings.get("links", []))
    links += (f'\n<li><span class="lbl">Contact</span><a href="mailto:{esc(p["email"])}">'
              f'{esc(p["email"])}</a> ({esc(p.get("pronouns", ""))})</li>')
    downloads = "\n".join(f'<a class="btn" href="{esc(d["url"])}">{esc(d["text"])}</a>'
                          for d in settings.get("downloads", []))
    secs = [html_section(rows, s, settings) for s in settings["layout"]["homepage"]]
    nav = "\n".join(f'<a href="#{s}">{esc(settings["labels"][s])}</a>'
                    for s, h in zip(settings["layout"]["homepage"], secs) if h)
    page = (TEMPLATES / "index.html").read_text(encoding="utf-8")
    subs = {
        "name": esc(f"{p['first_name']} {p['last_name']}"),
        "first_name": esc(p["first_name"]), "last_name": esc(p["last_name"]),
        "position": esc(p["position"]), "location": esc(p["location"]),
        "photo": esc(p.get("photo_web", "")),
        "intro_blocks": "\n".join(blocks), "links": links, "downloads": downloads, "nav": nav,
        "sections": "\n".join(s for s in secs if s),
        "updated": dt.date.today().strftime("%-d %B %Y"),
    }
    for k, v in subs.items():
        page = page.replace("<<" + k + ">>", v)
    OUT_HTML.write_text(page, encoding="utf-8")
    log(f"Wrote {OUT_HTML.relative_to(ROOT)}")


# --------------------------------------------------------------------------- #
#  BibTeX
# --------------------------------------------------------------------------- #
def bib_escape(s: str) -> str:
    return s.replace("&", r"\&").replace("%", r"\%").replace("#", r"\#").replace("_", r"\_")


def bib_authors(s: str) -> str:
    out = []
    for a in parse_authors(s):
        if a["literal"]:
            out.append("{" + a["literal"] + "}")
        else:
            out.append(f"{a['family']}, {a['given']}".strip(", "))
    return " and ".join(out)


def bibtype(r: dict) -> str:
    if val(r, "bibtype"):
        return val(r, "bibtype")
    return SECTIONS[r["section"]][1]


def bib_entry(r: dict) -> str:
    t = bibtype(r)
    venue_field = {"article": "journal", "inproceedings": "booktitle", "incollection": "booktitle",
                   "phdthesis": "school", "mastersthesis": "school", "techreport": "institution"}.get(t)
    f = [("author", bib_authors(val(r, "authors"))), ("title", "{" + bib_escape(val(r, "title")) + "}")]
    venue = val(r, "venue")
    if t == "unpublished" and venue:
        f.append(("howpublished", bib_escape(venue)))
    elif venue_field and venue:
        f.append((venue_field, bib_escape(venue)))
    elif venue:
        f.append(("howpublished", bib_escape(venue)))
    if t in {"phdthesis", "mastersthesis"} and not venue and val(r, "organisation"):
        f.append(("school", bib_escape(val(r, "organisation"))))
    for k, c in (("year", "year"), ("volume", "volume"), ("number", "issue"),
                 ("pages", "pages"), ("publisher", "publisher"), ("doi", "doi"), ("url", "url")):
        v = val(r, c)
        if c == "pages":
            v = v.replace("–", "--")
        if c == "doi":
            v = norm_doi(v)
        if v:
            f.append((k, v if k in {"url", "doi"} else bib_escape(v)))
    if r["section"] == "dataset":
        f.append(("type", "Dataset"))
    if r["section"] == "software":
        f.append(("type", "Software"))
    note = " ".join(x for x in (STATUS_TEXT.get(val(r, "status").lower(), ""), val(r, "note")) if x)
    if note:
        f.append(("note", bib_escape(note)))
    f.append(("keywords", r["section"]))
    if val(r, "abstract"):
        f.append(("abstract", "{" + bib_escape(val(r, "abstract")) + "}"))
    w = max(len(k) for k, _ in f)
    body = ",\n".join(f"  {k.ljust(w)} = {v if v.startswith('{') and k in ('title', 'abstract') else '{' + v + '}'}"
                      for k, v in f)
    return f"@{t}{{{r['id']},\n{body}\n}}"


def build_bib(rows: list[dict], settings: dict) -> None:
    out = [f"% Publication list of {settings['profile']['first_name']} {settings['profile']['last_name']}",
           "% GENERATED by tools/build.py from data/records.csv — edit the CSV, not this file.",
           f"% Updated {dt.date.today().isoformat()}. Encoding: UTF-8 (use biber or a UTF-8 aware BibTeX).", ""]
    for sec in settings["layout"]["reflist"]:
        items = select(rows, "reflist", sec)
        if items:
            out.append(f"% {'=' * 70}\n% {settings['labels'][sec]}\n% {'=' * 70}\n")
            out += [bib_entry(r) + "\n" for r in items]
    OUT_BIB.parent.mkdir(exist_ok=True)
    OUT_BIB.write_text("\n".join(out), encoding="utf-8")
    log(f"Wrote {OUT_BIB.relative_to(ROOT)}")


# --------------------------------------------------------------------------- #
#  Full publication list (LaTeX)
# --------------------------------------------------------------------------- #
def reflist_entry(r: dict) -> str:
    names = [full_name(a) for a in parse_authors(val(r, "authors"))]
    auth = tex(", ".join(names))
    parts = [f"\\refauthors{{{auth}}} ({tex(val(r, 'year'))})."]
    parts.append(f"\\reftitle{{{tex(val(r, 'title')).rstrip('.')}}}.")
    venue = val(r, "venue")
    if venue:
        num = venue_details(r, True)
        parts.append(f"\\textit{{{tex(venue)}}}" + (f", {tex(num)}." if num else "."))
    pub = val(r, "publisher")
    if pub and pub != venue:
        parts.append(tex(pub) + ".")
    if val(r, "section") == "dataset":
        parts.append("Dataset.")
    if val(r, "section") == "software":
        parts.append("Software.")
    st = STATUS_TEXT.get(val(r, "status").lower())
    if st:
        parts.append(st + ".")
    if val(r, "note"):
        parts.append(tex(val(r, "note")).rstrip(".") + ".")
    links = []
    doi = norm_doi(val(r, "doi"))
    if doi:
        links.append(f"\\href{{https://doi.org/{doi}}}{{doi:{tex(doi)}}}")
    if val(r, "url"):
        links.append(f"\\url{{{val(r, 'url')}}}")
    s = "\\begin{refentry}{" + tex(r["id"]) + "}\n" + " ".join(parts)
    if links:
        s += "\\\\\n" + " \\quad ".join(links)
    if val(r, "abstract"):
        s += "\n\\refabstract{" + tex(val(r, "abstract")) + "}"
    return s + "\n\\end{refentry}\n"


def build_reflist_tex(rows: list[dict], settings: dict) -> None:
    body = []
    count = 0
    for sec in settings["layout"]["reflist"]:
        items = select(rows, "reflist", sec)
        if items:
            count += len(items)
            body.append(f"\\cvsection{{{tex(settings['labels'][sec])}}}\n")
            body += [reflist_entry(r) for r in items]
    fill(TEMPLATES / "publication_list.tex", OUT_REFTEX, settings,
         {"body": "\n".join(body), "count": str(count)})
    log(f"Wrote {OUT_REFTEX.relative_to(ROOT)} ({count} entries)")


# --------------------------------------------------------------------------- #
#  CVs (LaTeX, cvClass.cls)
# --------------------------------------------------------------------------- #
def cv_section(rows, sec, settings, output) -> str:
    items = select(rows, output, sec)
    if not items:
        return ""
    short = output == "cv_short"
    n = settings["max_authors"][output]
    label = tex(settings["labels"][sec])
    if short and sec == "article":
        label = "Selected publications"
    out = [f"\\cvsection{{{label}}}"]
    if SECTIONS[sec][0]:
        out.append("\\begin{cvlist}")
        for r in items:
            out.append(f"\\cvitem{{{tex(val(r, 'year'))}}}{{{citation(r, settings, 'tex', n, details=not short, link_doi=not short)}}}")
        out.append("\\end{cvlist}")
    elif sec in {"position", "work", "education"}:
        out.append("\\begin{cvlist}")
        for r in items:
            org = ", ".join(tex(x) for x in (val(r, "organisation"), val(r, "location")) if x)
            text = f"\\textbf{{{italic_markup(val(r, 'title'), 'tex')}}}" + (f", {org}" if org else "")
            if short:
                if val(r, "short"):
                    text += f". {italic_markup(cap(val(r, 'short')), 'tex')}"
            else:
                desc = lines(val(r, "description"))
                if desc:
                    text += "\\par{\\color{graytext}" + " \\newline ".join(italic_markup(d, "tex") for d in desc) + "}"
            out.append(f"\\cvitem{{{tex(years(r))}}}{{{text}}}")
        out.append("\\end{cvlist}")
    elif sec == "conference":
        out.append("\\begin{cvlist}")
        for r in items:
            head = ", ".join(tex(x) for x in (val(r, "title"), val(r, "location")) if x)
            desc = lines(val(r, "description"))
            text = head + ("\\par{\\color{graytext}" + " \\newline ".join("– " + italic_markup(d, "tex") for d in desc) + "}" if desc else "")
            out.append(f"\\cvitem{{{tex(years(r))}}}{{{text}}}")
        out.append("\\end{cvlist}")
    elif sec in {"award", "service"}:
        out.append("\\begin{cvlist}")
        for r in items:
            text = italic_markup(val(r, "title"), "tex")
            if val(r, "organisation"):
                text += f", {tex(val(r, 'organisation'))}"
            out.append(f"\\cvitem{{{tex(years(r))}}}{{{text}}}")
        out.append("\\end{cvlist}")
    elif sec == "art":
        out.append("\\begin{cvlist}")
        for r in items:
            names, _ = author_tokens(val(r, "authors"), settings, None)
            who = ", ".join(tex(t) for t, _ in names)
            text = (f"{who} — " if who else "") + f"\\textit{{{tex(val(r, 'title'))}}}"
            if val(r, "venue"):
                text += f", {tex(val(r, 'venue'))}"
            if val(r, "location"):
                text += f" ({tex(val(r, 'location'))})"
            text += "."
            d = val(r, "short") if short else val(r, "description")
            if d:
                text += " " + italic_markup(cap(d).rstrip(".") + ".", "tex")
            out.append(f"\\cvitem{{{tex(years(r))}}}{{{text}}}")
        out.append("\\end{cvlist}")
    elif sec == "skill":
        out.append("\\begin{cvlist}")
        for r in items:
            d = (val(r, "short") or val(r, "description")) if short else val(r, "description")
            out.append(f"\\cvitem{{\\skilllabel{{{tex(val(r, 'title'))}}}}}{{{italic_markup(d, 'tex')}}}")
        out.append("\\end{cvlist}")
    return "\n".join(out) + "\n"


def fill(template: Path, target: Path, settings: dict, extra: dict) -> None:
    p = settings["profile"]
    subs = {
        "first_name": tex(p["first_name"]), "last_name": tex(p["last_name"]),
        "position": tex(p["position"]), "location": tex(p["location"]),
        "email": p["cv_email"], "phone": tex(p.get("phone") or ""),
        "website": tex(p["website"]), "github": tex(p["github"]),
        "photo": p.get("photo_cv", ""), "orcid": p.get("orcid", ""),
        "summary": tex(settings.get("cv_summary", "")),
        "date": dt.date.today().strftime("%-d %B %Y"),
    }
    subs.update(extra)
    s = template.read_text(encoding="utf-8")
    for k, v in subs.items():
        s = s.replace("<<" + k + ">>", v)
    left = re.findall(r"<<\w+>>", s)
    if left:
        raise BuildError(f"{template.name}: unknown placeholder(s) {left}")
    target.write_text(s, encoding="utf-8")


def build_cv(rows, settings, output) -> None:
    body = "\n".join(x for x in (cv_section(rows, s, settings, output) for s in settings["layout"][output]) if x)
    tpl = TEMPLATES / ("cv_long.tex" if output == "cv_long" else "cv_short.tex")
    target = OUT_CVLONG_TEX if output == "cv_long" else OUT_CVSHORT_TEX
    phone = settings["profile"].get("phone")
    fill(tpl, target, settings, {"body": body,
                                 "phoneline": f"\\mobile{{{tex(phone)}}}" if phone else ""})
    log(f"Wrote {target.relative_to(ROOT)}")


# --------------------------------------------------------------------------- #
#  Step 5: compile
# --------------------------------------------------------------------------- #
def xelatex(tex_file: Path, runs: int = 2) -> int:
    if not shutil.which("xelatex"):
        raise BuildError("xelatex not found. Install TeX Live (see docs/BUILD.md) or run with --no-pdf.")
    for _ in range(runs):
        res = subprocess.run(["xelatex", "-interaction=nonstopmode", "-halt-on-error", tex_file.name],
                             cwd=tex_file.parent, capture_output=True, text=True)
        if res.returncode != 0:
            logf = tex_file.with_suffix(".log")
            tail = logf.read_text(errors="ignore")[-3000:] if logf.exists() else res.stdout[-3000:]
            raise BuildError(f"LaTeX failed for {tex_file.name}:\n{tail}")
    logtxt = tex_file.with_suffix(".log").read_text(errors="ignore")
    m = re.search(r"Output written on .*?\((\d+) pages?", logtxt)
    missing = sorted(set(re.findall(r"Missing character: There is no (.) ", logtxt)))
    if missing:
        log(f"  ! {tex_file.name}: font lacks glyph(s) {' '.join(missing)}")
    for ext in (".aux", ".log", ".out", ".toc"):
        tex_file.with_suffix(ext).unlink(missing_ok=True)
    return int(m.group(1)) if m else 0


def compile_all(todo: set[str]) -> None:
    if "cv_long" in todo:
        n = xelatex(OUT_CVLONG_TEX)
        shutil.move(OUT_CVLONG_TEX.with_suffix(".pdf"), OUT_CVLONG_PDF)
        log(f"Compiled {OUT_CVLONG_PDF.relative_to(ROOT)} ({n} pages)")
    if "cv_short" in todo:
        n = xelatex(OUT_CVSHORT_TEX)
        shutil.move(OUT_CVSHORT_TEX.with_suffix(".pdf"), OUT_CVSHORT_PDF)
        log(f"Compiled {OUT_CVSHORT_PDF.relative_to(ROOT)} ({n} page{'s' if n != 1 else ''})")
        if n != 1:
            raise BuildError(f"The short CV is {n} pages; it must fit on one A4 page. "
                             "Set cv_short to 'no' for some rows in records.csv (or shorten their 'short' text).")
    if "reflist" in todo:
        n = xelatex(OUT_REFTEX)
        shutil.move(OUT_REFTEX.with_suffix(".pdf"), OUT_REFPDF)
        log(f"Compiled {OUT_REFPDF.relative_to(ROOT)} ({n} pages)")


# --------------------------------------------------------------------------- #
#  Main
# --------------------------------------------------------------------------- #
def run(only: list[str] | None = None, pdf: bool | None = None, refresh_doi: bool = False,
        check: bool = False) -> None:
    settings = load_settings()
    b = settings.get("build", {})
    rows = read_records()
    log(f"Read {len(rows)} rows from data/records.csv")

    rows = import_inbox(rows)                         # 1
    fetch = bool(b.get("fetch_doi", True))
    changed = False
    for r in rows:                                    # 2
        if SECTIONS.get(r["section"], (False,))[0] and fill_from_doi(r, fetch, refresh_doi):
            changed = True
    write_records(rows)                               # always keep the CSV tidy and complete
    if changed:
        log("Saved DOI metadata into data/records.csv")

    errors = validate(rows)                           # 3
    if errors:
        raise BuildError("Please fix these entries:\n  " + "\n  ".join(errors))
    log("Validation passed")
    if check:
        return

    todo = set(only) if only else {k for k in OUTPUTS if b.get(k, True)}
    if "homepage" in todo:                            # 4
        build_homepage(rows, settings)
    if "reflist" in todo:
        build_bib(rows, settings)
        build_reflist_tex(rows, settings)
    for cv in ("cv_long", "cv_short"):
        if cv in todo:
            build_cv(rows, settings, cv)

    do_pdf = b.get("compile_pdf", True) if pdf is None else pdf
    if do_pdf:                                        # 5
        compile_all(todo)
    log("Done.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", nargs="+", choices=OUTPUTS, help="build only these outputs")
    ap.add_argument("--no-pdf", action="store_true", help="write files but do not run LaTeX")
    ap.add_argument("--refresh-doi", action="store_true", help="download DOI metadata again")
    ap.add_argument("--check", action="store_true", help="import + validate only")
    a = ap.parse_args()
    try:
        run(a.only, False if a.no_pdf else None, a.refresh_doi, a.check)
    except BuildError as exc:
        print(f"\nBUILD STOPPED: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
