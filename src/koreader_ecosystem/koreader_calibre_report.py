from __future__ import annotations

import html
import json
from pathlib import Path

from koreader_ecosystem.koreader_calibre import PreviewItem, PreviewResult

_FIELD_LABELS = {
    "koreader_progress": "Progresso (0–1)",
    "koreader_status": "Status",
    "koreader_position": "Posição",
    "koreader_rating": "Avaliação (0–5)",
    "koreader_note": "Nota",
    "koreader_annotations": "Anotações",
}


def _escape(value: object) -> str:
    if value is None:
        text = "—"
    elif isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)
    return html.escape(text, quote=True)


def _field_value(field: str, value: object) -> str:
    if field == "koreader_annotations" and isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            pass
        else:
            value = json.dumps(decoded, ensure_ascii=False, sort_keys=True, indent=2)
    return _escape(value)


def _change_table(item: PreviewItem) -> str:
    rows: list[str] = []
    for field in item.new_values:
        label = _escape(_FIELD_LABELS.get(field, field))
        before = _field_value(field, item.old_values.get(field))
        after = _field_value(field, item.new_values[field])
        rows.append(
            "<tr>"
            f'<th scope="row">{label}</th>'
            f"<td><pre>{before}</pre></td>"
            f"<td><pre>{after}</pre></td>"
            "</tr>"
        )
    if not rows:
        return "<p>Nenhuma alteração proposta.</p>"
    return (
        "<table><thead><tr><th>Campo</th><th>Atual no Calibre</th>"
        "<th>Proposto pelo KOReader</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


def _render_item(item: PreviewItem) -> str:
    title = _escape(item.title or "Livro sem correspondência")
    authors = _escape(", ".join(item.authors) if item.authors else "Autor não identificado")
    warnings = "".join(f"<li>{_escape(warning)}</li>" for warning in item.warnings)
    warning_list = f'<ul class="warnings">{warnings}</ul>' if warnings else ""
    return (
        "<article>"
        f"<h2>{title}</h2>"
        f'<p class="authors">{authors}</p>'
        "<dl>"
        f"<dt>Correspondência</dt><dd>{_escape(item.match_status)}</dd>"
        f"<dt>Resultado</dt><dd>{_escape(item.reason_code)}</dd>"
        f"<dt>Origem</dt><dd><code>{_escape(item.relative_path)}</code></dd>"
        f"<dt>Chave para associação manual</dt><dd><code>{_escape(item.sidecar_key)}</code></dd>"
        "</dl>"
        f"{warning_list}"
        f"{_change_table(item)}"
        "</article>"
    )


def render_preview(preview: PreviewResult, output_path: Path) -> Path:
    """Write a self-contained, escaped HTML report with no network resources."""
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    changed = sum(bool(item.new_values) for item in preview.items)
    pending = sum(
        item.reason_code
        not in {"exact_calibre_uuid", "exact_isbn", "manual_mapping", "already_current"}
        for item in preview.items
    )
    content = "".join(_render_item(item) for item in preview.items)
    document = f"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy"
 content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>Prévia KOReader–Calibre</title>
<style>
:root {{ color-scheme: light dark; font: 16px/1.5 system-ui, sans-serif; }}
body {{ max-width: 1100px; margin: 2rem auto; padding: 0 1rem; }}
header, article {{ border: 1px solid #8886; border-radius: .8rem; }}
header, article {{ padding: 1rem 1.25rem; margin: 1rem 0; }}
article {{ overflow-wrap: anywhere; }}
table {{ border-collapse: collapse; width: 100%; table-layout: fixed; }}
th, td {{ border: 1px solid #8886; padding: .6rem; text-align: left; vertical-align: top; }}
pre {{ white-space: pre-wrap; overflow-wrap: anywhere; margin: 0; }}
dt {{ font-weight: 650; margin-top: .5rem; }}
dd {{ margin-left: 0; }}
.authors {{ opacity: .8; }}
.warnings {{ color: #b35b00; }}
code {{ font-size: .9em; }}
</style>
</head>
<body>
<header>
<h1>Prévia KOReader–Calibre</h1>
<p>Plano: <code>{_escape(preview.preview_id)}</code></p>
<p>{len(preview.items)} sidecars · {changed} com alterações · {pending} pendentes</p>
<p>Este arquivo é local. Revise as mudanças antes de aplicar.</p>
</header>
<main><h2>Pendências e livros</h2>{content}</main>
</body>
</html>
"""
    output.write_text(document, encoding="utf-8", newline="\n")
    return output
