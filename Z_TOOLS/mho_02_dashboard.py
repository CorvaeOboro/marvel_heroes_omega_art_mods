"""
MHO_02 DASHBOARD - Art Mod Progress Dashboard for Marvel Heroes Omega

FUNCTIONALITY
  Scans the ITEM/ directory and reports per-item status across the full
  art mod pipeline: base PSD/PNG/DDS, upscale PSD/PNG, reference images,
  and approval state.  Presents a scrollable thumbnail grid with color-coded
  stage indicators, group filtering, search, and a detail panel for editing
  per-item metadata (notes, rating, display name, type, approval).

  Per-item status tracked:
    - Has PSD (base resolution source)
    - Has PNG (base resolution export)
    - Has DDS (base resolution DDS conversion)
    - Has Upscale PSD (high-res source in upscale/ subfolder)
    - Has Upscale PNG (high-res export in upscale/ subfolder)
    - Has Reference Images (in the upscale per-item subfolder)
    - Approval state (APPROVED:: in item markdown file)

  Two view modes:
    - Card grid     : thumbnails with stage blocks, action buttons, names
    - Spreadsheet   : compact table with colored status blocks

  Batch actions:
    - Create Missing Upscales : copies base PSDs into upscale/ folders
    - Generate 2x Folders     : creates <group>_2x with 80px nearest-neighbor PNGs
    - Clean DDS               : removes base .dds files for visible groups
    - Refresh Scan / Previews : re-scan items or rebuild preview cache

TERMS
  - GROUP   : A folder under ITEM/ (e.g. ITEM_Artifacts2, ITEM_DarkRunes)
  - ITEM    : Identified by a shared stem for its PSD/PNG/DDS files
  - Items starting with "00_" are composite/reference images, not mod items.

FOLDER STRUCTURE per GROUP:
  ITEM/<GROUP>/
    item_name.psd        <- base PSD
    item_name.png        <- base PNG
    item_name.dds        <- base DDS
    upscale/             <- (or Upscale/)
      item_name/         <- per-item upscale subfolder
        item_name.psd    <- upscale PSD
        item_name.png    <- upscale PNG / generated preview (not a ref)
        item_name.md     <- metadata (APPROVED:: field)
        ref/             <- per-item reference images folder
          ref_image1.jpg
          ref_image2.png
          ...
      00_ref/            <- shared reference images

KEY COMPONENTS
  - discover_items()            : scans ITEM/ for groups + per-item assets
  - read/write_item_metadata()  : parses/writes .md metadata files
  - build_preview_image()       : generates and caches PNG thumbnails
  - _create_outlined_button()   : Canvas-based button with black text outline
  - MarvelHeroesDashboard       : main Tkinter application class
    - __init__                  : stats bar, controls, group filters, content
    - _get_visible_items()      : filtering (groups, search, composites, etc.)
    - _stage_bar()              : 7-stage colored block indicator
    - _show_item_detail()       : detail panel with preview, metadata, actions
    - _render_batch()           : batched async card rendering (32 per tick)
    - _draw_spreadsheet()       : table view with status blocks

STAGES (in order): P=PSD | N=PNG | U=Upscale PSD | u=Upscale PNG | R=Refs | A=Approved | D=DDS
  Green=present, Red=missing, Gray=not critical, Orange=folder exists but empty
  DDS is a post-approval step and is shown in grey when missing.

CONTROLS
  Left-click group button  = toggle visibility
  Right-click group button = solo that group
  Click thumbnail          = open detail panel
  Click 'fold'             = open group folder in Explorer
  Click 'ref'              = open ref folder in Explorer
  Click 'upsc'             = open upscale PSD in Photoshop (orange = creates from base if missing)
  Click 'approve'          = toggle APPROVED:: in markdown
  ORANGE BORDER            = item missing one or more stages

CONFIG
  Settings file  : mho_02_dashboard_settings.json
  Preview cache  : _preview_cache_mh/
  Metadata       : per-item .md files in upscale/<item>/ folders

QUICK USAGE
  python Z_TOOLS/mho_02_dashboard.py

TOOLSGROUP::TRACKING
SORTGROUP::1
SORTPRIORITY::2
STATUS::working
VERSION::20260721
"""

# region Imports

import os
import subprocess
import json
import shutil
import re
import hashlib
from pathlib import Path
from tkinter import *
from tkinter import ttk, messagebox as mb, filedialog
import tkinter.font as tkfont
from PIL import Image, ImageTk, ImageDraw
from typing import Dict, List, Optional, Set, Callable
from queue import Queue, Empty
import threading
import logging

logging.basicConfig(level=logging.INFO, format="%(message)s")

SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
ITEM_ROOT = PROJECT_ROOT / "ITEM"

SETTINGS_FILE = SCRIPT_DIR / "mho_02_dashboard_settings.json"
PREVIEW_CACHE_DIR = SCRIPT_DIR / "_preview_cache_mh"
PREVIEW_CACHE_DIR.mkdir(parents=True, exist_ok=True)

PREVIEW_MAX_DIM = 128
MAX_ASSET_WIDTH = 160
THUMB_SIZE = (MAX_ASSET_WIDTH, MAX_ASSET_WIDTH)

APPROVED_RE = re.compile(r"^APPROVED::\s*(.+)$", re.IGNORECASE)
RATING_RE = re.compile(r"^RATING::\s*(.+)$", re.IGNORECASE)
LATEST_RE = re.compile(r"^LATEST::\s*(.+)$", re.IGNORECASE)
BASE_MOD_RE = re.compile(r"^BASE_PSD_MODIFIED::\s*(.+)$", re.IGNORECASE)
UPSCALE_MOD_RE = re.compile(r"^UPSCALE_PSD_MODIFIED::\s*(.+)$", re.IGNORECASE)

# Placeholder for items with no PNG preview
PLACEHOLDER_IMG = PREVIEW_CACHE_DIR / "_placeholder.png"
if not PLACEHOLDER_IMG.exists():
    try:
        _ph = Image.new("RGBA", (64, 64), (40, 40, 40, 255))
        _ph.save(PLACEHOLDER_IMG, format="PNG")
    except Exception:
        pass

# Colors - muted/darkened palette
DARK_GRAY = "#15161a"
DARKER_GRAY = "#070809"
LIGHTER_GRAY = "#23262d"
WHITE = "#d8d8d2"
ACCENT = "#2a2e35"
GREEN = "#3d8c5a"
RED = "#7a3838"
YELLOW = "#87824a"
BLUE = "#4a7a8c"
PURPLE = "#6a5a8c"
ORANGE = "#8c6a3a"
BORDER_WARN = "#7a5a3a"
GRID_TEXT = "#8a8a82"

# Darker, muted button accents
FOLDER_BTN = "#20242a"
REF_BTN = "#2a2520"
UPSCALE_BTN = "#25202a"
APPROVE_BTN_ON = "#1f3328"
APPROVE_BTN_OFF = "#3d2a1f"
BTN_COL_WIDTH = 56

PHOTOSHOP_EXE = r"C:\Program Files\Adobe\Adobe Photoshop 2024\Photoshop.exe"

# endregion


# region Settings & OS

def load_settings() -> Dict:
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_settings(settings: Dict) -> None:
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
    except Exception as e:
        logging.error(f"Failed to save settings: {e}")


def open_explorer(path: str) -> None:
    try:
        if os.path.isdir(path):
            subprocess.Popen(["explorer", path])
        elif os.path.isfile(path):
            subprocess.Popen(["explorer", "/select,", path])
    except Exception as e:
        logging.error(f"Error opening Explorer for {path}: {e}")


def open_photoshop(path: str) -> None:
    try:
        if os.path.exists(path):
            subprocess.Popen([PHOTOSHOP_EXE, path])
    except Exception as e:
        logging.error(f"Error launching Photoshop: {e}")

# endregion


# region UI Utils

def _create_outlined_button(parent: Frame, text: str, bg: str, fg: str,
                            command: Callable, font=("Segoe UI", 9, "bold"),
                            padx: int = 8, pady: int = 3) -> Frame:
    """Create a clickable button with bold text and a 1px black outline.

    Uses a Canvas with create_text to draw the text 8 times in black at
    1px offsets, then once in the foreground color on top.
    """
    wrap = Frame(parent, bg=bg, highlightthickness=1, highlightbackground="black")

    # Measure text
    font_obj = tkfont.Font(font=font)
    text_w = font_obj.measure(text)
    text_h = font_obj.metrics("linespace")

    canvas_w = text_w + 4  # room for 1px outline on each side
    canvas_h = text_h + 4

    canvas = Canvas(wrap, width=canvas_w, height=canvas_h,
                    bg=bg, highlightthickness=0, bd=0)
    canvas.pack(padx=padx, pady=pady)

    cx = canvas_w // 2
    cy = canvas_h // 2

    # Draw 8-direction black outline
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            if dx == 0 and dy == 0:
                continue
            canvas.create_text(cx + dx, cy + dy, text=text,
                               font=font, fill="black", anchor="center")

    # Draw foreground text on top
    canvas.create_text(cx, cy, text=text, font=font, fill=fg, anchor="center")

    def _on_click(event=None):
        command()

    for widget in (wrap, canvas):
        widget.bind("<Button-1>", _on_click)
        widget.config(cursor="hand2")

    return wrap

# endregion


# region Metadata

def get_item_metadata_path(item: Dict) -> Optional[Path]:
    """Return the Path to the metadata .md file for this item."""
    try:
        upscale_folder = item.get("upscale_folder")
        if not upscale_folder:
            return None
        item_folder = Path(upscale_folder)
        return item_folder / f"{item['name']}.md"
    except Exception:
        return None


def parse_bool(val: str) -> Optional[bool]:
    v = val.strip().lower()
    if v in {"true", "yes", "y", "1"}:
        return True
    if v in {"false", "no", "n", "0"}:
        return False
    return None


def _mtime_str(path: Path) -> str:
    try:
        if path and path.exists():
            return str(int(path.stat().st_mtime))
    except Exception:
        pass
    return ""


def read_item_metadata(item: Dict) -> Dict:
    """Read item metadata from the .md file.

    Returns a dict with keys: approved, rating, latest, notes, item_type,
    display_name, base_psd_modified, upscale_psd_modified.
    """
    defaults = {
        "approved": None,
        "rating": "",
        "latest": "",
        "notes": "",
        "item_type": "",
        "display_name": "",
        "base_psd_modified": "",
        "upscale_psd_modified": "",
    }
    try:
        md_path = get_item_metadata_path(item)
        if not md_path or not md_path.exists():
            return dict(defaults)
        text = md_path.read_text(encoding="utf-8", errors="ignore")
        metadata = dict(defaults)
        lines = text.splitlines()
        in_body = False
        body_lines = []
        for line in lines:
            stripped = line.strip()
            if not in_body and stripped == "":
                in_body = True
                continue
            m = re.match(r"^([A-Z_]+)::\s*(.*)$", stripped, re.IGNORECASE)
            if m:
                key = m.group(1).upper()
                val = m.group(2).strip()
                if key == "APPROVED":
                    metadata["approved"] = parse_bool(val)
                elif key == "RATING":
                    metadata["rating"] = val
                elif key == "LATEST":
                    metadata["latest"] = val.lower()
                elif key == "TYPE":
                    metadata["item_type"] = val
                elif key == "DISPLAY_NAME":
                    metadata["display_name"] = val
                elif key == "BASE_PSD_MODIFIED":
                    metadata["base_psd_modified"] = val
                elif key == "UPSCALE_PSD_MODIFIED":
                    metadata["upscale_psd_modified"] = val
                continue
            if in_body:
                body_lines.append(line)
        metadata["notes"] = "\n".join(body_lines).strip()
        return metadata
    except Exception:
        return dict(defaults)


def write_item_metadata(item: Dict, metadata: Dict) -> None:
    """Write item metadata back to the .md file, preserving freeform notes."""
    try:
        md_path = get_item_metadata_path(item)
        if not md_path:
            return
        md_path.parent.mkdir(parents=True, exist_ok=True)
        lines = []
        approved = metadata.get("approved")
        if approved is True:
            lines.append("APPROVED:: true")
        elif approved is False:
            lines.append("APPROVED:: false")
        else:
            lines.append("APPROVED:: ")
        lines.append(f"RATING:: {metadata.get('rating', '')}")
        lines.append(f"LATEST:: {metadata.get('latest', '')}")
        lines.append(f"TYPE:: {metadata.get('item_type', '')}")
        lines.append(f"DISPLAY_NAME:: {metadata.get('display_name', '')}")
        lines.append(f"BASE_PSD_MODIFIED:: {metadata.get('base_psd_modified', '')}")
        lines.append(f"UPSCALE_PSD_MODIFIED:: {metadata.get('upscale_psd_modified', '')}")
        notes = metadata.get("notes", "").strip()
        if notes:
            lines.append("")
            lines.extend(notes.splitlines())
        md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        # Keep item dict in sync
        item.update(metadata)
    except Exception as e:
        logging.error(f"Error writing metadata: {e}")

# endregion


# region Discovery

def find_upscale_folder(group_path: Path) -> Optional[Path]:
    for name in ("upscale", "Upscale"):
        p = group_path / name
        if p.is_dir():
            return p
    return None


def discover_items(item_root: Path) -> List[Dict]:
    """Scan ITEM_ROOT for GROUPs and their items.

    Returns a list of item dicts with keys:
      group, name, psd, png, dds,
      upscale_psd, upscale_png, upscale_folder, ref_folder, ref_count,
      has_psd, has_png, has_dds, has_upscale_psd, has_upscale_png, has_refs,
      is_composite
    """
    items: List[Dict] = []
    if not item_root.exists():
        logging.error(f"ITEM_ROOT does not exist: {item_root}")
        return items

    for group_folder in sorted(item_root.iterdir()):
        if not group_folder.is_dir():
            continue
        group_name = group_folder.name
        if group_name.startswith(".") or group_name.startswith("00_"):
            continue

        logging.info(f"GROUP: {group_name}")

        upscale_dir = find_upscale_folder(group_folder)

        # Collect all PSD stems in the group root (excluding 00_ prefixed)
        psd_files = sorted(p for p in group_folder.glob("*.psd") if not p.name.startswith("00_"))
        png_files = sorted(p for p in group_folder.glob("*.png") if not p.name.startswith("00_"))
        dds_files = sorted(p for p in group_folder.glob("*.dds") if not p.name.startswith("00_"))

        # Build a set of all item names from any file type
        all_stems: Set[str] = set()
        for p in psd_files:
            all_stems.add(p.stem)
        for p in png_files:
            all_stems.add(p.stem)
        for p in dds_files:
            all_stems.add(p.stem)

        # Also collect upscale stems
        upscale_stems: Set[str] = set()
        if upscale_dir:
            for p in upscale_dir.glob("*.psd"):
                if not p.name.startswith("00_"):
                    upscale_stems.add(p.stem)
            for p in upscale_dir.glob("*.png"):
                if not p.name.startswith("00_"):
                    upscale_stems.add(p.stem)
            all_stems.update(upscale_stems)

        count = 0
        for stem in sorted(all_stems):
            is_composite = stem.startswith("00_")

            psd_path = group_folder / f"{stem}.psd"
            png_path = group_folder / f"{stem}.png"
            dds_path = group_folder / f"{stem}.dds"

            has_psd = psd_path.exists()
            has_png = png_path.exists()
            has_dds = dds_path.exists()

            # Upscale paths
            upscale_psd_path = None
            upscale_png_path = None
            ref_folder = None
            ref_count = 0

            if upscale_dir:
                item_folder = upscale_dir / stem
                # Support both conventions: files directly under upscale/
                # or inside a per-item folder upscale/<stem>/
                candidates_psd = [upscale_dir / f"{stem}.psd", item_folder / f"{stem}.psd"]
                candidates_png = [upscale_dir / f"{stem}.png", item_folder / f"{stem}.png"]
                for p in candidates_psd:
                    if p.exists():
                        upscale_psd_path = p
                        break
                for p in candidates_png:
                    if p.exists():
                        upscale_png_path = p
                        break

                # Ref folder is the "ref" subfolder inside the per-item upscale folder
                ref_candidates = [item_folder / "ref", item_folder / "Ref"]
                for p in ref_candidates:
                    if p.is_dir():
                        ref_folder = p
                        break

                if ref_folder and ref_folder.is_dir():
                    try:
                        ref_files = []
                        md_name = f"{stem}.md"
                        item_img_names = {f"{stem}.png".lower(), f"{stem}.psd".lower()}
                        excluded_names = {".ds_store", "thumbs.db", "desktop.ini"}
                        for ext in ("*.png", "*.jpg", "*.jpeg", "*.webp", "*.bmp"):
                            for f in ref_folder.glob(ext):
                                if f.name.startswith("."):
                                    continue
                                if f.name.lower() in excluded_names or f.name.lower() in item_img_names:
                                    continue
                                if f.is_file():
                                    ref_files.append(f)
                        ref_count = len(ref_files)
                    except Exception:
                        ref_count = 0

            has_upscale_psd = upscale_psd_path is not None and upscale_psd_path.exists()
            has_upscale_png = upscale_png_path is not None and upscale_png_path.exists()
            has_refs = ref_count > 0

            item_dict = {
                "group": group_name,
                "name": stem,
                "is_composite": is_composite,
                "psd": str(psd_path) if has_psd else None,
                "png": str(png_path) if has_png else None,
                "dds": str(dds_path) if has_dds else None,
                "upscale_psd": str(upscale_psd_path) if has_upscale_psd else None,
                "upscale_png": str(upscale_png_path) if has_upscale_png else None,
                "upscale_folder": str(item_folder if item_folder.is_dir() else upscale_dir) if upscale_dir else None,
                "ref_folder": str(ref_folder) if ref_folder and ref_folder.is_dir() else None,
                "ref_count": ref_count,
                "has_psd": has_psd,
                "has_png": has_png,
                "has_dds": has_dds,
                "has_upscale_psd": has_upscale_psd,
                "has_upscale_png": has_upscale_png,
                "has_refs": has_refs,
                "is_complete": all([has_psd, has_png, has_upscale_psd, has_upscale_png, has_refs]),
                "group_path": str(group_folder),
                "approved": None,
                "rating": "",
                "latest": "",
                "notes": "",
                "item_type": "",
                "display_name": "",
                "base_psd_modified": "",
                "upscale_psd_modified": "",
            }
            metadata = read_item_metadata(item_dict)
            item_dict.update(metadata)

            # Detect PSD modification changes
            base_psd_current = _mtime_str(psd_path) if has_psd else ""
            upscale_psd_current = _mtime_str(upscale_psd_path) if has_upscale_psd else ""
            item_dict["base_psd_current"] = base_psd_current
            item_dict["upscale_psd_current"] = upscale_psd_current
            item_dict["base_psd_changed"] = bool(
                base_psd_current and item_dict["base_psd_modified"] and base_psd_current != item_dict["base_psd_modified"]
            )
            item_dict["upscale_psd_changed"] = bool(
                upscale_psd_current and item_dict["upscale_psd_modified"] and upscale_psd_current != item_dict["upscale_psd_modified"]
            )

            items.append(item_dict)
            count += 1

        logging.info(f"  Found {count} items in GROUP '{group_name}'")

    logging.info(f"Total items discovered: {len(items)}")
    return items

# endregion


# region Previews

def _preview_cache_path(item: Dict, prefer_upscale: bool = False) -> Path:
    group = item.get("group", "group")
    name = item.get("name", "item")
    suffix = "upscale" if prefer_upscale else "base"
    return PREVIEW_CACHE_DIR / group / f"{name}_{suffix}.png"


def get_item_preview_source(item: Dict, prefer_upscale: bool = False) -> Optional[str]:
    """Return the best available PNG source path for an item preview.

    Default (prefer_upscale=False) prefers the base folder PNG.
    When prefer_upscale=True, the upscale PNG is tried first.
    """
    base_png = item.get("png")
    upscale_png = item.get("upscale_png")
    if prefer_upscale:
        if upscale_png and os.path.isfile(upscale_png):
            return upscale_png
        if base_png and os.path.isfile(base_png):
            return base_png
    else:
        if base_png and os.path.isfile(base_png):
            return base_png
        if upscale_png and os.path.isfile(upscale_png):
            return upscale_png
    return None


def build_preview_image(item: Dict, force_refresh: bool = False, prefer_upscale: bool = False) -> Path:
    """Build or retrieve a cached PNG preview for an item.

    Preview source is controlled by prefer_upscale:
      False -> base folder PNG (default)
      True  -> upscale PNG
    Falls back to the other source if the preferred one is missing.
    Uses placeholder if no PNG exists (PSD-only items).
    """
    cache_path = _preview_cache_path(item, prefer_upscale=prefer_upscale)

    source_png = get_item_preview_source(item, prefer_upscale=prefer_upscale)

    if cache_path.exists() and not force_refresh:
        if source_png:
            try:
                cache_mtime = cache_path.stat().st_mtime
                source_mtime = os.path.getmtime(source_png)
                if source_mtime > cache_mtime:
                    pass
                else:
                    return cache_path
            except Exception:
                return cache_path
        else:
            return cache_path

    try:
        if source_png:
            with Image.open(source_png) as img:
                w, h = img.size
                if max(w, h) > PREVIEW_MAX_DIM:
                    if w >= h:
                        new_w = PREVIEW_MAX_DIM
                        new_h = int(h * PREVIEW_MAX_DIM / w)
                    else:
                        new_h = PREVIEW_MAX_DIM
                        new_w = int(w * PREVIEW_MAX_DIM / h)
                else:
                    new_w, new_h = w, h
                img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
                out = Image.new("RGBA", (new_w, new_h), (24, 24, 24, 255))
                out.paste(img, (0, 0))
        else:
            size = PREVIEW_MAX_DIM
            out = Image.new("RGBA", (size, size), (32, 32, 32, 255))
            draw = ImageDraw.Draw(out)
            label = item.get("name", "(no name)")
            bbox = draw.textbbox((0, 0), label)
            text_w = bbox[2] - bbox[0]
            text_h = bbox[3] - bbox[1]
            x = max(4, (size - text_w) // 2)
            y = max(4, (size - text_h) // 2)
            draw.text((x, y), label, fill=(220, 220, 220, 255))

        cache_path.parent.mkdir(parents=True, exist_ok=True)
        out.save(cache_path, "PNG", optimize=True)
    except Exception as e:
        logging.error(f"Error building preview for {item.get('name')}: {e}")
        if PLACEHOLDER_IMG.exists():
            return PLACEHOLDER_IMG

    return cache_path


def get_group_color(group_name: str) -> str:
    hash_val = sum(ord(c) for c in group_name)
    colors = [
        "#1a1d21", "#1d1a1d", "#1a1d1a", "#1d1b1a",
        "#1a1b1d", "#1d1a1b",
    ]
    return colors[hash_val % len(colors)]

# endregion


# region UI Base

class MarvelHeroesDashboard:
    def __init__(self, root, items: List[Dict]):
        self.root = root
        self.items = items
        self.thumbs: List[ImageTk.PhotoImage] = []

        # Dark mode ttk scrollbar style
        style = ttk.Style()
        style.configure(
            "Dark.Vertical.TScrollbar",
            background=ACCENT,
            troughcolor="#000000",
            bordercolor="#000000",
            arrowcolor=WHITE,
            relief="flat",
            borderwidth=0,
        )
        style.map(
            "Dark.Vertical.TScrollbar",
            background=[("active", LIGHTER_GRAY)],
            arrowcolor=[("active", WHITE)],
        )
        self.excluded_groups: Set[str] = set()
        self.show_composites = BooleanVar(value=False)
        self.show_missing_only = BooleanVar(value=False)
        self.show_names = BooleanVar(value=True)
        self.spreadsheet_mode = BooleanVar(value=False)
        self.sort_missing_first = BooleanVar(value=False)
        self.hide_approved = BooleanVar(value=False)
        self.prefer_upscale_preview = BooleanVar(value=False)
        self.show_card_border = BooleanVar(value=False)
        self.selected_item = None
        self.detail_preview_image = None
        self.ps_path_var = StringVar(value=PHOTOSHOP_EXE)
        self.max_width_var = StringVar(value=str(MAX_ASSET_WIDTH))
        self.columns_var = StringVar(value="")
        self.search_var = StringVar(value="")

        # Solo mode state
        self.solo_mode_active = False
        self.solo_groups: Set[str] = set()
        self.saved_excluded_groups = None

        # Widget references for lightweight updates
        self.item_widgets: Dict[tuple, Dict] = {}
        self.spreadsheet_widgets: Dict[tuple, Dict] = {}

        # Render state for batched rendering
        self._render_version = 0

        self.root.title("Marvel Heroes Omega - Art Mod Dashboard")
        self.root.geometry("1500x800")
        self.root.configure(bg=DARK_GRAY)

        # --- Stats bar ---
        stats = self._compute_stats()
        stats_frame = Frame(root, bg=DARKER_GRAY, relief=FLAT, bd=1)
        stats_frame.pack(side=TOP, fill=X, padx=4, pady=(4, 2))

        Label(stats_frame, text="PROJECT PROGRESS DASHBOARD",
              bg=DARKER_GRAY, fg=WHITE, font=("Segoe UI", 11, "bold")).pack(side=LEFT, padx=8, pady=4)

        self.stat_labels: Dict[str, Label] = {}
        stat_items = [
            ("total", "Total", LIGHTER_GRAY),
            ("has_psd", "PSD", BLUE),
            ("has_png", "PNG", GREEN),
            ("has_dds", "DDS", YELLOW),
            ("has_upscale_psd", "Upscale PSD", PURPLE),
            ("has_upscale_png", "Upscale PNG", ORANGE),
            ("has_refs", "Has Refs", GREEN),
            ("approved", "Approved", GREEN),
            ("complete", "Complete", GREEN),
        ]
        stats_right = Frame(stats_frame, bg=DARKER_GRAY)
        stats_right.pack(side=RIGHT, padx=(0, 8))
        for key, label_text, color in stat_items:
            val = stats.get(key, 0)
            lbl = Label(stats_right, text=f"{label_text}: {val}",
                        bg=DARKER_GRAY, fg=color, font=("Segoe UI", 9))
            lbl.pack(side=LEFT, padx=4, pady=4)
            self.stat_labels[key] = lbl

        # --- Top controls ---
        top_bar = Frame(root, bg=DARK_GRAY)
        top_bar.pack(side=TOP, fill=X)

        controls = Frame(top_bar, bg=DARK_GRAY)
        controls.pack(side=TOP, fill=X)

        # Photoshop path
        Label(controls, text="Photoshop:", bg=DARK_GRAY, fg=WHITE).pack(side=LEFT, padx=(8, 2), pady=4)
        ps_entry = Entry(controls, textvariable=self.ps_path_var, width=35,
                         bg="#000000", fg=WHITE, insertbackground=WHITE,
                         relief=FLAT, highlightthickness=1, highlightbackground=LIGHTER_GRAY)
        ps_entry.pack(side=LEFT, padx=(0, 8), pady=4)
        self.ps_path_var.trace_add("write", self._update_photoshop_path)

        # Max width
        Label(controls, text="Thumb Width:", bg=DARK_GRAY, fg=WHITE).pack(side=LEFT, padx=(0, 2), pady=4)
        width_entry = Entry(controls, textvariable=self.max_width_var, width=5,
                            bg="#000000", fg=WHITE, insertbackground=WHITE,
                            relief=FLAT, highlightthickness=1, highlightbackground=LIGHTER_GRAY)
        width_entry.pack(side=LEFT, padx=(0, 4), pady=4)
        self.max_width_var.trace_add("write", self._update_max_width)

        # Column override (blank = auto)
        Label(controls, text="Cols:", bg=DARK_GRAY, fg=WHITE).pack(side=LEFT, padx=(0, 2), pady=4)
        cols_entry = Entry(controls, textvariable=self.columns_var, width=4,
                           bg="#000000", fg=WHITE, insertbackground=WHITE,
                           relief=FLAT, highlightthickness=1, highlightbackground=LIGHTER_GRAY)
        cols_entry.pack(side=LEFT, padx=(0, 4), pady=4)
        self.columns_var.trace_add("write", self._redraw)

        # Search filter (name, display name, type, group)
        Label(controls, text="Search:", bg=DARK_GRAY, fg=WHITE).pack(side=LEFT, padx=(4, 2), pady=4)
        search_entry = Entry(controls, textvariable=self.search_var, width=16,
                              bg="#000000", fg=WHITE, insertbackground=WHITE,
                              relief=FLAT, highlightthickness=1, highlightbackground=LIGHTER_GRAY)
        search_entry.pack(side=LEFT, padx=(0, 8), pady=4)
        self.search_var.trace_add("write", self._redraw)

        # Buttons
        Button(controls, text="Info", command=self._show_info,
               bg=ACCENT, fg=WHITE, relief=FLAT, font=("Segoe UI", 9, "bold"),
               activebackground=WHITE, activeforeground=ACCENT).pack(side=LEFT, padx=(0, 4), pady=4)

        Button(controls, text="Refresh Scan", command=self._refresh_scan,
               bg=ACCENT, fg=WHITE, relief=FLAT, font=("Segoe UI", 9),
               activebackground=WHITE, activeforeground=ACCENT).pack(side=LEFT, padx=(0, 4), pady=4)

        Button(controls, text="Refresh Previews", command=self._refresh_previews,
               bg=ACCENT, fg=WHITE, relief=FLAT, font=("Segoe UI", 9),
               activebackground=WHITE, activeforeground=ACCENT).pack(side=LEFT, padx=(0, 4), pady=4)

        Button(controls, text="Create Missing Upscales", command=self._create_missing_upscales,
               bg=ACCENT, fg=WHITE, relief=FLAT, font=("Segoe UI", 9, "bold"),
               activebackground=WHITE, activeforeground=ACCENT).pack(side=LEFT, padx=(0, 8), pady=4)

        Button(controls, text="Generate 2x Folders", command=self._generate_2x_folders,
               bg=ACCENT, fg=WHITE, relief=FLAT, font=("Segoe UI", 9, "bold"),
               activebackground=LIGHTER_GRAY, activeforeground=WHITE).pack(side=LEFT, padx=(0, 8), pady=4)

        Button(controls, text="Clean DDS", command=self._clean_dds_files,
               bg=ACCENT, fg=WHITE, relief=FLAT, font=("Segoe UI", 9, "bold"),
               activebackground=LIGHTER_GRAY, activeforeground=WHITE).pack(side=LEFT, padx=(0, 8), pady=4)

        # Checkbox filter row (separate from buttons)
        checkbox_row = Frame(top_bar, bg=DARK_GRAY)
        checkbox_row.pack(side=TOP, fill=X, padx=4, pady=(2, 4))

        Checkbutton(checkbox_row, text="Upscale Preview", variable=self.prefer_upscale_preview,
                    command=self._redraw, bg=DARK_GRAY, fg=WHITE, selectcolor=ACCENT,
                    activebackground=DARK_GRAY, activeforeground=WHITE).pack(side=LEFT, padx=4, pady=2)

        Checkbutton(checkbox_row, text="Names", variable=self.show_names,
                    command=self._redraw, bg=DARK_GRAY, fg=WHITE, selectcolor=ACCENT,
                    activebackground=DARK_GRAY, activeforeground=WHITE).pack(side=LEFT, padx=4, pady=2)

        Checkbutton(checkbox_row, text="Border", variable=self.show_card_border,
                    command=self._redraw, bg=DARK_GRAY, fg=WHITE, selectcolor=ACCENT,
                    activebackground=DARK_GRAY, activeforeground=WHITE).pack(side=LEFT, padx=4, pady=2)

        Checkbutton(checkbox_row, text="Missing First", variable=self.sort_missing_first,
                    command=self._redraw, bg=DARK_GRAY, fg=WHITE, selectcolor=ACCENT,
                    activebackground=DARK_GRAY, activeforeground=WHITE).pack(side=LEFT, padx=4, pady=2)

        Checkbutton(checkbox_row, text="Spreadsheet", variable=self.spreadsheet_mode,
                    command=self._redraw, bg=DARK_GRAY, fg=WHITE, selectcolor=ACCENT,
                    activebackground=DARK_GRAY, activeforeground=WHITE).pack(side=LEFT, padx=4, pady=2)

        Checkbutton(checkbox_row, text="Hide Approved", variable=self.hide_approved,
                    command=self._redraw, bg=DARK_GRAY, fg=WHITE, selectcolor=ACCENT,
                    activebackground=DARK_GRAY, activeforeground=WHITE).pack(side=LEFT, padx=4, pady=2)

        Checkbutton(checkbox_row, text="Composites (00_)", variable=self.show_composites,
                    command=self._redraw, bg=DARK_GRAY, fg=WHITE, selectcolor=ACCENT,
                    activebackground=DARK_GRAY, activeforeground=WHITE).pack(side=LEFT, padx=4, pady=2)

        Checkbutton(checkbox_row, text="Missing Only", variable=self.show_missing_only,
                    command=self._redraw, bg=DARK_GRAY, fg=WHITE, selectcolor=ACCENT,
                    activebackground=DARK_GRAY, activeforeground=WHITE).pack(side=LEFT, padx=4, pady=2)

        # Group filter row
        self.group_filter_frame = Frame(top_bar, bg=DARK_GRAY)
        self.group_filter_frame.pack(side=TOP, fill=X, padx=4, pady=4)
        self.group_buttons: Dict[str, Button] = {}
        self._build_group_filters()

        self._reflow_pending = False
        self._grid_reflow_pending = False
        self.group_filter_frame.bind("<Configure>", self._on_filter_frame_configure)

        # --- Main content area ---
        content = Frame(root, bg=DARKER_GRAY)
        content.pack(side=TOP, fill=BOTH, expand=True)

        # Left: scrollable item grid
        left_content = Frame(content, bg=DARKER_GRAY)
        left_content.pack(side=LEFT, fill=BOTH, expand=True)

        self.canvas = Canvas(left_content, bg=DARKER_GRAY, highlightthickness=0, bd=0)
        self.frame = Frame(self.canvas, bg=DARKER_GRAY)
        self.scroll_y = ttk.Scrollbar(left_content, orient=VERTICAL, command=self.canvas.yview,
                                       style="Dark.Vertical.TScrollbar")
        self.canvas.configure(yscrollcommand=self.scroll_y.set)
        self.canvas.pack(side=LEFT, fill=BOTH, expand=True)
        self.scroll_y.pack(side=RIGHT, fill=Y)
        self.canvas_window = self.canvas.create_window((0, 0), window=self.frame, anchor="nw")
        self.frame.bind("<Configure>",
                        lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<Configure>", self._on_canvas_configure)

        # Right: detail panel
        self.detail_panel = Frame(content, bg=DARK_GRAY, width=400, relief=FLAT, bd=0)
        self.detail_panel.pack(side=RIGHT, fill=Y, padx=(4, 0))
        self.detail_panel.pack_propagate(False)
        self.detail_panel.pack_forget()
        self._build_detail_panel()

        self._draw_grid()

    def _compute_stats(self) -> Dict[str, int]:
        if self.solo_mode_active:
            visible_groups = set(self.solo_groups)
        else:
            visible_groups = {i["group"] for i in self.items} - self.excluded_groups
        visible_items = [i for i in self.items if i["group"] in visible_groups]

        total = len(visible_items)
        has_psd = sum(1 for i in visible_items if i["has_psd"])
        has_png = sum(1 for i in visible_items if i["has_png"])
        has_dds = sum(1 for i in visible_items if i["has_dds"])
        has_upscale_psd = sum(1 for i in visible_items if i["has_upscale_psd"])
        has_upscale_png = sum(1 for i in visible_items if i["has_upscale_png"])
        has_refs = sum(1 for i in visible_items if i["has_refs"])
        approved = sum(1 for i in visible_items if i.get("approved") is True)
        complete = sum(1 for i in visible_items if i.get("is_complete"))
        return {
            "total": total, "has_psd": has_psd, "has_png": has_png,
            "has_dds": has_dds, "has_upscale_psd": has_upscale_psd,
            "has_upscale_png": has_upscale_png, "has_refs": has_refs,
            "approved": approved, "complete": complete,
        }

    def _update_stats(self) -> None:
        stats = self._compute_stats()
        for key, label in self.stat_labels.items():
            if key in stats:
                label.config(text=f"{label.cget('text').split(':')[0]}: {stats[key]}")

# endregion


# region UI Controls

    def _update_photoshop_path(self, *args) -> None:
        global PHOTOSHOP_EXE
        PHOTOSHOP_EXE = self.ps_path_var.get()

    def _update_max_width(self, *args) -> None:
        global MAX_ASSET_WIDTH, THUMB_SIZE
        try:
            new_w = int(self.max_width_var.get())
            if new_w != MAX_ASSET_WIDTH and new_w > 32:
                MAX_ASSET_WIDTH = new_w
                THUMB_SIZE = (MAX_ASSET_WIDTH, MAX_ASSET_WIDTH)
                self._redraw()
        except Exception:
            pass

    def _on_mousewheel(self, event) -> None:
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _on_filter_frame_configure(self, event) -> None:
        if self._reflow_pending:
            return
        self._reflow_pending = True
        self.root.after(100, self._do_reflow)

    def _do_reflow(self) -> None:
        self._reflow_pending = False
        self._build_group_filters()

    def _on_canvas_configure(self, event=None) -> None:
        canvas_width = self.canvas.winfo_width()
        if canvas_width > 1 and self.canvas_window:
            self.canvas.itemconfig(self.canvas_window, width=canvas_width)

        threshold = MAX_ASSET_WIDTH + BTN_COL_WIDTH + 14
        last = getattr(self, "_last_canvas_width", 0)
        if abs(canvas_width - last) < threshold and last > 0:
            return
        self._last_canvas_width = canvas_width

        if self._grid_reflow_pending:
            return
        self._grid_reflow_pending = True
        self.root.after(200, self._do_reflow_grid)

    def _do_reflow_grid(self) -> None:
        self._grid_reflow_pending = False
        self._draw_grid()

    def _get_visible_items(self) -> List[Dict]:
        visible = []
        query = self.search_var.get().strip().lower()
        for item in self.items:
            if item["group"] in self.excluded_groups:
                continue
            if item["is_composite"] and not self.show_composites.get():
                continue
            if self.hide_approved.get() and item.get("approved") is True:
                continue
            if self.show_missing_only.get() and item.get("is_complete"):
                continue
            if query:
                haystack = " ".join([
                    item["name"],
                    item.get("display_name", ""),
                    item.get("item_type", ""),
                    item["group"],
                ]).lower()
                if query not in haystack:
                    continue
            visible.append(item)

        if self.sort_missing_first.get():
            visible.sort(key=lambda i: (i["is_complete"], i["group"], i["name"]))
        else:
            visible.sort(key=lambda i: (i["group"], i["name"]))
        return visible

    def _build_group_filters(self) -> None:
        for w in self.group_filter_frame.winfo_children():
            w.destroy()
        self.group_buttons.clear()

        groups = sorted(set(i["group"] for i in self.items))
        for gname in groups:
            count = sum(1 for i in self.items if i["group"] == gname)
            is_excluded = gname in self.excluded_groups
            bg_color = LIGHTER_GRAY if not is_excluded else DARK_GRAY
            fg_color = WHITE if not is_excluded else ACCENT

            btn = Button(
                self.group_filter_frame,
                text=f"{gname} {count}",
                command=lambda gn=gname: self._toggle_group(gn),
                bg=bg_color, fg=fg_color,
                font=("Segoe UI", 16),
                relief=FLAT, bd=0,
                activebackground=LIGHTER_GRAY, activeforeground=WHITE,
                padx=12, pady=4,
            )
            btn.pack(side=LEFT, padx=2, pady=2)
            btn.bind("<Button-3>", lambda e, gn=gname: self._solo_group(gn))
            self.group_buttons[gname] = btn

    def _toggle_group(self, group_name: str) -> None:
        if self.solo_mode_active:
            self._exit_solo_mode()
        if group_name in self.excluded_groups:
            self.excluded_groups.discard(group_name)
        else:
            self.excluded_groups.add(group_name)
        self._build_group_filters()
        self._redraw()

    def _solo_group(self, group_name: str) -> None:
        if not self.solo_mode_active:
            self.solo_mode_active = True
            self.saved_excluded_groups = self.excluded_groups.copy()
            self.excluded_groups = {g for g in set(i["group"] for i in self.items) if g != group_name}
        else:
            if group_name in self.excluded_groups:
                self.excluded_groups.discard(group_name)
            else:
                self.excluded_groups.add(group_name)
                if not any(g not in self.excluded_groups for g in set(i["group"] for i in self.items)):
                    self._exit_solo_mode()
        self._build_group_filters()
        self._redraw()

    def _exit_solo_mode(self) -> None:
        self.solo_mode_active = False
        if self.saved_excluded_groups is not None:
            self.excluded_groups = self.saved_excluded_groups
            self.saved_excluded_groups = None
        self._build_group_filters()
        self._redraw()

# endregion


# region UI Actions

    def _show_info(self) -> None:
        mb.showinfo("Dashboard Info",
            "Marvel Heroes Omega Art Mod Dashboard\n\n"
            "FOLDER STRUCTURE:\n"
            "  ITEM/<GROUP>/\n"
            "    item_name.psd        <- base PSD\n"
            "    item_name.png        <- base PNG\n"
            "    item_name.dds        <- base DDS\n"
            "    upscale/             <- (or Upscale/)\n"
            "      item_name/         <- per-item upscale folder\n"
            "        item_name.psd    <- upscale PSD\n"
            "        item_name.png    <- upscale PNG (not a ref)\n"
            "        item_name.md     <- metadata (APPROVED::)\n"
            "        ref/             <- per-item reference images\n"
            "          ref_image.jpg\n\n"
            "STAGE BLOCKS (card strip and detail panel):\n"
            "  Order: P=PSD | N=PNG | U=Upscale PSD | u=Upscale PNG | R=Refs | A=Approved | D=DDS\n"
            "  Green = present/approved, Red = missing/not approved, Gray = unknown/not critical\n"
            "  DDS is a post-approval step and is shown in grey when missing\n"
            "  Refs only: Orange = ref folder exists but has no valid images\n\n"
            "ORANGE BORDER = item missing one or more stages\n\n"
            "CONTROLS:\n"
            "  Left-click group button = toggle visibility\n"
            "  Right-click group button = solo that group\n"
            "  Click thumbnail = open detail panel\n"
            "  Click 'folder' = open group folder in Explorer\n"
            "  Click 'ref' = open ref folder in Explorer\n"
            "  Click 'upscale' = open upscale PSD in Photoshop\n"
            "    (orange = creates from base if missing)\n"
            "  Click 'approve' = toggle APPROVED:: in markdown\n\n"
            "SPREADSHEET MODE: table view with colored status blocks\n"
            "MISSING FIRST: sorts incomplete items to top\n"
            "Create Missing Upscales: batch-copies base PSDs to upscale folders"
        )

    def _create_missing_upscales(self) -> None:
        created = 0
        skipped = 0
        errors = 0
        for item in self.items:
            if item["is_composite"]:
                continue
            base_psd = item.get("psd")
            group_path = item.get("group_path")
            if not base_psd or not os.path.isfile(base_psd) or not group_path:
                skipped += 1
                continue
            upscale_dir_path = os.path.join(group_path, "upscale")
            item_folder = os.path.join(upscale_dir_path, item["name"])
            os.makedirs(item_folder, exist_ok=True)
            target_psd = os.path.join(item_folder, f"{item['name']}.psd")
            if os.path.isfile(target_psd):
                skipped += 1
            else:
                try:
                    shutil.copy2(base_psd, target_psd)
                    created += 1
                except Exception:
                    errors += 1
            ref_folder = os.path.join(item_folder, "ref")
            os.makedirs(ref_folder, exist_ok=True)

        self._refresh_scan()
        mb.showinfo("Create Missing Upscales",
                    f"Created: {created}\nAlready existed: {skipped}\nErrors: {errors}")

    def _generate_2x_folders(self) -> None:
        """Generate or refresh _2x variant folders with 80px nearest-neighbor PNGs.

        For each visible group:
          - Creates <group>_2x folder if it doesn't exist
          - For each base PNG (excluding 00_):
            - Skips if a PSD exists in the 2x folder (manual work in progress)
            - Resizes base PNG to 80x80 using nearest-neighbor
            - Replaces existing 2x PNG if present
        """
        visible_groups = {item["group"] for item in self._get_visible_items()}
        if not visible_groups:
            mb.showinfo("Generate 2x Folders", "No visible groups to process.")
            return

        if not mb.askyesno(
            "Generate 2x Folders",
            f"Generate/refresh 80px nearest-neighbor PNGs for {len(visible_groups)} group(s)?\n\n"
            "This will:\n"
            "  - Create <group>_2x folders if missing\n"
            "  - Upscale base PNGs to 80x80 (nearest neighbor)\n"
            "  - Replace existing 2x PNGs (unless a PSD exists in the 2x folder)\n"
            "  - Skip items with PSDs in the 2x folder (manual work)",
        ):
            return

        generated = 0
        skipped_psd = 0
        errors = 0
        folders_created = 0

        for group_name in sorted(visible_groups):
            group_path = os.path.join(str(ITEM_ROOT), group_name)
            if not os.path.isdir(group_path):
                continue

            two_x_folder = os.path.join(str(ITEM_ROOT), f"{group_name}_2x")
            if not os.path.isdir(two_x_folder):
                os.makedirs(two_x_folder, exist_ok=True)
                folders_created += 1
                logging.info(f"Created 2x folder: {two_x_folder}")

            # Collect base PNGs (excluding 00_ prefix)
            base_pngs = sorted(
                f for f in os.listdir(group_path)
                if f.lower().endswith(".png") and not f.startswith("00_")
            )

            for png_name in base_pngs:
                stem = os.path.splitext(png_name)[0]
                two_x_psd = os.path.join(two_x_folder, f"{stem}.psd")
                two_x_png = os.path.join(two_x_folder, f"{stem}.png")

                # Skip if PSD exists in 2x folder (manual adjustments)
                if os.path.isfile(two_x_psd):
                    skipped_psd += 1
                    continue

                try:
                    base_png_path = os.path.join(group_path, png_name)
                    img = Image.open(base_png_path)
                    if img.size != (80, 80):
                        img = img.resize((80, 80), Image.NEAREST)
                    img.save(two_x_png, format="PNG")
                    generated += 1
                except Exception as e:
                    logging.error(f"Error generating 2x for {png_name}: {e}")
                    errors += 1

        self._refresh_scan()
        mb.showinfo(
            "Generate 2x Folders",
            f"Folders created: {folders_created}\n"
            f"PNGs generated: {generated}\n"
            f"Skipped (has PSD): {skipped_psd}\n"
            f"Errors: {errors}",
        )

    def _create_upscale_and_open(self, base_psd: str, item_name: str, group_path: str) -> None:
        try:
            upscale_dir_path = os.path.join(group_path, "upscale")
            item_folder = os.path.join(upscale_dir_path, item_name)
            os.makedirs(item_folder, exist_ok=True)
            target_psd = os.path.join(item_folder, f"{item_name}.psd")
            if not os.path.isfile(target_psd):
                shutil.copy2(base_psd, target_psd)
            ref_folder = os.path.join(item_folder, "ref")
            os.makedirs(ref_folder, exist_ok=True)
            self.root.after(300, lambda: open_photoshop(target_psd))
        except Exception as e:
            logging.error(f"Error creating upscale: {e}")

    def _clean_dds_files(self) -> None:
        """Delete base .dds files for the currently visible groups, with confirmation."""
        visible_groups = {item["group"] for item in self._get_visible_items()}
        if not visible_groups:
            mb.showinfo("Clean DDS", "No visible groups to clean.")
            return

        dds_paths = []
        for item in self.items:
            if item["group"] in visible_groups:
                dds_path = item.get("dds")
                if dds_path and os.path.isfile(dds_path):
                    dds_paths.append(dds_path)

        if not dds_paths:
            mb.showinfo("Clean DDS", "No DDS files to clean in visible groups.")
            return

        if not mb.askyesno(
            "Clean DDS",
            f"Delete {len(dds_paths)} base DDS file(s) from the visible groups?\n\n"
            "This only removes .dds files in the mod group folders, not source art.\n"
            "This action cannot be undone.",
        ):
            return

        deleted = 0
        errors = 0
        for path in dds_paths:
            try:
                os.remove(path)
                deleted += 1
            except Exception as e:
                logging.error(f"Error deleting {path}: {e}")
                errors += 1

        mb.showinfo("Clean DDS", f"Deleted: {deleted}\nErrors: {errors}")
        self._refresh_scan()

    def _move_item_to_folder(self, item: Dict) -> None:
        """Move an item (PSD, PNG, upscale folder + contents) to a different mod group folder.

        Copies files to the destination only if they don't already exist there,
        then verifies with SHA-256 hash checks before deleting the originals.
        """
        current_group = item.get("group", "")
        item_name = item.get("name", "")
        group_path = item.get("group_path", "")
        if not group_path or not os.path.isdir(group_path):
            mb.showerror("Move Item", f"Current group folder not found: {group_path}")
            return

        # Build list of available target groups (exclude current)
        available_groups = []
        if ITEM_ROOT.exists():
            for d in sorted(ITEM_ROOT.iterdir()):
                if d.is_dir() and not d.name.startswith(".") and not d.name.startswith("00_"):
                    if d.name != current_group:
                        available_groups.append(d.name)

        if not available_groups:
            mb.showinfo("Move Item", "No other mod group folders available to move to.")
            return

        # Simple selection dialog
        sel = Toplevel(self.root)
        sel.title("Move Item to Folder")
        sel.configure(bg=DARK_GRAY)
        sel.geometry("420x400")
        sel.transient(self.root)
        sel.grab_set()

        Label(sel, text=f"Move '{item_name}' from {current_group} to:",
              bg=DARK_GRAY, fg=WHITE, font=("Segoe UI", 10, "bold"),
              wraplength=380).pack(padx=8, pady=(8, 4))

        listbox_frame = Frame(sel, bg=DARK_GRAY)
        listbox_frame.pack(fill=BOTH, expand=True, padx=8, pady=4)

        listbox_scroll = ttk.Scrollbar(listbox_frame, orient=VERTICAL,
                                        style="Dark.Vertical.TScrollbar")
        listbox = Listbox(listbox_frame, yscrollcommand=listbox_scroll.set,
                          bg="#000000", fg=WHITE, selectbackground=ACCENT,
                          selectforeground=WHITE, font=("Segoe UI", 10),
                          relief=FLAT, highlightthickness=1,
                          highlightbackground=LIGHTER_GRAY, bd=0)
        listbox_scroll.config(command=listbox.yview)
        listbox.pack(side=LEFT, fill=BOTH, expand=True)
        listbox_scroll.pack(side=RIGHT, fill=Y)

        for gname in available_groups:
            listbox.insert(END, gname)

        result = {"target": None}

        def _confirm():
            sel_idx = listbox.curselection()
            if not sel_idx:
                mb.showwarning("Move Item", "Please select a target folder.", parent=sel)
                return
            result["target"] = available_groups[sel_idx[0]]
            sel.destroy()

        def _cancel():
            sel.destroy()

        btn_frame = Frame(sel, bg=DARK_GRAY)
        btn_frame.pack(fill=X, padx=8, pady=(4, 8))
        Button(btn_frame, text="Cancel", command=_cancel,
               bg=ACCENT, fg=WHITE, relief=FLAT, font=("Segoe UI", 9),
               activebackground=LIGHTER_GRAY, activeforeground=WHITE,
               padx=12, pady=4).pack(side=RIGHT, padx=(4, 0))
        Button(btn_frame, text="Move", command=_confirm,
               bg=ORANGE, fg=WHITE, relief=FLAT, font=("Segoe UI", 9, "bold"),
               activebackground=LIGHTER_GRAY, activeforeground=WHITE,
               padx=12, pady=4).pack(side=RIGHT)

        self.root.wait_window(sel)
        target_group = result["target"]
        if not target_group:
            return

        target_path = ITEM_ROOT / target_group
        if not target_path.is_dir():
            mb.showerror("Move Item", f"Target folder not found: {target_path}")
            return

        # --- Gather source files ---
        # Base files: PSD, PNG, DDS
        base_files = []
        for key in ("psd", "png", "dds"):
            p = item.get(key)
            if p and os.path.isfile(p):
                base_files.append(Path(p))

        # Upscale: only the per-item subfolder (upscale/<stem>/), not the entire upscale dir
        upscale_files = []
        upscale_item_folder = None
        upscale_psd = item.get("upscale_psd")
        upscale_png = item.get("upscale_png")

        # Compute the per-item upscale subfolder directly: <group>/upscale/<stem>/
        group_path_obj = Path(group_path)
        upscale_dir = find_upscale_folder(group_path_obj)
        if upscale_dir:
            per_item_folder = upscale_dir / item_name
            if per_item_folder.is_dir():
                upscale_item_folder = per_item_folder

        # Only include loose upscale PSD/PNG if they are directly in upscale/
        # (not inside the per-item subfolder, which is handled by rglob below)
        if upscale_psd and os.path.isfile(upscale_psd):
            psd_path_obj = Path(upscale_psd)
            if upscale_item_folder and psd_path_obj.is_relative_to(upscale_item_folder):
                pass  # will be caught by rglob
            else:
                upscale_files.append(psd_path_obj)
        if upscale_png and os.path.isfile(upscale_png):
            png_path_obj = Path(upscale_png)
            if upscale_item_folder and png_path_obj.is_relative_to(upscale_item_folder):
                pass  # will be caught by rglob
            else:
                upscale_files.append(png_path_obj)

        all_source_files = list(base_files) + list(upscale_files)

        # Collect all files within the per-item upscale subfolder only
        upscale_folder_contents = []
        if upscale_item_folder and upscale_item_folder.is_dir():
            for f in upscale_item_folder.rglob("*"):
                if f.is_file():
                    upscale_folder_contents.append(f)

        # Combine all unique source files
        all_files = set(all_source_files + upscale_folder_contents)
        if not all_files:
            mb.showinfo("Move Item", "No files found to move for this item.")
            return

        # --- Check for conflicts at destination ---
        # Determine destination upscale folder
        target_upscale_dir = None
        for name in ("upscale", "Upscale"):
            p = target_path / name
            if p.is_dir():
                target_upscale_dir = p
                break

        # Build mapping: source -> destination
        move_map = {}  # source_path -> dest_path
        skipped = []

        for src in sorted(all_files):
            # Determine relative path from the group root
            try:
                rel = src.relative_to(group_path)
            except ValueError:
                # File is outside group_path (shouldn't happen normally)
                skipped.append(str(src))
                continue

            dest = target_path / rel
            if dest.exists():
                skipped.append(str(dest))
                continue
            move_map[src] = dest

        if not move_map:
            mb.showinfo("Move Item",
                        f"All files already exist at the destination.\n"
                        f"Skipped {len(skipped)} file(s).\nNothing to move.")
            return

        # --- Confirmation ---
        file_list = "\n".join(f"  {src.name} -> {dest.parent.name}/{dest.name}"
                               for src, dest in sorted(move_map.items()))
        if skipped:
            file_list += f"\n\nSkipped (already exist): {len(skipped)}"

        if not mb.askyesno(
            "Move Item",
            f"Move {len(move_map)} file(s) from '{current_group}' to '{target_group}'?\n\n"
            f"Files will be copied, hash-verified, then originals deleted.\n\n{file_list}"
        ):
            return

        # --- Copy phase ---
        copied = []
        copy_errors = []
        for src, dest in sorted(move_map.items()):
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
                copied.append((src, dest))
            except Exception as e:
                copy_errors.append((str(src), str(e)))
                logging.error(f"Copy error: {src} -> {dest}: {e}")

        if copy_errors:
            err_list = "\n".join(f"  {s}: {e}" for s, e in copy_errors)
            mb.showerror("Move Item",
                         f"{len(copy_errors)} copy error(s) occurred.\n"
                         f"Originals will NOT be deleted.\n\n{err_list}")
            return

        # --- Hash verification phase ---
        def _file_hash(path: Path) -> str:
            h = hashlib.sha256()
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
            return h.hexdigest()

        hash_mismatches = []
        for src, dest in copied:
            try:
                if _file_hash(src) != _file_hash(dest):
                    hash_mismatches.append((str(src), str(dest)))
            except Exception as e:
                hash_mismatches.append((str(src), f"hash error: {e}"))

        if hash_mismatches:
            mismatch_list = "\n".join(f"  {s} != {d}" for s, d in hash_mismatches)
            mb.showerror("Move Item",
                         f"{len(hash_mismatches)} hash mismatch(es) detected!\n"
                         f"Originals will NOT be deleted.\n"
                         f"Copied files may be corrupted.\n\n{mismatch_list}")
            return

        # --- Delete originals ---
        deleted = 0
        delete_errors = []
        for src, dest in copied:
            try:
                os.remove(src)
                deleted += 1
            except Exception as e:
                delete_errors.append((str(src), str(e)))
                logging.error(f"Delete error: {src}: {e}")

        # Clean up empty source directories (e.g. empty upscale item folder)
        if upscale_item_folder and upscale_item_folder.is_dir():
            try:
                # Remove ref subfolder if empty
                ref_sub = upscale_item_folder / "ref"
                if ref_sub.is_dir():
                    try:
                        ref_sub.rmdir()
                    except OSError:
                        pass
                # Remove the item folder if empty
                upscale_item_folder.rmdir()
            except OSError:
                pass  # Not empty, leave it

        if delete_errors:
            err_list = "\n".join(f"  {s}: {e}" for s, e in delete_errors)
            mb.showwarning("Move Item",
                           f"Moved and verified {deleted} file(s), but {len(delete_errors)} "
                           f"original(s) could not be deleted.\n\n{err_list}")
        else:
            mb.showinfo("Move Item",
                        f"Successfully moved {deleted} file(s) from '{current_group}' to '{target_group}'.\n"
                        f"All files hash-verified.")

        # Refresh the dashboard to reflect the move
        self._refresh_scan()
        self._hide_detail_panel()

    def _approve_item(self, item: Dict) -> None:
        current = item.get("approved")
        new_val = not (current is True)
        metadata = read_item_metadata(item)
        metadata["approved"] = new_val
        write_item_metadata(item, metadata)
        self._update_stats()
        self._update_approval_display(item)

    def _update_approval_display(self, item: Dict) -> None:
        try:
            key = (item["group"], item["name"])
            if self.spreadsheet_mode.get():
                widgets = self.spreadsheet_widgets.get(key)
                if widgets:
                    lbl = widgets.get("approved_label")
                    if lbl:
                        lbl.config(text="True" if item["approved"] else "False",
                                   fg=GREEN if item["approved"] else WHITE)
            else:
                widgets = self.item_widgets.get(key)
                if widgets:
                    btn = widgets.get("approve_btn")
                    if btn:
                        btn.config(bg=APPROVE_BTN_ON if item["approved"] else APPROVE_BTN_OFF,
                                   activeforeground=WHITE)
        except Exception:
            pass

# endregion


# region UI Detail

    def _stage_bar(self, parent: Frame, item: Dict, compact: bool = True,
                   letters: bool = False, center: bool = False) -> Frame:
        """Build a colored-block stage indicator.

        Stages in order: PSD, PNG, Upscale PSD, Upscale PNG, Refs, Approved, DDS.
        DDS is a post-approval step and is shown in grey when missing.
        Green = present/approved, red = missing/not approved, gray = unknown/not critical.
        When letters=True, a single letter is placed directly under each block.
        """
        bg = parent.cget("bg")
        bar = Frame(parent, bg=bg)
        bar.pack(fill=X, pady=(1, 1))

        ref_path = item.get("ref_folder")
        ref_exists = ref_path is not None and os.path.isdir(ref_path)
        ref_color = GREEN if item["has_refs"] else (BORDER_WARN if ref_exists else RED)

        approved = item.get("approved")
        appr_color = GREEN if approved is True else RED if approved is False else LIGHTER_GRAY
        dds_color = GREEN if item["has_dds"] else LIGHTER_GRAY

        stages = [
            ("P", item["has_psd"]),
            ("N", item["has_png"]),
            ("U", item["has_upscale_psd"]),
            ("u", item["has_upscale_png"]),
            ("R", ref_color),
            ("A", appr_color),
            ("D", dds_color),
        ]

        block_size = 8 if compact else 12
        cell_w = block_size + 4
        cell_h = block_size + 12 if letters else block_size

        container = Frame(bar, bg=bg)
        if center:
            container.pack(anchor="center")
        else:
            container.pack(fill=X)

        full_labels = ["PSD", "PNG", "U-PSD", "U-PNG", "REF", "APR", "DDS"]

        for idx, (letter, val) in enumerate(stages):
            color = val if isinstance(val, str) else (GREEN if val else RED)
            cell = Frame(container, bg=bg, width=cell_w, height=cell_h)
            cell.pack(side=LEFT, padx=1)
            cell.pack_propagate(False)
            dot = Frame(cell, bg=color, width=block_size, height=block_size)
            dot.place(relx=0.5, y=0, anchor="n")
            if letters:
                Label(cell, text=letter, bg=bg, fg=GRID_TEXT,
                      font=("Segoe UI", 6 if compact else 7)).place(relx=0.5, rely=1.0, anchor="s")

        # Detail panel: full labels under the blocks
        if not compact and not letters:
            labels = Frame(bar, bg=bg)
            labels.pack(fill=X, pady=(1, 0))
            for label in full_labels:
                Label(labels, text=label, bg=bg, fg=WHITE,
                      font=("Segoe UI", 6), width=cell_w // 2).pack(side=LEFT, padx=1)

        return bar

    def _build_detail_panel(self) -> None:
        header = Frame(self.detail_panel, bg=DARK_GRAY)
        header.pack(side=TOP, fill=X, pady=(0, 8))
        Label(header, text="Item Details", bg=DARK_GRAY, fg=WHITE,
              font=("Segoe UI", 10, "bold")).pack(side=LEFT, padx=4)
        Button(header, text="✕", command=self._hide_detail_panel,
               bg=ACCENT, fg=WHITE, relief=FLAT, font=("Segoe UI", 9, "bold"),
               activebackground=WHITE, activeforeground=ACCENT,
               padx=6, pady=2).pack(side=RIGHT, padx=4)

        detail_canvas = Canvas(self.detail_panel, bg=DARK_GRAY, highlightthickness=0, bd=0)
        self.detail_frame = Frame(detail_canvas, bg=DARK_GRAY)
        detail_scroll = ttk.Scrollbar(self.detail_panel, orient=VERTICAL, command=detail_canvas.yview,
                                       style="Dark.Vertical.TScrollbar")
        detail_canvas.configure(yscrollcommand=detail_scroll.set)
        detail_canvas.pack(side=LEFT, fill=BOTH, expand=True)
        detail_scroll.pack(side=RIGHT, fill=Y)
        self.detail_window = detail_canvas.create_window(
            (0, 0), window=self.detail_frame, anchor=NW, width=detail_canvas.winfo_width())

        def _on_detail_canvas_configure(event):
            detail_canvas.itemconfig(self.detail_window, width=event.width)

        detail_canvas.bind("<Configure>", _on_detail_canvas_configure)
        self.detail_frame.bind("<Configure>",
                               lambda e: detail_canvas.configure(scrollregion=detail_canvas.bbox("all")))

    def _hide_detail_panel(self) -> None:
        self.detail_panel.pack_forget()
        self.selected_item = None
        self.detail_preview_image = None

    def _show_item_detail(self, item: Dict) -> None:
        self.selected_item = item
        if not self.detail_panel.winfo_ismapped():
            self.detail_panel.pack(side=RIGHT, fill=Y, padx=(4, 0))
            self.root.update_idletasks()

        for w in self.detail_frame.winfo_children():
            w.destroy()

        # Name
        display_name = item.get("display_name") or item["name"]
        Label(self.detail_frame, text=display_name, bg=DARK_GRAY, fg=WHITE,
              font=("Segoe UI", 12, "bold"), wraplength=320).pack(pady=(0, 4))
        Label(self.detail_frame, text=f"File: {item['name']}  |  Group: {item['group']}",
              bg=DARK_GRAY, fg=LIGHTER_GRAY, font=("Segoe UI", 9)).pack(pady=(0, 8))

        # Preview
        source_png = get_item_preview_source(item, prefer_upscale=self.prefer_upscale_preview.get())

        if source_png:
            try:
                with Image.open(source_png) as img:
                    w, h = img.size
                    max_dim = 320
                    if max(w, h) > max_dim:
                        if w >= h:
                            new_w = max_dim
                            new_h = int(h * max_dim / w)
                        else:
                            new_h = max_dim
                            new_w = int(w * max_dim / h)
                        img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
                    self.detail_preview_image = ImageTk.PhotoImage(img.copy())
                    img_label = Label(self.detail_frame, image=self.detail_preview_image,
                                      bg=DARKER_GRAY, cursor="hand2")
                    img_label.pack(pady=(0, 12))
                    psd_to_open = item.get("upscale_psd") or item.get("psd")
                    if psd_to_open:
                        img_label.bind("<Button-1>", lambda e: open_photoshop(psd_to_open))
            except Exception as e:
                logging.error(f"Error loading detail preview: {e}")
                Label(self.detail_frame, text="(preview unavailable)", bg=DARK_GRAY, fg=LIGHTER_GRAY).pack(pady=8)
        else:
            Label(self.detail_frame, text="(no PNG available)", bg=DARK_GRAY, fg=LIGHTER_GRAY).pack(pady=8)

        # Status grid
        status_frame = Frame(self.detail_frame, bg=DARKER_GRAY, relief=FLAT, bd=1)
        status_frame.pack(fill=X, pady=(0, 8), padx=4)

        Label(status_frame, text="Stages", bg=DARKER_GRAY, fg=WHITE,
              font=("Segoe UI", 9, "bold")).pack(anchor=W, padx=8, pady=(4, 2))

        self._stage_bar(status_frame, item, compact=False)

        ref_path = item.get("ref_folder")
        ref_exists = ref_path is not None and os.path.isdir(ref_path)
        if item["has_refs"]:
            Label(status_frame, text=f"  {item['ref_count']} reference image(s) in {ref_path}",
                  bg=DARKER_GRAY, fg=LIGHTER_GRAY, font=("Segoe UI", 8)).pack(anchor=W, padx=16, pady=2)
        elif ref_exists:
            Label(status_frame, text=f"  Ref folder empty: {ref_path}",
                  bg=DARKER_GRAY, fg=BORDER_WARN, font=("Segoe UI", 8)).pack(anchor=W, padx=16, pady=2)
        else:
            Label(status_frame, text=f"  No ref folder: {ref_path or '<none>'}",
                  bg=DARKER_GRAY, fg=RED, font=("Segoe UI", 8)).pack(anchor=W, padx=16, pady=2)

        # Notes & rating
        notes_frame = Frame(self.detail_frame, bg=DARK_GRAY)
        notes_frame.pack(fill=X, pady=(0, 8), padx=4)

        Label(notes_frame, text="Notes & Rating", bg=DARK_GRAY, fg=WHITE,
              font=("Segoe UI", 9, "bold")).pack(anchor=W, padx=4, pady=(4, 2))

        rating_frame = Frame(notes_frame, bg=DARK_GRAY)
        rating_frame.pack(fill=X, padx=4, pady=2)
        Label(rating_frame, text="Rating:", bg=DARK_GRAY, fg=WHITE,
              font=("Segoe UI", 8)).pack(side=LEFT)
        rating_var = StringVar(value=item.get("rating", ""))
        rating_combo = ttk.Combobox(
            rating_frame, textvariable=rating_var,
            values=["", "WIP", "OK", "Good", "Best", "Redo"],
            width=10, state="readonly")
        rating_combo.config(foreground=WHITE)
        rating_combo.pack(side=LEFT, padx=(4, 0))

        latest_frame = Frame(notes_frame, bg=DARK_GRAY)
        latest_frame.pack(fill=X, padx=4, pady=2)
        Label(latest_frame, text="Latest:", bg=DARK_GRAY, fg=WHITE,
              font=("Segoe UI", 8)).pack(side=LEFT)
        latest_var = StringVar(value=item.get("latest", ""))
        Radiobutton(latest_frame, text="Base PSD", variable=latest_var, value="base",
                    bg=DARK_GRAY, fg=WHITE, selectcolor=ACCENT,
                    activebackground=DARK_GRAY, activeforeground=WHITE,
                    font=("Segoe UI", 8)).pack(side=LEFT, padx=(4, 0))
        Radiobutton(latest_frame, text="Upscale PSD", variable=latest_var, value="upscale",
                    bg=DARK_GRAY, fg=WHITE, selectcolor=ACCENT,
                    activebackground=DARK_GRAY, activeforeground=WHITE,
                    font=("Segoe UI", 8)).pack(side=LEFT, padx=(4, 0))

        # Item identity fields (useful when filename != in-game name)
        id_frame = Frame(notes_frame, bg=DARK_GRAY)
        id_frame.pack(fill=X, padx=4, pady=2)
        Label(id_frame, text="Display Name:", bg=DARK_GRAY, fg=WHITE,
              font=("Segoe UI", 8)).pack(side=LEFT)
        display_name_var = StringVar(value=item.get("display_name", ""))
        Entry(id_frame, textvariable=display_name_var, width=20,
              bg="#000000", fg=WHITE, insertbackground=WHITE,
              relief=FLAT, highlightthickness=1, highlightbackground=LIGHTER_GRAY,
              font=("Segoe UI", 8)).pack(side=LEFT, padx=(4, 0))

        type_frame = Frame(notes_frame, bg=DARK_GRAY)
        type_frame.pack(fill=X, padx=4, pady=2)
        Label(type_frame, text="Type:", bg=DARK_GRAY, fg=WHITE,
              font=("Segoe UI", 8)).pack(side=LEFT)
        item_type_var = StringVar(value=item.get("item_type", ""))
        Entry(type_frame, textvariable=item_type_var, width=20,
              bg="#000000", fg=WHITE, insertbackground=WHITE,
              relief=FLAT, highlightthickness=1, highlightbackground=LIGHTER_GRAY,
              font=("Segoe UI", 8)).pack(side=LEFT, padx=(4, 0))

        # PSD modification tracking
        mod_frame = Frame(notes_frame, bg=DARK_GRAY)
        mod_frame.pack(fill=X, padx=4, pady=(4, 2))
        base_changed = item.get("base_psd_changed")
        upscale_changed = item.get("upscale_psd_changed")
        base_fg = RED if base_changed else LIGHTER_GRAY
        upscale_fg = RED if upscale_changed else LIGHTER_GRAY
        Label(mod_frame, text=f"Base PSD modified: {item.get('base_psd_current', 'n/a')}",
              bg=DARK_GRAY, fg=base_fg, font=("Segoe UI", 8)).pack(anchor=W)
        Label(mod_frame, text=f"Upscale PSD modified: {item.get('upscale_psd_current', 'n/a')}",
              bg=DARK_GRAY, fg=upscale_fg, font=("Segoe UI", 8)).pack(anchor=W)

        notes_text = Text(notes_frame, height=4, bg="#000000", fg=WHITE,
                          insertbackground=WHITE, relief=FLAT, font=("Segoe UI", 9),
                          highlightthickness=1, highlightbackground=LIGHTER_GRAY)
        notes_text.pack(fill=X, padx=4, pady=2)
        notes_text.insert("1.0", item.get("notes", ""))

        def _save_notes():
            metadata = read_item_metadata(item)
            metadata["rating"] = rating_var.get()
            metadata["latest"] = latest_var.get()
            metadata["item_type"] = item_type_var.get()
            metadata["display_name"] = display_name_var.get()
            metadata["notes"] = notes_text.get("1.0", "end-1c").strip()
            # Record current PSD mtimes as the documented baseline
            metadata["base_psd_modified"] = item.get("base_psd_current", "")
            metadata["upscale_psd_modified"] = item.get("upscale_psd_current", "")
            write_item_metadata(item, metadata)
            item["base_psd_changed"] = False
            item["upscale_psd_changed"] = False
            self._update_stats()
            self._redraw()

        save_btn = _create_outlined_button(
            notes_frame, "Save Notes", ACCENT, WHITE, _save_notes,
            font=("Segoe UI", 8, "bold"))
        save_btn.pack(anchor=E, padx=4, pady=(2, 4))

        # Folder buttons
        folder_frame = Frame(self.detail_frame, bg=DARK_GRAY)
        folder_frame.pack(fill=X, pady=(0, 8), padx=4)

        Label(folder_frame, text="Folders", bg=DARK_GRAY, fg=WHITE,
              font=("Segoe UI", 9, "bold")).pack(anchor=W, padx=4, pady=(4, 2))

        if item.get("psd") and os.path.isfile(item["psd"]):
            _create_outlined_button(
                folder_frame, "Open Base PSD", BLUE, WHITE,
                lambda p=item["psd"]: open_photoshop(p)
            ).pack(fill=X, padx=4, pady=2)

        if item.get("upscale_psd") and os.path.isfile(item["upscale_psd"]):
            _create_outlined_button(
                folder_frame, "Open Upscale PSD", PURPLE, WHITE,
                lambda p=item["upscale_psd"]: open_photoshop(p)
            ).pack(fill=X, padx=4, pady=2)

        folder_buttons = [
            ("Open Group Folder", item.get("group_path")),
            ("Open Upscale Folder", item.get("upscale_folder")),
            ("Open Ref Folder", item.get("ref_folder")),
        ]
        for label_text, path in folder_buttons:
            if path and os.path.isdir(path):
                _create_outlined_button(
                    folder_frame, label_text, ACCENT, WHITE,
                    lambda p=path: open_explorer(p)
                ).pack(fill=X, padx=4, pady=2)

        _create_outlined_button(
            folder_frame, "Toggle Approved", GREEN, WHITE,
            lambda it=item: self._approve_item(it)
        ).pack(fill=X, padx=4, pady=4)

        _create_outlined_button(
            folder_frame, "Move to Folder", ORANGE, WHITE,
            lambda it=item: self._move_item_to_folder(it)
        ).pack(fill=X, padx=4, pady=4)

# endregion


# region UI Grid

    def _refresh_scan(self) -> None:
        logging.info("Refreshing scan...")
        self.items = discover_items(ITEM_ROOT)
        self._update_stats()
        self._build_group_filters()
        self._redraw()

    def _refresh_previews(self) -> None:
        logging.info("Refreshing all previews...")
        prefer_upscale = self.prefer_upscale_preview.get()
        for item in self.items:
            build_preview_image(item, force_refresh=True, prefer_upscale=prefer_upscale)
        self._redraw()

    def _redraw(self, *args) -> None:
        self._update_stats()
        self._draw_grid()

    def _draw_grid(self) -> None:
        for w in self.frame.winfo_children():
            w.destroy()
        self.thumbs.clear()
        self.item_widgets.clear()
        self.spreadsheet_widgets.clear()

        if self.spreadsheet_mode.get():
            self._draw_spreadsheet()
            return

        visible = self._get_visible_items()
        max_display = 500
        if len(visible) > max_display:
            visible = visible[:max_display]

        self._render_sorted = visible
        self._render_idx = 0
        self._render_last_group = None
        self._render_group_row = 0
        self._render_group_col = 0
        self._render_version += 1
        self._render_batch(self._render_version)

    def _render_batch(self, version_token: int, batch_size: int = 32) -> None:
        if version_token != self._render_version:
            return
        n = len(self._render_sorted)
        end_idx = min(self._render_idx + batch_size, n)

        thumb_w = MAX_ASSET_WIDTH
        item_width = thumb_w + BTN_COL_WIDTH + 14
        try:
            fixed_cols = int(self.columns_var.get())
            cols = max(1, fixed_cols)
        except Exception:
            canvas_width = self.canvas.winfo_width()
            if canvas_width < item_width * 2:
                canvas_width = item_width * 6  # sensible default before first layout
            cols = max(1, canvas_width // item_width)

        for idx in range(self._render_idx, end_idx):
            item = self._render_sorted[idx]
            group = item["group"]

            if group != self._render_last_group:
                self._render_group_row += 1
                self._render_group_col = 0
                self._render_last_group = group
                group_count = sum(1 for i in self._render_sorted if i["group"] == group)
                Label(self.frame, text=f"  {group} {group_count}",
                      bg=DARKER_GRAY, fg=ACCENT, font=("Segoe UI", 8, "bold"),
                      anchor="w").grid(row=self._render_group_row, column=0,
                                       columnspan=cols, sticky="ew", padx=2, pady=(6, 2))
                self._render_group_row += 1

            try:
                preview_path = build_preview_image(item, prefer_upscale=self.prefer_upscale_preview.get())
                with Image.open(preview_path) as img:
                    src_w, src_h = img.size
                    # Use the source image's actual aspect ratio - no forced 16:9.
                    # For small pixel-art icons, always use nearest-neighbor at the
                    # largest integer multiple that fits within the target bounds.
                    if src_w <= thumb_w and src_h <= thumb_w:
                        scale = max(1, thumb_w // max(src_w, src_h))
                        scaled_w = src_w * scale
                        scaled_h = src_h * scale
                        img = img.resize((scaled_w, scaled_h), Image.Resampling.NEAREST)
                    else:
                        img.thumbnail((thumb_w, thumb_w), Image.Resampling.LANCZOS)
                    thumb = ImageTk.PhotoImage(img.copy())
                self.thumbs.append(thumb)

                is_complete = item.get("is_complete", False)
                card = Frame(self.frame, bg=DARKER_GRAY, relief=FLAT, bd=0, padx=0, pady=0)
                card.grid(row=self._render_group_row, column=self._render_group_col,
                          padx=2, pady=2, sticky="n")

                # Card grid: main content in col 0, buttons in col 1
                card.columnconfigure(0, weight=1)
                card.columnconfigure(1, minsize=BTN_COL_WIDTH)

                center_col = Frame(card, bg=DARKER_GRAY)
                center_col.grid(row=0, column=0, rowspan=4, sticky="nsew", padx=1, pady=1)

                btn_col = Frame(card, bg=DARKER_GRAY, width=BTN_COL_WIDTH)
                btn_col.grid(row=0, column=1, rowspan=4, sticky="ns", padx=(2, 0), pady=1)
                btn_col.grid_propagate(False)

                # Image centered in the main column
                show_border = self.show_card_border.get() and not is_complete
                img_container = Frame(center_col, bg=DARKER_GRAY)
                img_container.pack(fill=X, padx=1, pady=1)
                if show_border:
                    img_wrap = Frame(img_container, bg=BORDER_WARN, highlightthickness=0, bd=0)
                    img_wrap.pack(anchor="center")
                    img_label = Label(img_wrap, image=thumb, bg=DARKER_GRAY, cursor="hand2",
                                      highlightthickness=0, bd=0)
                    img_label.pack(padx=1, pady=1)
                else:
                    img_label = Label(img_container, image=thumb, bg=DARKER_GRAY, cursor="hand2",
                                      highlightthickness=0, bd=0)
                    img_label.pack(anchor="center")

                img_label.bind("<Button-1>", lambda e, i=item: self._show_item_detail(i))

                # Action buttons on the side
                small_opts = dict(font=("Segoe UI", 7), width=4, padx=0, pady=0,
                                  fg=GRID_TEXT, relief=FLAT, bd=0,
                                  highlightthickness=0, activebackground=LIGHTER_GRAY,
                                  activeforeground=GRID_TEXT, anchor="w")

                # Folder button
                row = Frame(btn_col, bg=DARKER_GRAY)
                row.pack(anchor="w", pady=1)
                Button(row, text="fold",
                       command=lambda p=item.get("group_path", ""): open_explorer(p),
                       bg=FOLDER_BTN, **small_opts, height=1).pack(side=LEFT)
                dot = Frame(row, bg=GREEN, width=6, height=6)
                dot.pack(side=LEFT, padx=(2, 0), pady=1)
                dot.pack_propagate(False)

                # Ref button
                ref_path = item.get("ref_folder")
                ref_exists = ref_path is not None and os.path.isdir(ref_path)
                ref_color = GREEN if item["has_refs"] else (BORDER_WARN if ref_exists else RED)
                row = Frame(btn_col, bg=DARKER_GRAY)
                row.pack(anchor="w", pady=1)
                Button(row, text="ref",
                       command=lambda p=item.get("ref_folder", ""): open_explorer(p),
                       bg=REF_BTN, **small_opts, height=1).pack(side=LEFT)
                dot = Frame(row, bg=ref_color, width=6, height=6)
                dot.pack(side=LEFT, padx=(2, 0), pady=1)
                dot.pack_propagate(False)

                # Upscale button
                has_upscale = item.get("upscale_psd") and os.path.isfile(item["upscale_psd"])
                row = Frame(btn_col, bg=DARKER_GRAY)
                row.pack(anchor="w", pady=1)
                if has_upscale:
                    Button(row, text="upsc",
                           command=lambda p=item["upscale_psd"]: open_photoshop(p),
                           bg=UPSCALE_BTN, **small_opts, height=1).pack(side=LEFT)
                else:
                    Button(row, text="upsc",
                           command=lambda b=item["psd"], n=item["name"], g=item.get("group_path", ""):
                               self._create_upscale_and_open(b, n, g),
                           bg=UPSCALE_BTN, **small_opts, height=1).pack(side=LEFT)
                dot = Frame(row, bg=GREEN if has_upscale else RED, width=6, height=6)
                dot.pack(side=LEFT, padx=(2, 0), pady=1)
                dot.pack_propagate(False)

                # Name immediately below the image
                if self.show_names.get():
                    name_text = item.get("display_name") or item["name"]
                    if len(name_text) > 22:
                        name_text = name_text[:20] + ".."
                    Label(center_col, text=name_text, bg=DARKER_GRAY, fg=GRID_TEXT,
                          font=("Segoe UI", 7), anchor="center",
                          wraplength=thumb_w + BTN_COL_WIDTH, justify=CENTER
                          ).pack(fill=X, padx=1, pady=(0, 1))

                # Centered stage indicator with letters under each block
                self._stage_bar(center_col, item, compact=True, letters=True, center=True)

                # Centered approve button below the stage bar
                approved = item.get("approved")
                approve_btn = Button(
                    center_col, text="approve",
                    command=lambda it=item: self._approve_item(it),
                    font=("Segoe UI", 7), width=12, padx=0, pady=0,
                    bg=APPROVE_BTN_ON if approved is True else APPROVE_BTN_OFF,
                    fg=GRID_TEXT, relief=FLAT, bd=0,
                    highlightthickness=0, activebackground=LIGHTER_GRAY,
                    activeforeground=GRID_TEXT, anchor="center", height=1)
                approve_btn.pack(anchor="center", pady=(2, 2))

                # Small summary of rating / latest version
                info_bits = []
                if item.get("rating"):
                    info_bits.append(item["rating"])
                if item.get("latest"):
                    info_bits.append(f"latest:{item['latest']}")
                if info_bits:
                    Label(center_col, text=" | ".join(info_bits), bg=DARKER_GRAY, fg=GRID_TEXT,
                          font=("Segoe UI", 6)).pack(anchor="center", pady=(0, 2))

                key = (item["group"], item["name"])
                self.item_widgets[key] = {"approve_btn": approve_btn, "btn_frame": btn_col}

            except Exception as e:
                logging.error(f"Error rendering card for {item.get('name')}: {e}")

            self._render_group_col += 1
            if self._render_group_col >= cols:
                self._render_group_col = 0
                self._render_group_row += 1

        self._render_idx = end_idx
        if self._render_idx < n and version_token == self._render_version:
            self.root.after(1, self._render_batch, version_token, batch_size)

    def _draw_spreadsheet(self) -> None:
        visible = self._get_visible_items()
        max_display = 500
        if len(visible) > max_display:
            visible = visible[:max_display]

        headers = ["Item Name", "Group", "PSD", "PNG", "UP_PSD", "UP_PNG", "Refs", "Approved", "DDS", "Actions"]
        col_widths = [max(len(h), max((len(i["name"]) for i in visible), default=10)),
                      max(len(h), max((len(i["group"]) for i in visible), default=10))]
        col_widths += [8, 8, 8, 8, 8, 10, 8, 10]

        for col_idx, (header, width) in enumerate(zip(headers, col_widths)):
            Label(self.frame, text=header, bg=LIGHTER_GRAY, fg=GRID_TEXT,
                  font=("Segoe UI", 9, "bold"), anchor="w", width=width, padx=4
                  ).grid(row=0, column=col_idx, sticky="w", padx=2, pady=4)

        for row_idx, item in enumerate(visible, start=1):
            row_bg = DARKER_GRAY if row_idx % 2 == 0 else DARK_GRAY
            row_frame = Frame(self.frame, bg=row_bg, relief=FLAT, bd=1)
            row_frame.grid(row=row_idx, column=0, columnspan=len(headers), sticky="ew", padx=2, pady=1)

            Label(row_frame, text=item["name"], bg=row_bg, fg=GRID_TEXT,
                  font=("Segoe UI", 8), anchor="w", width=col_widths[0], padx=4
                  ).grid(row=0, column=0, sticky="w", padx=2)
            Label(row_frame, text=item["group"], bg=row_bg, fg=GRID_TEXT,
                  font=("Segoe UI", 8), anchor="w", width=col_widths[1], padx=4
                  ).grid(row=0, column=1, sticky="w", padx=2)

            status_keys = ["has_psd", "has_png", "has_upscale_psd", "has_upscale_png", "has_refs"]
            for col_idx, key in enumerate(status_keys, start=2):
                val = item.get(key, False)
                block_color = GREEN if val else BORDER_WARN
                Label(row_frame, text="█████", bg=row_bg, fg=block_color,
                      font=("Segoe UI", 10), anchor="center", width=col_widths[col_idx]
                      ).grid(row=0, column=col_idx, sticky="w", padx=2)

            approved = item.get("approved")
            approved_text = "True" if approved is True else "False"
            approved_color = GREEN if approved is True else GRID_TEXT
            approved_label = Label(row_frame, text=approved_text, bg=row_bg, fg=approved_color,
                                   font=("Segoe UI", 8), anchor="center", width=col_widths[7])
            approved_label.grid(row=0, column=7, sticky="w", padx=2)

            # DDS shown as non-critical (grey when missing)
            dds_color = GREEN if item.get("has_dds") else LIGHTER_GRAY
            Label(row_frame, text="█████", bg=row_bg, fg=dds_color,
                  font=("Segoe UI", 10), anchor="center", width=col_widths[8]
                  ).grid(row=0, column=8, sticky="w", padx=2)

            approve_btn = Button(row_frame, text="Approve", command=lambda it=item: self._approve_item(it),
                                 bg=ACCENT, fg=WHITE, font=("Segoe UI", 7), relief=FLAT, padx=6, pady=2,
                                 activebackground=WHITE, activeforeground=ACCENT, width=8)
            approve_btn.grid(row=0, column=9, sticky="w", padx=4)

            key = (item["group"], item["name"])
            self.spreadsheet_widgets[key] = {"approved_label": approved_label, "approve_button": approve_btn}

# endregion


# region MAIN

def main():
    settings = load_settings()
    excluded = set(settings.get("excluded_groups", []))

    logging.info(f"Scanning ITEM_ROOT: {ITEM_ROOT}")
    items = discover_items(ITEM_ROOT)

    root = Tk()
    dashboard = MarvelHeroesDashboard(root, items)
    dashboard.excluded_groups = excluded
    dashboard._build_group_filters()
    dashboard._redraw()

    root.mainloop()

    settings["excluded_groups"] = list(dashboard.excluded_groups)
    settings["photoshop_path"] = PHOTOSHOP_EXE
    save_settings(settings)


if __name__ == "__main__":
    main()

# endregion
