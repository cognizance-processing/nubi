import json
import re
from typing import Dict, Any, List
from decimal import Decimal
from datetime import datetime, date
from html.parser import HTMLParser

import pandas as pd


def ensure_dict(val):
    """Normalise a value that should be a dict but may be a double-encoded JSON string."""
    if isinstance(val, dict):
        return val
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
    return val if isinstance(val, dict) else {}


def convert_to_json_serializable(obj):
    """Convert non-JSON-serializable types to JSON-compatible formats."""
    if isinstance(obj, Decimal):
        return float(obj)
    elif isinstance(obj, (datetime, date)):
        return obj.isoformat()
    elif isinstance(obj, bytes):
        return obj.decode('utf-8', errors='ignore')
    elif pd.isna(obj):
        return None
    return obj

def clean_dataframe_for_json(df: pd.DataFrame) -> List[Dict[str, Any]]:
    """Convert DataFrame to JSON-serializable list of dicts."""
    df = df.replace({pd.NaT: None, float('nan'): None})

    records = df.to_dict('records')

    cleaned_records = []
    for record in records:
        cleaned_record = {k: convert_to_json_serializable(v) for k, v in record.items()}
        cleaned_records.append(cleaned_record)

    return cleaned_records


def strip_markdown_code_block(raw: str) -> str:
    """
    Extract code from markdown code blocks or raw text.
    Handles cases where AI returns explanatory text with code.
    """
    if not raw:
        return ""

    trimmed = raw.strip()

    # Strategy 1: Look for ```html or ``` code fences
    code_blocks = []

    # Find all code blocks (```html...``` or ```...```)
    pattern = r'```(?:html)?\s*\n(.*?)```'
    matches = re.findall(pattern, trimmed, re.DOTALL)

    for match in matches:
        code_blocks.append(match.strip())

    # Strategy 2: If no code blocks found, look for HTML directly (<!DOCTYPE or <html)
    if not code_blocks:
        html_start = -1
        if '<!DOCTYPE' in trimmed:
            html_start = trimmed.find('<!DOCTYPE')
        elif '<html' in trimmed.lower():
            html_start = trimmed.lower().find('<html')

        if html_start != -1:
            html_content = trimmed[html_start:]
            html_end = html_content.lower().find('</html>')
            if html_end != -1:
                code_blocks.append(html_content[:html_end + 7].strip())
            else:
                code_blocks.append(html_content.strip())

    # Strategy 3: Return the largest code block (most likely to be the actual code)
    if code_blocks:
        valid_blocks = [
            block for block in code_blocks
            if len(block) > 50 and ('<' in block or 'DOCTYPE' in block)
        ]

        if valid_blocks:
            return max(valid_blocks, key=len)
        elif code_blocks:
            return max(code_blocks, key=len)

    # Strategy 4: Fallback - if it looks like HTML, return as-is
    if '<!DOCTYPE' in trimmed or '<html' in trimmed.lower():
        return trimmed

    return trimmed


class SimpleHTMLValidator(HTMLParser):
    """Simple HTML validator using Python's built-in HTMLParser."""
    def __init__(self):
        super().__init__()
        self.errors = []
        self.warnings = []
        self.info = []
        self.tags_found = set()
        self.open_tags = []
        self.has_html = False
        self.has_head = False
        self.has_body = False

    def handle_starttag(self, tag, attrs):
        self.tags_found.add(tag)
        self.open_tags.append(tag)

        if tag == 'html':
            self.has_html = True
        elif tag == 'head':
            self.has_head = True
        elif tag == 'body':
            self.has_body = True

        for attr, value in attrs:
            if attr == 'class' and 'widget' in value:
                self.info.append("Found widget element")
                break

    def handle_endtag(self, tag):
        if self.open_tags and self.open_tags[-1] == tag:
            self.open_tags.pop()
        elif tag in self.open_tags:
            self.open_tags.remove(tag)

    def error(self, message):
        self.errors.append(message)


def validate_html(html_code: str) -> Dict[str, Any]:
    """
    Validate HTML using Python's built-in HTMLParser.
    Returns validation results with warnings/errors.
    """
    try:
        parser = SimpleHTMLValidator()
        parser.feed(html_code)

        if not parser.has_html:
            parser.warnings.append("Missing <html> tag")
        if not parser.has_head:
            parser.warnings.append("Missing <head> tag")
        if not parser.has_body:
            parser.warnings.append("Missing <body> tag")

        html_lower = html_code.lower()

        if 'x-data' in html_lower or 'x-init' in html_lower:
            if 'alpinejs' not in html_lower:
                parser.warnings.append("Alpine.js directives found but CDN not included")
            else:
                parser.info.append("✓ Alpine.js CDN included")

        if 'new chart' in html_lower or 'chart(' in html_lower:
            if 'chart.js' not in html_lower and 'chartjs' not in html_lower:
                parser.warnings.append("Chart.js code found but CDN not included")
            else:
                parser.info.append("✓ Chart.js CDN included")

        if 'interact(' in html_lower:
            if 'interactjs' not in html_lower and 'interact.js' not in html_lower:
                parser.warnings.append("Interact.js code found but CDN not included")
            else:
                parser.info.append("✓ Interact.js CDN included")

        widget_count = html_code.count('class="widget"') + html_code.count("class='widget'")
        if widget_count > 0:
            parser.info.append(f"Found {widget_count} widget(s)")

        script_count = html_code.lower().count('<script')
        style_count = html_code.lower().count('<style')
        if script_count > 0:
            parser.info.append(f"Found {script_count} script tag(s)")
        if style_count > 0:
            parser.info.append(f"Found {style_count} style tag(s)")

        has_errors = len(parser.errors) > 0
        has_warnings = len(parser.warnings) > 0

        return {
            "valid": not has_errors,
            "errors": parser.errors,
            "warnings": parser.warnings,
            "info": parser.info,
            "summary": f"{'✓ Valid HTML' if not has_errors else '✗ Invalid HTML'} " +
                      f"({len(parser.warnings)} warning{'s' if len(parser.warnings) != 1 else ''}, " +
                      f"{len(parser.errors)} error{'s' if len(parser.errors) != 1 else ''})"
        }

    except Exception as e:
        return {
            "valid": False,
            "errors": [f"Parse error: {str(e)}"],
            "warnings": [],
            "info": [],
            "summary": "✗ Failed to parse HTML"
        }
