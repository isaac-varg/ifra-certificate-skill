---
name: ifra-certificate
metadata:
  author: Isaac Vargas
  version: 1.0.0
  category: productivity
  tags: [regulatory, chemistry, cosmetic, authoring]
  documentation: https://github.com/isaac–varg/ifra–certificate–skill
description: >
  Generate branded IFRA Certificate of Conformity PDFs using company details from config.json.
  Use this skill whenever the user wants to create, reformat, or generate an IFRA certificate,
  compliance document, or fragrance usage limits document. Triggers include: uploading a supplier
  IFRA PDF to reformat, providing fragrance blend percentages with supplier PDFs, asking about
  IFRA compliance documentation, or any request involving "IFRA certificate", "IFRA compliance",
  "fragrance certificate", "usage levels", or "certificate of conformity". Use even if the user
  just says "make me a certificate for this fragrance".
---

# IFRA Certificate of Conformity Skill

Generates multi-page branded IFRA Certificates of Conformity using your company's branding
(configured in `config.json`). Supports:

1. **Single supplier PDF reformat** — parse a supplier's IFRA PDF and reissue it as branded certificate using the company's branding
2. **Blend certificate** — combine multiple fragrance components (each with its own supplier IFRA PDF and blend %) into a single finished-blend certificate, with mathematically correct blended usage limits

## Output format

A 4-page PDF matching the established template:
- **Page 1**: Branded header (logo + regulatory info), product details, IFRA class usage table (Classes 1–12), boilerplate certification text
- **Pages 2–3**: IFRA Class Definitions table (all 12 classes with product types)
- **Page 4**: Disclaimer
- Footer on every page: company name left, page number right

Amendment version: **50th** (current)

---

## Quick start

### Step 0: One-time setup

Copy `config.example.json` to `config.json` and fill in your company details:

```json
{
  "company_name": "Your Company Name, Inc.",
  "phone": "(555) 123-4567",
  "email": "hello@yourcompany.com",
  "logo": "assets/logo.png",
  "amendment": "50th"
}
```

Replace `assets/logo.png` with your own company logo file (update the path in config if the filename differs).

### Step 1: Collect inputs

**For a single supplier PDF reformat:**
- Supplier IFRA PDF (uploaded file)
- Product name
- SKU / product code

**For a blend certificate:**
- Each component's: name, supplier IFRA PDF, and % share in the finished blend
- Blend percentages must sum to 100%; do not include composition in the pdf.
- Product name and SKU for the finished blend

### Step 2: Run the script

All generation logic lives in `scripts/generate_ifra.py`.  
The logo asset path is configured in `config.json` (default: `assets/logo.png`).  
The IFRA class definitions are at `references/ifra-classes.json`.

**Single supplier reformat:**
```bash
python3 /path/to/skill/scripts/generate_ifra.py \
  --input /path/to/supplier_ifra.pdf \
  --product-name "Product Name Here" \
  --sku "SKU123" \
  --output /mnt/user-data/outputs/certificate.pdf
```

**Blend certificate:**
```bash
python3 /path/to/skill/scripts/generate_ifra.py \
  --blend "Rose Absolute:/path/rose_ifra.pdf:40" "Vanilla CO2:/path/vanilla_ifra.pdf:60" \
  --product-name "Rose Vanilla Blend" \
  --sku "BL001" \
  --output /mnt/user-data/outputs/certificate.pdf
```

### Step 3: Present the file

```python
present_files(["/mnt/user-data/outputs/certificate.pdf"])
```

---

## Blend math

For a finished fragrance blend, the maximum safe usage level for each IFRA class is:

```
blended_limit[class] = min over each ingredient of:
    supplier_limit[class] / (ingredient_fraction)
```

Where `ingredient_fraction = pct_in_blend / 100`.

This gives the maximum % of the **finished blend** that can be used in a product while staying within each ingredient's individual IFRA limit. The script handles this automatically.

---

## Dependencies

```bash
pip install pdfplumber reportlab pillow --break-system-packages
```

---

## Edge cases & notes

- **Missing classes in supplier PDF**: If a supplier PDF doesn't list a class, it defaults to `0.000` (not permitted). Inform the user.
- **Blend percentages ≠ 100%**: Script will warn but continue. Correct with user if needed.
- **Supplier PDF parsing**: Uses both text extraction and table extraction for maximum compatibility across different supplier PDF formats. If extraction fails (e.g. scanned/image PDF), inform the user and ask them to provide values manually.
- **Manual override**: If the user provides class values directly (e.g. "Class 1: 0, Class 2: 5.5..."), skip PDF parsing and pass `levels` dict directly to `generate_certificate()`.
- **SKU not known**: Use product name as SKU if user doesn't have one.
- **Class 12**: Added to the 50th Amendment. Defaults to 0.000 if not in supplier PDF — note this to the user as Class 12 is new and many older supplier PDFs won't include it.
- **Usage Over 100%**: If any of the usage percentages are over 100%, correct this to be 100% which indicates an unlimited usage for that category.

---

## File structure

```
ifra-certificate/
├── SKILL.md                        ← this file
├── config.example.json             ← template — copy to config.json
├── config.json                     ← your company settings (git-ignored)
├── .gitignore
├── scripts/
│   └── generate_ifra.py           ← main generation script
├── assets/
│   └── logo.png                   ← your company logo
└── references/
    └── ifra-classes.json          ← All 12 IFRA class definitions (50th Amendment)
```
