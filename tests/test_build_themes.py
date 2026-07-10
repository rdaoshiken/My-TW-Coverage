"""Tests for scripts/build_themes.py section-filtered membership + Variant I roles.

The coverage repo loads scripts by path (scripts/ is not an importable package),
following the existing test_sync_publish_to_git.py precedent. Pure-function
assertions over synthetic report content — no filesystem corpus needed.
Run with: python3 -m pytest tests/ -q
"""
from __future__ import annotations

import importlib.util
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
