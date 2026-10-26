"""Create a client-facing simulation report, without database or debugging details."""

import json
import re
import sqlite3
from contextlib import closing
from html import escape

import pymupdf

# Only these approved stage outputs belong in the product's downloadable report.
STAGES = [
    ("clarify_extract", "Clarify & Extract", "The supplied facts, people, evidence, and chronology."),
    ("case_manager", "Case Manager", "A neutral account of the case and the limits of the supplied record."),
    (
        "prosecution_consultant",
        "Prosecution Strategy",
        "The prosecution position, its strengths, and its limitations.",
    ),
    ("defense_consultant", "Defense Strategy", "The defense position and the issues requiring examination."),
    (
        "prosecution_research",
        "Prosecution Research",
        "Relevant legal principles, source references, and research limitations.",
    ),
    ("defense_research", "Defense Research", "Legal research supporting and testing the defense position."),
    ("pros_r1", "Prosecution Opening", "The prosecution's opening submissions."),
    ("def_r1", "Defense Response", "The defense reply to the opening submissions."),
    ("pros_r2", "Prosecution Closing", "The prosecution's response and requested relief."),
    ("def_r2", "Defense Closing", "The final defense submissions and requested relief."),
    (
        "judge",
        "Judicial Assessment",
        "The final outcome, reasoning, issue findings, and unresolved questions.",
    ),
]
TITLES = {key: title for key, title, _ in STAGES}
EXTRACTION_FIELDS = {
    "laws": (
        "Laws mentioned",
        [
            ("act", "Act"),
            ("section", "Section"),
            ("title", "Provision"),
            ("relevance", "Relevance"),
            ("page_refs", "Source pages"),
        ],
    ),
    "witnesses": (
        "Witnesses",
        [
            ("name", "Name"),
            ("role", "Role"),
            ("statement_summary", "Account"),
            ("reliability_notes", "Limitations"),
            ("page_refs", "Source pages"),
        ],
    ),
    "evidence": (
        "Evidence",
        [
            ("description", "Description"),
            ("evidence_type", "Type"),
            ("relevance", "Relevance"),
            ("page_refs", "Source pages"),
        ],
    ),
    "entities": (
        "People and places",
        [
            ("name", "Name"),
            ("entity_type", "Type"),
            ("role", "Role"),
            ("notes", "Notes"),
            ("page_refs", "Source pages"),
        ],
    ),
    "timeline": (
        "Chronology",
        [("event_date", "Date"), ("event_description", "Event"), ("page_refs", "Source pages")],
    ),
}


def read_simulation(db_path):
    """Read relevant results in one read-only snapshot; never load pages or debug artifacts."""
    with closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN")
        extraction = {}
        for table, (_, fields) in EXTRACTION_FIELDS.items():
            columns = ", ".join(column for column, _ in fields)
            order = "sequence, id" if table == "timeline" else "id"
            extraction[table] = [
                dict(row) for row in conn.execute(f"SELECT {columns} FROM {table} ORDER BY {order}")
            ]
        row = conn.execute("SELECT meta_json FROM case_meta WHERE id=1").fetchone()
        summary = parse_json(row[0]) if row else {}
        reports = {}
        for row in conn.execute("SELECT report_type, content, structured_json FROM reports ORDER BY id"):
            if row["report_type"] in TITLES:
                reports[row["report_type"]] = {
                    "text": row["content"],
                    "data": parse_json(row["structured_json"]),
                }
        return extraction, summary, reports


def parse_json(value):
    """Use structured output when valid, otherwise fall back to the readable stage report."""
    try:
        result = json.loads(value or "{}")
        return result if isinstance(result, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def text(value):
    """Escape content, retain clickable sources, and wrap long links inside the margins."""
    value = re.sub(r"\*\*(.*?)\*\*", r"\1", str(value), flags=re.DOTALL)
    value = re.sub(r"(?m)^#{1,6}\s*", "", value)

    def wrapped(part):
        """Add visual wrap points without removing source characters."""
        part = re.sub(
            r"\S{61,}",
            lambda match: "\n".join(
                match.group()[start : start + 60] for start in range(0, len(match.group()), 60)
            ),
            part,
        )
        return escape(part).replace("\n", "<br>")

    result, start = [], 0
    for match in re.finditer(r"https?://[^\s<>]+", value):
        result.append(wrapped(value[start : match.start()]))
        result.append(
            f'<a style="color:#27747a;text-decoration:none" href="{escape(match.group(), quote=True)}">{wrapped(match.group())}</a>'
        )
        start = match.end()
    result.append(wrapped(value[start:]))
    return "".join(result)


def paragraph(value):
    """Set prose as readable paragraphs instead of database values or JSON."""
    return "".join(f"<p>{text(part)}</p>" for part in re.split(r"\n\s*\n", str(value)) if part.strip())


def items(values):
    """Render findings or notes as readable entries with human-friendly labels."""
    result = []
    for value in values or []:
        if isinstance(value, dict):
            result.append(
                fields(
                    value,
                    [
                        (
                            "id" if key == "id" else key,
                            "Reference" if key == "id" else key.replace("_", " ").capitalize(),
                        )
                        for key in value
                        if key not in ("created_at", "updated_at", "source_hash")
                        and (key != "id" or "url" in value)
                    ],
                )
            )
        else:
            result.append(paragraph(value))
    return "".join(result)


def fields(data, labels):
    """Display selected substantive fields; omit missing values and internal bookkeeping."""
    result = []
    for key, label in labels:
        value = data.get(key)
        if value is None or value == "" or value == [] or value == {}:
            continue
        if isinstance(value, dict):
            body = fields(value, [(name, name.replace("_", " ").capitalize()) for name in value])
        elif isinstance(value, list):
            body = (
                paragraph(", ".join(map(str, value)))
                if key in ("record_refs", "authority_refs", "page_refs", "responds_to")
                else items(value)
            )
        else:
            body = paragraph(value)
        # Short facts read as compact labeled lines; substantial analysis gets its own subheading.
        if isinstance(value, str) and len(value) <= 160 and "\n" not in value:
            result.append(f"<p class='compact'><b>{text(label)}.</b> {text(value)}</p>")
        elif key in ("record_refs", "authority_refs", "page_refs", "responds_to") and isinstance(value, list):
            result.append(f"<p class='compact'><b>{text(label)}.</b> {text(', '.join(map(str, value)))}</p>")
        else:
            result.append(f"<div class='field'><h4>{text(label)}</h4>{body}</div>")
    return "".join(result)


def extraction_section(extraction, summary, fallback):
    """Present extraction as a case summary and compact, labeled fact cards."""
    parts = [fields(summary, [("summary", "Case summary"), ("parties", "Parties")])]
    for table, (title, labels) in EXTRACTION_FIELDS.items():
        parts.append(f"<h3>{title}</h3>")
        rows = extraction[table]
        if not rows:
            parts.append("<p class='muted'>No information identified in the supplied record.</p>")
        for row in rows:
            # Small fact cards use a two-column layout; long accounts remain freely paginated prose.
            if sum(len(str(value or "")) for value in row.values()) < 600:
                card = (
                    "<table class='facts'>"
                    + "".join(
                        f"<tr><td class='fact-label'>{text(label)}</td><td>{text(row[key])}</td></tr>"
                        for key, label in labels
                        if row.get(key)
                    )
                    + "</table>"
                )
            else:
                card = fields(row, labels)
            parts.append("<div class='entry'>" + card + "</div>")
    if not summary.get("summary") and not any(extraction.values()):
        parts.append(paragraph(fallback))
    return "".join(parts)


def stage_section(key, report):
    """Format known agent output shapes without displaying schemas or duplicate raw JSON."""
    data = report["data"]
    if key == "case_manager" and data.get("neutral_factual_narrative"):
        return fields(
            data,
            [
                ("case_caption", "Case"),
                ("parties", "Parties"),
                ("court_and_stage", "Court and stage"),
                ("decision_scope", "Scope of assessment"),
                ("neutral_factual_narrative", "Neutral case narrative"),
                ("alleged_offences", "Alleged offences"),
                ("evidence_matrix", "Evidence overview"),
                ("witness_matrix", "Witness overview"),
                ("timeline_summary", "Chronology"),
                ("key_gaps_and_inconsistencies", "Gaps and uncertainties"),
                ("document_inventory", "Documents considered"),
                ("sources", "Legal sources"),
            ],
        )
    if key in ("pros_r1", "def_r1", "pros_r2", "def_r2") and data.get("submissions"):
        parts = []
        for index, submission in enumerate(data["submissions"], 1):
            submission = dict(submission)
            submission["responds_to"] = [
                re.sub(
                    r"([a-z_0-9]+)\.(\d+)",
                    lambda match: f"{TITLES.get(match[1], 'Opposing submission')} - point {match[2]}",
                    ref,
                )
                for ref in submission.get("responds_to", [])
            ]
            parts.append(
                f"<h3>Submission {index}</h3>"
                + fields(
                    submission,
                    [
                        ("proposition", "Position"),
                        ("factual_basis", "Factual basis"),
                        ("legal_analysis", "Legal analysis"),
                        ("record_refs", "Source pages"),
                        ("authority_refs", "Legal sources"),
                        ("responds_to", "Points addressed"),
                        ("concession_or_limit", "Limitation"),
                    ],
                )
            )
        parts.append(fields(data, [("relief_sought", "Relief requested")]))
        return "".join(parts)
    if key == "judge" and data.get("reasoning"):
        parts = [
            fields(
                data,
                [
                    ("procedural_basis", "Basis of assessment"),
                    ("verdict", "Outcome"),
                    ("reasoning", "Reasoning"),
                ],
            )
        ]
        for index, finding in enumerate(data.get("findings", []), 1):
            parts.append(
                f"<h3>Issue {index}</h3>"
                + fields(
                    finding,
                    [
                        ("act_section", "Act and section"),
                        ("accused_or_group", "Person or group considered"),
                        ("prosecution_position", "Prosecution position"),
                        ("defense_position", "Defense position"),
                        ("finding", "Assessment"),
                        ("record_refs", "Source pages"),
                        ("authority_refs", "Legal sources"),
                        ("missing_evidence", "Further evidence needed"),
                    ],
                )
            )
        parts.append(
            fields(
                data,
                [
                    ("unresolved_questions", "Unresolved questions"),
                    ("sections_applied", "Sections considered"),
                    ("probable_punishment", "Punishment considerations"),
                ],
            )
        )
        if data.get("confidence") is not None:
            parts.append(
                f"<h3>Record sufficiency</h3><p>{text(data['confidence'])} / 100</p>"
                "<p class='muted'>A self-assessment of the supplied record, not a probability of guilt.</p>"
            )
        return "".join(parts)
    # Consultant and research reports are already prose, including source lists and limitations.
    prose = re.sub(
        r"\b(pros_r1|def_r1|pros_r2|def_r2)\.(\d+)",
        lambda match: f"{TITLES[match[1]]} - point {match[2]}",
        report["text"],
    )
    headings = {
        "SEARCH PLAN:": "Research questions",
        "RESEARCH REPORT:": "Legal analysis",
        "SOURCES:": "Sources",
        "LIMITATIONS:": "Research limitations",
    }
    parts, buffer = [], []
    for line in prose.splitlines():
        if line.strip() in headings:
            parts.append(paragraph("\n".join(buffer)))
            buffer = []
            parts.append(f"<h3>{headings[line.strip()]}</h3>")
        else:
            buffer.append(line)
    parts.append(paragraph("\n".join(buffer)))
    return "".join(parts)


def build_case_pdf(db_path, library_metadata):
    """Build a designed, paginated client report with only final simulation outputs."""
    extraction, summary, reports = read_simulation(db_path)
    manager = reports.get("case_manager", {}).get("data", {})
    judge = reports.get("judge", {}).get("data", {})
    title = library_metadata.get("title") or "Case simulation"
    if re.fullmatch(r"Case [0-9a-f]{8}", title):
        title = manager.get("case_caption") or "Case simulation"
    parts = [
        "<div class='eyebrow'>COURTROOM AI / SIMULATION REPORT</div>",
        "<h1>Arguments.<br>Evidence.<br>Assessment.</h1>",
        f"<p class='case-title'>{text(title)}</p>",
        "<p class='intro'>A structured account of the supplied case, both sides' submissions, and the final judicial assessment.</p>",
    ]
    if library_metadata.get("selected_judge"):
        parts.append(
            "<p class='muted'>Presiding: "
            + text(library_metadata["selected_judge"].replace("_", " "))
            + "</p>"
        )
    outcome = judge.get("verdict") or library_metadata.get("verdict")
    if outcome:
        # Keep the cover concise; the full outcome and reasoning appear in the final section.
        cover_outcome = (
            outcome
            if len(outcome) <= 350
            else "See the Judicial Assessment for the full outcome and reasoning."
        )
        parts.append("<div class='summary'><h4>FINAL ASSESSMENT</h4>" + paragraph(cover_outcome) + "</div>")
    parts.append("<h3>In this report</h3><table class='contents'>")
    for index, (key, heading, _) in enumerate(STAGES, 1):
        parts.append(
            f"<tr><td class='number'>{index:02}</td><td><a style='color:#173348;text-decoration:none' href='#stage-{key}'>{escape(heading)}</a></td></tr>"
        )
    parts.append(
        "</table><p class='note'>AI-generated research and simulation support. Check original documents and legal authorities before relying on conclusions.</p>"
    )
    for index, (key, heading, description) in enumerate(STAGES, 1):
        parts.append(
            f"<h2 class='section' id='stage-{key}'><span class='number'>{index:02}</span> {escape(heading)}</h2>"
            f"<p class='section-intro'>{escape(description)}</p>"
        )
        report = reports.get(key)
        if key == "clarify_extract":
            parts.append(extraction_section(extraction, summary, report["text"] if report else ""))
        elif report:
            parts.append(stage_section(key, report))
        else:
            parts.append("<p class='muted'>This report is not available for this simulation.</p>")
    css = """
        body { font-family: sans-serif; font-size: 10.5pt; line-height: 1.5; color: #263747; }
        h1 { font-family: serif; font-size: 38pt; font-weight: normal; line-height: 1.1; color: #173348; margin: 22pt 0 14pt; }
        h2 { font-family: serif; font-size: 26pt; font-weight: normal; color: #173348; margin: 0 0 10pt; }
        h3 { font-family: serif; font-size: 15pt; color: #173348; margin: 17pt 0 7pt; page-break-after: avoid; }
        h4 { font-size: 9pt; color: #27747a; margin: 9pt 0 3pt; page-break-after: avoid; }
        p { margin: 0 0 9pt; }
        a { color: #173348; text-decoration: none; }
        .eyebrow { font-size: 8pt; color: #27747a; }
        .case-title { font-family: serif; font-size: 19pt; color: #173348; }
        .intro, .section-intro { color: #657681; font-size: 10pt; }
        .section-intro { border-bottom: 1pt solid #d8e2e5; padding-bottom: 14pt; margin-bottom: 19pt; }
        .section { page-break-before: always; }
        .summary { background-color: #eff5f5; border-left: 3pt solid #27747a; padding: 10pt 14pt; margin: 12pt 0; }
        .contents { width: 100%; font-size: 9pt; border-collapse: collapse; }
        .contents td { padding: 2pt 0; }
        .number { color: #27747a; }
        td.number { width: 28pt; }
        .compact { margin: 0 0 5pt; }
        .facts { width: 100%; border-collapse: collapse; font-size: 10pt; }
        .facts td { padding: 4pt 5pt 4pt 0; vertical-align: top; }
        .fact-label { width: 110pt; color: #27747a; font-weight: bold; font-size: 9pt; }
        .entry { border-top: 0.5pt solid #d8e2e5; padding-top: 5pt; margin-top: 8pt; }
        .muted, .note { color: #657681; font-size: 9pt; }
        .note { font-size: 8pt; margin-top: 16pt; }
    """
    story = pymupdf.Story(html="".join(parts), user_css=css)

    def page_layout(rect_number, filled):
        """Reserve generous margins, a running brand line, and page numbers."""
        page = pymupdf.paper_rect("a4")
        return page, pymupdf.Rect(50, 62, page.width - 50, page.height - 50), None

    with story.write_with_links(page_layout) as pdf:
        pdf.set_metadata({"title": "Courtroom AI - Simulation Report", "author": "Courtroom AI"})
        for index, page in enumerate(pdf, 1):
            page.insert_text((50, 29), "COURTROOM AI", fontsize=9, color=(0.09, 0.20, 0.28))
            page.insert_text(
                (page.rect.width - 161, 29), "SIMULATION REPORT", fontsize=8, color=(0.39, 0.46, 0.50)
            )
            page.draw_line((50, 39), (page.rect.width - 50, 39), color=(0.15, 0.45, 0.48), width=0.6)
            page.draw_line(
                (50, page.rect.height - 36),
                (page.rect.width - 50, page.rect.height - 36),
                color=(0.85, 0.89, 0.90),
                width=0.5,
            )
            page.insert_text(
                (50, page.rect.height - 23), "Argue. Research. Decide.", fontsize=8, color=(0.39, 0.46, 0.50)
            )
            page.insert_text(
                (page.rect.width - 108, page.rect.height - 23), f"{index:02} / {len(pdf):02}", fontsize=8
            )
        return pdf.tobytes(garbage=4, deflate=True)
