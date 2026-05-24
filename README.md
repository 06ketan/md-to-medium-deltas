# md-to-medium-deltas

Convert standard Markdown into Medium's undocumented, internal JSON "Deltas" protocol for programmatic publishing.

## The Problem

Before 2025, developers could programmatically publish to Medium using their official REST API by sending standard Markdown or HTML. 

Medium has since deprecated its public API and stopped issuing integration tokens. The only way to programmatically post rich content to Medium now is to reverse-engineer their internal, React-based web editor protocol, which uses a proprietary JSON "Delta" structure (`POST /p/{id}/deltas`).

Existing libraries only convert Medium articles *to* Markdown. **This library does the opposite:** it parses Markdown and generates exact Delta operations (with correct inline markup text offsets) to reconstruct your article in Medium's editor.

## Features

- **Accurate Text Offsets:** Automatically calculates character-exact boundaries for inline markup (bold, italic, code).
- **Rich Elements Supported:**
  - Headings (H1, H2, H3)
  - Inline formatting (Bold, Italic, Strikethrough, Code)
  - Auto-linked Hyperlinks and Markdown Links
  - Blockquotes
  - Code blocks
  - Ordered and Unordered Lists
- **Intelligent Pre-processing:** Fixes edge cases with list elements and correctly drops Markdown dividers that Medium handles natively.

## Installation

```bash
pip install md-to-medium-deltas
```

*(Note: Requires Python 3.12+)*

## Usage

```python
from md_to_medium_deltas import markdown_to_deltas

markdown_text = """
# My Awesome Post

This is a paragraph with **bold** and *italic* text, plus a [link](https://github.com).

- Bullet point one
- Bullet point two

```python
print("Hello World")
```
"""

# The start_index indicates the starting delta sequence position.
# Pass 1 if your request already created a title delta at index 0.
# By default, `strip_h1=True` drops the leading `# Title` from the body
# so it doesn't duplicate your separate Title field.
deltas = markdown_to_deltas(markdown_text, start_index=1, strip_h1=True)

import json
print(json.dumps(deltas, indent=2))
```

This output can then be directly sent as the payload to Medium's `POST /p/{id}/deltas` internal endpoint.

## How it works

This package uses a robust two-step pipeline:
1. `python-markdown` parses the raw Markdown into compliant HTML.
2. `selectolax` traverses the HTML AST and translates DOM nodes into Medium's Delta paradigm, tracking text accumulation to emit exact inline-markup offsets.

## License
MIT
