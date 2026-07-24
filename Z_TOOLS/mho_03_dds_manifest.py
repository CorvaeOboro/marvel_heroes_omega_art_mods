"""
MHO_03 DDS MANIFEST - Mod Pack Export Tool for Marvel Heroes Omega

FUNCTIONALITY
  Batch-converts PSD or PNG source images into DDS texture files using texconv.exe,
  then writes a manifest.json describing texture replacements for MHModManager.
  Optionally copies the resulting DDS + manifest to a target mod folder.

  Actions:
    - EXPORT DDS     : converts source images to DDS (only if source is newer),
                       writes manifest, optionally copies to target folder
    - REFRESH TARGET : re-writes manifest from existing DDS files and copies to
                       target (no re-conversion)
    - PSD to PNG     : exports PSDs to PNGs in selected folders (only if PSD is
                       newer than existing PNG)
    - CLEAN DDS      : removes all .dds files from selected folders
    - CREATE ZIP     : creates per-folder datetime zips in 00_MODS/ containing
                       .dds + manifest.json (skips if existing zip is up-to-date)
    - CREATE ALL ZIP : creates combined ITEM_ALL zip with merged manifest including
                       textures, UPK replacements, and string mods; optionally
                       includes UI_Item_NoGlassOverlay and PowerNotReady_removal

DDS SETTINGS
  Format options  : DXT5, BC3_UNORM, BC7_UNORM, BC7_UNORM_SRGB, R8G8B8_UNORM
  BC quality      : normal, max, quick (only for BC/DXT formats)
  DDS header      : Default, Force DX9, Force DX10
  Other           : generate mipmaps, premultiply alpha
  For older importers, DXT5 + Force DX9 header is the safest.
  R8G8B8A8_UNORM is uncompressed if quality is still insufficient.

PSD MODE
  Uses psd_tools to composite PSD layers, extracts saved alpha channel if
  present (when channel count exceeds color mode channels), exports via
  temporary TGA to texconv.

MANIFEST JSON FORMAT
  {
    "Name": "<folder_name>",
    "Author": "<author>",
    "Version": "<YYYYMMDD>",
    "Replacements": [
      {"TextureName": "item_name", "DdsFileName": "item_name.dds"},
      ...
    ],
    "HasTextures": true,
    "TextureReplacementCount": <int>
  }

KEY COMPONENTS
  - process_folder()     : converts source files to DDS, writes manifest
  - write_manifest()      : writes manifest.json for a folder
  - copy_to_target()      : copies DDS + manifest to target mod folder
  - run_export()          : GUI-triggered batch export across selected folders
  - run_refresh()         : GUI-triggered manifest refresh from existing DDS
  - run_psd_to_png()      : GUI-triggered PSD to PNG export (skip if up-to-date)
  - run_clean_dds()       : GUI-triggered DDS file removal from selected folders
  - run_create_zip()      : GUI-triggered per-folder zip creation in 00_MODS/
  - run_create_all_zip()  : GUI-triggered combined ALL zip with UI+SFX merge
  - main()                : standalone Tkinter GUI (sections: Settings, Folders, Actions, Log)

CONFIG
  Settings file : mho_03_dds_manifest_config.json

QUICK USAGE
  python Z_TOOLS/mho_03_dds_manifest.py

TOOLSGROUP::PIPELINE
SORTGROUP::2
SORTPRIORITY::1
STATUS::working
VERSION::20260721
"""

# region Imports

import json
import os
import shutil
import subprocess
import sys
import tempfile
import tkinter as tk
import zipfile
from tkinter import messagebox, filedialog, ttk
from datetime import datetime

CONFIG_FILE = "mho_03_dds_manifest_config.json"

# endregion


# region Core Pipeline

def process_folder(folder_path, texconv_path, author, use_psd, dds_format,
                   bc_quality, dds_header, generate_mipmaps, premultiply_alpha,
                   ignore_00_prefix, log_func):
    if use_psd:
        try:
            from psd_tools import PSDImage
            from psd_tools.constants import ColorMode
            from PIL import Image
        except ImportError:
            log_func("ERROR: psd_tools and Pillow are required for PSD mode.")
            return False

    source_ext = ".psd" if use_psd else ".png"
    source_files = [f for f in os.listdir(folder_path)
                    if f.lower().endswith(source_ext) and not (ignore_00_prefix and f.startswith("00_"))]
    source_files.sort()

    if not source_files:
        log_func(f"  No {source_ext} files found in {folder_path}, skipping.")
        return False

    replacements = []
    any_new = False
    for source_name in source_files:
        base_name = os.path.splitext(source_name)[0]
        dds_name = base_name + ".dds"
        source_path = os.path.join(folder_path, source_name)
        dds_path = os.path.join(folder_path, dds_name)

        needs_export = True
        if os.path.isfile(dds_path):
            psd_mtime = os.path.getmtime(source_path)
            dds_mtime = os.path.getmtime(dds_path)
            if dds_mtime >= psd_mtime:
                needs_export = False
                log_func(f"  Skipping {source_name} (up to date)")

        if not needs_export:
            replacements.append({
                "TextureName": base_name,
                "DdsFileName": dds_name
            })
            continue

        if use_psd:
            log_func(f"  Compositing {source_name} -> temp TGA (RGBA)")
            psd = PSDImage.open(source_path)
            composite = psd.composite(force=True).convert("RGB")
            width, height = composite.size

            alpha_img = None
            try:
                header = psd._record.header
                color_channels = {
                    ColorMode.BITMAP: 1,
                    ColorMode.GRAYSCALE: 1,
                    ColorMode.INDEXED: 1,
                    ColorMode.RGB: 3,
                    ColorMode.CMYK: 4,
                    ColorMode.MULTICHANNEL: 1,
                    ColorMode.DUOTONE: 1,
                    ColorMode.LAB: 3,
                }.get(header.color_mode, 3)
                if header.channels > color_channels and header.depth == 8:
                    channel_bytes = psd._record.image_data.get_data(header, split=True)
                    alpha_img = Image.frombytes("L", (width, height), channel_bytes[-1])
            except Exception as e:
                log_func(f"    WARNING: could not read saved alpha channel ({e}); exporting opaque.")

            if alpha_img is not None:
                composite.putalpha(alpha_img)
            else:
                composite = composite.convert("RGBA")

            tmp_fd, input_path = tempfile.mkstemp(suffix=".tga")
            os.close(tmp_fd)
            composite.save(input_path)
        else:
            input_path = source_path

        log_func(f"  Converting {source_name} -> {dds_name} ({dds_format})")
        try:
            args = [texconv_path, "-nologo", "-y", "-f", dds_format, "-o", folder_path]
            if dds_format.upper().startswith(("BC", "DXT")):
                if bc_quality == "max":
                    args.extend(["-bc", "x"])
                elif bc_quality == "quick":
                    args.extend(["-bc", "q"])
            if dds_header == "Force DX9":
                args.append("-dx9")
            elif dds_header == "Force DX10":
                args.append("-dx10")
            if generate_mipmaps:
                args.extend(["-m", "0"])
            if premultiply_alpha:
                args.append("-pmalpha")
            args.append(input_path)

            subprocess.run(args, check=True, capture_output=True)
        except subprocess.CalledProcessError as e:
            log_func(f"    ERROR converting {source_name}: {e}")
            if e.stdout:
                log_func(f"    texconv stdout: {e.stdout.decode(errors='replace').strip()}")
            if e.stderr:
                log_func(f"    texconv stderr: {e.stderr.decode(errors='replace').strip()}")
            if use_psd:
                os.remove(input_path)
            continue

        produced_name = os.path.splitext(os.path.basename(input_path))[0] + ".dds"
        produced_path = os.path.join(folder_path, produced_name)
        if produced_path != dds_path:
            os.replace(produced_path, dds_path)

        if use_psd:
            os.remove(input_path)

        any_new = True
        replacements.append({
            "TextureName": base_name,
            "DdsFileName": dds_name
        })

    if not any_new:
        log_func(f"  No changes detected, skipping manifest.")
        return False

    write_manifest(folder_path, author, replacements)
    log_func(f"  Wrote manifest.json with {len(replacements)} texture replacement(s).")
    return True


# endregion


# region Manifest & Target

def write_manifest(folder_path, author, replacements):
    folder_name = os.path.basename(folder_path)
    version = datetime.now().strftime("%Y%m%d")
    manifest = {
        "Name": folder_name,
        "Author": author,
        "Version": version,
        "Replacements": replacements,
        "AchievementReplacements": [],
        "StoreReplacements": [],
        "Languages": [],
        "UpkReplacements": [],
        "AudioPacks": [],
        "HasTextures": True,
        "TextureReplacementCount": len(replacements)
    }

    manifest_path = os.path.join(folder_path, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)


def copy_to_target(folder_path, target_base, entry, log_func):
    target_folder = os.path.join(target_base, entry)
    try:
        os.makedirs(target_folder, exist_ok=True)
        for f in os.listdir(folder_path):
            if f.lower().endswith(".dds") or f.lower() == "manifest.json":
                shutil.copy2(os.path.join(folder_path, f), target_folder)
        log_func(f"  Copied to: {target_folder}")
    except Exception as e:
        log_func(f"  ERROR copying to target: {e}")


# endregion


# region Export Operations

def run_export(texconv_entry, author_entry, source_mode_var, export_target_var,
               target_entry, folder_vars, dds_format_var, bc_quality_var,
               dds_header_var, mipmaps_var, pmalpha_var, ignore_00_var,
               total_replace_var, log_text, root, config=None):
    texconv_path = texconv_entry.get().strip()
    author = author_entry.get().strip()
    use_psd = source_mode_var.get() == "psd"

    if not texconv_path:
        messagebox.showerror("Missing Input", "Please provide the path to texconv.exe.")
        return
    if not os.path.isfile(texconv_path):
        messagebox.showerror("Invalid Path", f"texconv.exe not found at:\n{texconv_path}")
        return

    do_export_target = export_target_var.get()
    target_base = target_entry.get().strip() if do_export_target else ""
    if do_export_target and not target_base:
        messagebox.showerror("Missing Input", "Please provide the target export folder path.")
        return

    log_text.delete("1.0", tk.END)
    def log(msg):
        log_text.insert(tk.END, msg + "\n")
        log_text.see(tk.END)
        root.update_idletasks()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    item_dir = os.path.join(script_dir, "..", "ITEM")
    item_dir = os.path.normpath(item_dir)

    if not os.path.isdir(item_dir):
        messagebox.showerror("Error", f"ITEM directory not found:\n{item_dir}")
        return

    selected_folders = [name for name, var in folder_vars.items() if var.get()]
    if not selected_folders:
        messagebox.showwarning("No Folders Selected", "Please select at least one mod folder to process.")
        return

    dds_format = dds_format_var.get()
    bc_quality = bc_quality_var.get()
    dds_header = dds_header_var.get()
    generate_mipmaps = mipmaps_var.get()
    premultiply_alpha = pmalpha_var.get()
    ignore_00_prefix = ignore_00_var.get()

    if config is not None:
        config.update({
            "texconv_path": texconv_path,
            "author": author,
            "source_mode": "psd" if use_psd else "png",
            "target_folder": target_entry.get().strip(),
            "export_target": do_export_target,
            "total_replace": total_replace_var.get(),
            "ignore_00_prefix": ignore_00_prefix,
            "dds_format": dds_format,
            "bc_quality": bc_quality,
            "dds_header": dds_header,
            "generate_mipmaps": generate_mipmaps,
            "premultiply_alpha": premultiply_alpha,
        })
        save_config(config)

    log(f"Processing folders in: {item_dir}")
    log(f"Source mode: {'PSD' if use_psd else 'PNG'}")
    log(f"DDS format: {dds_format}  |  BC quality: {bc_quality}  |  "
        f"Header: {dds_header}  |  "
        f"Mipmaps: {'on' if generate_mipmaps else 'off'}  |  "
        f"Premultiply alpha: {'on' if premultiply_alpha else 'off'}  |  "
        f"Ignore 00_: {'on' if ignore_00_prefix else 'off'}")
    processed = 0
    for entry in selected_folders:
        folder_path = os.path.join(item_dir, entry)
        if os.path.isdir(folder_path):
            log(f"Processing folder: {entry}")
            if process_folder(folder_path, texconv_path, author, use_psd, dds_format,
                              bc_quality, dds_header, generate_mipmaps, premultiply_alpha,
                              ignore_00_prefix, log):
                processed += 1
                if do_export_target and target_base:
                    copy_to_target(folder_path, target_base, entry, log)

    log(f"\nDone. Processed {processed} folder(s).")


def run_refresh(author_entry, target_entry, folder_vars, total_replace_var, log_text, root, config=None):
    author = author_entry.get().strip()
    target_base = target_entry.get().strip()
    if not target_base:
        messagebox.showerror("Missing Input", "Please provide the target export folder path.")
        return

    log_text.delete("1.0", tk.END)
    def log(msg):
        log_text.insert(tk.END, msg + "\n")
        log_text.see(tk.END)
        root.update_idletasks()

    item_dir = get_item_dir()
    if not os.path.isdir(item_dir):
        messagebox.showerror("Error", f"ITEM directory not found:\n{item_dir}")
        return

    selected_folders = [name for name, var in folder_vars.items() if var.get()]
    if not selected_folders:
        messagebox.showwarning("No Folders Selected", "Please select at least one mod folder to refresh.")
        return

    log("Refreshing target folder(s)...")
    processed = 0
    for entry in selected_folders:
        folder_path = os.path.join(item_dir, entry)
        if not os.path.isdir(folder_path):
            continue
        log(f"Refreshing folder: {entry}")

        dds_files = [f for f in os.listdir(folder_path) if f.lower().endswith(".dds")]
        dds_files.sort()
        if not dds_files:
            log("  No DDS files found, skipping.")
            continue

        replacements = []
        for dds_name in dds_files:
            base_name = os.path.splitext(dds_name)[0]
            replacements.append({
                "TextureName": base_name,
                "DdsFileName": dds_name
            })

        write_manifest(folder_path, author, replacements)
        log(f"  Wrote manifest.json with {len(replacements)} texture replacement(s).")

        target_folder = os.path.join(target_base, entry)
        if total_replace_var.get() and os.path.isdir(target_folder):
            try:
                shutil.rmtree(target_folder)
                log(f"  Removed existing target folder: {target_folder}")
            except Exception as e:
                log(f"  ERROR removing target folder: {e}")

        copy_to_target(folder_path, target_base, entry, log)
        processed += 1

    if config is not None:
        config.update({
            "author": author,
            "target_folder": target_base,
            "total_replace": total_replace_var.get(),
        })
        save_config(config)

    log(f"\nDone. Refreshed {processed} folder(s).")


def run_psd_to_png(folder_vars, ignore_00_var, log_text, root):
    log_text.delete("1.0", tk.END)
    def log(msg):
        log_text.insert(tk.END, msg + "\n")
        log_text.see(tk.END)
        root.update_idletasks()

    try:
        from psd_tools import PSDImage
        from psd_tools.constants import ColorMode
        from PIL import Image
    except ImportError:
        messagebox.showerror("Missing Dependency", "psd_tools and Pillow are required for PSD to PNG export.")
        return

    item_dir = get_item_dir()
    if not os.path.isdir(item_dir):
        messagebox.showerror("Error", f"ITEM directory not found:\n{item_dir}")
        return

    selected_folders = [name for name, var in folder_vars.items() if var.get()]
    if not selected_folders:
        messagebox.showwarning("No Folders Selected", "Please select at least one mod folder.")
        return

    ignore_00 = ignore_00_var.get()
    log(f"PSD to PNG export — folders: {', '.join(selected_folders)}")
    total_exported = 0
    total_skipped = 0

    for entry in selected_folders:
        folder_path = os.path.join(item_dir, entry)
        if not os.path.isdir(folder_path):
            continue
        log(f"Folder: {entry}")
        psd_files = [f for f in os.listdir(folder_path)
                     if f.lower().endswith(".psd") and not (ignore_00 and f.startswith("00_"))]
        psd_files.sort()

        if not psd_files:
            log("  No PSD files found, skipping.")
            continue

        for psd_name in psd_files:
            base_name = os.path.splitext(psd_name)[0]
            psd_path = os.path.join(folder_path, psd_name)
            png_path = os.path.join(folder_path, base_name + ".png")

            if os.path.isfile(png_path):
                psd_mtime = os.path.getmtime(psd_path)
                png_mtime = os.path.getmtime(png_path)
                if png_mtime >= psd_mtime:
                    total_skipped += 1
                    log(f"  Skipping {psd_name} (PNG up to date)")
                    continue

            log(f"  Exporting {psd_name} -> {base_name}.png")
            try:
                psd = PSDImage.open(psd_path)
                composite = psd.composite(force=True).convert("RGB")
                width, height = composite.size

                alpha_img = None
                try:
                    header = psd._record.header
                    color_channels = {
                        ColorMode.BITMAP: 1, ColorMode.GRAYSCALE: 1,
                        ColorMode.INDEXED: 1, ColorMode.RGB: 3,
                        ColorMode.CMYK: 4, ColorMode.MULTICHANNEL: 1,
                        ColorMode.DUOTONE: 1, ColorMode.LAB: 3,
                    }.get(header.color_mode, 3)
                    if header.channels > color_channels and header.depth == 8:
                        channel_bytes = psd._record.image_data.get_data(header, split=True)
                        alpha_img = Image.frombytes("L", (width, height), channel_bytes[-1])
                except Exception:
                    pass

                if alpha_img is not None:
                    composite.putalpha(alpha_img)
                else:
                    composite = composite.convert("RGBA")

                composite.save(png_path)
                total_exported += 1
            except Exception as e:
                log(f"    ERROR exporting {psd_name}: {e}")

    log(f"\nDone. Exported {total_exported} PNG(s), skipped {total_skipped} (up to date).")


def run_clean_dds(folder_vars, log_text, root):
    log_text.delete("1.0", tk.END)
    def log(msg):
        log_text.insert(tk.END, msg + "\n")
        log_text.see(tk.END)
        root.update_idletasks()

    item_dir = get_item_dir()
    if not os.path.isdir(item_dir):
        messagebox.showerror("Error", f"ITEM directory not found:\n{item_dir}")
        return

    selected_folders = [name for name, var in folder_vars.items() if var.get()]
    if not selected_folders:
        messagebox.showwarning("No Folders Selected", "Please select at least one mod folder.")
        return

    if not messagebox.askyesno("Confirm Clean DDS",
                               f"Remove all .dds files from {len(selected_folders)} selected folder(s)?"):
        return

    log(f"Cleaning DDS files — folders: {', '.join(selected_folders)}")
    total_removed = 0

    for entry in selected_folders:
        folder_path = os.path.join(item_dir, entry)
        if not os.path.isdir(folder_path):
            continue
        dds_files = [f for f in os.listdir(folder_path) if f.lower().endswith(".dds")]
        if not dds_files:
            log(f"  {entry}: no DDS files found.")
            continue
        for dds_name in dds_files:
            dds_path = os.path.join(folder_path, dds_name)
            try:
                os.remove(dds_path)
                total_removed += 1
            except Exception as e:
                log(f"    ERROR removing {dds_name}: {e}")
        log(f"  {entry}: removed {len(dds_files)} DDS file(s).")

    log(f"\nDone. Removed {total_removed} DDS file(s).")


def _find_existing_zip(mods_dir, prefix):
    """Find the most recent existing zip in mods_dir matching <prefix>_YYYYMMDD.zip."""
    pattern = prefix + "_"
    best = None
    best_date = ""
    if not os.path.isdir(mods_dir):
        return None
    for f in os.listdir(mods_dir):
        if f.startswith(pattern) and f.lower().endswith(".zip"):
            date_part = f[len(pattern):-4]
            if len(date_part) == 8 and date_part.isdigit():
                if date_part > best_date:
                    best_date = date_part
                    best = f
    return os.path.join(mods_dir, best) if best else None


def _folder_has_newer_files(folder_path, zip_path):
    """Return True if any file in folder_path is newer than zip_path's mtime."""
    if not os.path.isfile(zip_path):
        return True
    zip_mtime = os.path.getmtime(zip_path)
    for f in os.listdir(folder_path):
        f_path = os.path.join(folder_path, f)
        if os.path.isfile(f_path):
            if os.path.getmtime(f_path) > zip_mtime:
                return True
    return False


def run_create_zip(folder_vars, author_entry, log_text, root):
    log_text.delete("1.0", tk.END)
    def log(msg):
        log_text.insert(tk.END, msg + "\n")
        log_text.see(tk.END)
        root.update_idletasks()

    item_dir = get_item_dir()
    if not os.path.isdir(item_dir):
        messagebox.showerror("Error", f"ITEM directory not found:\n{item_dir}")
        return

    selected_folders = [name for name, var in folder_vars.items() if var.get()]
    if not selected_folders:
        messagebox.showwarning("No Folders Selected", "Please select at least one mod folder.")
        return

    script_dir = os.path.dirname(os.path.abspath(__file__))
    mods_dir = os.path.normpath(os.path.join(script_dir, "..", "00_MODS"))
    os.makedirs(mods_dir, exist_ok=True)

    date_str = datetime.now().strftime("%Y%m%d")
    author = author_entry.get().strip() or "CorvaeOboro"
    log(f"Creating per-folder ZIPs in: {mods_dir}")
    log(f"Date stamp: {date_str}")
    zips_created = []
    zips_skipped = 0

    for entry in selected_folders:
        folder_path = os.path.join(item_dir, entry)
        if not os.path.isdir(folder_path):
            continue

        dds_files = sorted([f for f in os.listdir(folder_path) if f.lower().endswith(".dds")])
        if not dds_files:
            log(f"  {entry}: no DDS files, skipping.")
            continue

        # Check if an existing zip with a previous date suffix is up-to-date
        existing_zip = _find_existing_zip(mods_dir, entry)
        if existing_zip and not _folder_has_newer_files(folder_path, existing_zip):
            zips_skipped += 1
            log(f"  Skipping {entry} (zip up to date: {os.path.basename(existing_zip)})")
            continue

        zip_name = f"{entry}_{date_str}.zip"
        zip_path = os.path.join(mods_dir, zip_name)

        replacements = []
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for dds_name in dds_files:
                dds_path = os.path.join(folder_path, dds_name)
                arcname = f"{entry}/{dds_name}"
                zf.write(dds_path, arcname)
                base_name = os.path.splitext(dds_name)[0]
                replacements.append({"TextureName": base_name, "DdsFileName": dds_name})

            folder_manifest = {
                "Name": entry,
                "Author": author,
                "Version": date_str,
                "Replacements": replacements,
                "AchievementReplacements": [],
                "StoreReplacements": [],
                "Languages": [],
                "UpkReplacements": [],
                "AudioPacks": [],
                "HasTextures": True,
                "TextureReplacementCount": len(replacements),
            }
            zf.writestr(f"{entry}/manifest.json", json.dumps(folder_manifest, indent=2))

        zips_created.append(zip_name)
        log(f"  Created {zip_name} ({len(dds_files)} DDS files)")

    log(f"\nDone. Created {len(zips_created)} ZIP(s), skipped {zips_skipped} (up to date).")


def run_create_all_zip(folder_vars, author_entry, include_ui_sfx_var, log_text, root):
    log_text.delete("1.0", tk.END)
    def log(msg):
        log_text.insert(tk.END, msg + "\n")
        log_text.see(tk.END)
        root.update_idletasks()

    item_dir = get_item_dir()
    if not os.path.isdir(item_dir):
        messagebox.showerror("Error", f"ITEM directory not found:\n{item_dir}")
        return

    selected_folders = [name for name, var in folder_vars.items() if var.get()]
    if not selected_folders:
        messagebox.showwarning("No Folders Selected", "Please select at least one mod folder.")
        return

    script_dir = os.path.dirname(os.path.abspath(__file__))
    mods_dir = os.path.normpath(os.path.join(script_dir, "..", "00_MODS"))
    os.makedirs(mods_dir, exist_ok=True)

    date_str = datetime.now().strftime("%Y%m%d")
    author = author_entry.get().strip() or "CorvaeOboro"
    include_ui_sfx = include_ui_sfx_var.get()
    log(f"Creating ALL ZIP in: {mods_dir}")
    log(f"Date stamp: {date_str}")
    if include_ui_sfx:
        log(f"Including UI + SFX mods")

    all_replacements = []
    all_dds_files = []  # (arcname, source_path)

    # Extra mod data to merge into the ALL zip
    extra_upk_replacements = []
    extra_upk_files = []       # (arcname, source_path)
    extra_languages = []
    extra_language_files = []  # (arcname, source_path)

    if include_ui_sfx:
        # --- UI UPK mod ---
        ui_mod_dir = os.path.normpath(os.path.join(script_dir, "..", "UI", "UI_Item_NoGlassOverlay"))
        ui_manifest_path = os.path.join(ui_mod_dir, "manifest.json")
        if os.path.isfile(ui_manifest_path):
            with open(ui_manifest_path, "r", encoding="utf-8") as f:
                ui_manifest = json.load(f)
            for upk_name in ui_manifest.get("UpkReplacements", []):
                upk_path = os.path.join(ui_mod_dir, upk_name)
                if os.path.isfile(upk_path):
                    extra_upk_replacements.append(upk_name)
                    extra_upk_files.append((upk_name, upk_path))
            log(f"  UI_Item_NoGlassOverlay: {len(extra_upk_replacements)} UPK file(s)")
        else:
            log(f"  UI_Item_NoGlassOverlay: manifest.json not found, skipping.")

        # --- SFX string mod ---
        sfx_mod_dir = os.path.normpath(os.path.join(script_dir, "..", "SFX", "PowerNotReady_removal"))
        sfx_manifest_path = os.path.join(sfx_mod_dir, "manifest.json")
        if os.path.isfile(sfx_manifest_path):
            with open(sfx_manifest_path, "r", encoding="utf-8") as f:
                sfx_manifest = json.load(f)
            for lang in sfx_manifest.get("Languages", []):
                lang_path = os.path.join(sfx_mod_dir, f"{lang}.json")
                if os.path.isfile(lang_path):
                    extra_languages.append(lang)
                    extra_language_files.append((f"{lang}.json", lang_path))
            log(f"  PowerNotReady_removal: {len(extra_languages)} language file(s)")
        else:
            log(f"  PowerNotReady_removal: manifest.json not found, skipping.")

    for entry in selected_folders:
        folder_path = os.path.join(item_dir, entry)
        if not os.path.isdir(folder_path):
            continue

        dds_files = sorted([f for f in os.listdir(folder_path) if f.lower().endswith(".dds")])
        if not dds_files:
            continue

        for dds_name in dds_files:
            dds_path = os.path.join(folder_path, dds_name)
            arcname = f"{entry}/{dds_name}"
            base_name = os.path.splitext(dds_name)[0]
            all_replacements.append({"TextureName": base_name, "DdsFileName": f"{entry}/{dds_name}"})
            all_dds_files.append((arcname, dds_path))

    if not all_dds_files and not extra_upk_files and not extra_language_files:
        log("No files to include in ALL zip.")
        return

    # Check if existing ALL zip is up-to-date against all source folders + extra mods
    existing_all_zip = _find_existing_zip(mods_dir, "ITEM_ALL")
    if existing_all_zip:
        zip_mtime = os.path.getmtime(existing_all_zip)
        needs_rebuild = False
        for entry in selected_folders:
            folder_path = os.path.join(item_dir, entry)
            if os.path.isdir(folder_path) and _folder_has_newer_files(folder_path, existing_all_zip):
                needs_rebuild = True
                break
        if not needs_rebuild and include_ui_sfx:
            for extra_dir in [
                os.path.normpath(os.path.join(script_dir, "..", "UI", "UI_Item_NoGlassOverlay")),
                os.path.normpath(os.path.join(script_dir, "..", "SFX", "PowerNotReady_removal")),
            ]:
                if os.path.isdir(extra_dir) and _folder_has_newer_files(extra_dir, existing_all_zip):
                    needs_rebuild = True
                    break
        if not needs_rebuild:
            log(f"ALL zip up to date: {os.path.basename(existing_all_zip)}")
            return

    all_zip_name = f"ITEM_ALL_{date_str}.zip"
    all_zip_path = os.path.join(mods_dir, all_zip_name)

    with zipfile.ZipFile(all_zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for arcname, src_path in all_dds_files:
            zf.write(src_path, arcname)

        for arcname, src_path in extra_upk_files:
            zf.write(src_path, arcname)

        for arcname, src_path in extra_language_files:
            zf.write(src_path, arcname)

        combined_manifest = {
            "Name": "ITEM_ALL",
            "Author": author,
            "Version": date_str,
            "Replacements": all_replacements,
            "AchievementReplacements": [],
            "StoreReplacements": [],
            "Languages": extra_languages,
            "UpkReplacements": extra_upk_replacements,
            "AudioPacks": [],
            "HasTextures": len(all_replacements) > 0,
            "TextureReplacementCount": len(all_replacements),
            "HasUpkReplacements": len(extra_upk_replacements) > 0,
            "HasStrings": len(extra_languages) > 0,
        }
        zf.writestr("manifest.json", json.dumps(combined_manifest, indent=2))

    parts = []
    if all_dds_files:
        parts.append(f"{len(all_dds_files)} DDS")
    if extra_upk_files:
        parts.append(f"{len(extra_upk_files)} UPK")
    if extra_language_files:
        parts.append(f"{len(extra_language_files)} lang")
    log(f"  Created {all_zip_name} ({', '.join(parts)})")
    log("\nDone.")


# endregion


# region Utility

def browse_texconv(texconv_entry):
    path = filedialog.askopenfilename(
        title="Select texconv.exe",
        filetypes=[("Executable", "texconv.exe"), ("All Files", "*.*")]
    )
    if path:
        texconv_entry.delete(0, tk.END)
        texconv_entry.insert(0, path)


def browse_target_folder(target_entry):
    path = filedialog.askdirectory(title="Select Target Export Folder")
    if path:
        target_entry.delete(0, tk.END)
        target_entry.insert(0, path)


def get_item_dir():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    item_dir = os.path.join(script_dir, "..", "ITEM")
    return os.path.normpath(item_dir)


def load_config():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(script_dir, CONFIG_FILE)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(values):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(script_dir, CONFIG_FILE)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(values, f, indent=2)
    except Exception:
        pass


def discover_folders(item_dir):
    if not os.path.isdir(item_dir):
        return []
    return sorted([entry for entry in os.listdir(item_dir) if os.path.isdir(os.path.join(item_dir, entry))])


def refresh_folder_list(folder_frame, folder_vars, item_dir, log_func=None):
    for widget in folder_frame.winfo_children():
        widget.destroy()
    folder_vars.clear()

    folders = discover_folders(item_dir)
    if not folders:
        tk.Label(
            folder_frame, text="No mod folders found in ITEM.", bg="#1e1e1e", fg="#b0b0b0"
        ).pack(fill=tk.X, pady=5)
        return

    for name in folders:
        var = tk.BooleanVar(value=True)
        folder_vars[name] = var
        cb = tk.Checkbutton(
            folder_frame, text=name, variable=var,
            bg="#1e1e1e", fg="#b0b0b0", selectcolor="#000000",
            activebackground="#1e1e1e", activeforeground="#b0b0b0",
            anchor=tk.W
        )
        cb.pack(fill=tk.X, padx=5, pady=1)


def set_all_folders(folder_vars, value):
    for var in folder_vars.values():
        var.set(value)


# endregion


# region UI Main

def main():
    BG = "#1a1a1a"
    FG = "#b0b0b0"
    ENTRY_BG = "#000000"
    SECTION_BG = "#222222"
    LIST_BG = "#1e1e1e"
    ACCENT = "#3a5a7a"
    TITLE_FG = "#5a8ab8"
    LABEL_FG = "#888888"
    BTN_UTILITY = "#333333"
    BTN_UTILITY_ACT = "#444444"

    config = load_config()

    root = tk.Tk()
    root.title("MHO Mod Pack Export")
    root.geometry("760x860")
    root.minsize(640, 700)
    root.configure(bg=BG)

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure(
        "Dark.Vertical.TScrollbar",
        background=ACCENT, troughcolor="#000000", bordercolor="#000000",
        arrowcolor="#cccccc", relief="flat", borderwidth=0,
    )
    style.map("Dark.Vertical.TScrollbar", activebackground=[("active", "#4a6a8a")])
    style.configure(
        "BlackDropdown.TCombobox",
        fieldbackground="#000000", background="#000000",
        foreground="#cccccc", arrowcolor="#cccccc",
    )
    style.map(
        "BlackDropdown.TCombobox",
        fieldbackground=[("readonly", "#000000"), ("active", "#000000")],
        selectbackground=[("readonly", "#333333")],
        selectforeground=[("readonly", "#cccccc")],
    )

    # =================== SECTION 1: Settings ===================
    settings_frame = tk.LabelFrame(root, text="  Settings  ", bg=BG, fg=TITLE_FG,
                                   padx=12, pady=8, labelanchor=tk.NW)
    settings_frame.pack(fill=tk.X, padx=10, pady=(8, 4))
    settings_frame.columnconfigure(1, weight=1)

    tk.Label(settings_frame, text="texconv.exe:", bg=BG, fg=LABEL_FG).grid(row=0, column=0, sticky=tk.W, pady=3)
    texconv_entry = tk.Entry(settings_frame, width=50, bg=ENTRY_BG, fg=FG, insertbackground=FG)
    texconv_entry.grid(row=0, column=1, sticky=tk.EW, pady=3, padx=5)
    texconv_entry.insert(0, config.get("texconv_path", "c:/tools/texconv.exe"))
    tk.Button(
        settings_frame, text="...", bg=BTN_UTILITY, fg=FG, width=3,
        activebackground=BTN_UTILITY_ACT, activeforeground="#ffffff",
        command=lambda: browse_texconv(texconv_entry)
    ).grid(row=0, column=2, pady=3)

    tk.Label(settings_frame, text="Author:", bg=BG, fg=LABEL_FG).grid(row=1, column=0, sticky=tk.W, pady=3)
    author_entry = tk.Entry(settings_frame, width=50, bg=ENTRY_BG, fg=FG, insertbackground=FG)
    author_entry.grid(row=1, column=1, sticky=tk.EW, pady=3, padx=5)
    author_entry.insert(0, config.get("author", "CorvaeOboro"))

    source_mode_var = tk.StringVar(value=config.get("source_mode", "psd"))
    tk.Label(settings_frame, text="Source:", bg=BG, fg=LABEL_FG).grid(row=2, column=0, sticky=tk.W, pady=3)
    mode_frame = tk.Frame(settings_frame, bg=BG)
    mode_frame.grid(row=2, column=1, columnspan=2, sticky=tk.W, pady=3)
    tk.Radiobutton(
        mode_frame, text="PSD", variable=source_mode_var, value="psd",
        bg=BG, fg=FG, selectcolor=ENTRY_BG, activebackground=BG, activeforeground=FG
    ).pack(side=tk.LEFT, padx=(0, 12))
    tk.Radiobutton(
        mode_frame, text="PNG", variable=source_mode_var, value="png",
        bg=BG, fg=FG, selectcolor=ENTRY_BG, activebackground=BG, activeforeground=FG
    ).pack(side=tk.LEFT)

    # DDS Settings sub-section
    dds_frame = tk.LabelFrame(settings_frame, text="  DDS Settings  ", bg=SECTION_BG, fg=LABEL_FG,
                              padx=8, pady=6, labelanchor=tk.NW)
    dds_frame.grid(row=3, column=0, columnspan=3, sticky=tk.EW, pady=(6, 2))
    dds_frame.columnconfigure((1, 3, 5), weight=1)

    dds_format_var = tk.StringVar(value=config.get("dds_format", "DXT5"))
    tk.Label(dds_frame, text="Format:", bg=SECTION_BG, fg=LABEL_FG).grid(row=0, column=0, sticky=tk.W, padx=4)
    ttk.Combobox(dds_frame, textvariable=dds_format_var,
                 values=["DXT5", "BC3_UNORM", "BC7_UNORM", "BC7_UNORM_SRGB", "R8G8B8A8_UNORM"],
                 state="readonly", width=14, style="BlackDropdown.TCombobox").grid(row=0, column=1, sticky=tk.EW, padx=4)

    bc_quality_var = tk.StringVar(value=config.get("bc_quality", "max"))
    tk.Label(dds_frame, text="BC Quality:", bg=SECTION_BG, fg=LABEL_FG).grid(row=0, column=2, sticky=tk.W, padx=4)
    ttk.Combobox(dds_frame, textvariable=bc_quality_var,
                 values=["normal", "max", "quick"],
                 state="readonly", width=10, style="BlackDropdown.TCombobox").grid(row=0, column=3, sticky=tk.EW, padx=4)

    dds_header_var = tk.StringVar(value=config.get("dds_header", "Default"))
    tk.Label(dds_frame, text="Header:", bg=SECTION_BG, fg=LABEL_FG).grid(row=0, column=4, sticky=tk.W, padx=4)
    ttk.Combobox(dds_frame, textvariable=dds_header_var,
                 values=["Default", "Force DX9", "Force DX10"],
                 state="readonly", width=12, style="BlackDropdown.TCombobox").grid(row=0, column=5, sticky=tk.EW, padx=4)

    mipmaps_var = tk.BooleanVar(value=config.get("generate_mipmaps", False))
    pmalpha_var = tk.BooleanVar(value=config.get("premultiply_alpha", False))
    ignore_00_var = tk.BooleanVar(value=config.get("ignore_00_prefix", True))

    dds_checks = tk.Frame(dds_frame, bg=SECTION_BG)
    dds_checks.grid(row=1, column=0, columnspan=6, sticky=tk.W, pady=(4, 0))
    tk.Checkbutton(
        dds_checks, text="Mipmaps", variable=mipmaps_var,
        bg=SECTION_BG, fg=FG, selectcolor=ENTRY_BG, activebackground=SECTION_BG, activeforeground=FG
    ).pack(side=tk.LEFT, padx=(0, 16))
    tk.Checkbutton(
        dds_checks, text="Premultiply alpha", variable=pmalpha_var,
        bg=SECTION_BG, fg=FG, selectcolor=ENTRY_BG, activebackground=SECTION_BG, activeforeground=FG
    ).pack(side=tk.LEFT, padx=(0, 16))
    tk.Checkbutton(
        dds_checks, text='Ignore "00_" files', variable=ignore_00_var,
        bg=SECTION_BG, fg=FG, selectcolor=ENTRY_BG, activebackground=SECTION_BG, activeforeground=FG
    ).pack(side=tk.LEFT)

    # Target folder sub-section
    target_frame = tk.LabelFrame(settings_frame, text="  Target Folder (optional)  ", bg=SECTION_BG, fg=LABEL_FG,
                                 padx=8, pady=6, labelanchor=tk.NW)
    target_frame.grid(row=4, column=0, columnspan=3, sticky=tk.EW, pady=(4, 0))
    target_frame.columnconfigure(1, weight=1)

    export_target_var = tk.BooleanVar(value=config.get("export_target", True))
    total_replace_var = tk.BooleanVar(value=config.get("total_replace", False))

    tk.Checkbutton(
        target_frame, text="Also copy to target folder", variable=export_target_var,
        bg=SECTION_BG, fg=FG, selectcolor=ENTRY_BG, activebackground=SECTION_BG, activeforeground=FG
    ).grid(row=0, column=0, columnspan=3, sticky=tk.W, pady=(0, 4))
    tk.Checkbutton(
        target_frame, text="Total replace on refresh", variable=total_replace_var,
        bg=SECTION_BG, fg=FG, selectcolor=ENTRY_BG, activebackground=SECTION_BG, activeforeground=FG
    ).grid(row=1, column=0, columnspan=3, sticky=tk.W, pady=(0, 4))

    tk.Label(target_frame, text="Path:", bg=SECTION_BG, fg=LABEL_FG).grid(row=2, column=0, sticky=tk.W, padx=4)
    target_entry = tk.Entry(target_frame, width=50, bg=ENTRY_BG, fg=FG, insertbackground=FG)
    target_entry.grid(row=2, column=1, sticky=tk.EW, pady=2, padx=4)
    target_entry.insert(0, config.get("target_folder", r"D:\GAMES\MarvelHeroesOmega\MHModManager_v1_0_1\data\mods"))
    tk.Button(
        target_frame, text="...", bg=BTN_UTILITY, fg=FG, width=3,
        activebackground=BTN_UTILITY_ACT, activeforeground="#ffffff",
        command=lambda: browse_target_folder(target_entry)
    ).grid(row=2, column=2, pady=2)

    # =================== SECTION 2: Mod Folders ===================
    folder_section = tk.LabelFrame(root, text="  Mod Folders  ", bg=BG, fg=TITLE_FG,
                                   padx=10, pady=8, labelanchor=tk.NW)
    folder_section.pack(fill=tk.BOTH, expand=False, padx=10, pady=4)

    folder_controls = tk.Frame(folder_section, bg=BG)
    folder_controls.pack(fill=tk.X, pady=(0, 5))

    item_dir = get_item_dir()
    folder_vars = {}

    folder_canvas = tk.Canvas(folder_section, bg=LIST_BG, highlightthickness=0, height=140)
    folder_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    folder_scrollbar = ttk.Scrollbar(folder_section, orient=tk.VERTICAL, command=folder_canvas.yview,
                                     style="Dark.Vertical.TScrollbar")
    folder_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
    folder_canvas.configure(yscrollcommand=folder_scrollbar.set)
    folder_frame = tk.Frame(folder_canvas, bg=LIST_BG)
    folder_canvas_window = folder_canvas.create_window((0, 0), window=folder_frame, anchor=tk.NW)

    def on_canvas_configure(event):
        folder_canvas.itemconfig(folder_canvas_window, width=event.width)
    folder_canvas.bind("<Configure>", on_canvas_configure)

    def update_scrollregion(event=None):
        folder_canvas.configure(scrollregion=folder_canvas.bbox("all"))
    folder_frame.bind("<Configure>", update_scrollregion)

    for text, val in [("Select All", True), ("Select None", False)]:
        tk.Button(
            folder_controls, text=text, bg=BTN_UTILITY, fg=FG, padx=8, pady=2,
            activebackground=BTN_UTILITY_ACT, activeforeground="#ffffff",
            command=lambda v=val: set_all_folders(folder_vars, v)
        ).pack(side=tk.LEFT, padx=3)
    tk.Button(
        folder_controls, text="Refresh List", bg=BTN_UTILITY, fg=FG, padx=8, pady=2,
        activebackground=BTN_UTILITY_ACT, activeforeground="#ffffff",
        command=lambda: refresh_folder_list(folder_frame, folder_vars, item_dir)
    ).pack(side=tk.LEFT, padx=3)

    refresh_folder_list(folder_frame, folder_vars, item_dir)

    # =================== SECTION 3: Actions ===================
    # Button color palette
    BTN_EXPORT = "#2d5a2d"
    BTN_EXPORT_ACT = "#3a7a3a"
    BTN_REFRESH = "#4a3a6b"
    BTN_REFRESH_ACT = "#5c4a80"
    BTN_PSDPNG = "#2d4a5a"
    BTN_PSDPNG_ACT = "#3a5c70"
    BTN_CLEAN = "#5a2d2d"
    BTN_CLEAN_ACT = "#7a3a3a"
    BTN_ZIP = "#5a5a2d"
    BTN_ZIP_ACT = "#7a7a3a"
    BTN_ALLZIP = "#2d5a2d"
    BTN_ALLZIP_ACT = "#3a7a3a"

    actions_frame = tk.LabelFrame(root, text="  Actions  ", bg=BG, fg=TITLE_FG,
                                  padx=10, pady=8, labelanchor=tk.NW)
    actions_frame.pack(fill=tk.X, padx=10, pady=4)

    def _action_btn(parent, text, bg_color, active_color, cmd, font_size=10):
        return tk.Button(
            parent, text=text, bg=bg_color, fg="#e0e0e0",
            font=("Segoe UI", font_size, "bold"), padx=14, pady=6,
            activebackground=active_color, activeforeground="#ffffff",
            command=cmd
        )

    # Row 1: DDS pipeline (green / purple)
    dds_row = tk.Frame(actions_frame, bg=BG)
    dds_row.pack(fill=tk.X, pady=(0, 4))

    _action_btn(dds_row, "EXPORT DDS", BTN_EXPORT, BTN_EXPORT_ACT,
                lambda: run_export(texconv_entry, author_entry, source_mode_var, export_target_var,
                                   target_entry, folder_vars, dds_format_var, bc_quality_var,
                                   dds_header_var, mipmaps_var, pmalpha_var, ignore_00_var,
                                   total_replace_var, log_text, root, config),
                font_size=11).pack(side=tk.LEFT, padx=4)

    _action_btn(dds_row, "REFRESH TARGET", BTN_REFRESH, BTN_REFRESH_ACT,
                lambda: run_refresh(author_entry, target_entry, folder_vars, total_replace_var, log_text, root, config),
                font_size=11).pack(side=tk.LEFT, padx=4)

    # Row 2: File utilities (blue / red)
    util_row = tk.Frame(actions_frame, bg=BG)
    util_row.pack(fill=tk.X, pady=(0, 4))

    _action_btn(util_row, "PSD to PNG", BTN_PSDPNG, BTN_PSDPNG_ACT,
                lambda: run_psd_to_png(folder_vars, ignore_00_var, log_text, root)).pack(side=tk.LEFT, padx=4)

    _action_btn(util_row, "Clean DDS", BTN_CLEAN, BTN_CLEAN_ACT,
                lambda: run_clean_dds(folder_vars, log_text, root)).pack(side=tk.LEFT, padx=4)

    # Row 3: ZIP packaging (gold / green)
    zip_row = tk.Frame(actions_frame, bg=BG)
    zip_row.pack(fill=tk.X, pady=(0, 4))

    _action_btn(zip_row, "CREATE ZIP", BTN_ZIP, BTN_ZIP_ACT,
                lambda: run_create_zip(folder_vars, author_entry, log_text, root)).pack(side=tk.LEFT, padx=4)

    _action_btn(zip_row, "CREATE ALL ZIP", BTN_ALLZIP, BTN_ALLZIP_ACT,
                lambda: run_create_all_zip(folder_vars, author_entry, include_ui_sfx_var, log_text, root)).pack(side=tk.LEFT, padx=4)

    include_ui_sfx_var = tk.BooleanVar(value=config.get("include_ui_sfx_in_all", True))
    tk.Checkbutton(
        zip_row, text="Include UI + SFX", variable=include_ui_sfx_var,
        bg=BG, fg=FG, selectcolor=ENTRY_BG, activebackground=BG, activeforeground=FG
    ).pack(side=tk.LEFT, padx=8)

    # =================== SECTION 4: Log ===================
    log_frame = tk.LabelFrame(root, text="  Log  ", bg=BG, fg=TITLE_FG,
                              padx=4, pady=4, labelanchor=tk.NW)
    log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(4, 10))

    log_text = tk.Text(log_frame, wrap=tk.WORD, bg=ENTRY_BG, fg=FG, insertbackground=FG,
                       font=("Consolas", 9))
    log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    log_scrollbar = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=log_text.yview,
                                  style="Dark.Vertical.TScrollbar")
    log_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
    log_text.config(yscrollcommand=log_scrollbar.set)
    log_scrollbar.config(command=log_text.yview)

    root.mainloop()


if __name__ == "__main__":
    main()

# endregion
