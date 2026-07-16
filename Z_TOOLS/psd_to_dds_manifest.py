"""
PSD/PNG to DDS export with manifest .json for Marvel Heroes Omega mod

"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import tkinter as tk
from tkinter import messagebox, filedialog, ttk
from datetime import datetime

CONFIG_FILE = "psd_to_dds_manifest_config.json"

def process_folder(folder_path, texconv_path, author, use_psd, dds_format,
                   bc_quality, dds_header, generate_mipmaps, premultiply_alpha, log_func):
    if use_psd:
        try:
            from psd_tools import PSDImage
            from psd_tools.constants import ColorMode
            from PIL import Image
        except ImportError:
            log_func("ERROR: psd_tools and Pillow are required for PSD mode.")
            return False

    source_ext = ".psd" if use_psd else ".png"
    source_files = [f for f in os.listdir(folder_path) if f.lower().endswith(source_ext)]
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


def run_export(texconv_entry, author_entry, source_mode_var, export_target_var,
               target_entry, folder_vars, dds_format_var, bc_quality_var,
               dds_header_var, mipmaps_var, pmalpha_var, total_replace_var,
               log_text, root, config=None):
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

    if config is not None:
        config.update({
            "texconv_path": texconv_path,
            "author": author,
            "source_mode": "psd" if use_psd else "png",
            "target_folder": target_entry.get().strip(),
            "export_target": do_export_target,
            "total_replace": total_replace_var.get(),
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
        f"Mipmaps: {'on' if generate_mipmaps else 'off'}  |  Premultiply alpha: {'on' if premultiply_alpha else 'off'}")
    processed = 0
    for entry in selected_folders:
        folder_path = os.path.join(item_dir, entry)
        if os.path.isdir(folder_path):
            log(f"Processing folder: {entry}")
            if process_folder(folder_path, texconv_path, author, use_psd, dds_format,
                              bc_quality, dds_header, generate_mipmaps, premultiply_alpha, log):
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
            folder_frame, text="No mod folders found in ITEM.", bg="#333333", fg="#cccccc"
        ).pack(fill=tk.X, pady=5)
        return

    for name in folders:
        var = tk.BooleanVar(value=True)
        folder_vars[name] = var
        cb = tk.Checkbutton(
            folder_frame, text=name, variable=var,
            bg="#333333", fg="#cccccc", selectcolor="#000000",
            activebackground="#333333", activeforeground="#cccccc",
            anchor=tk.W
        )
        cb.pack(fill=tk.X, padx=5, pady=1)


def set_all_folders(folder_vars, value):
    for var in folder_vars.values():
        var.set(value)


def main():
    BG = "#2b2b2b"
    FG = "#cccccc"
    ENTRY_BG = "#000000"
    BTN_BG = "#4a4a4a"
    LIST_BG = "#333333"

    config = load_config()

    root = tk.Tk()
    root.title("PSD/PNG to DDS mod Manifest Batch Exporter")
    root.geometry("700x800")
    root.minsize(600, 650)
    root.configure(bg=BG)

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure(
        "BlackDropdown.TCombobox",
        fieldbackground="#000000",
        background="#000000",
        foreground="#cccccc",
        arrowcolor="#cccccc",
    )
    style.map(
        "BlackDropdown.TCombobox",
        fieldbackground=[("readonly", "#000000"), ("active", "#000000")],
        selectbackground=[("readonly", "#333333")],
        selectforeground=[("readonly", "#cccccc")],
    )

    top_btn_frame = tk.Frame(root, bg=BG)
    top_btn_frame.pack(fill=tk.X, padx=10, pady=(10, 5))

    frame = tk.Frame(root, padx=10, pady=10, bg=BG)
    frame.pack(fill=tk.X)

    tk.Label(frame, text="texconv.exe Path:", bg=BG, fg=FG).grid(row=0, column=0, sticky=tk.W, pady=2)
    texconv_entry = tk.Entry(frame, width=50, bg=ENTRY_BG, fg=FG, insertbackground=FG)
    texconv_entry.grid(row=0, column=1, sticky=tk.EW, pady=2, padx=5)
    texconv_entry.insert(0, config.get("texconv_path", "c:/tools/texconv.exe"))
    tk.Button(
        frame, text="Browse...", bg=BTN_BG, fg=FG, activebackground="#555555", activeforeground="#ffffff",
        command=lambda: browse_texconv(texconv_entry)
    ).grid(row=0, column=2, pady=2)

    tk.Label(frame, text="Author:", bg=BG, fg=FG).grid(row=1, column=0, sticky=tk.W, pady=2)
    author_entry = tk.Entry(frame, width=50, bg=ENTRY_BG, fg=FG, insertbackground=FG)
    author_entry.grid(row=1, column=1, sticky=tk.EW, pady=2, padx=5)
    author_entry.insert(0, config.get("author", "CorvaeOboro"))

    source_mode_var = tk.StringVar(value=config.get("source_mode", "psd"))
    tk.Label(frame, text="Source Format:", bg=BG, fg=FG).grid(row=2, column=0, sticky=tk.W, pady=2)
    mode_frame = tk.Frame(frame, bg=BG)
    mode_frame.grid(row=2, column=1, columnspan=2, sticky=tk.W, pady=2)
    tk.Radiobutton(
        mode_frame, text="PSD", variable=source_mode_var, value="psd",
        bg=BG, fg=FG, selectcolor=ENTRY_BG, activebackground=BG, activeforeground=FG
    ).pack(side=tk.LEFT, padx=(0, 10))
    tk.Radiobutton(
        mode_frame, text="PNG", variable=source_mode_var, value="png",
        bg=BG, fg=FG, selectcolor=ENTRY_BG, activebackground=BG, activeforeground=FG
    ).pack(side=tk.LEFT)

    dds_settings_frame = tk.LabelFrame(frame, text="DDS Settings", bg=BG, fg=FG, padx=5, pady=5)
    dds_settings_frame.grid(row=3, column=0, columnspan=3, sticky=tk.EW, pady=5)
    dds_settings_frame.columnconfigure((0, 2, 4), weight=1)

    dds_format_var = tk.StringVar(value=config.get("dds_format", "DXT5"))
    tk.Label(dds_settings_frame, text="DDS Format:", bg=BG, fg=FG).grid(row=0, column=0, sticky=tk.W, padx=5)
    dds_format_combo = ttk.Combobox(dds_settings_frame, textvariable=dds_format_var,
                                    values=["DXT5", "BC3_UNORM", "BC7_UNORM", "BC7_UNORM_SRGB", "R8G8B8A8_UNORM"],
                                    state="readonly", width=14, style="BlackDropdown.TCombobox")
    dds_format_combo.grid(row=0, column=1, sticky=tk.EW, padx=5)

    bc_quality_var = tk.StringVar(value=config.get("bc_quality", "max"))
    tk.Label(dds_settings_frame, text="BC Quality:", bg=BG, fg=FG).grid(row=0, column=2, sticky=tk.W, padx=5)
    bc_quality_combo = ttk.Combobox(dds_settings_frame, textvariable=bc_quality_var,
                                    values=["normal", "max", "quick"],
                                    state="readonly", width=10, style="BlackDropdown.TCombobox")
    bc_quality_combo.grid(row=0, column=3, sticky=tk.EW, padx=5)

    dds_header_var = tk.StringVar(value=config.get("dds_header", "Default"))
    tk.Label(dds_settings_frame, text="DDS Header:", bg=BG, fg=FG).grid(row=0, column=4, sticky=tk.W, padx=5)
    dds_header_combo = ttk.Combobox(dds_settings_frame, textvariable=dds_header_var,
                                    values=["Default", "Force DX9", "Force DX10"],
                                    state="readonly", width=12, style="BlackDropdown.TCombobox")
    dds_header_combo.grid(row=0, column=5, sticky=tk.EW, padx=5)

    mipmaps_var = tk.BooleanVar(value=config.get("generate_mipmaps", False))
    pmalpha_var = tk.BooleanVar(value=config.get("premultiply_alpha", False))

    format_checks_frame = tk.Frame(frame, bg=BG)
    format_checks_frame.grid(row=4, column=0, columnspan=3, sticky=tk.W, pady=2)
    tk.Checkbutton(
        format_checks_frame, text="Generate mipmaps", variable=mipmaps_var,
        bg=BG, fg=FG, selectcolor=ENTRY_BG, activebackground=BG, activeforeground=FG
    ).pack(side=tk.LEFT, padx=(0, 20))
    tk.Checkbutton(
        format_checks_frame, text="Premultiply alpha", variable=pmalpha_var,
        bg=BG, fg=FG, selectcolor=ENTRY_BG, activebackground=BG, activeforeground=FG
    ).pack(side=tk.LEFT)

    export_target_var = tk.BooleanVar(value=config.get("export_target", True))
    total_replace_var = tk.BooleanVar(value=config.get("total_replace", False))

    target_checks_frame = tk.Frame(frame, bg=BG)
    target_checks_frame.grid(row=5, column=0, columnspan=3, sticky=tk.W, pady=2)
    tk.Checkbutton(
        target_checks_frame, text="Also export to target folder", variable=export_target_var,
        bg=BG, fg=FG, selectcolor=ENTRY_BG, activebackground=BG, activeforeground=FG
    ).pack(side=tk.LEFT, padx=(0, 20))
    tk.Checkbutton(
        target_checks_frame, text="Total replace on refresh", variable=total_replace_var,
        bg=BG, fg=FG, selectcolor=ENTRY_BG, activebackground=BG, activeforeground=FG
    ).pack(side=tk.LEFT)

    tk.Label(frame, text="Target Folder:", bg=BG, fg=FG).grid(row=6, column=0, sticky=tk.W, pady=2)
    target_entry = tk.Entry(frame, width=50, bg=ENTRY_BG, fg=FG, insertbackground=FG)
    target_entry.grid(row=6, column=1, sticky=tk.EW, pady=2, padx=5)
    target_entry.insert(0, config.get("target_folder", r"D:\GAMES\MarvelHeroesOmega\MHModManager_v1_0_1\data\mods"))
    tk.Button(
        frame, text="Browse...", bg=BTN_BG, fg=FG, activebackground="#555555", activeforeground="#ffffff",
        command=lambda: browse_target_folder(target_entry)
    ).grid(row=6, column=2, pady=2)

    frame.columnconfigure(1, weight=1)

    folder_section = tk.LabelFrame(root, text="Detected Mod Folders", bg=BG, fg=FG, padx=10, pady=10)
    folder_section.pack(fill=tk.BOTH, expand=False, padx=10, pady=5)

    folder_controls = tk.Frame(folder_section, bg=BG)
    folder_controls.pack(fill=tk.X, pady=(0, 5))

    item_dir = get_item_dir()
    folder_vars = {}

    folder_canvas = tk.Canvas(folder_section, bg=LIST_BG, highlightthickness=0)
    folder_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    scrollbar = tk.Scrollbar(folder_section, command=folder_canvas.yview, bg=BTN_BG)
    scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
    folder_canvas.configure(yscrollcommand=scrollbar.set)
    folder_frame = tk.Frame(folder_canvas, bg=LIST_BG)
    folder_canvas_window = folder_canvas.create_window((0, 0), window=folder_frame, anchor=tk.NW)

    def on_canvas_configure(event):
        folder_canvas.itemconfig(folder_canvas_window, width=event.width)
    folder_canvas.bind("<Configure>", on_canvas_configure)

    def update_scrollregion(event=None):
        folder_canvas.configure(scrollregion=folder_canvas.bbox("all"))
    folder_frame.bind("<Configure>", update_scrollregion)

    tk.Button(
        folder_controls, text="Select All", bg=BTN_BG, fg=FG,
        command=lambda: set_all_folders(folder_vars, True)
    ).pack(side=tk.LEFT, padx=5)
    tk.Button(
        folder_controls, text="Select None", bg=BTN_BG, fg=FG,
        command=lambda: set_all_folders(folder_vars, False)
    ).pack(side=tk.LEFT, padx=5)
    tk.Button(
        folder_controls, text="Refresh", bg=BTN_BG, fg=FG,
        command=lambda: refresh_folder_list(folder_frame, folder_vars, item_dir)
    ).pack(side=tk.LEFT, padx=5)

    refresh_folder_list(folder_frame, folder_vars, item_dir)

    EXPORT_BG = "#4a6b4a"
    EXPORT_ACTIVE = "#5c855c"
    REFRESH_BG = "#5c4a6b"
    REFRESH_ACTIVE = "#6e5a7d"

    btn = tk.Button(
        top_btn_frame,
        text="EXPORT DDS",
        bg=EXPORT_BG,
        fg="#e0e0e0",
        font=("Segoe UI", 12, "bold"),
        padx=20,
        pady=10,
        activebackground=EXPORT_ACTIVE,
        activeforeground="#ffffff",
        command=lambda: run_export(texconv_entry, author_entry, source_mode_var, export_target_var,
                                   target_entry, folder_vars, dds_format_var, bc_quality_var,
                                   dds_header_var, mipmaps_var, pmalpha_var, total_replace_var,
                                   log_text, root, config)
    )
    btn.pack(side=tk.LEFT, padx=5)

    refresh_btn = tk.Button(
        top_btn_frame,
        text="REFRESH TARGET",
        bg=REFRESH_BG,
        fg="#e0e0e0",
        font=("Segoe UI", 12, "bold"),
        padx=20,
        pady=10,
        activebackground=REFRESH_ACTIVE,
        activeforeground="#ffffff",
        command=lambda: run_refresh(author_entry, target_entry, folder_vars, total_replace_var, log_text, root, config)
    )
    refresh_btn.pack(side=tk.LEFT, padx=5)

    log_label = tk.Label(root, text="Log:", anchor=tk.W, bg=BG, fg=FG)
    log_label.pack(fill=tk.X, padx=10)

    log_text = tk.Text(root, wrap=tk.WORD, height=12, bg=ENTRY_BG, fg=FG, insertbackground=FG)
    log_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

    log_scrollbar = tk.Scrollbar(log_text, bg=BTN_BG, troughcolor=BG)
    log_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
    log_text.config(yscrollcommand=log_scrollbar.set)
    log_scrollbar.config(command=log_text.yview)

    root.mainloop()


if __name__ == "__main__":
    main()
