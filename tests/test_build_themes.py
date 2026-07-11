"""Tests for scripts/build_themes.py section-filtered membership + Variant I roles.

The coverage repo loads scripts by path (scripts/ is not an importable package),
following the existing test_sync_publish_to_git.py precedent. Pure-function
assertions over synthetic report content — no filesystem corpus needed.
Run with: python3 -m pytest tests/ -q
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_themes.py"
_spec = importlib.util.spec_from_file_location("build_themes", _SCRIPT)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)  # type: ignore[union-attr]


# --- (a) section filtering: a wikilink only in 業務簡介 is NOT a member ---------
def test_desc_only_wikilink_not_member() -> None:
    content = (
        "## 業務簡介\n"
        "本公司是 [[AI 伺服器]] 概念股，名列受惠族群。\n"
        "\n"
        "## 供應鏈位置\n"
        "**上游 (材料):**\n"
        "- 銅材\n"
        "**中游 (製造):**\n"
        "- 本公司\n"
        "**下游 (客戶):**\n"
        "- 一般工業客戶\n"
    )
    roles, unparsed = mod._derive_memberships(content)
    assert "AI 伺服器" not in roles  # desc-only mention must not confer membership
    assert unparsed is False


# --- (b) Variant I four-way inversion table -----------------------------------
def test_variant_i_four_way_mapping() -> None:
    content = (
        "## 供應鏈位置\n"
        "分層前的敘述提到 [[PreTheme]]。\n"
        "**上游 (材料):**\n"
        "- 採購 [[UpstreamTheme]] 原料\n"
        "**中游 (製造):**\n"
        "- 自身即 [[MidTheme]] 業務\n"
        "**下游 (應用):**\n"
        "- 供貨給 [[DownTheme]] 終端\n"
    )
    roles, unparsed = mod._derive_memberships(content)
    # company upstream -> theme downstream (company consumes the theme's output)
    assert roles["UpstreamTheme"] == "downstream"
    # company downstream -> theme upstream (company supplies into the theme)
    assert roles["DownTheme"] == "upstream"
    # company midstream / self -> theme midstream
    assert roles["MidTheme"] == "midstream"
    # before any tier label -> related
    assert roles["PreTheme"] == "related"
    assert unparsed is False


# --- (c) (report, theme) dedup + cross-tier priority (midstream wins) ----------
def test_dedup_cross_tier_priority_midstream_wins() -> None:
    content = (
        "## 供應鏈位置\n"
        "**中游 (製造):**\n"
        "- 本業即 [[ThemeX]]\n"
        "**下游 (客戶):**\n"
        "- 也供貨 [[ThemeX]]，再提一次 [[ThemeX]]\n"
    )
    roles, unparsed = mod._derive_memberships(content)
    # 中游 -> midstream, 下游 -> upstream; midstream outranks upstream
    assert roles["ThemeX"] == "midstream"
    # exactly one membership row per (report, theme)
    entries = [(t, r) for t, r in roles.items() if t == "ThemeX"]
    assert entries == [("ThemeX", "midstream")]
    assert unparsed is False


def test_dedup_cross_tier_priority_upstream_over_downstream() -> None:
    content = (
        "## 供應鏈位置\n"
        "**上游 (材料):**\n"
        "- 採購 [[ThemeY]] 原料\n"
        "**下游 (客戶):**\n"
        "- 也供貨 [[ThemeY]]\n"
    )
    roles, _ = mod._derive_memberships(content)
    # company upstream -> theme downstream; company downstream -> theme upstream;
    # upstream(1) outranks downstream(2)
    assert roles["ThemeY"] == "upstream"


# --- (d) SC section present but NO tier labels -> related + unparsed record ----
def test_no_tier_labels_related_and_unparsed() -> None:
    content = (
        "## 供應鏈位置\n"
        "本公司採業務單元式分層，涵蓋 [[ThemeA]] 與 [[ThemeB]]，無上中下游標籤。\n"
    )
    roles, unparsed = mod._derive_memberships(content)
    assert roles["ThemeA"] == "related"
    assert roles["ThemeB"] == "related"
    assert unparsed is True


# --- (d2) NO 供應鏈位置 heading at all -> graceful degradation, no crash --------
def test_no_supply_chain_section_degrades_gracefully() -> None:
    content = (
        "## 業務簡介\n"
        "本公司是 [[ThemeA]] 概念股。\n"
        "## 主要客戶及供應商\n"
        "### 主要客戶\n"
        "- [[ThemeB]]\n"
    )
    roles, unparsed = mod._derive_memberships(content)
    assert roles == {}            # no SC section -> no members
    assert unparsed is False      # empty SC is not an unparsed-tier report


# --- (e) hand-authored 相關主題 line preserved verbatim through build_theme_page
def test_related_line_preserved_in_build_theme_page() -> None:
    related = "**相關主題:** [[CoWoS]]、[[HBM]]"
    wl_map = {
        "AI 伺服器": [
            {
                "ticker": "3017",
                "company": "奇鋐",
                "sector": "Electronic Components",
                "role": "upstream",
            }
        ]
    }
    page = mod.build_theme_page(
        "AI 伺服器",
        mod.THEME_DEFINITIONS["AI 伺服器"],
        wl_map,
        existing_related_line=related,
    )
    assert related in page          # carried over verbatim
    assert page.count(related) == 1  # not duplicated / synthesized


def test_h3_inside_supply_chain_stays_in_scope():
    """H3 subheadings belong to their parent H2 section (review HIGH-1)."""
    content = (
        "## 供應鏈位置\n\n**上游 (原料):**\n- 原料商\n\n"
        "### 細分子段\n- [[H3內題材]] 相關佈局\n\n"
        "## 財務概況\n- [[財務段題材]] 不可入\n"
    )
    memberships, unparsed = mod._derive_memberships(content)
    assert "H3內題材" in memberships  # still supply-chain scope
    assert "財務段題材" not in memberships  # other H2 closes the section
    assert unparsed is False


def test_empty_content_degrades_gracefully():
    """Error case (review MEDIUM-2): empty/whitespace reports never crash."""
    assert mod._derive_memberships("") == ({}, False)
    assert mod._derive_memberships("   \n\n") == ({}, False)


def test_unclosed_wikilink_is_not_a_member():
    """Error case (review MEDIUM-2): malformed [[unclosed token is ignored."""
    content = "## 供應鏈位置\n\n**中游:**\n- [[未閉合題材 佈局中\n"
    memberships, _ = mod._derive_memberships(content)
    assert memberships == {}


def test_english_tier_labels_case_insensitive():
    """Gemini PR #6: **UPSTREAM**/**downstream** variants parse, no KeyError."""
    content = (
        "## 供應鏈位置\n\n**UPSTREAM:**\n- [[大寫題材]]\n\n"
        "**downstream (apps):**\n- [[小寫題材]]\n"
    )
    memberships, unparsed = mod._derive_memberships(content)
    assert memberships["大寫題材"] == "downstream"  # company upstream -> theme downstream
    assert memberships["小寫題材"] == "upstream"    # company downstream -> theme upstream
    assert unparsed is False


# ===========================================================================
# Manual member preservation (Cortex #833/#834 companion). A theme page is the
# SSOT for admin edits: (人工)-annotated member lines and the **人工排除:** line
# survive a rebuild, mirroring the existing 相關主題 preservation (PR #5).
# ===========================================================================

_THEME = "AI 伺服器"


def _derived(ticker, company, sector, role):
    return {"ticker": ticker, "company": company, "sector": sector, "role": role}


def _write_page(tmp_path, body: str) -> str:
    filepath = tmp_path / "theme.md"
    filepath.write_text(body, encoding="utf-8")
    return str(filepath)


# --- (a) a manual line in an existing page survives rebuild verbatim ----------
def test_manual_line_survives_rebuild_verbatim(tmp_path) -> None:
    manual_line = "- **6435 大中** (人工)"
    existing = (
        "# AI 伺服器供應鏈\n\n> desc\n\n**涵蓋公司數:** 2\n\n---\n\n"
        "## 上游 (2)\n\n"
        "- **1111 甲公司** (Chemicals)\n"
        f"{manual_line}\n\n"
    )
    filepath = _write_page(tmp_path, existing)

    manual_by_role, exclude_line = mod.extract_manual_edits(filepath)
    assert manual_by_role == {"upstream": [("6435", manual_line)]}
    assert exclude_line is None

    wl_map = {_THEME: [_derived("1111", "甲公司", "Chemicals", "upstream")]}
    page = mod.build_theme_page(
        _THEME,
        mod.THEME_DEFINITIONS[_THEME],
        wl_map,
        manual_by_role=manual_by_role,
        exclude_line=exclude_line,
    )
    assert manual_line in page                 # verbatim, incl. (人工) annotation
    assert page.count(manual_line) == 1
    # counts reflect the merged result: 1 derived + 1 manual, both upstream
    assert "## 上游 (2)" in page
    assert "**涵蓋公司數:** 2" in page
    # the manual line sits inside the 上游 section
    upstream_block = page.split("## 上游 (2)")[1]
    assert manual_line in upstream_block


# --- (b) manual line for a ticker ALSO derived -> exactly one line, manual wins
def test_manual_line_overrides_derived_same_ticker() -> None:
    manual_line = "- **6435 大中** (人工)"
    # derived scan puts 6435 in 上游; admin set-role'd the manual line to 下游.
    wl_map = {
        _THEME: [
            _derived("6435", "大中", "Chemicals", "upstream"),
            _derived("1111", "甲公司", "Chemicals", "upstream"),
        ]
    }
    manual_by_role = {"downstream": [("6435", manual_line)]}
    page = mod.build_theme_page(
        _THEME,
        mod.THEME_DEFINITIONS[_THEME],
        wl_map,
        manual_by_role=manual_by_role,
    )
    # exactly one line mentions 6435 — the manual one
    assert page.count("6435") == 1
    assert manual_line in page
    assert "- **6435 大中** (Chemicals)" not in page   # derived form dropped
    # it lives in 下游 (admin's chosen section), not 上游
    assert "## 下游 (1)" in page
    assert "## 上游 (1)" in page                        # only 1111 survives here
    downstream_block = page.split("## 下游 (1)")[1]
    assert manual_line in downstream_block
    assert "**涵蓋公司數:** 2" in page                  # 1111 + manual 6435


# --- (c) 人工排除 line suppresses a derived ticker AND is carried over verbatim -
def test_exclusion_suppresses_derived_and_carried_verbatim(tmp_path) -> None:
    exclude_line = "**人工排除:** [[9999]]"
    existing = (
        "# AI 伺服器供應鏈\n\n> desc\n\n**涵蓋公司數:** 2\n\n"
        f"{exclude_line}\n\n---\n\n"
        "## 上游 (2)\n\n"
        "- **1111 甲公司** (Chemicals)\n"
        "- **9999 乙公司** (Chemicals)\n\n"
    )
    filepath = _write_page(tmp_path, existing)

    manual_by_role, extracted_exclude = mod.extract_manual_edits(filepath)
    assert manual_by_role == {}
    assert extracted_exclude == exclude_line

    wl_map = {
        _THEME: [
            _derived("1111", "甲公司", "Chemicals", "upstream"),
            _derived("9999", "乙公司", "Chemicals", "upstream"),
        ]
    }
    page = mod.build_theme_page(
        _THEME,
        mod.THEME_DEFINITIONS[_THEME],
        wl_map,
        exclude_line=extracted_exclude,
    )
    assert "- **9999 乙公司** (Chemicals)" not in page  # derived member suppressed
    assert "- **1111 甲公司** (Chemicals)" in page       # sibling kept
    assert exclude_line in page                        # carried over verbatim
    # 9999 survives only inside the verbatim exclusion line, never as a member
    assert page.count("9999") == 1
    assert "## 上游 (1)" in page
    assert "**涵蓋公司數:** 1" in page


# --- (d) neither manual nor exclude -> identical to plain derivation -----------
def test_no_manual_edits_identical_to_plain_derivation() -> None:
    wl_map = {
        _THEME: [
            _derived("1111", "甲公司", "Chemicals", "upstream"),
            _derived("2222", "乙公司", "Chemicals", "downstream"),
        ]
    }
    plain = mod.build_theme_page(_THEME, mod.THEME_DEFINITIONS[_THEME], wl_map)
    with_empty = mod.build_theme_page(
        _THEME,
        mod.THEME_DEFINITIONS[_THEME],
        wl_map,
        manual_by_role={},
        exclude_line=None,
    )
    assert plain == with_empty          # no behaviour change when no admin edits
    assert "**涵蓋公司數:** 2" in plain


# --- (e) excluded ticker that is ALSO a manual line -> manual wins (add>exclude)
def test_manual_readd_beats_exclusion() -> None:
    manual_line = "- **6435 大中** (人工)"
    wl_map = {_THEME: [_derived("6435", "大中", "Chemicals", "upstream")]}
    # 6435 is both listed for exclusion AND manually re-added to 下游.
    page = mod.build_theme_page(
        _THEME,
        mod.THEME_DEFINITIONS[_THEME],
        wl_map,
        manual_by_role={"downstream": [("6435", manual_line)]},
        exclude_line="**人工排除:** [[6435]]",
    )
    assert manual_line in page                       # manual re-add wins
    assert "- **6435 大中** (Chemicals)" not in page   # derived form still dropped
    assert "## 下游 (1)" in page
    assert "**涵蓋公司數:** 1" in page


# --- (f) 人工排除 line inertness against the Cortex ETL member/relation regexes -
def test_exclusion_line_etl_grammar_inertness() -> None:
    """The 人工排除 line matches neither the member nor the relation regex used
    by Cortex's coverage_etl._parse_themes (mirrors 相關主題-adjacent inertness).

    KNOWN CORTEX FOLLOW-UP: _parse_themes explicitly recognises + skips the
    相關主題 line BEFORE its fallback wikilink scan, so those links never become
    members. 人工排除 is not yet recognised there, so its bare [[DDDD]] wikilinks
    would be re-ingested by that fallback (coverage_etl.py ~616-629). Cortex must
    give 人工排除 the same explicit-skip treatment before its API emits the line;
    the assertion below reproduces the gap so the follow-up cannot be forgotten.
    """
    exclude_line = "**人工排除:** [[9999]] [[6435]]"
    # Regexes copied verbatim from coverage_etl.py:541-551 (do NOT import Cortex).
    member_pattern = re.compile(r"^\s*-\s+\*\*(\d{4,6})\s+(.+?)\*\*\s*(?:\((.+?)\))?")
    relation_line_pattern = re.compile(r"^\*\*相關主題[：:]\*\*\s*(.+)$")
    assert member_pattern.match(exclude_line) is None      # not a member line
    assert relation_line_pattern.match(exclude_line) is None  # not a relation line

    # Reproduce the ETL fallback path (coverage_etl.py ~616-629) to lock in the
    # documented gap: bare numeric wikilinks look like TW tickers.
    wikilink_pattern = re.compile(r"\[\[([^\]]+)\]\]")
    likely_ticker = re.compile(r"^\d{4,6}$")
    caught_by_fallback = [
        tok for tok in wikilink_pattern.findall(exclude_line)
        if likely_ticker.match(tok.strip())
    ]
    assert caught_by_fallback == ["9999", "6435"]  # -> Cortex-side skip required
