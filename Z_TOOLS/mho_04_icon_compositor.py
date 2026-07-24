"""
MHO_04 ICON COMPOSITOR - Item Icon Compositor with Auto-Deduce for Marvel Heroes Omega

FUNCTIONALITY
  Composites ITEM PNGs onto a dark background with a tinted radial gradient
  behind the item.  Classification is based on filename:
    - names containing "cosmic"                 -> yellow glow (cosmic)
    - names containing "unique"                 -> gold/brown glow
    - names containing "artifact" or "item_art"  -> red-orange glow
    - names containing "craft"/"material"/"reagent" -> teal-blue glow (crafting)
    - names containing "misc"                   -> purple glow (misc)
    - everything else                          -> purple glow (unknown/fallback)

  COSMIC ARTIFACTS:  A toggle "Render Artifacts as Cosmic" overrides all
  artifact-classified items to use the cosmic yellow glow instead of their
  normal red-orange.  This replicates the "cosmically enhanced artifacts"
  appearance from Marvel Heroes Omega.

  The default colors and brightness/falloff values are hardcoded from
  auto-deduce tuned results against ground-truth reference composites:
    - UNIQUE    : glow (0.80, 0.57, 0.23)  brightness 0.40  falloff 1.06
    - ARTIFACT  : glow (0.90, 0.27, 0.15)  brightness 0.70  falloff 1.71
    - CRAFTING  : glow (0.20, 0.60, 0.80)  brightness 0.60  falloff 1.30
    - MISC      : glow (0.60, 0.20, 0.80)  brightness 0.70  falloff 1.50
    - COSMIC    : glow (1.00, 0.85, 0.20)  brightness 0.50  falloff 1.20
    - UNKNOWN   : glow (0.75, 0.10, 0.70)  brightness 0.80  falloff 1.70
    - BACKGROUND: (0.082, 0.035, 0.020)  dark warm brown

  Two reference pairs are used to deduce/tune the composite parameters:
    - one UNIQUE item image and its completed ground-truth composite
    - one ARTIFACT item image and its completed ground-truth composite

  All colors are edited as RGB floats (0.0 - 1.0).  Additional radial-gradient
  parameters (brightness and falloff) can be tweaked in real time while a
  side-by-side preview shows the ground truth vs. the current rendered result.

  AUTO DEDUCE uses scipy differential evolution (or grid search fallback) to
  find optimal background, glow color, brightness, and falloff parameters that
  minimize MSE against both reference ground-truth images simultaneously.

  RADIAL MASK
  The radial gradient mask is generated procedurally by default, eliminating
  the need to ship a separate mask image file.  The mask has a flat white
  center (radius < r_flat), then a power-law falloff to a corner value:
    value = 1.0 - (1.0 - corner_val) * clamp((r - r_flat) / (r_max - r_flat), 0, 1) ^ power
  Parameters (derived from the original Marvel Heroes rarity-bg mask):
    r_flat_frac = 0.425   (flat radius as fraction of half-size)
    corner_val  = 0.28    (mask value at the farthest corner)
    power       = 1.2     (falloff curve exponent)
  An external mask PNG can still be specified to override the procedural mask.

  All inputs, reference paths, and settings are saved as JSON from the last run
  and restored on startup.

  Outputs are written to a "render" subfolder inside the TARGET folder, keeping
  original filenames.

KEY COMPONENTS
  - classify_name()         : filename-based classification (cosmic/unique/artifact/crafting/misc/unknown)
  - generate_radial_mask()  : procedural radial gradient mask (no external file needed)
  - make_composite()        : composites item image onto bg with radial glow mask
  - composite_item()        : loads item image then calls make_composite
  - compare_images()        : MSE/RMSE/accuracy comparison vs ground truth
  - auto_deduce_all()       : scipy optimization of all parameters from ref pairs
  - _grid_search()          : fallback coarse grid search when scipy unavailable
  - process()               : batch-process all PNGs in a target folder
  - main()                  : Tkinter GUI with live preview, color editors, actions

COMPOSITE PIPELINE
  1. Load item PNG (RGBA, 40x40)
  2. Generate or load radial mask (grayscale, 40x40)
  3. Create bg-colored base layer
  4. Apply glow color through mask with brightness/falloff
  5. Alpha-composite item on top
  6. Save to render/ subfolder

CONFIG
  Settings file : mho_04_icon_compositor_config.json

QUICK USAGE
  python Z_TOOLS/mho_04_icon_compositor.py

TOOLSGROUP::PIPELINE
SORTGROUP::2
SORTPRIORITY::2
STATUS::working
VERSION::20260721
"""

# region Imports

import json
import os
import re
import tkinter as tk
from tkinter import messagebox, filedialog

import numpy as np
from PIL import Image, ImageChops, ImageTk

SIZE = 40
PREVIEW_SCALE = 6
CONFIG_FILE = "mho_04_icon_compositor_config.json"

# Tuned defaults from auto-deduce against ground-truth reference composites.
# Values are in 0-1 float RGB (will be scaled to 0-255 at render time).
DEFAULT_BG = (0.0824, 0.0353, 0.0196)
DEFAULT_UNIQUE = (0.8, 0.57, 0.2314)
DEFAULT_UNIQUE_BRIGHT = 0.4
DEFAULT_UNIQUE_FALLOFF = 1.057
DEFAULT_ARTIFACT = (0.9, 0.2745, 0.1451)
DEFAULT_ARTIFACT_BRIGHT = 0.7
DEFAULT_ARTIFACT_FALLOFF = 1.711
DEFAULT_CRAFTING = (0.2, 0.6, 0.8)
DEFAULT_CRAFTING_BRIGHT = 0.6
DEFAULT_CRAFTING_FALLOFF = 1.3
DEFAULT_MISC = (0.6, 0.2, 0.8)
DEFAULT_MISC_BRIGHT = 0.7
DEFAULT_MISC_FALLOFF = 1.5
DEFAULT_COSMIC = (1.0, 0.85, 0.2)
DEFAULT_COSMIC_BRIGHT = 0.5
DEFAULT_COSMIC_FALLOFF = 1.2
DEFAULT_UNKNOWN = (0.75, 0.1, 0.7)
DEFAULT_UNKNOWN_BRIGHT = 0.8
DEFAULT_UNKNOWN_FALLOFF = 1.7

# All classification types in priority order.
CLASS_TYPES = ["cosmic", "unique", "artifact", "crafting", "misc", "unknown"]
# Types that have reference pairs for auto-deduce (the rest are tune-by-eye).
REF_TYPES = ["unique", "artifact"]
# Additional types shown in a single frame (no reference pairs).
ADDL_TYPES = ["crafting", "misc", "cosmic", "unknown"]

# Procedural radial mask parameters (derived from the original MH rarity-bg mask).
MASK_R_FLAT_FRAC = 0.425
MASK_CORNER_VALUE = 0.28
MASK_POWER = 1.2

# endregion


# region Color & Mask

def parse_float_color(text):
    """Parse a hex color or an R/G/B triplet into a 0-255 (R,G,B) tuple.

    Float values in the 0.0-1.0 range are treated as normalized and scaled
    to 0-255.  Integer values are used directly.
    """
    text = text.strip()
    if text.startswith("#"):
        text = text[1:]
    if re.fullmatch(r"[0-9a-fA-F]{6}", text):
        return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))
    parts = [float(p.strip()) for p in text.split(",") if p.strip()]
    if len(parts) != 3:
        raise ValueError(f"Cannot parse color: {text}")
    if max(parts) <= 1.0:
        parts = [p * 255.0 for p in parts]
    return tuple(np.clip(parts, 0.0, 255.0).astype(int).tolist())


def floats_to_string(rgb):
    """Convert a 0-255 (R,G,B) tuple to a normalized float string."""
    return "{:.4f}, {:.4f}, {:.4f}".format(*(c / 255.0 for c in rgb))


def classify_name(name):
    lower = name.lower()
    if "cosmic" in lower:
        return "cosmic"
    if "unique" in lower:
        return "unique"
    if "artifact" in lower or "item_art" in lower:
        return "artifact"
    if "craft" in lower or "material" in lower or "reagent" in lower:
        return "crafting"
    if "misc" in lower:
        return "misc"
    return "unknown"


def load_image(path, size=SIZE):
    img = Image.open(path).convert("RGBA")
    if img.size != (size, size):
        img = img.resize((size, size), Image.LANCZOS)
    return img


def generate_radial_mask(size=SIZE, r_flat_frac=MASK_R_FLAT_FRAC,
                        corner_value=MASK_CORNER_VALUE, power=MASK_POWER):
    """Generate a procedural radial gradient mask.

    The mask is white (1.0) in the center within r_flat, then falls off
    following a power-law curve to corner_value at the farthest corner.
    This replicates the original Marvel Heroes rarity-bg mask without
    requiring an external image file.
    """
    half = (size - 1) / 2.0
    r_flat = r_flat_frac * half
    r_max = half * np.sqrt(2)

    y, x = np.ogrid[:size, :size]
    r = np.sqrt((x - half) ** 2 + (y - half) ** 2)

    t = np.clip((r - r_flat) / (r_max - r_flat), 0.0, 1.0)
    mask_arr = (1.0 - (1.0 - corner_value) * t ** power)

    mask_arr = np.clip(mask_arr * 255.0, 0, 255).astype(np.uint8)
    return Image.fromarray(mask_arr, mode="L")


def load_mask(path=None, size=SIZE):
    """Load a mask image from file, or generate one procedurally.

    If path is None or empty, a procedural radial gradient mask is generated.
    """
    if not path or not os.path.isfile(path):
        return generate_radial_mask(size)
    img = Image.open(path)
    if img.mode != "L":
        img = img.convert("L")
    if img.size != (size, size):
        img = img.resize((size, size), Image.LANCZOS)
    return img


def make_composite(item, mask, bg_color, glow_color, brightness, falloff, size=SIZE):
    """Composite an already-loaded item image with the current parameters."""
    base = Image.new("RGBA", (size, size), bg_color + (255,))

    mask_arr = np.array(mask, dtype=np.float32) / 255.0
    if falloff != 1.0 and falloff > 0.0:
        mask_arr = np.power(mask_arr, falloff)

    alpha_arr = np.clip(mask_arr * brightness, 0.0, 1.0) * 255.0
    glow = Image.new("RGBA", (size, size), glow_color + (0,))
    glow.putalpha(Image.fromarray(alpha_arr.astype(np.uint8)))

    result = Image.alpha_composite(base, glow)
    result = Image.alpha_composite(result, item)
    return result


def composite_item(item_path, mask, bg_color, glow_color, brightness, falloff=1.0, size=SIZE):
    item = load_image(item_path, size)
    return make_composite(item, mask, bg_color, glow_color, brightness, falloff, size)


# endregion


# region Comparison 

def compare_images(rendered, ground_truth_image):
    """Return MSE, RMSE, max pixel difference, and an accuracy percentage."""
    gt = ground_truth_image.convert("RGBA")
    if gt.size != rendered.size:
        gt = gt.resize(rendered.size, Image.LANCZOS)
    diff = ImageChops.difference(rendered, gt)
    diff_arr = np.array(diff, dtype=np.float32)
    mse = float(np.mean(diff_arr ** 2))
    rmse = float(np.sqrt(mse))
    max_diff = float(np.max(diff_arr))
    accuracy = max(0.0, 1.0 - rmse / 255.0) * 100.0
    return mse, rmse, max_diff, accuracy


def _params_to_colors(x):
    """Convert optimization vector to RGB tuples and scalars."""
    bg = tuple(np.clip(x[0:3] * 255.0, 0.0, 255.0).astype(int).tolist())
    unique = tuple(np.clip(x[3:6] * 255.0, 0.0, 255.0).astype(int).tolist())
    artifact = tuple(np.clip(x[6:9] * 255.0, 0.0, 255.0).astype(int).tolist())
    unique_b = float(x[9])
    unique_f = float(x[10])
    artifact_b = float(x[11])
    artifact_f = float(x[12])
    return bg, unique, artifact, unique_b, unique_f, artifact_b, artifact_f


def auto_deduce_all(unique_item_path, unique_gt_path,
                    artifact_item_path, artifact_gt_path,
                    mask, bg_color, unique_color, artifact_color,
                    unique_bright, artifact_bright,
                    unique_falloff, artifact_falloff,
                    deduce_bg=True, log_func=None):
    """Find parameters that best match both reference pairs.

    Uses scipy's differential evolution if available; otherwise a coarse
    grid search.  Returns dict of optimized values.
    """
    unique_item = load_image(unique_item_path)
    unique_gt = load_image(unique_gt_path)
    artifact_item = load_image(artifact_item_path)
    artifact_gt = load_image(artifact_gt_path)

    size = mask.size[0]

    def render_pair(item, bg, glow, b, f):
        return make_composite(item, mask, bg, glow, b, f, size)

    def objective(x):
        bg, unique, artifact, ub, uf, ab, af = _params_to_colors(x)
        ru = render_pair(unique_item, bg, unique, ub, uf)
        ra = render_pair(artifact_item, bg, artifact, ab, af)
        mse_u = compare_images(ru, unique_gt)[0]
        mse_a = compare_images(ra, artifact_gt)[0]
        return mse_u + mse_a

    x0 = np.array([
        *(c / 255.0 for c in bg_color),
        *(c / 255.0 for c in unique_color),
        *(c / 255.0 for c in artifact_color),
        max(unique_bright, 0.01), max(unique_falloff, 0.1),
        max(artifact_bright, 0.01), max(artifact_falloff, 0.1),
    ], dtype=float)

    # Background bounds are only active when deduce_bg is True; otherwise fixed.
    bg_lo = [0.0, 0.0, 0.0] if deduce_bg else x0[0:3].tolist()
    bg_hi = [1.0, 1.0, 1.0] if deduce_bg else x0[0:3].tolist()

    bounds = [
        *zip(bg_lo, bg_hi),
        (0.0, 1.0), (0.0, 1.0), (0.0, 1.0),  # unique glow
        (0.0, 1.0), (0.0, 1.0), (0.0, 1.0),  # artifact glow
        (0.05, 3.0), (0.1, 3.0),             # unique brightness, falloff
        (0.05, 3.0), (0.1, 3.0),             # artifact brightness, falloff
    ]

    try:
        from scipy.optimize import differential_evolution
        if log_func:
            log_func("Running scipy differential evolution...")
        result = differential_evolution(
            objective,
            bounds,
            x0=x0,
            maxiter=100,
            popsize=8,
            polish=True,
            tol=1e-4,
            seed=42,
        )
        x_best = result.x
    except Exception as e:
        if log_func:
            log_func(f"scipy optimize failed ({e}); falling back to grid search.")
        x_best = _grid_search(
            objective, x0, bounds, log_func=log_func
        )

    bg, unique, artifact, ub, uf, ab, af = _params_to_colors(x_best)

    ru = render_pair(unique_item, bg, unique, ub, uf)
    ra = render_pair(artifact_item, bg, artifact, ab, af)
    _, _, _, acc_u = compare_images(ru, unique_gt)
    _, _, _, acc_a = compare_images(ra, artifact_gt)

    return {
        "bg_color": bg,
        "unique_color": unique,
        "unique_bright": ub,
        "unique_falloff": uf,
        "artifact_color": artifact,
        "artifact_bright": ab,
        "artifact_falloff": af,
        "unique_accuracy": acc_u,
        "artifact_accuracy": acc_a,
    }


def _grid_search(objective, x0, bounds, log_func=None):
    """Coarse grid search fallback for environments without scipy."""
    best_x = x0.copy()
    best_score = objective(x0)

    # Refine only brightness/falloff with small color/background perturbations.
    steps = {
        9: np.linspace(0.2, 2.0, 10),   # unique brightness
        10: np.linspace(0.5, 2.0, 8),   # unique falloff
        11: np.linspace(0.2, 2.0, 10),  # artifact brightness
        12: np.linspace(0.5, 2.0, 8),   # artifact falloff
    }

    for idx, values in steps.items():
        for value in values:
            x = best_x.copy()
            x[idx] = value
            score = objective(x)
            if score < best_score:
                best_score = score
                best_x = x

    if log_func:
        log_func("Grid search complete.")
    return best_x


# endregion


# region Batch Process

def process(target_dir, mask_path, bg_color, class_params, cosmic_artifact, log_func):
    if not os.path.isdir(target_dir):
        messagebox.showerror("Error", f"TARGET folder not found:\n{target_dir}")
        return

    mask = load_mask(mask_path)  # procedural fallback if mask_path is empty/missing
    render_dir = os.path.join(target_dir, "render")
    os.makedirs(render_dir, exist_ok=True)

    files = sorted(f for f in os.listdir(target_dir) if f.lower().endswith(".png"))
    if not files:
        log_func("No PNG images found in TARGET folder.")
        return

    for f in files:
        cls = classify_name(f)
        if cosmic_artifact and cls == "artifact":
            cls = "cosmic"
            log_func(f"{f}: artifact -> cosmic (cosmic artifact mode)")
        glow_color, brightness, falloff = class_params.get(cls, class_params["unknown"])

        item_path = os.path.join(target_dir, f)
        rendered = composite_item(item_path, mask, bg_color, glow_color, brightness, falloff)
        out_path = os.path.join(render_dir, f)
        rendered.save(out_path)
        log_func(f"{f} ({cls}) -> rendered")

    log_func(f"\nDone. Rendered {len(files)} image(s) to: {render_dir}")


# endregion


# region UI Helpers

def browse_folder(entry):
    path = filedialog.askdirectory()
    if path:
        entry.delete(0, tk.END)
        entry.insert(0, path)


def browse_file(entry):
    path = filedialog.askopenfilename(filetypes=[("PNG files", "*.png"), ("All files", "*.*")])
    if path:
        entry.delete(0, tk.END)
        entry.insert(0, path)


def load_config(script_dir):
    path = os.path.join(script_dir, CONFIG_FILE)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save_config(script_dir, values):
    path = os.path.join(script_dir, CONFIG_FILE)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(values, f, indent=2)


def set_entry(entry, value):
    entry.delete(0, tk.END)
    entry.insert(0, value)


def add_rgb_row(parent, row, label, entries, default_rgb, config, config_key):
    tk.Label(parent, text=label, bg=parent.cget("bg"), fg="#cccccc").grid(row=row, column=0, sticky=tk.W, pady=1)

    r_entry = tk.Entry(parent, width=8, bg="#000000", fg="#cccccc", insertbackground="#cccccc")
    g_entry = tk.Entry(parent, width=8, bg="#000000", fg="#cccccc", insertbackground="#cccccc")
    b_entry = tk.Entry(parent, width=8, bg="#000000", fg="#cccccc", insertbackground="#cccccc")

    tk.Label(parent, text="R", bg=parent.cget("bg"), fg="#cccccc").grid(row=row, column=1, sticky=tk.E, padx=(8, 2), pady=1)
    r_entry.grid(row=row, column=2, sticky=tk.W, padx=(0, 6), pady=1)
    tk.Label(parent, text="G", bg=parent.cget("bg"), fg="#cccccc").grid(row=row, column=3, sticky=tk.E, padx=(4, 2), pady=1)
    g_entry.grid(row=row, column=4, sticky=tk.W, padx=(0, 6), pady=1)
    tk.Label(parent, text="B", bg=parent.cget("bg"), fg="#cccccc").grid(row=row, column=5, sticky=tk.E, padx=(4, 2), pady=1)
    b_entry.grid(row=row, column=6, sticky=tk.W, pady=1)

    default = floats_to_string(default_rgb)
    saved_parts = [p.strip() for p in config.get(config_key, default).split(",")]
    default_parts = [p.strip() for p in default.split(",")]
    while len(saved_parts) < 3:
        saved_parts.append(default_parts[len(saved_parts)])
    set_entry(r_entry, saved_parts[0])
    set_entry(g_entry, saved_parts[1])
    set_entry(b_entry, saved_parts[2])

    entries.extend([r_entry, g_entry, b_entry])


def add_glow_row(parent, row, label, rgb_entries, default_rgb, default_bright, default_falloff,
                 config, color_key, bright_key, falloff_key):
    """Add a single-row glow color + brightness + falloff editor."""
    tk.Label(parent, text=label, bg=parent.cget("bg"), fg="#cccccc").grid(
        row=row, column=0, sticky=tk.W, pady=1
    )

    inner = tk.Frame(parent, bg=parent.cget("bg"))
    inner.grid(row=row, column=1, sticky=tk.W, pady=1)

    r_entry = tk.Entry(inner, width=8, bg="#000000", fg="#cccccc", insertbackground="#cccccc")
    g_entry = tk.Entry(inner, width=8, bg="#000000", fg="#cccccc", insertbackground="#cccccc")
    b_entry = tk.Entry(inner, width=8, bg="#000000", fg="#cccccc", insertbackground="#cccccc")

    tk.Label(inner, text="R", bg=inner.cget("bg"), fg="#cccccc").pack(side=tk.LEFT, padx=(0, 2))
    r_entry.pack(side=tk.LEFT, padx=(0, 6))
    tk.Label(inner, text="G", bg=inner.cget("bg"), fg="#cccccc").pack(side=tk.LEFT, padx=(0, 2))
    g_entry.pack(side=tk.LEFT, padx=(0, 6))
    tk.Label(inner, text="B", bg=inner.cget("bg"), fg="#cccccc").pack(side=tk.LEFT, padx=(0, 2))
    b_entry.pack(side=tk.LEFT, padx=(0, 12))

    tk.Label(inner, text="Brightness:", bg=inner.cget("bg"), fg="#cccccc").pack(side=tk.LEFT, padx=(0, 2))
    bright_entry = tk.Entry(inner, width=10, bg="#000000", fg="#cccccc", insertbackground="#cccccc")
    bright_entry.pack(side=tk.LEFT, padx=(0, 6))

    tk.Label(inner, text="Falloff:", bg=inner.cget("bg"), fg="#cccccc").pack(side=tk.LEFT, padx=(0, 2))
    falloff_entry = tk.Entry(inner, width=10, bg="#000000", fg="#cccccc", insertbackground="#cccccc")
    falloff_entry.pack(side=tk.LEFT)

    default = floats_to_string(default_rgb)
    saved_parts = [p.strip() for p in config.get(color_key, default).split(",")]
    default_parts = [p.strip() for p in default.split(",")]
    while len(saved_parts) < 3:
        saved_parts.append(default_parts[len(saved_parts)])
    set_entry(r_entry, saved_parts[0])
    set_entry(g_entry, saved_parts[1])
    set_entry(b_entry, saved_parts[2])
    set_entry(bright_entry, str(config.get(bright_key, default_bright)))
    set_entry(falloff_entry, str(config.get(falloff_key, default_falloff)))

    rgb_entries.extend([r_entry, g_entry, b_entry])
    return bright_entry, falloff_entry


def read_rgb_entries(entries):
    text = ", ".join(e.get().strip() for e in entries)
    return parse_float_color(text)


def set_rgb_entries(entries, rgb):
    text = floats_to_string(rgb)
    parts = text.split(",")
    for e, p in zip(entries, parts):
        set_entry(e, p.strip())


# endregion


# region UI Main

def main():
    BG = "#2b2b2b"
    FG = "#cccccc"
    ENTRY_BG = "#000000"
    BTN_BG = "#4a4a4a"

    root = tk.Tk()
    root.title("Item Icon Compositor")
    root.geometry("950x900")
    root.minsize(900, 800)
    root.configure(bg=BG)

    script_dir = os.path.dirname(os.path.abspath(__file__))
    config = load_config(script_dir) or {}

    # Cached reference images for live preview.
    ref_cache = {}

    def get_ref_images(which):
        """Load and cache the item/ground-truth pair for the selected class."""
        paths = {
            "unique": (unique_item_entry.get().strip(), unique_gt_entry.get().strip()),
            "artifact": (artifact_item_entry.get().strip(), artifact_gt_entry.get().strip()),
        }
        item_path, gt_path = paths[which]
        if not item_path or not gt_path:
            return None, None
        key = (item_path, gt_path)
        if key not in ref_cache:
            if not os.path.isfile(item_path) or not os.path.isfile(gt_path):
                return None, None
            ref_cache[key] = (load_image(item_path), load_image(gt_path))
        return ref_cache[key]

    def add_file_row(parent, row, label, config_key, default=""):
        tk.Label(parent, text=label, bg=parent.cget("bg"), fg=FG).grid(row=row, column=0, sticky=tk.W, pady=2)
        entry = tk.Entry(parent, width=65, bg=ENTRY_BG, fg=FG, insertbackground=FG)
        entry.grid(row=row, column=1, sticky=tk.EW, padx=5, pady=2)
        set_entry(entry, config.get(config_key, default))
        tk.Button(parent, text="Browse...", bg=BTN_BG, fg=FG,
                  command=lambda: browse_file(entry)).grid(row=row, column=2, pady=2)
        return entry

    # Render button at the very top.
    render_frame = tk.Frame(root, bg=BG)
    render_frame.pack(fill=tk.X, padx=10, pady=(3, 0))

    tk.Button(
        render_frame, text="RENDER", bg=BTN_BG, fg="#ffffff", font=("Segoe UI", 12, "bold"),
        padx=20, pady=3,
        command=lambda: do_render()
    ).pack(side=tk.LEFT, padx=5)

    # Top frame: TARGET, mask, background color.
    top_frame = tk.Frame(root, padx=10, pady=3, bg=BG)
    top_frame.pack(fill=tk.X)

    tk.Label(top_frame, text="TARGET Folder:", bg=BG, fg=FG).grid(row=0, column=0, sticky=tk.W, pady=2)
    target_entry = tk.Entry(top_frame, width=65, bg=ENTRY_BG, fg=FG, insertbackground=FG)
    target_entry.grid(row=0, column=1, sticky=tk.EW, padx=5, pady=2)
    set_entry(target_entry, config.get("target_dir", os.path.join(script_dir, "..", "ITEM")))
    tk.Button(top_frame, text="Browse...", bg=BTN_BG, fg=FG,
              command=lambda: browse_folder(target_entry)).grid(row=0, column=2, pady=2)

    tk.Label(top_frame, text="Radial Mask (optional):", bg=BG, fg=FG).grid(row=1, column=0, sticky=tk.W, pady=2)
    mask_entry = tk.Entry(top_frame, width=65, bg=ENTRY_BG, fg=FG, insertbackground=FG)
    mask_entry.grid(row=1, column=1, sticky=tk.EW, padx=5, pady=2)
    set_entry(mask_entry, config.get("mask_path", ""))
    tk.Button(top_frame, text="Browse...", bg=BTN_BG, fg=FG,
              command=lambda: browse_file(mask_entry)).grid(row=1, column=2, pady=2)
    tk.Label(top_frame, text="(leave blank for procedural mask)", bg=BG, fg="#666666",
             font=("Segoe UI", 8)).grid(row=1, column=3, sticky=tk.W, padx=(4, 0))

    bg_rgb_entries = []
    add_rgb_row(top_frame, 2, "Background Color:", bg_rgb_entries, DEFAULT_BG, config, "bg_color")

    top_frame.columnconfigure(1, weight=1)

    # Reference pair frames.
    unique_frame = tk.LabelFrame(root, text="Unique Reference Pair", bg=BG, fg=FG, padx=10, pady=3)
    unique_frame.pack(fill=tk.X, padx=10, pady=3)

    unique_item_entry = add_file_row(unique_frame, 0, "Unique Item Image:", "unique_item_path")
    unique_gt_entry = add_file_row(unique_frame, 1, "Unique Ground Truth:", "unique_gt_path")

    unique_rgb_entries = []
    unique_bright_entry, unique_falloff_entry = add_glow_row(
        unique_frame, 2, "Glow Color:", unique_rgb_entries,
        DEFAULT_UNIQUE, DEFAULT_UNIQUE_BRIGHT, DEFAULT_UNIQUE_FALLOFF,
        config, "unique_color", "unique_bright", "unique_falloff"
    )

    unique_frame.columnconfigure(1, weight=1)

    artifact_frame = tk.LabelFrame(root, text="Artifact Reference Pair", bg=BG, fg=FG, padx=10, pady=3)
    artifact_frame.pack(fill=tk.X, padx=10, pady=3)

    artifact_item_entry = add_file_row(artifact_frame, 0, "Artifact Item Image:", "artifact_item_path")
    artifact_gt_entry = add_file_row(artifact_frame, 1, "Artifact Ground Truth:", "artifact_gt_path")

    artifact_rgb_entries = []
    artifact_bright_entry, artifact_falloff_entry = add_glow_row(
        artifact_frame, 2, "Glow Color:", artifact_rgb_entries,
        DEFAULT_ARTIFACT, DEFAULT_ARTIFACT_BRIGHT, DEFAULT_ARTIFACT_FALLOFF,
        config, "artifact_color", "artifact_bright", "artifact_falloff"
    )

    artifact_frame.columnconfigure(1, weight=1)

    # Additional coloration frame (crafting, misc, cosmic, unknown).
    addl_frame = tk.LabelFrame(root, text="Additional Coloration", bg=BG, fg=FG, padx=10, pady=3)
    addl_frame.pack(fill=tk.X, padx=10, pady=3)

    addl_rgb_entries = {}
    addl_bright_entries = {}
    addl_falloff_entries = {}

    _addl_defs = [
        ("crafting", "Crafting (teal-blue):", DEFAULT_CRAFTING, DEFAULT_CRAFTING_BRIGHT, DEFAULT_CRAFTING_FALLOFF),
        ("misc", "Misc (purple):", DEFAULT_MISC, DEFAULT_MISC_BRIGHT, DEFAULT_MISC_FALLOFF),
        ("cosmic", "Cosmic (yellow):", DEFAULT_COSMIC, DEFAULT_COSMIC_BRIGHT, DEFAULT_COSMIC_FALLOFF),
        ("unknown", "Unknown (fallback):", DEFAULT_UNKNOWN, DEFAULT_UNKNOWN_BRIGHT, DEFAULT_UNKNOWN_FALLOFF),
    ]

    for _row, (_cls, _label, _color, _bright, _falloff) in enumerate(_addl_defs):
        addl_rgb_entries[_cls] = []
        addl_bright_entries[_cls], addl_falloff_entries[_cls] = add_glow_row(
            addl_frame, _row, _label, addl_rgb_entries[_cls],
            _color, _bright, _falloff,
            config, f"{_cls}_color", f"{_cls}_bright", f"{_cls}_falloff"
        )

    addl_frame.columnconfigure(1, weight=1)

    # Action controls above preview.
    action_frame = tk.Frame(root, bg=BG)
    action_frame.pack(pady=3)

    deduce_bg_var = tk.BooleanVar(value=config.get("deduce_bg", True))
    tk.Checkbutton(action_frame, text="Deduce background", variable=deduce_bg_var,
                   bg=BG, fg=FG, selectcolor=BG, activebackground=BG, activeforeground=FG).pack(side=tk.LEFT, padx=5)

    cosmic_artifact_var = tk.BooleanVar(value=config.get("cosmic_artifact", False))
    tk.Checkbutton(action_frame, text="Render Artifacts as Cosmic", variable=cosmic_artifact_var,
                   bg=BG, fg=FG, selectcolor=BG, activebackground=BG, activeforeground=FG).pack(side=tk.LEFT, padx=5)

    tk.Button(
        action_frame, text="AUTO DEDUCE", bg=BTN_BG, fg="#ffffff", font=("Segoe UI", 10, "bold"),
        padx=15, pady=3,
        command=lambda: do_auto_deduce()
    ).pack(side=tk.LEFT, padx=5)

    # Preview panel.
    preview_frame = tk.LabelFrame(root, text="Live Preview", bg=BG, fg=FG, padx=10, pady=3)
    preview_frame.pack(fill=tk.X, padx=10, pady=3)

    preview_canvas = tk.Canvas(preview_frame, bg=BG, highlightthickness=0)
    preview_canvas.grid(row=0, column=0, pady=3)

    preview_label = tk.Label(preview_frame, text="GT", bg=BG, fg=FG)
    preview_label.grid(row=1, column=0)

    log_text = tk.Text(root, wrap=tk.WORD, height=12, bg=ENTRY_BG, fg=FG, insertbackground=FG)
    log_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 3))

    def log(msg):
        log_text.insert(tk.END, msg + "\n")
        log_text.see(tk.END)
        root.update_idletasks()

    def collect_config():
        data = {
            "target_dir": target_entry.get().strip(),
            "mask_path": mask_entry.get().strip(),
            "bg_color": ", ".join(e.get().strip() for e in bg_rgb_entries),
            "unique_item_path": unique_item_entry.get().strip(),
            "unique_gt_path": unique_gt_entry.get().strip(),
            "unique_color": ", ".join(e.get().strip() for e in unique_rgb_entries),
            "unique_bright": unique_bright_entry.get().strip(),
            "unique_falloff": unique_falloff_entry.get().strip(),
            "artifact_item_path": artifact_item_entry.get().strip(),
            "artifact_gt_path": artifact_gt_entry.get().strip(),
            "artifact_color": ", ".join(e.get().strip() for e in artifact_rgb_entries),
            "artifact_bright": artifact_bright_entry.get().strip(),
            "artifact_falloff": artifact_falloff_entry.get().strip(),
            "deduce_bg": deduce_bg_var.get(),
            "cosmic_artifact": cosmic_artifact_var.get(),
        }
        for cls in ADDL_TYPES:
            data[f"{cls}_color"] = ", ".join(e.get().strip() for e in addl_rgb_entries[cls])
            data[f"{cls}_bright"] = addl_bright_entries[cls].get().strip()
            data[f"{cls}_falloff"] = addl_falloff_entries[cls].get().strip()
        return data

    def get_params():
        p = {
            "target_dir": target_entry.get().strip(),
            "mask_path": mask_entry.get().strip(),
            "bg_color": read_rgb_entries(bg_rgb_entries),
            "unique_color": read_rgb_entries(unique_rgb_entries),
            "unique_bright": float(unique_bright_entry.get().strip()),
            "unique_falloff": float(unique_falloff_entry.get().strip()),
            "artifact_color": read_rgb_entries(artifact_rgb_entries),
            "artifact_bright": float(artifact_bright_entry.get().strip()),
            "artifact_falloff": float(artifact_falloff_entry.get().strip()),
        }
        for cls in ADDL_TYPES:
            p[f"{cls}_color"] = read_rgb_entries(addl_rgb_entries[cls])
            p[f"{cls}_bright"] = float(addl_bright_entries[cls].get().strip())
            p[f"{cls}_falloff"] = float(addl_falloff_entries[cls].get().strip())
        return p

    def update_preview(*_):
        u_item, u_gt = get_ref_images("unique")
        a_item, a_gt = get_ref_images("artifact")
        if u_item is None or u_gt is None or a_item is None or a_gt is None:
            preview_canvas.delete("all")
            preview_label.config(text="Select valid reference pairs to preview")
            return

        try:
            p = get_params()
        except Exception as e:
            preview_label.config(text=f"Invalid parameter: {e}")
            return

        try:
            mask = load_mask(p["mask_path"])
        except Exception as e:
            preview_label.config(text=f"Mask error: {e}")
            return

        u_rendered = make_composite(
            u_item, mask, p["bg_color"], p["unique_color"],
            p["unique_bright"], p["unique_falloff"]
        )
        a_rendered = make_composite(
            a_item, mask, p["bg_color"], p["artifact_color"],
            p["artifact_bright"], p["artifact_falloff"]
        )

        _, _, _, u_acc = compare_images(u_rendered, u_gt)
        _, _, _, a_acc = compare_images(a_rendered, a_gt)

        # Build side-by-side image: artifact GT, artifact preview, unique GT, unique preview.
        w = SIZE * PREVIEW_SCALE
        h = SIZE * PREVIEW_SCALE
        gap = 20
        combined = Image.new("RGBA", (w * 4 + gap * 3, h), (43, 43, 43, 255))

        a_gt_scaled = a_gt.resize((w, h), Image.NEAREST)
        a_ren_scaled = a_rendered.resize((w, h), Image.NEAREST)
        u_gt_scaled = u_gt.resize((w, h), Image.NEAREST)
        u_ren_scaled = u_rendered.resize((w, h), Image.NEAREST)

        combined.paste(a_gt_scaled, (0, 0))
        combined.paste(a_ren_scaled, (w + gap, 0))
        combined.paste(u_gt_scaled, ((w + gap) * 2, 0))
        combined.paste(u_ren_scaled, ((w + gap) * 3, 0))

        photo = ImageTk.PhotoImage(combined)

        preview_canvas.config(width=combined.width, height=combined.height)
        preview_canvas.delete("all")
        preview_canvas.create_image(0, 0, anchor=tk.NW, image=photo)
        preview_canvas.image = photo
        preview_label.config(
            text=f"Artifact GT | Artifact Preview | Unique GT | Unique Preview    "
                 f"(artifact {a_acc:.2f}% | unique {u_acc:.2f}%)"
        )

    def do_render():
        log_text.delete("1.0", tk.END)
        try:
            p = get_params()
            save_config(script_dir, collect_config())
            class_params = {
                "unique": (p["unique_color"], p["unique_bright"], p["unique_falloff"]),
                "artifact": (p["artifact_color"], p["artifact_bright"], p["artifact_falloff"]),
            }
            for cls in ADDL_TYPES:
                class_params[cls] = (p[f"{cls}_color"], p[f"{cls}_bright"], p[f"{cls}_falloff"])
            process(
                p["target_dir"],
                p["mask_path"],
                p["bg_color"],
                class_params,
                cosmic_artifact_var.get(),
                log,
            )
        except Exception as e:
            messagebox.showerror("Error", str(e))
            log(f"ERROR: {e}")

    def do_auto_deduce():
        log_text.delete("1.0", tk.END)
        try:
            p = get_params()
            if not all(os.path.isfile(path) for path in (
                unique_item_entry.get().strip(), unique_gt_entry.get().strip(),
                artifact_item_entry.get().strip(), artifact_gt_entry.get().strip()
            )):
                raise ValueError("All four reference images must exist for auto-deduce.")

            save_config(script_dir, collect_config())
            ref_cache.clear()

            mask = load_mask(p["mask_path"])
            log("Auto-deducing parameters for both reference pairs...")

            result = auto_deduce_all(
                unique_item_entry.get().strip(), unique_gt_entry.get().strip(),
                artifact_item_entry.get().strip(), artifact_gt_entry.get().strip(),
                mask,
                p["bg_color"], p["unique_color"], p["artifact_color"],
                p["unique_bright"], p["artifact_bright"],
                p["unique_falloff"], p["artifact_falloff"],
                deduce_bg=deduce_bg_var.get(),
                log_func=log,
            )

            set_rgb_entries(bg_rgb_entries, result["bg_color"])
            set_rgb_entries(unique_rgb_entries, result["unique_color"])
            set_rgb_entries(artifact_rgb_entries, result["artifact_color"])
            set_entry(unique_bright_entry, f"{result['unique_bright']:.3f}")
            set_entry(unique_falloff_entry, f"{result['unique_falloff']:.3f}")
            set_entry(artifact_bright_entry, f"{result['artifact_bright']:.3f}")
            set_entry(artifact_falloff_entry, f"{result['artifact_falloff']:.3f}")

            log(f"Background color: {floats_to_string(result['bg_color'])}")
            log(f"UNIQUE accuracy: {result['unique_accuracy']:.2f}%  "
                f"brightness={result['unique_bright']:.3f}  falloff={result['unique_falloff']:.3f}")
            log(f"ARTIFACT accuracy: {result['artifact_accuracy']:.2f}%  "
                f"brightness={result['artifact_bright']:.3f}  falloff={result['artifact_falloff']:.3f}")

            update_preview()
        except Exception as e:
            messagebox.showerror("Error", str(e))
            log(f"ERROR: {e}")

    # Bind parameter changes to preview updates.
    all_entries = [
        mask_entry,
        unique_item_entry, unique_gt_entry,
        artifact_item_entry, artifact_gt_entry,
        unique_bright_entry, unique_falloff_entry,
        artifact_bright_entry, artifact_falloff_entry,
        *bg_rgb_entries, *unique_rgb_entries, *artifact_rgb_entries,
    ]
    for cls in ADDL_TYPES:
        all_entries.extend(addl_rgb_entries[cls])
        all_entries.append(addl_bright_entries[cls])
        all_entries.append(addl_falloff_entries[cls])
    for entry in all_entries:
        entry.bind("<FocusOut>", update_preview)
        entry.bind("<Return>", update_preview)

    root.after(100, update_preview)
    root.mainloop()


if __name__ == "__main__":
    main()

# endregion
