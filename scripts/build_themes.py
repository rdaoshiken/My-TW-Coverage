"""
build_themes.py — Generate thematic investment screens from wikilink graph.

Scans all ticker reports for wikilinks, groups companies by theme (technology,
material, application), and generates markdown pages showing the full value chain
for each theme.

Usage:
  python scripts/build_themes.py              # Rebuild all themes
  python scripts/build_themes.py --list       # List available themes
  python scripts/build_themes.py "CoWoS"      # Rebuild single theme

Output: themes/ folder with one .md per theme.
"""

from __future__ import annotations

import os
import re
import sys
from collections import defaultdict

REPORTS_DIR = os.path.join(os.path.dirname(__file__), "..", "Pilot_Reports")
THEMES_DIR = os.path.join(os.path.dirname(__file__), "..", "themes")

# Bracket wikilink token, shared by every scan. Kept module-level (not inline)
# so membership and role derivation match the same token grammar as Cortex's
# coverage_etl.py wikilink parser (Cortex #802).
WIKILINK_PATTERN = re.compile(r"\[\[([^\]]+)\]\]")

# A supply-chain tier label is a bold tier keyword, e.g. "**上游 (金屬材料):**"
# or the bulleted form "*   **中游 (本體)**:" — bracketed annotations and English
# variants tolerated. Deliberately NOT line-anchored (user ruling 2026-07-11):
# ~248 reports use the bulleted form, which a ^-anchored pattern would wrongly
# degrade to 'related'. Non-anchored reproduces the prototype's measurements
# (13 unparsed reports). Heading-style tier labels do not occur in the corpus.
# A tier keyword names the COMPANY's own position in ITS chain.
_COMPANY_TIER_BY_LABEL = {
    "上游": "upstream",
    "中游": "midstream",
    "下游": "downstream",
    "upstream": "upstream",
    "midstream": "midstream",
    "downstream": "downstream",
}

# English labels matched case-insensitively (Gemini review on PR #6); lookups
# lowercase the captured group, which is a no-op for the Chinese keys.
TIER_LABEL_PATTERN = re.compile(
    r"\*\*\s*(" + "|".join(re.escape(label) for label in _COMPANY_TIER_BY_LABEL) + ")",
    re.IGNORECASE,
)

# Variant I (chain-perspective inversion): a report describes the company's own
# chain, so a theme wikilink's company-tier maps to the THEME's tier by inversion.
#   company downstream (theme is its end-application) -> company supplies theme  -> theme upstream
#   company upstream   (theme is its raw input)       -> company consumes theme  -> theme downstream
#   company midstream  (the theme IS its own business)                           -> theme midstream
#   before any tier label ("pre")                                               -> related
_THEME_ROLE_BY_COMPANY_TIER = {
    "downstream": "upstream",
    "upstream": "downstream",
    "midstream": "midstream",
    "pre": "related",
}

# Cross-tier tie-break when one theme is hit under several tiers in one report:
# self-identity (midstream) is the strongest signal, related the weakest.
_ROLE_PRIORITY = {"midstream": 0, "upstream": 1, "downstream": 2, "related": 3}

# Hand-authored "**相關主題:** [[X]]" lines on existing theme pages are curated
# ground truth (they feed Cortex's coverage_theme_relation table, Cortex #802)
# and must survive a rebuild verbatim. Both halfwidth and fullwidth colons are
# accepted, matching the Cortex ETL parser.
RELATED_LINE_PATTERN = re.compile(r"^\*\*相關主題[：:]\*\*.*$", re.MULTILINE)


def extract_related_line(theme_filepath):
    """Return the existing hand-authored 相關主題 line of a theme page, or None."""
    if not os.path.exists(theme_filepath):
        return None
    with open(theme_filepath, "r", encoding="utf-8") as f:
        match = RELATED_LINE_PATTERN.search(f.read())
    return match.group(0) if match else None

# Curated themes with supply chain role hints
# Format: theme_wikilink -> { display_name, description }
# NOTE: theme-to-theme links live ONLY on the theme pages themselves
# (the hand-curated "**相關主題:** [[X]]" line) — never defined here.
THEME_DEFINITIONS = {
    # === Advanced Packaging ===
    "CoWoS": {
        "name": "CoWoS 先進封裝",
        "desc": "台積電 Chip-on-Wafer-on-Substrate 2.5D 先進封裝技術，AI 晶片關鍵製程",
    },
    "HBM": {
        "name": "HBM 高頻寬記憶體",
        "desc": "High Bandwidth Memory，AI 加速器必備的高速堆疊記憶體",
    },
    "CPO": {
        "name": "CPO 共封裝光學",
        "desc": "Co-Packaged Optics，將光學元件整合於晶片封裝中以突破頻寬瓶頸",
    },
    # === Photonics ===
    "矽光子": {
        "name": "矽光子 Silicon Photonics",
        "desc": "以矽基製程整合光學元件，實現高速光互連，下一代資料中心核心技術",
    },
    "VCSEL": {
        "name": "VCSEL 垂直共振腔面射型雷射",
        "desc": "3D 感測、光通訊及 LiDAR 核心光源元件",
    },
    # === Compound Semiconductors ===
    "碳化矽": {
        "name": "碳化矽 SiC",
        "desc": "第三代半導體材料，耐高壓高溫，電動車逆變器及充電樁關鍵材料",
    },
    "氮化鎵": {
        "name": "氮化鎵 GaN",
        "desc": "第三代半導體材料，高頻高效，5G 基站、快充及衛星通訊核心",
    },
    "磷化銦": {
        "name": "磷化銦 InP",
        "desc": "III-V 族化合物半導體，光通訊雷射及高速光電元件基板材料",
    },
    # === AI / Data Center ===
    "AI 伺服器": {
        "name": "AI 伺服器供應鏈",
        "desc": "AI 訓練與推論伺服器完整供應鏈，從晶片到系統到散熱",
    },
    "資料中心": {
        "name": "資料中心供應鏈",
        "desc": "超大規模資料中心基礎設施，涵蓋伺服器、網通、電源、散熱",
    },
    # === EV / Automotive ===
    "電動車": {
        "name": "電動車供應鏈",
        "desc": "電動車完整供應鏈，從電池材料到功率元件到車用電子",
    },
    # === Applications ===
    "5G": {
        "name": "5G 通訊供應鏈",
        "desc": "5G 基礎建設與終端應用，涵蓋基站、天線、射頻前端、濾波器",
    },
    "低軌衛星": {
        "name": "低軌衛星 LEO Satellite",
        "desc": "低軌道衛星通訊供應鏈，天線、地面站、射頻模組",
    },
    # === Process / Equipment ===
    "EUV": {
        "name": "EUV 極紫外光微影",
        "desc": "先進製程關鍵微影技術，7nm 以下節點必備",
    },
    # === Materials ===
    "光阻液": {
        "name": "光阻液 Photoresist",
        "desc": "半導體微影製程關鍵化學材料",
    },
    "ABF 載板": {
        "name": "ABF 載板",
        "desc": "Ajinomoto Build-up Film 載板，高階 IC 封裝基板",
    },
    "矽晶圓": {
        "name": "矽晶圓",
        "desc": "半導體製造最基礎的原材料",
    },
    # === Key customers (cross-industry) ===
    "Apple": {
        "name": "Apple 蘋果供應鏈",
        "desc": "蘋果公司台灣供應鏈成員",
    },
    "NVIDIA": {
        "name": "NVIDIA 輝達供應鏈",
        "desc": "NVIDIA GPU 及 AI 平台台灣供應鏈",
    },
    "Tesla": {
        "name": "Tesla 特斯拉供應鏈",
        "desc": "特斯拉電動車台灣供應鏈成員",
    },
}


def _split_sections(content: str) -> dict[str, str]:
    """Split report markdown into desc / supply_chain / customers by H2 headings.

    Only H2 headings switch sections: the three named ones open their buffer,
    any other H2 (財務概況 …) closes the current one. H3 subheadings belong to
    their parent section — content under an H3 inside 供應鏈位置 is still
    supply-chain content. ``desc`` and ``customers`` are returned for potential
    future use but no longer feed membership.
    """
    sections = {"desc": "", "supply_chain": "", "customers": ""}
    buffers: dict[str, list[str]] = {key: [] for key in sections}
    current: str | None = None
    for line in content.splitlines():
        if line.startswith("## "):
            heading = line[3:].strip()
            if heading.startswith("業務簡介"):
                current = "desc"
            elif heading.startswith("供應鏈位置"):
                current = "supply_chain"
            elif heading.startswith("主要客戶及供應商"):
                current = "customers"
            else:
                current = None
            continue
        if current is not None:
            buffers[current].append(line)
    for key in sections:
        sections[key] = "\n".join(buffers[key])
    return sections


def _split_supply_chain_tiers(sc_text: str) -> list[tuple[str, str]]:
    """Split a 供應鏈位置 section into ``(company_tier, segment_text)`` slices.

    Segments are delimited by bold tier labels. Text before the first label is
    tagged ``"pre"``. A section with no tier label at all yields a single
    ``[("pre", sc_text)]`` — the caller records such reports as unparsed.
    """
    matches = list(TIER_LABEL_PATTERN.finditer(sc_text))
    if not matches:
        return [("pre", sc_text)]

    segments: list[tuple[str, str]] = []
    leading = sc_text[: matches[0].start()]
    if leading.strip():
        segments.append(("pre", leading))
    for index, match in enumerate(matches):
        tier = _COMPANY_TIER_BY_LABEL[match.group(1).lower()]
        end = matches[index + 1].start() if index + 1 < len(matches) else len(sc_text)
        segments.append((tier, sc_text[match.start():end]))
    return segments


def _derive_memberships(content: str) -> tuple[dict[str, str], bool]:
    """Derive ``{theme: role}`` memberships from one report's markdown.

    Only bracket wikilinks inside the 供應鏈位置 section count. Each theme's
    company-tier is inverted to a theme-role (Variant I); a theme hit under
    several tiers collapses to its highest-priority role, so every report
    contributes at most one row per theme. Returns the membership map and a
    flag marking reports whose SC section had no parseable tier labels.
    """
    sections = _split_sections(content)
    sc_text = sections["supply_chain"]
    segments = _split_supply_chain_tiers(sc_text)
    has_real_tier = any(tier != "pre" for tier, _ in segments)
    is_unparsed = bool(sc_text.strip()) and not has_real_tier

    theme_role: dict[str, str] = {}
    for tier, segment_text in segments:
        role = _THEME_ROLE_BY_COMPANY_TIER[tier]
        for theme in set(WIKILINK_PATTERN.findall(segment_text)):
            previous = theme_role.get(theme)
            if previous is None or _ROLE_PRIORITY[role] < _ROLE_PRIORITY[previous]:
                theme_role[theme] = role
    return theme_role, is_unparsed


def scan_wikilinks() -> dict[str, list[dict[str, str]]]:
    """Scan all reports, return ``{theme: [{ticker, company, sector, role}]}``.

    Membership is section-filtered (供應鏈位置 bracket wikilinks only) with
    Variant I role inversion. Reports whose SC section lacks tier labels have
    their themes degraded to ``related`` and are surfaced in a warning log — the
    degradation is never silent.
    """
    wl_map: dict[str, list[dict[str, str]]] = defaultdict(list)
    unparsed_reports: list[tuple[str, str, str]] = []

    for sector_dir in os.listdir(REPORTS_DIR):
        sector_path = os.path.join(REPORTS_DIR, sector_dir)
        if not os.path.isdir(sector_path):
            continue
        for f in os.listdir(sector_path):
            if not f.endswith(".md"):
                continue
            m = re.match(r"^(\d{4})_(.+)\.md$", f)
            if not m:
                continue
            ticker, company = m.group(1), m.group(2)
            filepath = os.path.join(sector_path, f)
            with open(filepath, "r", encoding="utf-8") as fh:
                content = fh.read()

            memberships, is_unparsed = _derive_memberships(content)
            if is_unparsed:
                unparsed_reports.append((ticker, company, sector_dir))
            for theme, role in memberships.items():
                wl_map[theme].append(
                    {
                        "ticker": ticker,
                        "company": company,
                        "sector": sector_dir,
                        "role": role,
                    }
                )

    if unparsed_reports:
        print(
            f"\n[warn] {len(unparsed_reports)} report(s) have a 供應鏈位置 section "
            "with no parseable tier label; their themes degraded to 'related':"
        )
        for ticker, company, sector_dir in sorted(unparsed_reports):
            print(f"  - {ticker} {company} ({sector_dir})")

    return wl_map


def build_theme_page(theme_tag, theme_def, wl_map, existing_related_line=None):
    """Build a single theme markdown page.

    existing_related_line: the hand-curated 相關主題 line from the current
    page, carried over verbatim. The page is the single source of truth for
    theme-to-theme links; rebuilds never synthesize or alter this line.
    """
    entries = wl_map.get(theme_tag, [])
    if not entries:
        return None

    lines = []
    lines.append(f"# {theme_def['name']}")
    lines.append("")
    lines.append(f"> {theme_def['desc']}")
    lines.append("")
    lines.append(f"**涵蓋公司數:** {len(entries)}")
    lines.append("")

    # Related themes: the page itself is the single source of truth — a
    # hand-curated line is carried over verbatim, and the script never
    # synthesizes one (theme-to-theme links are Obsidian-curated only).
    if existing_related_line is not None:
        lines.append(existing_related_line)
        lines.append("")

    lines.append("---")
    lines.append("")

    # Group by role
    upstream = [e for e in entries if e["role"] == "upstream"]
    midstream = [e for e in entries if e["role"] == "midstream"]
    downstream = [e for e in entries if e["role"] == "downstream"]
    other = [e for e in entries if e["role"] == "related"]

    def format_entries(entries):
        # Group by sector
        by_sector = defaultdict(list)
        for e in entries:
            by_sector[e["sector"]].append(e)
        result = []
        for sector in sorted(by_sector.keys()):
            items = sorted(by_sector[sector], key=lambda x: x["ticker"])
            for item in items:
                result.append(
                    f"- **{item['ticker']} {item['company']}** ({sector})"
                )
        return result

    if upstream:
        lines.append(f"## 上游 ({len(upstream)})")
        lines.append("")
        lines.extend(format_entries(upstream))
        lines.append("")

    if midstream:
        lines.append(f"## 中游 ({len(midstream)})")
        lines.append("")
        lines.extend(format_entries(midstream))
        lines.append("")

    if downstream:
        lines.append(f"## 下游 ({len(downstream)})")
        lines.append("")
        lines.extend(format_entries(downstream))
        lines.append("")

    if other:
        lines.append(f"## 相關公司 ({len(other)})")
        lines.append("")
        lines.extend(format_entries(other))
        lines.append("")

    return "\n".join(lines)


def build_index(themes_built):
    """Build themes/README.md index."""
    lines = []
    lines.append("# Thematic Investment Screens")
    lines.append("")
    lines.append("> Auto-generated supply chain maps for thematic investing.")
    lines.append("> Regenerate: `python scripts/build_themes.py`")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Group by category
    categories = {
        "先進封裝": ["CoWoS", "HBM", "CPO"],
        "光電與化合物半導體": ["矽光子", "VCSEL", "碳化矽", "氮化鎵", "磷化銦"],
        "AI / 資料中心": ["AI 伺服器", "資料中心", "NVIDIA"],
        "電動車 / 車用": ["電動車", "Tesla"],
        "通訊": ["5G", "低軌衛星"],
        "製程與設備": ["EUV"],
        "材料": ["光阻液", "ABF 載板", "矽晶圓"],
        "品牌供應鏈": ["Apple", "NVIDIA", "Tesla"],
    }

    for cat_name, tags in categories.items():
        lines.append(f"## {cat_name}")
        lines.append("")
        for tag in tags:
            if tag in themes_built:
                count = themes_built[tag]
                safe_name = tag.replace(" ", "_").replace("/", "_")
                lines.append(f"- [{tag}]({safe_name}.md) — {count} 家公司")
        lines.append("")

    return "\n".join(lines)


def main():
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    os.makedirs(THEMES_DIR, exist_ok=True)

    args = sys.argv[1:]

    if "--list" in args:
        for tag, defn in sorted(THEME_DEFINITIONS.items()):
            print(f"  {tag}: {defn['name']}")
        return

    print("Scanning wikilinks across all reports...")
    wl_map = scan_wikilinks()
    print(f"Found {len(wl_map)} unique wikilinks.\n")

    # Filter to requested theme or build all
    if args and args[0] != "--list":
        themes_to_build = {args[0]: THEME_DEFINITIONS.get(args[0])}
        if not themes_to_build[args[0]]:
            print(f"Theme '{args[0]}' not in THEME_DEFINITIONS. Use --list to see available themes.")
            return
    else:
        themes_to_build = THEME_DEFINITIONS

    themes_built = {}
    for tag, defn in themes_to_build.items():
        safe_name = tag.replace(" ", "_").replace("/", "_")
        filepath = os.path.join(THEMES_DIR, f"{safe_name}.md")
        existing_related_line = extract_related_line(filepath)
        page = build_theme_page(tag, defn, wl_map, existing_related_line)
        if page:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(page)
            count = len(wl_map.get(tag, []))
            themes_built[tag] = count
            print(f"  {tag}: {count} companies -> {safe_name}.md")

    # Build index
    index = build_index(themes_built)
    with open(os.path.join(THEMES_DIR, "README.md"), "w", encoding="utf-8") as f:
        f.write(index)

    print(f"\nDone. Generated {len(themes_built)} theme pages in themes/")


if __name__ == "__main__":
    main()
