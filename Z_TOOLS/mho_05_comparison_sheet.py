"""
MHO_05 COMPARISON SHEET - Image Comparison Compositor for Marvel Heroes Omega

FUNCTIONALITY
  A UI for comparing and compositing original Marvel Heroes Omega art assets
  with altered versions.  Images are matched by filename stem and arranged on
  a draggable canvas in rows for visualization.  Generates a single composite
  PNG showing BEFORE / AFTER (and optional ALTERNATE BEFORE) pairs stacked
  vertically, with rows of pairs arranged horizontally.

  The tool searches a TARGET folder for AFTER images (BMP or PNG), then finds
  matching BEFORE images from either:
    - a custom BEFORE folder (if provided), OR
    - a subfolder named "backup" or "original" in the TARGET folder
  An optional ALTERNATE BEFORE folder can provide a third image per match.
  Images are matched by filename stem.

  A draggable canvas allows arranging images into rows.  Row detection is
  automatic based on y-position overlap.  Arrangements can be saved/loaded
  as composite.json files in the TARGET folder.

  Triplet states per item:
    - IN_COMPOSITION  : included in the generated composite
    - ON_CANVAS_ONLY  : visible on canvas but excluded from composite
    - HIDDEN          : not shown anywhere

  Output options:
    - Matte onto black background
    - Add outer padding (configurable px)
    - Scale output by 2x (nearest-neighbor)
    - Evenly space row items to fill row width
    - Swap source/original order (AFTER on top vs BEFORE on top)
    - Include/exclude originals in composite
    - PNG-only mode (skip BMP files)

KEY COMPONENTS
  - find_matching_triplets()         : matches BEFORE/ALTERNATE/AFTER by stem
  - create_vertical_composite()      : stacks BEFORE/AFTER vertically
  - create_final_composite()         : assembles rows into final image
  - _build_row_image()               : builds a single row from verticals
  - _apply_output_options()          : matte, padding, scale post-processing
  - process_target_folder()          : full pipeline from folder to composite.png
  - DraggableImage                   : canvas widget with drag-and-drop
  - CompositeArrangementUI           : main Tkinter application class
    - create_ui / reset_layout        : canvas, controls, unused panel
    - detect_rows / update_row_vis    : automatic row grouping by y-position
    - load_json_layout / save_arrange : JSON layout persistence
    - generate_composite              : triggers composite generation

COMPOSITE JSON FORMAT (saved to TARGET/composite.json)
  {
    "rows": [
      {"items": [{"match_key": "item_name", "order_index": 0}, ...]},
      ...
    ]
  }

CONFIG
  Settings file : mho_05_comparison_sheet_config.json
  Layout files  : composite.json in each TARGET folder

QUICK USAGE
  python Z_TOOLS/mho_05_comparison_sheet.py

TOOLSGROUP::TRACKING
SORTGROUP::1
SORTPRIORITY::3
STATUS::working
VERSION::20260721
"""

# region Imports
import os
import json
import numpy as np
from PIL import Image, ImageTk
import tkinter as tk
from tkinter import filedialog, messagebox

# Configuration for paddings (in pixels)
VERTICAL_PADDING = 10
HORIZONTAL_PADDING = 20
DEFAULT_OUTER_PAD = 32

# Canvas layout constants
CANVAS_THUMBNAIL_SIZE = (100, 100)
CANVAS_GRID_PADDING = 20
CANVAS_UNUSED_THUMB_SIZE = (80, 80)
CANVAS_ROW_HEIGHT = 150
CANVAS_UNUSED_PANEL_WIDTH = 140
CANVAS_STACK_OFFSET = 30
CANVAS_ROW_BOX_PAD = 5
CANVAS_ROW_TEXT_HEIGHT = 20

# UI Colors
DARK_GRAY = "#1E1E1E"
DARKER_GRAY = "#252526"
LIGHTER_GRAY = "#333333"
BUTTON_BLACK = "#000000"
WHITE = "#FFFFFF"
ROW_COLORS = ["#3C4C7C", "#4C3C7C", "#7C3C4C", "#3C7C4C"]  # Row highlight colors

# Muted button colors
MUTED_GREEN = "#3C7C5C"
MUTED_BLUE = "#3C5C7C"
MUTED_PURPLE = "#6C4C7C"

# Persistent settings file (stored next to this script)
CONFIG_FILE = "mho_05_comparison_sheet_config.json"

# endregion


# region Config


def _script_dir():
    """Return the directory containing this script."""
    return os.path.dirname(os.path.abspath(__file__))


def _rel_path(path, base_dir):
    """Return a path relative to base_dir when possible, otherwise the original absolute path."""
    if not path:
        return ""
    try:
        abs_path = os.path.abspath(path)
        abs_base = os.path.abspath(base_dir)
        return os.path.relpath(abs_path, abs_base)
    except ValueError:
        return path


def _abs_path(path, base_dir):
    """Resolve a path relative to base_dir; return absolute path if already absolute."""
    if not path:
        return ""
    if os.path.isabs(path):
        return os.path.normpath(path)
    return os.path.normpath(os.path.join(base_dir, path))


def load_config():
    """Load the last-used settings from the script's config JSON file."""
    config_path = os.path.join(_script_dir(), CONFIG_FILE)
    if not os.path.isfile(config_path):
        return None
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save_config(values):
    """Save the current settings to the script's config JSON file as relative paths."""
    base_dir = _script_dir()
    config_path = os.path.join(base_dir, CONFIG_FILE)
    try:
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(values, f, indent=2)
    except Exception as e:
        print(f"DEBUG: Failed to save config: {e}")

# endregion


# region Image Utils

def get_content_bbox(image):
    """
    Compute the bounding box of the non-blank area of an image.
    Blank pixels are those that are either fully transparent or pure black (RGB (0,0,0)).
    Returns a tuple (left, upper, right, lower).
    """
    im = image.convert("RGBA")
    data = np.array(im)
    mask = ~((data[:, :, 3] == 0) | ((data[:, :, 0] == 0) & (data[:, :, 1] == 0) & (data[:, :, 2] == 0)))
    coords = np.argwhere(mask)
    if coords.size == 0:
        return (0, 0, image.width, image.height)
    y0, x0 = coords.min(axis=0)
    y1, x1 = coords.max(axis=0) + 1  # include last pixel
    return (x0, y0, x1, y1)

def _valid_image_ext(filename, png_only=False):
    if png_only:
        return filename.lower().endswith(".png")
    return filename.lower().endswith(".png") or filename.lower().endswith(".bmp")


def get_match_key(filename):
    """Return the filename stem used for matching BEFORE/AFTER pairs."""
    return os.path.splitext(filename)[0]


def format_match_key(key):
    """Format a match key for display or JSON."""
    return str(key)


def _get_config_match_key(item):
    """Read match key from a composite.json item, falling back to legacy 'hex_id'."""
    return item.get("match_key") or item.get("hex_id")

# endregion


# region MATCH items triplet

def find_matching_triplets(target_folder, custom_before_folder=None, alternate_before_folder=None, png_only=False):
    """
    Searches for AFTER images in the target folder (BMP or PNG files)
    and finds matching BEFORE images from either:
      - a custom BEFORE folder (if provided) OR
      - a subfolder named "backup" or "original" in the target folder.
    If an alternate_before_folder is provided, matching ALTERNATE BEFORE images are also found.
    Images are matched by filename stem.
    
    Returns a tuple:
      (triplets, stats)
      where triplets is a list of (before_path, alternate_before_path, after_path)
      and stats is a dict containing counts of potential matches
    """
    triplets = []
    stats = {
        "after_count": 0,
        "before_count": 0,
        "matched_count": 0
    }
    
    # Determine BEFORE folder:
    if custom_before_folder and os.path.isdir(custom_before_folder):
        before_folder = custom_before_folder
        print(f"DEBUG: Using custom BEFORE folder: {before_folder}")
    else:
        before_folder = None
        for folder_name in ["backup", "original"]:
            potential_path = os.path.join(target_folder, folder_name)
            if os.path.isdir(potential_path):
                before_folder = potential_path
                print(f"DEBUG: Found BEFORE folder (backup/original): {before_folder}")
                break
        if before_folder is None:
            print("DEBUG: No BEFORE folder (custom or backup/original) found.")
    
    def _build_source_dict(folder, label):
        """Build a filename-stem -> filename mapping for a BEFORE folder."""
        source_dict = {}
        if not folder or not os.path.isdir(folder):
            if folder:
                print(f"DEBUG: Provided {label} folder does not exist: {folder}")
            return [], source_dict

        files = [
            f for f in os.listdir(folder)
            if os.path.isfile(os.path.join(folder, f)) and _valid_image_ext(f, png_only)
        ]
        print(f"DEBUG: Potential {label} images found in {label} folder:")
        for f in files:
            print("  " + f)

        for f in files:
            key = get_match_key(f)
            if key:
                current = source_dict.get(key)
                if current and current.lower().endswith(".bmp") and f.lower().endswith(".png"):
                    source_dict[key] = f
                elif not current:
                    source_dict[key] = f
        return files, source_dict

    # List AFTER images in target folder (BMP or PNG), skipping subdirectories
    after_files = [
        f for f in os.listdir(target_folder)
        if os.path.isfile(os.path.join(target_folder, f)) and _valid_image_ext(f, png_only)
    ]
    for af in after_files:
        print(f"DEBUG: Found AFTER candidate in target folder: {af}")
    stats["after_count"] = len(after_files)
    print("DEBUG: Potential AFTER images found in target folder:")
    for af in after_files:
        print("  " + af)

    before_files, before_dict = _build_source_dict(before_folder, "BEFORE")
    stats["before_count"] = len(before_files)
    if not before_folder:
        print("DEBUG: No BEFORE folder (custom or backup/original) found.")

    _, alternate_before_dict = _build_source_dict(alternate_before_folder, "ALTERNATE BEFORE")

    # Match AFTER images with BEFORE (and ALTERNATE BEFORE if available) using match key
    for af in after_files:
        key = get_match_key(af)
        if key:
            before_path = None
            if key in before_dict:
                before_path = os.path.join(before_folder, before_dict[key])
            alternate_before_path = None
            if alternate_before_dict and (key in alternate_before_dict):
                alternate_before_path = os.path.join(alternate_before_folder, alternate_before_dict[key])
            if before_path:
                # Construct AFTER path - ensure it's directly in target folder, not in subdirectories
                after_path = os.path.join(target_folder, af)
                # Normalize paths to ensure consistency
                after_path = os.path.normpath(after_path)
                before_path = os.path.normpath(before_path)
                if alternate_before_path:
                    alternate_before_path = os.path.normpath(alternate_before_path)
                
                # Verify the after_path exists and is actually in the target folder
                if not os.path.exists(after_path):
                    print(f"DEBUG: WARNING - AFTER path does not exist: {after_path}")
                    continue
                if not os.path.samefile(os.path.dirname(after_path), target_folder):
                    print(f"DEBUG: WARNING - AFTER path is not in target folder: {after_path}")
                    continue
                    
                triplets.append((before_path, alternate_before_path, after_path))
                stats["matched_count"] += 1
                print(f"DEBUG: Found match: BEFORE: {before_path} | ALTERNATE BEFORE: {alternate_before_path} | AFTER: {after_path}")
            else:
                print(f"DEBUG: No BEFORE match for AFTER image: {af} (key: {key})")
    return triplets, stats

def create_vertical_composite(before_img, after_img, alternate_before_img=None, vertical_padding=VERTICAL_PADDING, swap_source=False):
    """
    Create a vertical composite from before and after images (and optional alternate before).
    If swap_source is True, AFTER is placed at the top and BEFORE at the bottom.
    """
    print("DEBUG: Creating vertical composite")
    print(f"DEBUG: Image sizes - Before: {before_img.size}, After: {after_img.size}, "
          f"Alt Before: {alternate_before_img.size if alternate_before_img else 'None'}")
    
    # Convert images to RGBA if they aren't already
    before_img = before_img.convert('RGBA')
    after_img = after_img.convert('RGBA')
    if alternate_before_img:
        alternate_before_img = alternate_before_img.convert('RGBA')
    
    # Get content bounding boxes
    before_bbox = get_content_bbox(before_img)
    after_bbox = get_content_bbox(after_img)
    alt_before_bbox = get_content_bbox(alternate_before_img) if alternate_before_img else None
    
    print(f"DEBUG: Content bboxes - Before: {before_bbox}, After: {after_bbox}, "
          f"Alt Before: {alt_before_bbox}")
    
    # Crop images to content
    before_img = before_img.crop(before_bbox)
    after_img = after_img.crop(after_bbox)
    if alternate_before_img:
        alternate_before_img = alternate_before_img.crop(alt_before_bbox)
    
    # Calculate dimensions
    max_width = max(before_img.width, after_img.width)
    if alternate_before_img:
        max_width = max(max_width, alternate_before_img.width)
    
    total_height = before_img.height + after_img.height + vertical_padding
    if alternate_before_img:
        total_height += alternate_before_img.height + vertical_padding
    
    print(f"DEBUG: Vertical composite dimensions: {max_width}x{total_height}")
    
    # Create composite
    composite = Image.new('RGBA', (max_width, total_height), (0, 0, 0, 0))
    
    # Determine order
    top_img, bottom_img = (after_img, before_img) if swap_source else (before_img, after_img)
    top_label, bottom_label = ("AFTER", "BEFORE") if swap_source else ("BEFORE", "AFTER")
    
    # Paste images
    y_offset = 0
    
    # Paste top image
    x_offset = (max_width - top_img.width) // 2
    print(f"DEBUG: Pasting {top_label} at ({x_offset}, {y_offset})")
    composite.paste(top_img, (x_offset, y_offset), top_img)
    y_offset += top_img.height + vertical_padding
    
    # Paste alternate before if present
    if alternate_before_img:
        x_offset = (max_width - alternate_before_img.width) // 2
        print(f"DEBUG: Pasting ALT BEFORE at ({x_offset}, {y_offset})")
        composite.paste(alternate_before_img, (x_offset, y_offset), alternate_before_img)
        y_offset += alternate_before_img.height + vertical_padding
    
    # Paste bottom image
    x_offset = (max_width - bottom_img.width) // 2
    print(f"DEBUG: Pasting {bottom_label} at ({x_offset}, {y_offset})")
    composite.paste(bottom_img, (x_offset, y_offset), bottom_img)
    
    print(f"DEBUG: Vertical composite created successfully: {composite.size}")
    return composite


def _apply_output_options(final_composite, matte_black=False, outer_padding=False, outer_pad_amt=DEFAULT_OUTER_PAD, scale2x=False):
    """Apply matte-black, outer-padding, and 2x-scale options to the composite."""
    if outer_padding:
        w, h = final_composite.size
        padded = Image.new('RGBA', (w + outer_pad_amt * 2, h + outer_pad_amt * 2), (0, 0, 0, 0))
        padded.paste(final_composite, (outer_pad_amt, outer_pad_amt), final_composite)
        final_composite = padded
        print(f"DEBUG: Added outer padding of {outer_pad_amt}px")

    if matte_black:
        w, h = final_composite.size
        matted = Image.new('RGB', (w, h), (0, 0, 0))
        matted.paste(final_composite, (0, 0), final_composite)
        final_composite = matted
        print("DEBUG: Matted onto black background")

    if scale2x:
        w, h = final_composite.size
        final_composite = final_composite.resize((w * 2, h * 2), resample=Image.NEAREST)
        print(f"DEBUG: Scaled output by 2x to {(w * 2, h * 2)}")

    return final_composite


def _build_row_image(row_verticals, horizontal_padding, alternate_spacing, max_row_width=None):
    """Build a single row image from a list of vertical composites."""
    if not row_verticals:
        return None

    n = len(row_verticals)
    row_height = max(img.height for img in row_verticals)

    if alternate_spacing:
        row_width = max_row_width if max_row_width is not None else (
            sum(img.width for img in row_verticals) + horizontal_padding * (n - 1) if n > 1
            else sum(img.width for img in row_verticals)
        )
        if n == 1:
            paddings = [(row_width - row_verticals[0].width) // 2]
        else:
            total_imgs_width = sum(img.width for img in row_verticals)
            min_total_pad = horizontal_padding * (n - 1)
            extra_space = row_width - (total_imgs_width + min_total_pad)
            hpad = horizontal_padding + (extra_space // (n - 1)) if extra_space > 0 else horizontal_padding
            paddings = [0] + [hpad] * (n - 1)

        row_img = Image.new("RGBA", (row_width, row_height), (0, 0, 0, 0))
        x = 0
        for idx, img in enumerate(row_verticals):
            row_img.paste(img, (x, (row_height - img.height) // 2), img)
            if idx < len(row_verticals) - 1:
                x += img.width + paddings[idx + 1]
            else:
                x += img.width
        return row_img

    # Standard fixed horizontal padding
    row_width = sum(img.width for img in row_verticals) + horizontal_padding * (n - 1)
    row_img = Image.new("RGBA", (row_width, row_height), (0, 0, 0, 0))
    x = 0
    for img in row_verticals:
        row_img.paste(img, (x, (row_height - img.height) // 2), img)
        x += img.width + horizontal_padding
    return row_img


def create_final_composite(triplets, vertical_padding=VERTICAL_PADDING, horizontal_padding=HORIZONTAL_PADDING, composite_config=None, include_originals=True, matte_black=False, outer_padding=False, scale2x=False, outer_pad_amt=DEFAULT_OUTER_PAD, alternate_spacing=False, swap_source=False):
    """
    Create the final composite image from the given triplets.
    If composite_config is provided, it will be used to arrange the triplets in rows.
    If include_originals is False, BEFORE images will be skipped (replaced with blank).
    """
    print(f"\nDEBUG: Creating final composite from {len(triplets)} triplets")
    print(f"DEBUG: Using padding - Vertical: {vertical_padding}px, Horizontal: {horizontal_padding}px")

    if composite_config and "rows" in composite_config:
        print("\nDEBUG: Using provided composite configuration")
        print("DEBUG: Configuration structure:")
        for row_idx, row in enumerate(composite_config["rows"]):
            print(f"  Row {row_idx}: {len(row['items'])} items")
            for item in row["items"]:
                print(f"    - Item: stem={item['match_key']}, order_index={item.get('order_index', 0)}")

        # Group triplets by rows according to config
        rows = []
        key_to_triplet = {get_match_key(after_path): triplet for triplet in triplets
                         for _, _, after_path in [triplet]}

        for row_idx, row in enumerate(composite_config["rows"]):
            row_triplets = []
            print(f"\nDEBUG: Processing row {row_idx}")

            for item in row["items"]:
                key = _get_config_match_key(item)
                if key in key_to_triplet:
                    triplet = key_to_triplet[key]
                    row_triplets.append(triplet)
                    print(f"  Added asset: {os.path.basename(triplet[2])} (key: {format_match_key(key)})")
                else:
                    print(f"  WARNING: Could not find triplet for key: {format_match_key(key)}")
            
            if row_triplets:
                rows.append(row_triplets)
        
        # Process each row to create row composites
        print("\nDEBUG: Creating row composites")
        row_images = []
        max_width = 0
        total_height = -vertical_padding  # Start with -padding since we add padding for each row
        
        for row_idx, row_triplets in enumerate(rows):
            print(f"\nDEBUG: Creating composite for row {row_idx}")
            row_verticals = []
            
            # Create vertical composites for each triplet in the row
            for triplet in row_triplets:
                before_path, alt_before_path, after_path = triplet
                print(f"  Processing: {os.path.basename(after_path)} (key: {format_match_key(get_match_key(after_path))})")
                
                try:
                    before_img = Image.open(before_path) if before_path and os.path.exists(before_path) else None
                    if before_img:
                        print(f"    Loaded BEFORE from: {before_path}")
                    
                    after_img = Image.open(after_path) if after_path and os.path.exists(after_path) else None
                    if after_img:
                        print(f"    Loaded AFTER from: {after_path}")
                    else:
                        print(f"    ERROR: Could not load AFTER from: {after_path}")
                    
                    alternate_before_img = Image.open(alt_before_path) if alt_before_path and os.path.exists(alt_before_path) else None
                    if alternate_before_img:
                        print(f"    Loaded ALTERNATE BEFORE from: {alt_before_path}")
                    
                    # If not including originals, blank the appropriate image
                    if not include_originals:
                        if swap_source:
                            # Show original (BEFORE), blank source (AFTER)
                            after_img = Image.new("RGBA", (1, 1), (0,0,0,0))
                            print("    Blanked AFTER (swap_source=True)")
                        else:
                            # Show source (AFTER), blank original (BEFORE)
                            before_img = Image.new("RGBA", (1, 1), (0,0,0,0))
                            print("    Blanked BEFORE (include_originals=False)")
                    
                    vertical = create_vertical_composite(before_img, after_img, alternate_before_img, vertical_padding, swap_source=swap_source)
                    print(f"    Created vertical composite: {vertical.size}")
                    
                    row_verticals.append(vertical)
                    
                except Exception as e:
                    print(f"    ERROR: Failed to process triplet: {e}")
                    continue
            
            if not row_verticals:
                continue
            row_images.append(row_verticals)

        if not row_images:
            print("ERROR: No row images were created")
            return None

        # Determine final row width for alternate spacing
        if alternate_spacing:
            max_row_width = max(
                sum(img.width for img in row_v) + horizontal_padding * (len(row_v) - 1)
                for row_v in row_images
            ) if len(row_images) > 1 else (
                sum(img.width for img in row_images[0]) if row_images else 0
            )
            print(f"DEBUG: Alternate spacing: max row width = {max_row_width}")
        else:
            max_row_width = None

        # Build final row images
        built_rows = []
        max_width = 0
        total_height = -vertical_padding
        for row_verticals in row_images:
            row_img = _build_row_image(row_verticals, horizontal_padding, alternate_spacing, max_row_width)
            if row_img:
                built_rows.append(row_img)
                max_width = max(max_width, row_img.width)
                total_height += row_img.height + vertical_padding

        if not built_rows:
            print("ERROR: No row images were created")
            return None

        # Create final composite from rows
        print(f"\nDEBUG: Creating final composite: {max_width}x{total_height}")
        final_composite = Image.new('RGBA', (max_width, total_height), (0, 0, 0, 0))

        # Paste rows
        y_offset = 0
        for idx, row_image in enumerate(built_rows):
            x_offset = (max_width - row_image.width) // 2
            print(f"  Pasting row {idx} at ({x_offset}, {y_offset})")
            final_composite.paste(row_image, (x_offset, y_offset), row_image)
            y_offset += row_image.height + vertical_padding

        final_composite = _apply_output_options(final_composite, matte_black, outer_padding, outer_pad_amt, scale2x)
        print("\nDEBUG: Final composite created successfully")
        return final_composite

    else:
        print("DEBUG: No composite configuration provided, creating single column layout")
        # Create vertical layout (existing code for backward compatibility)
        return create_single_column_composite(triplets, vertical_padding, horizontal_padding, swap_source=swap_source)

def create_single_column_composite(triplets, vertical_padding, horizontal_padding, swap_source=False):
    """Helper function to create a single-column composite (old style)"""
    print("\nDEBUG: Creating single-column composite")
    verticals = []
    max_width = 0
    total_height = -vertical_padding

    for triplet in triplets:
        before_path, alt_before_path, after_path = triplet
        print(f"  Processing: {os.path.basename(after_path)} (key: {format_match_key(get_match_key(after_path))})")
        
        try:
            before_img = Image.open(before_path).convert('RGBA')
            after_img = Image.open(after_path).convert('RGBA')
            alt_before_img = Image.open(alt_before_path).convert('RGBA') if alt_before_path else None
            
            # Note: single-column composite path doesn't support include_originals
            # It's only used when no composite_config is provided
            
            vertical = create_vertical_composite(before_img, after_img, alt_before_img, vertical_padding, swap_source=swap_source)
            print(f"    Created vertical composite: {vertical.size}")
            
            verticals.append(vertical)
            max_width = max(max_width, vertical.width)
            total_height += vertical.height + vertical_padding
            
        except Exception as e:
            print(f"    ERROR: Failed to process triplet: {e}")
            continue
    
    if not verticals:
        print("ERROR: No vertical composites were created")
        return None
    
    print(f"\nDEBUG: Creating final single-column composite: {max_width}x{total_height}")
    final_composite = Image.new('RGBA', (max_width, total_height), (0, 0, 0, 0))
    
    y_offset = 0
    for idx, vertical in enumerate(verticals):
        x_offset = (max_width - vertical.width) // 2
        print(f"  Pasting vertical {idx} at ({x_offset}, {y_offset})")
        final_composite.paste(vertical, (x_offset, y_offset), vertical)
        y_offset += vertical.height + vertical_padding
    
    print("\nDEBUG: Single-column composite created successfully")
    return final_composite

def load_composite_json(json_path):
    """
    Load a composite configuration from a JSON file.
    Returns None if the file doesn't exist or is invalid.
    """
    try:
        if os.path.exists(json_path):
            with open(json_path, "r") as f:
                return json.load(f)
    except Exception as e:
        print(f"DEBUG: Error loading composite.json: {e}")
    return None

def process_target_folder(target_folder, custom_before_folder=None, alternate_before_folder=None, composite_config=None, include_originals=True, vertical_padding=VERTICAL_PADDING, horizontal_padding=HORIZONTAL_PADDING, matte_black=False, outer_padding=False, scale2x=False, outer_pad_amt=32, alternate_spacing=False, png_only=False, swap_source=False):
    """
    Process the given target folder by finding matching image triplets,
    creating the composite image, and saving it in the target folder.
    Returns the output path if successful.
    """
    if not os.path.isdir(target_folder):
        print(f"ERROR: Target folder does not exist: {target_folder}")
        return None
    print(f"DEBUG: Processing target folder: {target_folder}")

    triplets, stats = find_matching_triplets(target_folder, custom_before_folder, alternate_before_folder, png_only=png_only)
    if not triplets:
        print("DEBUG: No matching image triplets found.")
        return None

    # If composite_config is provided, reorder triplets according to the configuration
    if composite_config and "rows" in composite_config:
        ordered_triplets = []
        key_to_triplet = {get_match_key(after_path): triplet for triplet in triplets
                         for _, _, after_path in [triplet]}

        for row in composite_config["rows"]:
            for item in row["items"]:
                key = _get_config_match_key(item)
                if key in key_to_triplet:
                    ordered_triplets.append(key_to_triplet[key])

        # Add any remaining triplets that weren't in the config
        config_keys = {_get_config_match_key(item) for row in composite_config["rows"] for item in row["items"]}
        remaining_triplets = [triplet for triplet in triplets
                            if get_match_key(triplet[2]) not in config_keys]
        triplets = ordered_triplets + remaining_triplets

    print(f"DEBUG: Creating composite from {len(triplets)} triplets")
    final_image = create_final_composite(
        triplets, vertical_padding=vertical_padding, horizontal_padding=horizontal_padding,
        composite_config=composite_config, include_originals=include_originals,
        matte_black=matte_black, outer_padding=outer_padding, scale2x=scale2x, outer_pad_amt=outer_pad_amt,
        alternate_spacing=alternate_spacing, swap_source=swap_source
    )
    if final_image:
        output_path = os.path.join(target_folder, "composite.png")
        final_image.save(output_path)
        print(f"DEBUG: Final composite saved to {output_path}")
        return output_path
    return None

# endregion


# region Draggable Image

class DraggableImage:
    def __init__(self, canvas, x, y, image, match_key, thumbnail_size=CANVAS_THUMBNAIL_SIZE):
        self.canvas = canvas
        self.match_key = match_key
        self.row_id = None
        self.thumbnail_size = thumbnail_size
        
        # Create thumbnail
        img = Image.open(image)
        img.thumbnail(thumbnail_size)
        self.photo = ImageTk.PhotoImage(img)
        
        # Create canvas image and text with white text on dark background
        self.image_item = canvas.create_image(x, y, image=self.photo, anchor="n")
        # Position text directly under the image, centered
        text_y = y + thumbnail_size[1] + 2
        self.text_item = canvas.create_text(x + thumbnail_size[0]//2, text_y,
                                        text=format_match_key(match_key), anchor="n", fill=WHITE)
        
        # Bind mouse events
        canvas.tag_bind(self.image_item, '<Button-1>', self.on_press)
        canvas.tag_bind(self.image_item, '<B1-Motion>', self.on_drag)
        canvas.tag_bind(self.image_item, '<ButtonRelease-1>', self.on_release)
        
        self.drag_data = {"x": 0, "y": 0, "dragging": False}
        self.current_pos = [x, y]

    def get_position(self):
        """Get current position of the image"""
        bbox = self.canvas.bbox(self.image_item)
        if bbox:
            return [bbox[0], bbox[1]]  # Use top-left corner
        return self.current_pos

    def set_position(self, x, y):
        """Set position of both image and text"""
        current_pos = self.get_position()
        dx = x - current_pos[0]
        dy = y - current_pos[1]
        
        self.canvas.move(self.image_item, dx, dy)
        self.canvas.move(self.text_item, dx, dy)
        self.current_pos = [x, y]

    def on_press(self, event):
        self.drag_data["x"] = event.x
        self.drag_data["y"] = event.y
        self.drag_data["dragging"] = True
        # Raise this image above others
        self.canvas.tag_raise(self.image_item)
        self.canvas.tag_raise(self.text_item)
    
    def on_drag(self, event):
        if not self.drag_data["dragging"]:
            return
            
        dx = event.x - self.drag_data["x"]
        dy = event.y - self.drag_data["y"]
        
        self.canvas.move(self.image_item, dx, dy)
        self.canvas.move(self.text_item, dx, dy)
        
        self.drag_data["x"] = event.x
        self.drag_data["y"] = event.y
        
        self.current_pos[0] += dx
        self.current_pos[1] += dy
        
        # Notify parent window to update row detection
        self.canvas.event_generate("<<ArrangementChanged>>")
    
    def on_release(self, event):
        self.drag_data["dragging"] = False
        # Notify parent window to update row detection
        self.canvas.event_generate("<<ArrangementChanged>>")

# endregion


# region UI App

class CompositeArrangementUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Image Comparison Compositor")
        self.root.configure(bg=DARK_GRAY)

        # Project-local settings storage
        self.script_dir = _script_dir()
        self.config = load_config() or {}

        # Variables
        self.draggable_images = []
        self.triplets = []
        self.selection_rect = None
        self.selection_start = None
        self.rows = {}  # Dictionary to store row assignments

        # State mapping: match_key -> 'IN_COMPOSITION', 'ON_CANVAS_ONLY', 'HIDDEN'
        self.triplet_states = {}
        self.unused_triplet_widgets = {}
        self.create_ui()
        
    def clear_unused_panel(self):
        for widget in self.unused_items_container.winfo_children():
            widget.destroy()

    def add_unused_triplet_widget(self, triplet, match_key):
        # Create a thumbnail for the unused panel
        from PIL import ImageTk, Image
        img_path = triplet[2]
        try:
            img = Image.open(img_path)
            img.thumbnail(CANVAS_UNUSED_THUMB_SIZE)
            photo = ImageTk.PhotoImage(img)
            frame = tk.Frame(self.unused_items_container, bg=LIGHTER_GRAY, bd=2, relief=tk.RIDGE)
            label = tk.Label(frame, image=photo, bg=LIGHTER_GRAY)
            label.image = photo
            label.pack()
            text = tk.Label(frame, text=format_match_key(match_key), bg=LIGHTER_GRAY, fg=WHITE, font=("Arial", 8))
            text.pack()
            frame.pack(pady=4, padx=2)
            # Right-click menu
            menu = tk.Menu(frame, tearoff=0, bg=LIGHTER_GRAY, fg=WHITE)
            menu.add_command(label="Show on Canvas (not in composition)", command=lambda: self.show_on_canvas_only(triplet, match_key))
            menu.add_command(label="Hide", command=lambda: self.hide_triplet(match_key))
            def popup(event):
                menu.tk_popup(event.x_root, event.y_root)
            frame.bind("<Button-3>", popup)
            label.bind("<Button-3>", popup)
            text.bind("<Button-3>", popup)
            self.unused_triplet_widgets[match_key] = frame
        except Exception as e:
            pass  # Ignore image load errors

    def show_on_canvas_only(self, triplet, match_key):
        # Add to canvas but not in composition
        x, y = CANVAS_STACK_OFFSET, CANVAS_STACK_OFFSET + CANVAS_STACK_OFFSET * len(self.draggable_images)
        img = DraggableImage(self.canvas, x, y, triplet[2], match_key)
        self.draggable_images.append(img)
        self.triplet_states[match_key] = 'ON_CANVAS_ONLY'
        self.add_canvas_context_menu(img, match_key)
        # Remove from unused panel
        widget = self.unused_triplet_widgets.get(match_key)
        if widget:
            widget.destroy()
            del self.unused_triplet_widgets[match_key]

    def hide_triplet(self, match_key):
        # Remove from both canvas and unused panel
        self.triplet_states[match_key] = 'HIDDEN'
        widget = self.unused_triplet_widgets.get(match_key)
        if widget:
            widget.destroy()
            del self.unused_triplet_widgets[match_key]
        # Also remove from canvas if present
        for img in list(self.draggable_images):
            if img.match_key == match_key:
                self.canvas.delete(img.image_item)
                self.canvas.delete(img.text_item)
                self.draggable_images.remove(img)

    def add_canvas_context_menu(self, draggable_img, match_key):
        # Add right-click menu for images on canvas
        menu = tk.Menu(self.canvas, tearoff=0, bg=LIGHTER_GRAY, fg=WHITE)
        if self.triplet_states.get(match_key) == 'IN_COMPOSITION':
            menu.add_command(label="Set as 'On Canvas Only' (exclude from composition)", command=lambda: self.set_on_canvas_only(draggable_img, match_key))
        else:
            menu.add_command(label="Set as 'In Composition' (include in composition)", command=lambda: self.set_in_composition(draggable_img, match_key))
        menu.add_command(label="Hide", command=lambda: self.hide_triplet(match_key))
        def popup(event):
            menu.tk_popup(event.x_root, event.y_root)
        self.canvas.tag_bind(draggable_img.image_item, '<Button-3>', popup)
        self.canvas.tag_bind(draggable_img.text_item, '<Button-3>', popup)

    def set_on_canvas_only(self, draggable_img, match_key):
        self.triplet_states[match_key] = 'ON_CANVAS_ONLY'
        # Visually distinguish (e.g., dim)
        self.canvas.itemconfig(draggable_img.image_item, stipple="gray50")
        self.add_canvas_context_menu(draggable_img, match_key)

    def set_in_composition(self, draggable_img, match_key):
        self.triplet_states[match_key] = 'IN_COMPOSITION'
        self.canvas.itemconfig(draggable_img.image_item, stipple="")
        self.add_canvas_context_menu(draggable_img, match_key)

    # region UI creation
    def create_ui(self):
        # Main container
        main_container = tk.Frame(self.root, bg=DARK_GRAY)
        main_container.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Top frame for folder inputs
        input_frame = tk.Frame(main_container, bg=DARK_GRAY)
        input_frame.pack(fill=tk.X, pady=(0, 10))
        
        # Target folder
        tk.Label(input_frame, text="Target Folder:", bg=DARK_GRAY, fg=WHITE).grid(row=0, column=0, sticky="w")
        self.target_entry = tk.Entry(input_frame, width=50, bg=DARKER_GRAY, fg=WHITE)
        self.target_entry.grid(row=0, column=1, padx=5)
        default_target = _abs_path(self.config.get("target_dir", os.path.join(self.script_dir, "..", "ITEM")), self.script_dir)
        self.target_entry.delete(0, tk.END)
        self.target_entry.insert(0, default_target)
        tk.Button(input_frame, text="Browse", command=self.browse_target,
                 bg=BUTTON_BLACK, fg=WHITE, activebackground=BUTTON_BLACK,
                 activeforeground=WHITE).grid(row=0, column=2)
        self.target_stats_label = tk.Label(input_frame, text="", bg=DARK_GRAY, fg=WHITE)
        self.target_stats_label.grid(row=0, column=3, padx=10)
        
        # Custom BEFORE folder
        self.use_custom_before = tk.BooleanVar(value=self.config.get("use_custom_before", False))
        tk.Checkbutton(input_frame, text="Custom BEFORE folder", variable=self.use_custom_before,
                      bg=DARK_GRAY, fg=WHITE, selectcolor=BUTTON_BLACK,
                      activebackground=DARK_GRAY, activeforeground=WHITE).grid(row=1, column=0, sticky="w")
        self.custom_before_entry = tk.Entry(input_frame, width=50, bg=DARKER_GRAY, fg=WHITE)
        self.custom_before_entry.grid(row=1, column=1, padx=5)
        default_custom_before = _abs_path(self.config.get("custom_before_dir", ""), self.script_dir)
        self.custom_before_entry.delete(0, tk.END)
        self.custom_before_entry.insert(0, default_custom_before)
        tk.Button(input_frame, text="Browse", command=self.browse_custom_before,
                 bg=BUTTON_BLACK, fg=WHITE, activebackground=BUTTON_BLACK,
                 activeforeground=WHITE).grid(row=1, column=2)
        self.before_stats_label = tk.Label(input_frame, text="", bg=DARK_GRAY, fg=WHITE)
        self.before_stats_label.grid(row=1, column=3, padx=10)
        
        # ALTERNATE BEFORE folder
        self.use_alternate_before = tk.BooleanVar(value=self.config.get("use_alternate_before", False))
        tk.Checkbutton(input_frame, text="ALTERNATE BEFORE folder", variable=self.use_alternate_before,
                      bg=DARK_GRAY, fg=WHITE, selectcolor=BUTTON_BLACK,
                      activebackground=DARK_GRAY, activeforeground=WHITE).grid(row=2, column=0, sticky="w")
        self.alt_before_entry = tk.Entry(input_frame, width=50, bg=DARKER_GRAY, fg=WHITE)
        self.alt_before_entry.grid(row=2, column=1, padx=5)
        default_alt_before = _abs_path(self.config.get("alternate_before_dir", ""), self.script_dir)
        self.alt_before_entry.delete(0, tk.END)
        self.alt_before_entry.insert(0, default_alt_before)
        tk.Button(input_frame, text="Browse", command=self.browse_alt_before,
                 bg=BUTTON_BLACK, fg=WHITE, activebackground=BUTTON_BLACK,
                 activeforeground=WHITE).grid(row=2, column=2)
        
        options_frame = tk.Frame(main_container, bg=DARK_GRAY)
        options_frame.pack(fill=tk.X, pady=(0, 0))

        self.include_originals = tk.BooleanVar(value=self.config.get("include_originals", True))
        include_chk = tk.Checkbutton(options_frame, text="Include Originals in Composite", variable=self.include_originals,
                                    bg=DARK_GRAY, fg=WHITE, selectcolor=BUTTON_BLACK,
                                    activebackground=DARK_GRAY, activeforeground=WHITE)
        include_chk.pack(side=tk.LEFT, padx=5)

        self.use_png_only = tk.BooleanVar(value=self.config.get("png_only", False))
        png_only_chk = tk.Checkbutton(options_frame, text="Use PNG Only (no BMP)", variable=self.use_png_only,
                                      bg=DARK_GRAY, fg=WHITE, selectcolor=BUTTON_BLACK,
                                      activebackground=DARK_GRAY, activeforeground=WHITE,
                                      command=lambda: self.on_target_changed(None))
        png_only_chk.pack(side=tk.LEFT, padx=5)

        self.swap_source = tk.BooleanVar(value=self.config.get("swap_source", False))
        swap_chk = tk.Checkbutton(options_frame, text="Swap Source/Original", variable=self.swap_source,
                                  bg=DARK_GRAY, fg=WHITE, selectcolor=BUTTON_BLACK,
                                  activebackground=DARK_GRAY, activeforeground=WHITE)
        swap_chk.pack(side=tk.LEFT, padx=5)

        # Output options
        self.matte_black_var = tk.BooleanVar(value=self.config.get("matte_black", True))
        self.outer_padding_var = tk.BooleanVar(value=self.config.get("outer_padding", True))
        self.scale2x_var = tk.BooleanVar(value=self.config.get("scale2x", True))
        self.alternate_spacing_var = tk.BooleanVar(value=self.config.get("alternate_spacing", False))
        matte_chk = tk.Checkbutton(options_frame, text="Matte onto Black Background", variable=self.matte_black_var,
                                   bg=DARK_GRAY, fg=WHITE, selectcolor=BUTTON_BLACK,
                                   activebackground=DARK_GRAY, activeforeground=WHITE)
        matte_chk.pack(side=tk.LEFT, padx=5)
        pad_chk = tk.Checkbutton(options_frame, text="Add Outer Padding", variable=self.outer_padding_var,
                                 bg=DARK_GRAY, fg=WHITE, selectcolor=BUTTON_BLACK,
                                 activebackground=DARK_GRAY, activeforeground=WHITE)
        pad_chk.pack(side=tk.LEFT, padx=5)
        scale_chk = tk.Checkbutton(options_frame, text="Scale Output by 2x (Nearest)", variable=self.scale2x_var,
                                   bg=DARK_GRAY, fg=WHITE, selectcolor=BUTTON_BLACK,
                                   activebackground=DARK_GRAY, activeforeground=WHITE)
        scale_chk.pack(side=tk.LEFT, padx=5)
        alt_spacing_chk = tk.Checkbutton(options_frame, text="Evenly Space Row Items to Fill Row", variable=self.alternate_spacing_var,
                                   bg=DARK_GRAY, fg=WHITE, selectcolor=BUTTON_BLACK,
                                   activebackground=DARK_GRAY, activeforeground=WHITE)
        alt_spacing_chk.pack(side=tk.LEFT, padx=5)

        padding_frame = tk.Frame(main_container, bg=DARK_GRAY)
        padding_frame.pack(fill=tk.X, pady=(0, 8))
        self.vert_pad_var = tk.StringVar(value=str(self.config.get("vertical_padding", VERTICAL_PADDING)))
        self.horiz_pad_var = tk.StringVar(value=str(self.config.get("horizontal_padding", HORIZONTAL_PADDING)))
        self.outer_pad_amt_var = tk.StringVar(value=str(self.config.get("outer_pad_amount", DEFAULT_OUTER_PAD)))
        tk.Label(padding_frame, text="Row Padding:", bg=DARK_GRAY, fg=WHITE).pack(side=tk.LEFT, padx=(5,2))
        self.vert_pad_entry = tk.Entry(padding_frame, width=4, textvariable=self.vert_pad_var, bg=DARKER_GRAY, fg=WHITE)
        self.vert_pad_entry.pack(side=tk.LEFT)
        tk.Label(padding_frame, text="Item Padding:", bg=DARK_GRAY, fg=WHITE).pack(side=tk.LEFT, padx=(10,2))
        self.horiz_pad_entry = tk.Entry(padding_frame, width=4, textvariable=self.horiz_pad_var, bg=DARKER_GRAY, fg=WHITE)
        self.horiz_pad_entry.pack(side=tk.LEFT)
        tk.Label(padding_frame, text="Outer Padding(px):", bg=DARK_GRAY, fg=WHITE).pack(side=tk.LEFT, padx=(10,2))
        self.outer_pad_amt_entry = tk.Entry(padding_frame, width=4, textvariable=self.outer_pad_amt_var, bg=DARKER_GRAY, fg=WHITE)
        self.outer_pad_amt_entry.pack(side=tk.LEFT)

        buttons_frame = tk.Frame(main_container, bg=DARK_GRAY)
        buttons_frame.pack(fill=tk.X, pady=(0, 10))
        tk.Button(buttons_frame, text="Reset Layout", command=self.reset_layout,
                 bg=MUTED_GREEN, fg=WHITE, activebackground=MUTED_GREEN, 
                 activeforeground=WHITE).pack(side=tk.LEFT, padx=5)
        tk.Button(buttons_frame, text="Save Arrangement", command=self.save_arrangement,
                 bg=MUTED_BLUE, fg=WHITE, activebackground=MUTED_BLUE,
                 activeforeground=WHITE).pack(side=tk.LEFT, padx=5)
        generate_btn = tk.Button(buttons_frame, text="Generate Composite", command=self.generate_composite,
                             bg=MUTED_PURPLE, fg=WHITE, activebackground=MUTED_PURPLE,
                             activeforeground=WHITE)
        generate_btn.pack(side=tk.LEFT, padx=5)
        generate_btn.configure(relief=tk.RAISED, borderwidth=2)

        # Bind target-entry events to refresh the UI
        self.target_entry.bind("<Return>", self.on_target_changed)
        self.target_entry.bind("<FocusOut>", self.on_target_changed)
        
        # JSON files frame
        self.json_frame = tk.Frame(main_container, bg=DARK_GRAY)
        self.json_frame.pack(fill=tk.X, pady=(0, 10))
        
        # Canvas and Unused Items frame
        canvas_and_unused = tk.Frame(main_container, bg=DARK_GRAY)
        canvas_and_unused.pack(fill=tk.BOTH, expand=True)

        # Canvas
        self.canvas = tk.Canvas(canvas_and_unused, bg=DARKER_GRAY, highlightthickness=0)
        scrollbar_y = tk.Scrollbar(canvas_and_unused, orient=tk.VERTICAL, command=self.canvas.yview)
        scrollbar_x = tk.Scrollbar(canvas_and_unused, orient=tk.HORIZONTAL, command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=scrollbar_y.set, xscrollcommand=scrollbar_x.set)
        scrollbar_y.pack(side=tk.RIGHT, fill=tk.Y)
        scrollbar_x.pack(side=tk.BOTTOM, fill=tk.X)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Unused items panel
        self.unused_panel = tk.Frame(canvas_and_unused, bg=LIGHTER_GRAY, width=CANVAS_UNUSED_PANEL_WIDTH)
        self.unused_panel.pack(side=tk.RIGHT, fill=tk.Y, padx=(10,0))
        unused_label = tk.Label(self.unused_panel, text="Unused Items", bg=LIGHTER_GRAY, fg=WHITE)
        unused_label.pack(pady=(5,2))
        self.unused_items_container = tk.Frame(self.unused_panel, bg=LIGHTER_GRAY)
        self.unused_items_container.pack(fill=tk.BOTH, expand=True)
        
        # Bind events
        self.canvas.bind("<<ArrangementChanged>>", self.on_arrangement_changed)
        self.canvas.bind("<ButtonPress-1>", self.on_select_start)
        self.canvas.bind("<B1-Motion>", self.on_select_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_select_end)
        self.target_entry.bind("<FocusOut>", self.on_target_changed)

    # endregion

    # region Layout  

    def browse_target(self):
        folder = filedialog.askdirectory(title="Select Target Folder")
        if folder:
            self.target_entry.delete(0, tk.END)
            self.target_entry.insert(0, folder)
            self.on_target_changed(None)
    
    def browse_custom_before(self):
        folder = filedialog.askdirectory(title="Select Custom BEFORE Folder")
        if folder:
            self.custom_before_entry.delete(0, tk.END)
            self.custom_before_entry.insert(0, folder)
    
    def browse_alt_before(self):
        folder = filedialog.askdirectory(title="Select ALTERNATE BEFORE Folder")
        if folder:
            self.alt_before_entry.delete(0, tk.END)
            self.alt_before_entry.insert(0, folder)
    
    def find_json_files(self, folder):
        """Find and validate JSON files in the target folder"""
        json_files = []
        try:
            for file in os.listdir(folder):
                if file.endswith('.json'):
                    path = os.path.join(folder, file)
                    try:
                        with open(path, 'r') as f:
                            data = json.load(f)
                            if 'rows' in data:  # Basic validation
                                json_files.append(path)
                    except:
                        continue
        except:
            pass
        return json_files
    
    def update_json_buttons(self, folder):
        """Update JSON file buttons"""
        # Clear existing buttons
        for widget in self.json_frame.winfo_children():
            widget.destroy()
        
        # Add label
        tk.Label(self.json_frame, text="Available Layouts:", 
                 bg=DARK_GRAY, fg=WHITE).pack(side=tk.LEFT, padx=(0, 10))
        
        # Add buttons for each JSON file
        json_files = self.find_json_files(folder)
        for json_file in json_files:
            name = os.path.basename(json_file)
            btn = tk.Button(self.json_frame, text=name, 
                           command=lambda f=json_file: self.load_json_layout(f),
                           bg=BUTTON_BLACK, fg=WHITE, activebackground=BUTTON_BLACK, 
                           activeforeground=WHITE)
            btn.pack(side=tk.LEFT, padx=5)
        
        # Load first JSON if available
        if json_files:
            self.load_json_layout(json_files[0])
    
    def on_target_changed(self, event):
        """Handle target folder change"""
        target = self.target_entry.get().strip()
        if target and os.path.isdir(target):
            # Update triplets
            custom_before = self.custom_before_entry.get().strip() if self.use_custom_before.get() else None
            alt_before = self.alt_before_entry.get().strip() if self.use_alternate_before.get() else None
            png_only = self.use_png_only.get()
            self.triplets, stats = find_matching_triplets(target, custom_before, alt_before, png_only=png_only)
            
            # Update stats labels
            self.update_stats_labels(stats)
            
            # Update JSON buttons and canvas
            self.update_json_buttons(target)
            if not self.triplets:
                messagebox.showerror("Error", "No matching image triplets found.")
            else:
                self.reset_layout()
    
    def load_json_layout(self, json_file):
        """Load layout from JSON file"""
        try:
            with open(json_file, 'r') as f:
                config = json.load(f)
            if not config.get("rows"):
                raise ValueError("Invalid layout file: no rows defined")

            # Clear current layout
            self.canvas.delete("all")
            self.draggable_images = []
            self.triplet_states = {}
            self.clear_unused_panel()
            self.unused_triplet_widgets = {}

            # Create dictionary mapping match keys to triplets
            key_to_triplet = {get_match_key(after_path): triplet
                              for triplet in self.triplets
                              for _, _, after_path in [triplet]}

            # Mark all as HIDDEN by default
            for key in key_to_triplet.keys():
                self.triplet_states[key] = 'HIDDEN'

            # Process each row in the configuration
            row_height = CANVAS_ROW_HEIGHT
            thumb_w = CANVAS_THUMBNAIL_SIZE[0]
            padding = CANVAS_GRID_PADDING
            for row_idx, row in enumerate(config["rows"]):
                base_y = row_idx * row_height + padding
                num_images = len(row["items"])
                total_width = num_images * thumb_w + (num_images - 1) * padding
                start_x = (self.canvas.winfo_width() - total_width) // 2
                for idx, item in enumerate(row["items"]):
                    key = _get_config_match_key(item)
                    if key in key_to_triplet:
                        x = start_x + idx * (thumb_w + padding)
                        y = base_y + (row.get("y_offset", 0))
                        triplet = key_to_triplet[key]
                        img = DraggableImage(self.canvas, x, y, triplet[2], key)
                        self.draggable_images.append(img)
                        self.triplet_states[key] = 'IN_COMPOSITION'
                        self.add_canvas_context_menu(img, key)

            # Add unused triplets to unused panel
            for key, triplet in key_to_triplet.items():
                if self.triplet_states.get(key) == 'HIDDEN':
                    # Add to unused panel as thumbnail
                    self.add_unused_triplet_widget(triplet, key)
                    self.triplet_states[key] = 'HIDDEN'  # Explicit

            # Update row visualization
            self.rows = self.detect_rows()
            self.update_row_visualization()
            self.canvas.update_idletasks()
            self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load layout: {str(e)}")

    def reset_layout(self):
        """Reset canvas layout with 16:9 aspect ratio approximation"""
        self.canvas.delete("all")
        self.draggable_images = []
        self.rows = {}
        self.triplet_states = {}
        self.clear_unused_panel()
        self.unused_triplet_widgets = {}
        # Optionally repopulate unused panel if triplets are available
        if hasattr(self, 'triplets'):
            key_to_triplet = {get_match_key(after_path): triplet
                              for triplet in self.triplets
                              for _, _, after_path in [triplet]}
            for key, triplet in key_to_triplet.items():
                self.triplet_states[key] = 'HIDDEN'
                self.add_unused_triplet_widget(triplet, key)

        
        if not self.triplets:
            return
        
        # Calculate grid dimensions to approximate 16:9
        total_images = len(self.triplets)
        ratio = 16/9
        
        # Calculate number of rows to approximate 16:9 ratio
        num_rows = int(np.sqrt(total_images / ratio))
        if num_rows < 1:
            num_rows = 1
        
        images_per_row = total_images // num_rows
        if images_per_row < 1:
            images_per_row = 1
        
        # Create grid layout
        thumbnail_size = CANVAS_THUMBNAIL_SIZE
        padding = CANVAS_GRID_PADDING

        for idx, (_, _, after_path) in enumerate(self.triplets):
            row = idx // images_per_row
            col = idx % images_per_row

            x = col * (thumbnail_size[0] + padding) + padding
            y = row * (thumbnail_size[1] + padding * 2) + padding

            key = get_match_key(after_path)
            if key:
                img = DraggableImage(self.canvas, x, y, after_path, key, thumbnail_size)
                self.draggable_images.append(img)
        
        # Update row visualization
        self.rows = self.detect_rows()
        self.update_row_visualization()
        
        # Update canvas scroll region
        self.canvas.update_idletasks()
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def detect_rows(self):
        """Automatically detect rows based on y-position overlap"""
        if not self.draggable_images:
            return {}

        # Sort images by y position
        sorted_images = sorted(self.draggable_images, key=lambda img: img.get_position()[1])
        
        # Initialize rows
        rows = {}
        next_row_id = 0
        
        # Process each image
        for img in sorted_images:
            x, y = img.get_position()
            img_height = img.thumbnail_size[1]
            
            # Check if image overlaps with any existing row
            found_row = False
            for row_id, row_images in rows.items():
                # Check if this image's vertical range overlaps with any image in the row
                row_y = row_images[0].get_position()[1]  # Use first image in row as reference
                if abs(y - row_y) < img_height * 0.5:  # 50% overlap threshold
                    rows[row_id].append(img)
                    img.row_id = row_id
                    found_row = True
                    break
            
            # If no overlapping row found, create new row
            if not found_row:
                rows[next_row_id] = [img]
                img.row_id = next_row_id
                next_row_id += 1
        
        return rows

    # endregion

    # region Events

    def on_select_start(self, event):
        """Start selection rectangle"""
        # Only start selection if not clicking on an image
        if not self.canvas.find_withtag(tk.CURRENT):
            self.selection_start = (self.canvas.canvasx(event.x), self.canvas.canvasy(event.y))
            if self.selection_rect:
                self.canvas.delete(self.selection_rect)
            self.selection_rect = self.canvas.create_rectangle(
                self.selection_start[0], self.selection_start[1],
                self.selection_start[0], self.selection_start[1],
                outline=WHITE, dash=(4, 4))
    
    def on_select_drag(self, event):
        """Update selection rectangle"""
        if self.selection_start:
            curx = self.canvas.canvasx(event.x)
            cury = self.canvas.canvasy(event.y)
            self.canvas.coords(self.selection_rect,
                            self.selection_start[0], self.selection_start[1],
                            curx, cury)
    
    def on_select_end(self, event):
        """Process selection"""
        if self.selection_start and self.selection_rect:
            x1, y1 = self.selection_start
            x2 = self.canvas.canvasx(event.x)
            y2 = self.canvas.canvasy(event.y)
            
            # Update row visualization
            self.rows = self.detect_rows()
            self.update_row_visualization()
            
            self.canvas.delete(self.selection_rect)
            self.selection_rect = None
            self.selection_start = None
    
    def update_row_visualization(self):
        """Update row visualization"""
        # Clear existing row visualization
        self.canvas.delete("row_box")
        
        # Group images by row
        row_groups = {}
        for img in self.draggable_images:
            if img.row_id is not None:
                if img.row_id not in row_groups:
                    row_groups[img.row_id] = []
                row_groups[img.row_id].append(img)
        
        # Draw row boxes
        for row_id, images in row_groups.items():
            if not images:
                continue
            
            # Calculate row bounds
            positions = [img.get_position() for img in images]
            thumb_w, thumb_h = CANVAS_THUMBNAIL_SIZE
            box_pad = CANVAS_ROW_BOX_PAD
            min_x = min(pos[0] for pos in positions) - box_pad
            max_x = max(pos[0] for pos in positions) + thumb_w + box_pad
            min_y = min(pos[1] for pos in positions) - box_pad
            max_y = max(pos[1] for pos in positions) + thumb_h + CANVAS_ROW_TEXT_HEIGHT + box_pad
            
            # Draw row box
            color = ROW_COLORS[row_id % len(ROW_COLORS)]
            box = self.canvas.create_rectangle(
                min_x, min_y, max_x, max_y,
                fill=color, outline=WHITE, width=2,
                tags=("row_box",)
            )
            self.canvas.tag_lower(box)
    
    def on_arrangement_changed(self, event):
        """Handle arrangement changes by updating row detection"""
        self.rows = self.detect_rows()
        self.update_row_visualization()
    
    def update_stats_labels(self, stats):
        """Update the stats labels with current counts"""
        self.target_stats_label.config(text=f"Found: {stats['after_count']} assets")
        if stats['before_count'] > 0:
            self.before_stats_label.config(text=f"Matches: {stats['matched_count']}/{stats['before_count']}")
        else:
            self.before_stats_label.config(text="")

    # endregion

    # region Generate

    def generate_composite_config(self):
        """Generate composite configuration from current arrangement"""
        config = {"rows": []}
        
        # Group images by row
        rows = self.detect_rows()
        
        # Process each row
        for row_id, images in rows.items():
            # Sort images in row by x position
            sorted_images = sorted(images, key=lambda img: img.get_position()[0])
            
            # Create row configuration
            row_config = {
                "items": [
                    {
                        "match_key": format_match_key(img.match_key),  # Format match key for JSON
                        "order_index": idx
                    }
                    for idx, img in enumerate(sorted_images)
                ]
            }
            
            # Add row to config
            config["rows"].append(row_config)
        
        return config

    def collect_config(self):
        """Collect current UI inputs into a dictionary, storing paths relative to this script."""
        base_dir = self.script_dir
        return {
            "target_dir": _rel_path(self.target_entry.get().strip(), base_dir),
            "use_custom_before": self.use_custom_before.get(),
            "custom_before_dir": _rel_path(self.custom_before_entry.get().strip(), base_dir),
            "use_alternate_before": self.use_alternate_before.get(),
            "alternate_before_dir": _rel_path(self.alt_before_entry.get().strip(), base_dir),
            "include_originals": self.include_originals.get(),
            "png_only": self.use_png_only.get(),
            "swap_source": self.swap_source.get(),
            "matte_black": self.matte_black_var.get(),
            "outer_padding": self.outer_padding_var.get(),
            "scale2x": self.scale2x_var.get(),
            "alternate_spacing": self.alternate_spacing_var.get(),
            "vertical_padding": self.vert_pad_var.get(),
            "horizontal_padding": self.horiz_pad_var.get(),
            "outer_pad_amount": self.outer_pad_amt_var.get(),
        }

    def save_arrangement(self):
        """Save current arrangement to JSON file"""
        if not self.draggable_images:
            messagebox.showerror("Error", "No images to save")
            return
        
        target = self.target_entry.get().strip()
        if not target:
            messagebox.showerror("Error", "No target folder specified")
            return
        
        # Generate configuration
        config = self.generate_composite_config()
        
        # Save to JSON file
        json_path = os.path.join(target, "composite.json")
        try:
            with open(json_path, 'w') as f:
                json.dump(config, f, indent=2)
            print(f"DEBUG: Saved arrangement to {json_path}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save arrangement: {str(e)}")

        save_config(self.collect_config())

    def generate_composite(self):
        """Generate composite image from current arrangement"""
        if not self.triplets:
            messagebox.showerror("Error", "No images to arrange")
            return
        config = self.generate_composite_config()
        target = self.target_entry.get().strip()
        custom_before = self.custom_before_entry.get().strip() if self.use_custom_before.get() else None
        alt_before = self.alt_before_entry.get().strip() if self.use_alternate_before.get() else None
        include_originals = self.include_originals.get()

        # Get paddings from UI
        try:
            vertical_padding = int(self.vert_pad_var.get())
        except Exception:
            vertical_padding = VERTICAL_PADDING
        try:
            horizontal_padding = int(self.horiz_pad_var.get())
        except Exception:
            horizontal_padding = HORIZONTAL_PADDING

        # If originals are excluded and paddings are still at default, use smaller paddings
        if not include_originals:
            if self.vert_pad_var.get() == str(VERTICAL_PADDING):
                vertical_padding = 4
                self.vert_pad_var.set(str(vertical_padding))
            if self.horiz_pad_var.get() == str(HORIZONTAL_PADDING):
                horizontal_padding = 8
                self.horiz_pad_var.set(str(horizontal_padding))

        # --- Pass new output options to process_target_folder ---
        matte_black = self.matte_black_var.get()
        outer_padding = self.outer_padding_var.get()
        scale2x = self.scale2x_var.get()
        try:
            outer_pad_amt = int(self.outer_pad_amt_var.get())
        except Exception:
            outer_pad_amt = DEFAULT_OUTER_PAD
        alternate_spacing = self.alternate_spacing_var.get()

        png_only = self.use_png_only.get()
        swap_source = self.swap_source.get()
        output = process_target_folder(
            target, custom_before, alt_before, composite_config=config, include_originals=include_originals,
            vertical_padding=vertical_padding, horizontal_padding=horizontal_padding,
            matte_black=matte_black, outer_padding=outer_padding, scale2x=scale2x, outer_pad_amt=outer_pad_amt,
            alternate_spacing=alternate_spacing, png_only=png_only,
            swap_source=swap_source
        )
        if output:
            messagebox.showinfo("Success", f"Composite image created at:\n{output}")
        else:
            messagebox.showerror("Error", "Failed to create composite image")

        save_config(self.collect_config())

    # endregion

# endregion


# region MAIN

def main():
    root = tk.Tk()
    app = CompositeArrangementUI(root)
    root.mainloop()

# endregion

if __name__ == "__main__":
    main()

# endregion
