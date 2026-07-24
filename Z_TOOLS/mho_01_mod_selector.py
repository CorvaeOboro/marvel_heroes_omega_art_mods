"""
MHO_01 TEXTUREPACK SELECTOR - Client-Facing Mod Selector for Marvel Heroes Omega

FUNCTIONALITY
  A client-facing tool for assembling custom texture packs from pre-existing
  DDS files.  No texconv or external dependencies required , the tool reads
  .dds files that have already been generated and packages them into a zip
  with a merged manifest for MHModManager.

  Scans the sibling ITEM/ directory and groups related mod folders by base
  name, detecting variant suffixes (_2x, _borderless).  Presents a scrollable
  card-based UI where each group shows:
    - A group-level checkbox to enable/disable the entire set
    - Variant radio buttons (base, 2x, borderless) to pick which folder variant
    - An expandable per-file list with individual checkboxes for each DDS file
      (so users can exclude specific items, e.g. keep an amulet unaltered)

  Example workflow:
    1. Enable ITEM_DarkRunes (base variant, all 44 DDS files checked)
    2. Enable ITEM_ArtifactsArmor_2x (2x variant, uncheck item_amulet.dds)
    3. Enter preset name "MyCustomPack" (or leave blank to use author name)
    4. Click GENERATE TEXTUREPACK ZIP
    5. Output: 00_MODS/ITEM_MyCustomPack_20260721203000.zip containing:
       - All selected .dds files (flat, no subfolders)
       - manifest.json listing every included texture replacement for use with ModManager

  The zip filename uses the format:
    ITEM_<preset_or_author>_<YYYYMMDDHHMMSS>.zip

KEY COMPONENTS
  - discover_mod_groups()         : groups ITEM/ folders by base name + variant
  - list_dds_files()              : enumerates .dds files in a mod folder
  - build_merged_manifest()       : writes a single manifest from all selections
  - generate_texturepack_zip()    : copies selected DDS + manifest into a zip
  - _GroupCard                    : per-group widget + file checkbox state
  - MarvelModSelector             : main Tkinter application class
    - _build_ui / _build_settings  : UI layout (settings, card grid, log)
    - _populate_cards              : renders mod group cards with previews
    - _toggle_file_list            : expand/collapse per-file checkboxes
    - preset management            : load/save/import/export JSON presets

VARIANT SUFFIXES ( folder-level )
    _borderless   -> "No Outline" variant
    _nooutline    -> "No Outline" variant (alternate spelling)
    _2x           -> "80px" variant (80x80 resolution)
    (no suffix)   -> "40px" base variant (40x40 resolution)

COMPOSITE IMAGES
  COMPOSITE_IMAGES dict maps group base names to their 00_ prefix
  composite PNG (e.g. ITEM_ArtifactsArmor -> 00_ITEM_ArtifactsArmor.png).
  Used as the card preview thumbnail when available.

DEFAULT PRESET
  On first load (no saved config), DEFAULT_PRESET is applied:
    ITEM_ArtifactsArmor  -> 2x (80px)
    ITEM_DarkRunes       -> base (40px) since these are in crafting recipes we DONT scale them 2x
    ITEM_UruReForged     -> 2x (80px)

PRESETS
  The current selections and settings are auto-saved to
  mho_01_mod_selector_config.json on every export.
  Use Load/Save Preset to store named configurations in
  mho_01_mod_selector_presets/ for sharing or for a git-hosted site.

  Preset JSON format (selections include per-file exclusions):

    {
      "name": "My Preset",
      "author": "Example",
      "output_folder": "D:/GAMES/MarvelHeroesOmega/00_MH_Art_Mods/00_MODS",
      "selections": {
        "ITEM_DarkRunes": {
          "enabled": true,
          "variant": "base",
          "excluded_files": []
        },
        "ITEM_ArtifactsArmor": {
          "enabled": true,
          "variant": "2x",
          "excluded_files": ["item_amulet.dds"]
        }
      }
    }

CONFIG
  Auto-saved settings : mho_01_mod_selector_config.json
  Named presets       : mho_01_mod_selector_presets/

QUICK USAGE
  python Z_TOOLS/mho_01_mod_selector.py

TOOLSGROUP::INSTALL
SORTGROUP::1
SORTPRIORITY::1
STATUS::working
VERSION::20260721
"""

# region Imports

import json
import os
import zipfile
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk
from datetime import datetime
from PIL import Image, ImageTk


_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = "mho_01_mod_selector_config.json"
PRESETS_DIR = "mho_01_mod_selector_presets"

# Variant suffixes recognised at the MOD FOLDER level.
# Order matters: longer/more-specific suffixes should come first.
VARIANT_SUFFIXES = [
    ("borderless", "_borderless"),
    ("nooutline", "_nooutline"),
    ("2x", "_2x"),
]

# Friendly labels for variants.
VARIANT_LABELS = {
    "base": "40px",
    "2x": "80px",
    "borderless": "No Outline",
    "nooutline": "No Outline",
}

# Composite preview images (00_ prefix PNGs) found in each mod folder.
# Used as the card preview thumbnail for the group.
COMPOSITE_IMAGES = {
    "ITEM_ArtifactsArmor": "00_ITEM_ArtifactsArmor.png",
    "ITEM_DarkRunes": "00_ITEM_DarkRunes.png",
    "ITEM_UruReForged": "00_ITEM_UruReForged.png",
}

# Hardcoded priority ordering for known mod folders.
# Higher number = higher up on screen. Known released sets start at 100.
# Unknown folders get priority 0 and sort alphabetically below known ones.
KNOWN_MODS = {
    "ITEM_DarkRunes":       300,
    "ITEM_ArtifactsArmor":  200,
    "ITEM_UruReForged":     100,
    "ITEM_Artifacts2":       90,
    "ITEM_EquipmentOutlined": 80,
    "ITEM_Third":            70,
    "Item_Gift":             60,
}

# Hardcoded default preset: which groups are enabled and which variant.
# Applied on first load when no saved config exists.
DEFAULT_PRESET = {
    "author": "Example",
    "preset_name": "",
    "output_folder": "",
    "selections": {
        "ITEM_ArtifactsArmor": {"enabled": True, "variant": "2x", "excluded_files": []},
        "ITEM_DarkRunes": {"enabled": True, "variant": "base", "excluded_files": []},
        "ITEM_UruReForged": {"enabled": True, "variant": "2x", "excluded_files": []},
    },
}

# UI color palette
BG = "#121212"
FG = "#cccccc"
ENTRY_BG = "#000000"
ENTRY_FG = "#ffffff"
BTN_BG = "#2a2a2a"
BTN_FG = "#ffffff"
ACCENT_BG = "#3a5a7a"
ACCENT_ACTIVE = "#4a6a8a"
WARN_BG = "#6b4a4a"
CARD_BG = "#0e0e0e"
TITLE_FG = "#5a8ab8"

# endregion


# region Utility 

def get_item_dir():
    """Return the absolute path to the sibling ITEM/ directory."""
    return os.path.normpath(os.path.join(_SCRIPT_DIR, "..", "ITEM"))


def load_config():
    path = os.path.join(_SCRIPT_DIR, CONFIG_FILE)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(values):
    path = os.path.join(_SCRIPT_DIR, CONFIG_FILE)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(values, f, indent=2)
    except Exception as exc:
        print(f"Failed to save config: {exc}")


def ensure_presets_dir():
    path = os.path.join(_SCRIPT_DIR, PRESETS_DIR)
    os.makedirs(path, exist_ok=True)
    return path


def discover_mod_groups(item_dir):
    """Discover ITEM folders and group base/variant siblings.

    Returns a dict: {base_name: {"base": folder_name, "2x": folder_name, ...}}
    """
    groups = {}
    if not os.path.isdir(item_dir):
        return groups
    for folder in sorted(os.listdir(item_dir)):
        folder_path = os.path.join(item_dir, folder)
        if not os.path.isdir(folder_path):
            continue
        base = folder
        variant = "base"
        for var_key, suffix in VARIANT_SUFFIXES:
            if base.endswith(suffix):
                base = base[: -len(suffix)]
                variant = var_key
                break
        groups.setdefault(base, {})[variant] = folder
    return groups


def find_preview_image(folder_path, base_name=None):
    """Pick the best preview image inside a mod folder.

    Checks COMPOSITE_IMAGES first for a hardcoded 00_ composite,
    then falls back to any 00_ PNG, then any PNG in the folder.
    """
    if not os.path.isdir(folder_path):
        return None
    # Check hardcoded composite image first
    if base_name and base_name in COMPOSITE_IMAGES:
        composite_name = COMPOSITE_IMAGES[base_name]
        composite_path = os.path.join(folder_path, composite_name)
        if os.path.isfile(composite_path):
            return composite_path
    # Fall back to any 00_ PNG in the folder
    candidates = []
    for name in os.listdir(folder_path):
        if name.lower().startswith("00_") and name.lower().endswith(".png"):
            candidates.append(os.path.join(folder_path, name))
    if not candidates:
        for name in sorted(os.listdir(folder_path)):
            if name.lower().endswith(".png"):
                candidates.append(os.path.join(folder_path, name))
    return candidates[0] if candidates else None


def list_dds_files(folder_path):
    """Return sorted list of .dds filenames in a folder (excluding 00_ prefix)."""
    if not os.path.isdir(folder_path):
        return []
    return sorted(
        f for f in os.listdir(folder_path)
        if f.lower().endswith(".dds") and not f.lower().startswith("00_")
    )


def count_dds_files(folder_path):
    """Return how many DDS files exist in the folder root."""
    return len(list_dds_files(folder_path))


def build_merged_manifest(selections, item_dir, cards, author, pack_name):
    """Build a single merged manifest from all selected groups/files.

    Returns (manifest_dict, file_list) where file_list is a list of
    (source_path, dds_filename) tuples for copying into the zip.
    """
    replacements = []
    file_list = []
    seen_textures = set()

    for base, sel in selections.items():
        if not sel.get("enabled"):
            continue
        variant = sel.get("variant", "base")
        card = cards.get(base)
        if not card or variant not in card.variants:
            continue
        folder = card.variants[variant]
        folder_path = os.path.join(item_dir, folder)
        excluded = set(sel.get("excluded_files", []))

        for dds_name in list_dds_files(folder_path):
            if dds_name in excluded:
                continue
            texture_name = os.path.splitext(dds_name)[0]
            if texture_name in seen_textures:
                continue
            seen_textures.add(texture_name)
            replacements.append({
                "TextureName": texture_name,
                "DdsFileName": dds_name,
            })
            file_list.append((os.path.join(folder_path, dds_name), dds_name))

    version = datetime.now().strftime("%Y%m%d")
    manifest = {
        "Name": pack_name,
        "Author": author,
        "Version": version,
        "Replacements": replacements,
        "AchievementReplacements": [],
        "StoreReplacements": [],
        "Languages": [],
        "UpkReplacements": [],
        "AudioPacks": [],
        "HasTextures": True,
        "TextureReplacementCount": len(replacements),
    }
    return manifest, file_list

# endregion


# region Group Data 

class _GroupCard:
    """Holder for the widgets and state of a single mod-group card."""

    def __init__(self, base_name, variants, item_dir):
        self.base_name = base_name
        self.variants = dict(variants)  # {variant_key: folder_name}
        self.item_dir = item_dir
        self.enabled_var = tk.BooleanVar(value=False)
        self.variant_var = tk.StringVar(value="base")
        self.preview_path = find_preview_image(
            os.path.join(item_dir, variants.get("base", list(variants.values())[0])),
            base_name=base_name,
        )
        self.photo = None
        # Per-file checkbox state: {dds_filename: BooleanVar}
        self.file_vars = {}
        self.expanded = False
        self.file_frame = None  # Frame for expandable file list

    def get_current_folder(self):
        """Return the folder name for the currently selected variant."""
        variant = self.variant_var.get()
        return self.variants.get(variant, list(self.variants.values())[0])

    def get_current_folder_path(self):
        return os.path.join(self.item_dir, self.get_current_folder())

    def refresh_file_vars(self):
        """Populate file_vars for the currently selected variant's DDS files."""
        folder_path = self.get_current_folder_path()
        dds_files = list_dds_files(folder_path)
        # Keep existing vars for files still present; create new ones for new files.
        new_vars = {}
        for dds_name in dds_files:
            if dds_name in self.file_vars:
                new_vars[dds_name] = self.file_vars[dds_name]
            else:
                new_vars[dds_name] = tk.BooleanVar(value=True)
        self.file_vars = new_vars

    def get_excluded_files(self):
        """Return list of DDS filenames that are unchecked."""
        return [name for name, var in self.file_vars.items() if not var.get()]

    def set_excluded_files(self, excluded):
        """Uncheck the given files, check all others."""
        excluded_set = set(excluded)
        for name, var in self.file_vars.items():
            var.set(name not in excluded_set)

# endregion


# region UI Base

class MarvelModSelector:
    def __init__(self, master):
        self.master = master
        self.master.title("Marvel Heroes Omega Texture Pack Selector")
        self.master.configure(bg=BG)
        self.master.geometry("1200x850")
        self.master.minsize(900, 600)

        self.config = load_config()
        self.item_dir = get_item_dir()
        self.groups = discover_mod_groups(self.item_dir)
        self.cards = {}  # base_name -> _GroupCard
        for base, variants in self.groups.items():
            self.cards[base] = _GroupCard(base, variants, self.item_dir)

        self._build_styles()
        self._build_ui()
        self._load_initial_state()

    def _build_styles(self):
        style = ttk.Style(self.master)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=FG)
        style.configure("TLabelframe", background=BG, bordercolor="#1a1a1a", relief="solid", borderwidth=1)
        style.configure("TLabelframe.Label", background=BG, foreground="#666666")
        style.configure(
            "BlackDropdown.TCombobox",
            fieldbackground=ENTRY_BG,
            background=ENTRY_BG,
            foreground=FG,
            arrowcolor=FG,
        )
        style.map(
            "BlackDropdown.TCombobox",
            fieldbackground=[("readonly", ENTRY_BG), ("active", ENTRY_BG)],
            selectbackground=[("readonly", "#333333")],
            selectforeground=[("readonly", FG)],
        )
        style.configure(
            "Vertical.TScrollbar",
            background=ACCENT_BG,
            troughcolor="#000000",
            bordercolor="#000000",
            arrowcolor="#cccccc",
            relief="flat",
            borderwidth=0,
        )

    def _build_ui(self):
        # ---------- Title bar ----------
        tk.Label(
            self.master,
            text="Marvel Heroes Omega Texture Pack Selector",
            bg=BG, fg=TITLE_FG,
            font=("Segoe UI", 16, "bold"),
        ).pack(fill=tk.X, padx=10, pady=(0, 4))

        # ---------- Output folder bar (top, full width) ----------
        output_bar = tk.Frame(self.master, bg=BG)
        output_bar.pack(fill=tk.X, padx=10, pady=(0, 5))

        # Zip preview to the left of Output label
        self.zip_preview_label = tk.Label(
            output_bar, text="", bg=BG, fg="#4ade80", font=("Segoe UI", 9, "bold"),
            anchor=tk.W,
        )
        self.zip_preview_label.pack(side=tk.LEFT, padx=(0, 8))

        tk.Label(output_bar, text="Output:", bg=BG, fg=FG, font=("Segoe UI", 9, "bold")).pack(
            side=tk.LEFT, padx=(0, 4)
        )
        self.output_entry = tk.Entry(
            output_bar, bg=ENTRY_BG, fg=ENTRY_FG, insertbackground=ENTRY_FG,
        )
        self.output_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        default_output = os.path.normpath(os.path.join(_SCRIPT_DIR, "..", "00_MODS"))
        self.output_entry.insert(0, self.config.get("output_folder", default_output))
        tk.Button(
            output_bar,
            text="Browse",
            bg=ACCENT_BG,
            fg=BTN_FG,
            command=self._browse_output_folder,
        ).pack(side=tk.LEFT, padx=2)

        # ---------- Main paned area ----------
        paned = tk.PanedWindow(self.master, orient=tk.HORIZONTAL, bg=BG)
        paned.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        # Left: settings (holds all controls + status)
        settings_frame = tk.LabelFrame(
            paned, text="Controls", bg=BG, fg=FG, padx=4, pady=4
        )
        self._build_settings(settings_frame)
        paned.add(settings_frame, width=180, minsize=160)

        # Right: scrollable mod group list
        right_frame = tk.Frame(paned, bg=BG)
        paned.add(right_frame, width=800, minsize=400)

        self.canvas = tk.Canvas(right_frame, bg=BG, highlightthickness=0)
        vscroll = ttk.Scrollbar(
            right_frame, orient="vertical", command=self.canvas.yview
        )
        self.canvas.configure(yscrollcommand=vscroll.set)
        vscroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.cards_frame = tk.Frame(self.canvas, bg=BG)
        window = self.canvas.create_window((0, 0), window=self.cards_frame, anchor="nw")

        def _on_canvas_configure(event):
            self.canvas.itemconfig(window, width=event.width)

        self.canvas.bind("<Configure>", _on_canvas_configure)
        self.cards_frame.bind(
            "<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )
        self._bind_mousewheel(self.canvas)

        self._populate_cards()

    def _build_settings(self, parent):
        cfg = self.config
        parent.columnconfigure(0, weight=1)

        row = 0

        # ---------- Generate ZIP (top, outlined text) ----------
        gen_canvas = self._create_outlined_button(
            parent, "GENERATE ZIP",
            font=("Segoe UI", 11, "bold"),
            fg=BTN_FG, outline="black", bg=ACCENT_BG,
            command=self.generate_texturepack_zip,
        )
        gen_canvas.grid(row=row, column=0, sticky=tk.EW, pady=(0, 4))

        def _resize_gen(event):
            gen_canvas.config(width=event.width)

        gen_canvas.bind("<Configure>", _resize_gen)
        row += 1

        # ---------- Pack Info ----------
        pack_frame = tk.LabelFrame(parent, text="Pack Info", bg=BG, fg=FG, padx=4, pady=3)
        pack_frame.grid(row=row, column=0, sticky=tk.EW, pady=(0, 2))
        pack_frame.columnconfigure(1, weight=1)
        row += 1

        tk.Label(pack_frame, text="Prefix:", bg=BG, fg=FG).grid(
            row=0, column=0, sticky=tk.W, pady=1
        )
        self.prefix_entry = tk.Entry(
            pack_frame, bg=ENTRY_BG, fg=ENTRY_FG, insertbackground=ENTRY_FG
        )
        self.prefix_entry.grid(row=0, column=1, sticky=tk.EW, pady=1, padx=3)
        self.prefix_entry.insert(0, cfg.get("zip_prefix", "ITEM"))

        tk.Label(pack_frame, text="PackName:", bg=BG, fg=FG).grid(
            row=1, column=0, sticky=tk.W, pady=1
        )
        self.preset_name_entry = tk.Entry(
            pack_frame, bg=ENTRY_BG, fg=ENTRY_FG, insertbackground=ENTRY_FG
        )
        self.preset_name_entry.grid(row=1, column=1, sticky=tk.EW, pady=1, padx=3)
        self.preset_name_entry.insert(0, cfg.get("preset_name", ""))

        tk.Label(pack_frame, text="Author:", bg=BG, fg=FG).grid(
            row=2, column=0, sticky=tk.W, pady=1
        )
        self.author_entry = tk.Entry(
            pack_frame, bg=ENTRY_BG, fg=ENTRY_FG, insertbackground=ENTRY_FG
        )
        self.author_entry.grid(row=2, column=1, sticky=tk.EW, pady=1, padx=3)
        self.author_entry.insert(0, cfg.get("author", "Example"))

        # ---------- Preset Management (2x2 grid) ----------
        preset_frame = tk.LabelFrame(parent, text="Presets", bg=BG, fg=FG, padx=4, pady=3)
        preset_frame.grid(row=row, column=0, sticky=tk.EW, pady=(0, 2))
        row += 1

        tk.Button(
            preset_frame, text="Load", bg=BTN_BG, fg=BTN_FG,
            command=self.load_preset,
        ).grid(row=0, column=0, sticky=tk.EW, padx=2, pady=1)
        tk.Button(
            preset_frame, text="Save", bg=BTN_BG, fg=BTN_FG,
            command=self.save_preset,
        ).grid(row=0, column=1, sticky=tk.EW, padx=2, pady=1)
        tk.Button(
            preset_frame, text="Import", bg=BTN_BG, fg=BTN_FG,
            command=self.import_preset,
        ).grid(row=1, column=0, sticky=tk.EW, padx=2, pady=1)
        tk.Button(
            preset_frame, text="Export", bg=BTN_BG, fg=BTN_FG,
            command=self.export_preset,
        ).grid(row=1, column=1, sticky=tk.EW, padx=2, pady=1)

        # ---------- Selection ----------
        sel_frame = tk.LabelFrame(parent, text="Selection", bg=BG, fg=FG, padx=4, pady=3)
        sel_frame.grid(row=row, column=0, sticky=tk.EW, pady=(0, 2))
        row += 1

        tk.Button(
            sel_frame, text="Select All", bg="#3a6b3a", fg=BTN_FG,
            command=self.select_all,
        ).grid(row=0, column=0, sticky=tk.EW, padx=2, pady=1)
        tk.Button(
            sel_frame, text="Select None", bg="#5a3a6b", fg=BTN_FG,
            command=self.select_none,
        ).grid(row=0, column=1, sticky=tk.EW, padx=2, pady=1)

        # ---------- Status ----------
        self.status_label = tk.Label(
            parent, text="Ready", bg=BG, fg="#4ade80", anchor=tk.W,
            font=("Segoe UI", 8), wraplength=160, justify=tk.LEFT,
        )
        self.status_label.grid(row=row, column=0, sticky=tk.EW, pady=(2, 0))
        row += 1

        # ---------- Pack Summary ----------
        summary_frame = tk.LabelFrame(parent, text="Summary", bg=BG, fg=FG, padx=4, pady=3)
        summary_frame.grid(row=row, column=0, sticky=tk.EW, pady=(2, 0))
        row += 1

        self.pack_summary_text = tk.Text(
            summary_frame, bg=BG, fg=FG, font=("Segoe UI", 8),
            wrap=tk.WORD, height=10, relief="flat", bd=0,
            highlightthickness=0, padx=2, pady=2,
        )
        self.pack_summary_text.pack(fill=tk.X, expand=True)
        self.pack_summary_text.config(state="disabled")

        # Bind zip preview updates
        self.author_entry.bind("<KeyRelease>", self._update_zip_preview)
        self.preset_name_entry.bind("<KeyRelease>", self._update_zip_preview)
        self.prefix_entry.bind("<KeyRelease>", self._update_zip_preview)
        self._update_zip_preview()

    def _create_big_checkbox(self, parent, variable, size=36, bg=CARD_BG):
        """Create a custom canvas-based checkbox that renders much larger
        than the system default. Drives the given tk.BooleanVar so it
        behaves identically to a standard Checkbutton.

        Draws a square outline; when checked, fills with accent blue and
        draws a white checkmark. Clicking toggles the variable.
        """
        box = size
        pad = 4
        canvas = tk.Canvas(parent, width=box + pad * 2, height=box + pad * 2,
                           bg=bg, highlightthickness=0, bd=0)

        x0, y0 = pad, pad
        x1, y1 = pad + box, pad + box

        def _redraw():
            canvas.delete("all")
            checked = variable.get()
            if checked:
                canvas.create_rectangle(x0, y0, x1, y1,
                                        fill=ACCENT_BG, outline=ACCENT_ACTIVE,
                                        width=2)
                cx = (x0 + x1) // 2
                cy = (y0 + y1) // 2
                s = box // 3
                canvas.create_line(cx - s, cy, cx - s // 2, cy + s,
                                   fill="#ffffff", width=3, capstyle="round")
                canvas.create_line(cx - s // 2, cy + s, cx + s, cy - s,
                                   fill="#ffffff", width=3, capstyle="round")
            else:
                canvas.create_rectangle(x0, y0, x1, y1,
                                        fill=ENTRY_BG, outline="#555555",
                                        width=2)

        def _on_click(event=None):
            variable.set(not variable.get())
            _redraw()

        def _on_var_changed(*_):
            if canvas.winfo_exists():
                _redraw()

        canvas.bind("<Button-1>", _on_click)
        canvas.config(cursor="hand2")
        variable.trace_add("write", _on_var_changed)
        _redraw()
        return canvas

    def _create_outlined_button(self, parent, text, font=("Segoe UI", 11, "bold"),
                                fg="#ffffff", outline="black", bg=ACCENT_BG,
                                command=None, width=None):
        """Create a canvas-based button with 8-direction black outline text.

        Draws the text 8 times in the outline color at 1px offsets, then once
        in the foreground color on top. The canvas fills with bg and acts as
        a clickable button.
        """
        font_obj = tkfont.Font(font=font)
        text_w = font_obj.measure(text)
        text_h = font_obj.metrics("linespace")

        pad_x = 12
        pad_y = 8
        canvas_w = (width if width else text_w + pad_x * 2)
        canvas_h = text_h + pad_y * 2

        canvas = tk.Canvas(parent, width=canvas_w, height=canvas_h,
                           bg=bg, highlightthickness=0, bd=2, relief="raised")

        cx = canvas_w // 2
        cy = canvas_h // 2

        # Draw 8-direction outline
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                canvas.create_text(cx + dx, cy + dy, text=text,
                                   font=font, fill=outline, anchor="center")

        # Draw foreground text on top
        canvas.create_text(cx, cy, text=text, font=font, fill=fg, anchor="center")

        def _on_press(event):
            canvas.config(relief="sunken")

        def _on_release(event):
            canvas.config(relief="raised")
            if command:
                command()

        def _on_enter(event):
            canvas.config(cursor="hand2")

        canvas.bind("<ButtonPress-1>", _on_press)
        canvas.bind("<ButtonRelease-1>", _on_release)
        canvas.bind("<Enter>", _on_enter)
        return canvas

    def _create_outlined_label(self, parent, text, font=("Segoe UI", 12, "bold"),
                               fg="#ffffff", outline="black", bg=CARD_BG):
        """Create a label with 8-direction black outline behind white text.

        Uses a Canvas with create_text to draw the text 8 times in black at
        1px offsets, then once in the foreground color on top.
        Returns (canvas, fg_text_id) so the foreground color can be updated.
        """
        font_obj = tkfont.Font(font=font)
        text_w = font_obj.measure(text)
        text_h = font_obj.metrics("linespace")

        canvas_w = text_w + 4
        canvas_h = text_h + 4

        canvas = tk.Canvas(parent, width=canvas_w, height=canvas_h,
                           bg=bg, highlightthickness=0, bd=0)

        cx = canvas_w // 2
        cy = canvas_h // 2

        # Draw 8-direction outline
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                canvas.create_text(cx + dx, cy + dy, text=text,
                                   font=font, fill=outline, anchor="center")

        # Draw foreground text on top
        fg_text_id = canvas.create_text(cx, cy, text=text, font=font, fill=fg, anchor="center")
        return canvas, fg_text_id

    def _populate_cards(self):
        # Remove all traces from card variables before destroying widgets
        for card in self.cards.values():
            for var in (card.enabled_var, card.variant_var):
                for mode in ("write", "read", "unset"):
                    try:
                        var.trace_remove(mode, "")
                    except (tk.TclError, ValueError):
                        pass

        for widget in self.cards_frame.winfo_children():
            widget.destroy()

        # Sort by known priority (higher = top), unknown folders sort
        # alphabetically below known ones at priority 0.
        sorted_bases = sorted(
            self.cards.keys(),
            key=lambda b: (-KNOWN_MODS.get(b, 0), b),
        )

        self.cards_frame.columnconfigure(0, weight=1)
        self.cards_frame.columnconfigure(1, weight=1)

        row = 0
        col = 0
        for base in sorted_bases:
            card = self.cards[base]
            variants = list(card.variants.keys())
            card.refresh_file_vars()

            card_frame = tk.Frame(self.cards_frame, bg=CARD_BG, bd=1, relief="solid")
            card_frame.grid(row=row, column=col, sticky=tk.EW, pady=1, padx=1)
            card_frame.columnconfigure(1, weight=1)

            # --- Row 0: Enable checkbox (left) + title + variant toggles (right) ---
            top_frame = tk.Frame(card_frame, bg=CARD_BG)
            top_frame.grid(row=0, column=0, columnspan=2, sticky=tk.EW, padx=2, pady=(1, 0))
            top_frame.columnconfigure(1, weight=1)

            # Enable checkbox on the left
            enable_cb = self._create_big_checkbox(
                top_frame, card.enabled_var, size=24, bg=CARD_BG,
            )
            enable_cb.grid(row=0, column=0, sticky=tk.W, padx=(2, 4))

            # Outlined title (color set dynamically below)
            title_canvas, title_text_id = self._create_outlined_label(
                top_frame, base, font=("Segoe UI", 11, "bold"),
                fg="#999999", outline="black", bg=CARD_BG,
            )
            title_canvas.grid(row=0, column=1, sticky=tk.W, padx=2)

            # DDS count
            dds_count = len(card.file_vars)
            dds_lbl = tk.Label(
                top_frame,
                text=f"({dds_count} DDS)",
                bg=CARD_BG,
                fg="#666666",
                font=("Segoe UI", 8),
            )
            dds_lbl.grid(row=0, column=2, sticky=tk.W, padx=(4, 4))

            # Variant radios on the right side of top row
            radio_frame = tk.Frame(top_frame, bg=CARD_BG)
            radio_frame.grid(row=0, column=3, sticky=tk.E, padx=(4, 2))

            radio_widgets = {}
            for var_key in variants:
                label = VARIANT_LABELS.get(var_key, var_key)
                folder = card.variants[var_key]
                rb = tk.Radiobutton(
                    radio_frame,
                    text=label,
                    variable=card.variant_var,
                    value=var_key,
                    bg=CARD_BG,
                    fg="#888888",
                    selectcolor=ENTRY_BG,
                    activebackground=CARD_BG,
                    activeforeground="#cccccc",
                    font=("Segoe UI", 11, "bold"),
                    padx=6,
                    pady=2,
                    indicatoron=False,
                    relief="flat",
                )
                rb.pack(side=tk.LEFT, padx=(0, 4))
                rb.bind(
                    "<Enter>",
                    lambda e, f=folder: self._set_status(f"Folder: {f}"),
                )
                rb.bind("<Leave>", lambda e: self._set_status("Ready"))
                radio_widgets[var_key] = rb

            # Expand/collapse button
            expand_btn = tk.Button(
                top_frame,
                text="[+]",
                bg=BTN_BG,
                fg=FG,
                relief="flat",
                font=("Segoe UI", 8),
                width=3,
            )
            expand_btn.grid(row=0, column=4, sticky=tk.E, padx=(2, 2))

            # --- Dynamic color updater ---
            COLOR_DISABLED = "#777777"
            COLOR_ENABLED = "#6aaa6a"
            COLOR_VAR_OFF = "#888888"
            COLOR_VAR_ON = "#7ccc7c"

            def _update_title_color(*_):
                try:
                    if not title_canvas.winfo_exists():
                        return
                    if not dds_lbl.winfo_exists():
                        return
                    if card.enabled_var.get():
                        title_canvas.itemconfig(title_text_id, fill=COLOR_ENABLED)
                        dds_lbl.config(fg="#888888")
                    else:
                        title_canvas.itemconfig(title_text_id, fill=COLOR_DISABLED)
                        dds_lbl.config(fg="#555555")
                except Exception as exc:
                    print(f"_update_title_color error: {exc}")

            def _update_radio_colors(*_):
                current = card.variant_var.get()
                for vk, rb in radio_widgets.items():
                    if not rb.winfo_exists():
                        continue
                    if vk == current:
                        rb.config(fg=COLOR_VAR_ON, selectcolor=CARD_BG)
                    else:
                        rb.config(fg=COLOR_VAR_OFF, selectcolor=CARD_BG)

            card.enabled_var.trace_add("write", _update_title_color)
            card.variant_var.trace_add("write", _update_radio_colors)
            card.enabled_var.trace_add("write", lambda *_: self._update_pack_summary())
            card.variant_var.trace_add("write", lambda *_: self._update_pack_summary())
            _update_title_color()
            _update_radio_colors()

            # --- Row 1: Wide preview image (max height 300px) ---
            preview = tk.Label(
                card_frame,
                bg=CARD_BG,
                relief="flat",
            )
            preview.grid(row=1, column=0, columnspan=2, sticky=tk.EW, padx=2, pady=1)
            if card.preview_path and os.path.isfile(card.preview_path):
                try:
                    img = Image.open(card.preview_path)
                    w, h = img.size
                    target_w = 600
                    max_h = 300
                    scale = min(target_w / w, max_h / h, 1.0)
                    new_w = int(w * scale)
                    new_h = int(h * scale)
                    if new_w != w or new_h != h:
                        img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
                    card.photo = ImageTk.PhotoImage(img)
                    preview.config(image=card.photo)
                except Exception as exc:
                    preview.config(text="(preview failed)")
                    print(f"Preview failed for {base}: {exc}")
            else:
                preview.config(text="(no preview)")

            # Variant change triggers file list refresh
            card.variant_var.trace_add("write", lambda *_: self._on_variant_changed(card))

            # Expandable per-file frame (hidden by default)
            file_frame = tk.Frame(card_frame, bg=CARD_BG)
            card.file_frame = file_frame

            # Populate file checkboxes
            self._populate_file_list(card)

            # Toggle expand/collapse
            def _toggle():
                self._toggle_file_list(card, expand_btn)

            expand_btn.config(command=_toggle)

            col += 1
            if col > 1:
                col = 0
                row += 1

        self._update_pack_summary()

    def _populate_file_list(self, card):
        """Populate the per-file checkbox list inside card.file_frame."""
        for widget in card.file_frame.winfo_children():
            widget.destroy()

        dds_files = sorted(card.file_vars.keys())
        if not dds_files:
            tk.Label(
                card.file_frame,
                text="(no DDS files found)",
                bg=CARD_BG, fg="#666666", font=("Segoe UI", 8),
            ).grid(row=0, column=0, columnspan=3, sticky=tk.W, padx=20, pady=2)
            return

        # Select all / none for this group
        btn_frame = tk.Frame(card.file_frame, bg=CARD_BG)
        btn_frame.grid(row=0, column=0, columnspan=3, sticky=tk.W, padx=20, pady=(4, 2))
        tk.Button(
            btn_frame, text="All", bg=BTN_BG, fg=FG, relief="flat",
            font=("Segoe UI", 8), width=4,
            command=lambda: [v.set(True) for v in card.file_vars.values()],
        ).pack(side=tk.LEFT, padx=(0, 4))
        tk.Button(
            btn_frame, text="None", bg=BTN_BG, fg=FG, relief="flat",
            font=("Segoe UI", 8), width=4,
            command=lambda: [v.set(False) for v in card.file_vars.values()],
        ).pack(side=tk.LEFT)

        # File checkboxes in columns
        col = 0
        for idx, dds_name in enumerate(dds_files):
            row_idx = 1 + idx // 3
            col_idx = idx % 3
            tk.Checkbutton(
                card.file_frame,
                text=dds_name,
                variable=card.file_vars[dds_name],
                bg=CARD_BG,
                fg=FG,
                selectcolor=ENTRY_BG,
                font=("Segoe UI", 8),
                anchor=tk.W,
            ).grid(row=row_idx, column=col_idx, sticky=tk.W, padx=(20, 10), pady=1)

    def _toggle_file_list(self, card, expand_btn):
        if card.expanded:
            card.file_frame.grid_forget()
            card.expanded = False
            expand_btn.config(text="[+]")
        else:
            card.file_frame.grid(row=2, column=0, columnspan=2, sticky=tk.EW, pady=(0, 2))
            card.expanded = True
            expand_btn.config(text="[-]")

    def _on_variant_changed(self, card):
        """When variant radio changes, refresh the file list for that variant."""
        card.refresh_file_vars()
        self._populate_file_list(card)
        if card.expanded:
            card.file_frame.grid(row=2, column=0, columnspan=2, sticky=tk.EW, pady=(0, 2))

# endregion


# region UI Helpers

    def _bind_mousewheel(self, canvas):
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            return "break"

        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _on_mousewheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

    def _set_status(self, text):
        self.status_label.config(text=text)

    def _log(self, msg):
        self._set_status(msg)

    def _browse_output_folder(self):
        path = filedialog.askdirectory(title="Select Output Folder")
        if path:
            self.output_entry.delete(0, tk.END)
            self.output_entry.insert(0, path)
            self._update_zip_preview()

    def _update_zip_preview(self, *_):
        prefix = self.prefix_entry.get().strip() or "ITEM"
        name = self.preset_name_entry.get().strip()
        if not name:
            name = self.author_entry.get().strip() or "Example"
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        preview = f"{prefix}_{name}_{timestamp}.zip"
        self.zip_preview_label.config(text=preview)
        self._update_pack_summary()

    def _update_pack_summary(self, *_):
        """Update the pack summary text widget with current selections."""
        lines = []
        total_dds = 0
        enabled_count = 0
        for base in sorted(self.cards.keys()):
            card = self.cards[base]
            if not card.enabled_var.get():
                continue
            enabled_count += 1
            variant = card.variant_var.get()
            label = VARIANT_LABELS.get(variant, variant)
            folder = card.variants.get(variant, "?")
            dds_files = list_dds_files(os.path.join(self.item_dir, folder))
            excluded = set(card.get_excluded_files())
            dds_count = len([f for f in dds_files if f not in excluded])
            total_dds += dds_count
            lines.append(f"  {base.replace('ITEM_', '')} {label} - {dds_count}")

        self.pack_summary_text.config(state="normal")
        self.pack_summary_text.delete("1.0", tk.END)
        self.pack_summary_text.tag_configure("green", foreground="#6aaa6a")
        if lines:
            for line in lines:
                parts = line.rsplit(" - ", 1)
                if len(parts) == 2:
                    self.pack_summary_text.insert(tk.END, f"  {parts[0]} - ")
                    self.pack_summary_text.insert(tk.END, parts[1], "green")
                    self.pack_summary_text.insert(tk.END, "\n")
                else:
                    self.pack_summary_text.insert(tk.END, line + "\n")
            self.pack_summary_text.insert(tk.END, f"\nTotal: {enabled_count} groups, ")
            self.pack_summary_text.insert(tk.END, str(total_dds), "green")
            self.pack_summary_text.insert(tk.END, " files")
        else:
            self.pack_summary_text.insert("1.0", "(no groups selected)")
        self.pack_summary_text.config(state="disabled")

    def select_all(self):
        for card in self.cards.values():
            card.enabled_var.set(True)

    def select_none(self):
        for card in self.cards.values():
            card.enabled_var.set(False)

    def _get_selections(self):
        """Return {base_name: {enabled, variant, excluded_files}} for all cards."""
        selections = {}
        for base, card in self.cards.items():
            selections[base] = {
                "enabled": card.enabled_var.get(),
                "variant": card.variant_var.get(),
                "excluded_files": card.get_excluded_files(),
            }
        return selections

# endregion


# region UI Presets

    def _build_preset_data(self, name="Untitled"):
        return {
            "name": name,
            "author": self.author_entry.get().strip(),
            "preset_name": self.preset_name_entry.get().strip(),
            "zip_prefix": self.prefix_entry.get().strip(),
            "output_folder": self.output_entry.get().strip(),
            "selections": self._get_selections(),
        }

    def _apply_preset_data(self, data):
        self.author_entry.delete(0, tk.END)
        self.author_entry.insert(0, data.get("author", "Example"))
        self.preset_name_entry.delete(0, tk.END)
        self.preset_name_entry.insert(0, data.get("preset_name", ""))
        self.prefix_entry.delete(0, tk.END)
        self.prefix_entry.insert(0, data.get("zip_prefix", "ITEM"))
        self.output_entry.delete(0, tk.END)
        output_folder = data.get("output_folder", "")
        if not output_folder:
            output_folder = os.path.normpath(os.path.join(_SCRIPT_DIR, "..", "00_MODS"))
        self.output_entry.insert(0, output_folder)

        selections = data.get("selections", {})
        for base, card in self.cards.items():
            info = selections.get(base, {})
            if not isinstance(info, dict):
                info = {"enabled": bool(info), "variant": "base"}
            card.enabled_var.set(info.get("enabled", False))
            variant = info.get("variant", "base")
            if variant in card.variants:
                card.variant_var.set(variant)
            else:
                card.variant_var.set("base")
            card.refresh_file_vars()
            card.set_excluded_files(info.get("excluded_files", []))

        self._populate_cards()
        self._update_zip_preview()

    def load_preset(self):
        presets_dir = ensure_presets_dir()
        path = filedialog.askopenfilename(
            title="Load Preset",
            initialdir=presets_dir,
            filetypes=[("JSON Presets", "*.json"), ("All Files", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._apply_preset_data(data)
            self._set_status(f"Loaded preset: {os.path.basename(path)}")
        except Exception as exc:
            messagebox.showerror("Preset Error", f"Failed to load preset:\n{exc}")

    def save_preset(self):
        presets_dir = ensure_presets_dir()
        path = filedialog.asksaveasfilename(
            title="Save Preset",
            initialdir=presets_dir,
            defaultextension=".json",
            filetypes=[("JSON Presets", "*.json")],
        )
        if not path:
            return
        try:
            data = self._build_preset_data(name=os.path.splitext(os.path.basename(path))[0])
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            self._set_status(f"Saved preset: {os.path.basename(path)}")
        except Exception as exc:
            messagebox.showerror("Preset Error", f"Failed to save preset:\n{exc}")

    def import_preset(self):
        path = filedialog.askopenfilename(
            title="Import Preset",
            filetypes=[("JSON Presets", "*.json"), ("All Files", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._apply_preset_data(data)
            self._set_status(f"Imported preset: {os.path.basename(path)}")
        except Exception as exc:
            messagebox.showerror("Preset Error", f"Failed to import preset:\n{exc}")

    def export_preset(self):
        path = filedialog.asksaveasfilename(
            title="Export Preset for Sharing",
            defaultextension=".json",
            filetypes=[("JSON Presets", "*.json")],
        )
        if not path:
            return
        try:
            data = self._build_preset_data(name=os.path.splitext(os.path.basename(path))[0])
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            self._set_status(f"Exported preset: {os.path.basename(path)}")
        except Exception as exc:
            messagebox.showerror("Preset Error", f"Failed to export preset:\n{exc}")

    def _save_config(self):
        self.config = self._build_preset_data(name="__config__")
        self.config.pop("name", None)
        save_config(self.config)

# endregion


# region UI Export

    def generate_texturepack_zip(self):
        """Build a zip from selected DDS files + merged manifest."""
        selections = self._get_selections()
        enabled = {base: sel for base, sel in selections.items() if sel["enabled"]}
        if not enabled:
            messagebox.showwarning("No Selection", "Select at least one mod group to package.")
            return

        author = self.author_entry.get().strip() or "Example"
        preset_name = self.preset_name_entry.get().strip()
        if not preset_name:
            preset_name = author
        output_folder = self.output_entry.get().strip()
        if not output_folder:
            messagebox.showerror("Missing Input", "Provide an output folder.")
            return

        prefix = self.prefix_entry.get().strip() or "ITEM"

        # Build merged manifest and file list
        manifest, file_list = build_merged_manifest(
            selections, self.item_dir, self.cards, author, f"{prefix}_{preset_name}"
        )

        if not file_list:
            messagebox.showwarning(
                "No Files",
                "No DDS files found in the selected groups/variants.\n"
                "Check that your ITEM folders contain .dds files.",
            )
            return

        # Generate zip filename: <prefix>_<name>_<YYYYMMDDHHMMSS>.zip
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        zip_name = f"{prefix}_{preset_name}_{timestamp}.zip"
        zip_path = os.path.join(output_folder, zip_name)

        self._set_status(f"Building: {zip_name} ({len(enabled)} groups, {len(file_list)} files)")

        try:
            os.makedirs(output_folder, exist_ok=True)
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                manifest_json = json.dumps(manifest, indent=2)
                zf.writestr("manifest.json", manifest_json)

                for src_path, dds_name in file_list:
                    zf.write(src_path, dds_name)

            self._save_config()
            self._set_status(f"Created: {zip_name} ({len(file_list)} DDS + manifest)")

        except Exception as exc:
            messagebox.showerror("Zip Error", f"Failed to create zip:\n{exc}")
            self._set_status(f"ERROR: {exc}")

    def _load_initial_state(self):
        if self.config:
            self._apply_preset_data(self.config)
        else:
            self._apply_preset_data(DEFAULT_PRESET)

# endregion


# region MAIN

def main():
    root = tk.Tk()
    app = MarvelModSelector(root)
    root.mainloop()


if __name__ == "__main__":
    main()

# endregion
