"""
PSD to DDS export with manifest .json for Marvel Heroes Omega mod

"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import tkinter as tk
from tkinter import messagebox, filedialog
from datetime import datetime

USE_PSD = True

def process_folder(folder_path, texconv_path, author, log_func):
    if USE_PSD:
        try:
            from psd_tools import PSDImage
            from psd_tools.constants import ColorMode
            from PIL import Image
        except ImportError:
            log_func("ERROR: psd_tools and Pillow are required for PSD mode.")
            return False

    source_ext = ".psd" if USE_PSD else ".png"
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

        if USE_PSD:
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

        log_func(f"  Converting {source_name} -> {dds_name}")
        try:
            subprocess.run(
                [texconv_path, "-f", "DXT5", "-bc", "x", "-y", "-o", folder_path, input_path],
                check=True,
                capture_output=True
            )
        except subprocess.CalledProcessError as e:
            log_func(f"    ERROR converting {source_name}: {e}")
            if USE_PSD:
                os.remove(input_path)
            continue

        produced_name = os.path.splitext(os.path.basename(input_path))[0] + ".dds"
        produced_path = os.path.join(folder_path, produced_name)
        if produced_path != dds_path:
            os.replace(produced_path, dds_path)

        if USE_PSD:
            os.remove(input_path)

        any_new = True
        replacements.append({
            "TextureName": base_name,
            "DdsFileName": dds_name
        })

    if not any_new:
        log_func(f"  No changes detected, skipping manifest.")
        return False

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

    log_func(f"  Wrote manifest.json with {len(replacements)} texture replacement(s).")
    return True


def run_export(texconv_entry, author_entry, export_target_var, target_entry, log_text, root):
    texconv_path = texconv_entry.get().strip()
    author = author_entry.get().strip()

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
    item_dir = os.path.join(script_dir, "..", "ITEMS")
    item_dir = os.path.normpath(item_dir)

    if not os.path.isdir(item_dir):
        messagebox.showerror("Error", f"ITEM directory not found:\n{item_dir}")
        return

    log(f"Processing folders in: {item_dir}")
    processed = 0
    for entry in os.listdir(item_dir):
        folder_path = os.path.join(item_dir, entry)
        if os.path.isdir(folder_path):
            log(f"Processing folder: {entry}")
            if process_folder(folder_path, texconv_path, author, log):
                processed += 1
                if do_export_target and target_base:
                    target_folder = os.path.join(target_base, entry)
                    try:
                        os.makedirs(target_folder, exist_ok=True)
                        for f in os.listdir(folder_path):
                            if f.lower().endswith(".dds") or f.lower() == "manifest.json":
                                shutil.copy2(os.path.join(folder_path, f), target_folder)
                        log(f"  Copied to: {target_folder}")
                    except Exception as e:
                        log(f"  ERROR copying to target: {e}")

    log(f"\nDone. Processed {processed} folder(s).")
    messagebox.showinfo("Export Complete", f"Processed {processed} folder(s).")


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


def main():
    BG = "#2b2b2b"
    FG = "#cccccc"
    ENTRY_BG = "#000000"
    BTN_BG = "#4a4a4a"

    root = tk.Tk()
    root.title("PSD to DDS mod Manifest Batch Exporter")
    root.geometry("650x550")
    root.minsize(550, 450)
    root.configure(bg=BG)

    frame = tk.Frame(root, padx=10, pady=10, bg=BG)
    frame.pack(fill=tk.X)

    tk.Label(frame, text="texconv.exe Path:", bg=BG, fg=FG).grid(row=0, column=0, sticky=tk.W, pady=2)
    texconv_entry = tk.Entry(frame, width=50, bg=ENTRY_BG, fg=FG, insertbackground=FG)
    texconv_entry.grid(row=0, column=1, sticky=tk.EW, pady=2, padx=5)
    texconv_entry.insert(0, "c:/tools/texconv.exe")
    tk.Button(
        frame, text="Browse...", bg=BTN_BG, fg=FG, activebackground="#555555", activeforeground="#ffffff",
        command=lambda: browse_texconv(texconv_entry)
    ).grid(row=0, column=2, pady=2)

    tk.Label(frame, text="Author:", bg=BG, fg=FG).grid(row=1, column=0, sticky=tk.W, pady=2)
    author_entry = tk.Entry(frame, width=50, bg=ENTRY_BG, fg=FG, insertbackground=FG)
    author_entry.grid(row=1, column=1, sticky=tk.EW, pady=2, padx=5)
    author_entry.insert(0, "CorvaeOboro")

    export_target_var = tk.BooleanVar(value=True)
    tk.Checkbutton(
        frame, text="Also export to target folder", variable=export_target_var,
        bg=BG, fg=FG, selectcolor=ENTRY_BG, activebackground=BG, activeforeground=FG
    ).grid(row=2, column=0, columnspan=3, sticky=tk.W, pady=2)

    tk.Label(frame, text="Target Folder:", bg=BG, fg=FG).grid(row=3, column=0, sticky=tk.W, pady=2)
    target_entry = tk.Entry(frame, width=50, bg=ENTRY_BG, fg=FG, insertbackground=FG)
    target_entry.grid(row=3, column=1, sticky=tk.EW, pady=2, padx=5)
    target_entry.insert(0, r"D:\GAMES\MarvelHeroesOmega\MHModManager_v1_0_1\data\mods")
    tk.Button(
        frame, text="Browse...", bg=BTN_BG, fg=FG, activebackground="#555555", activeforeground="#ffffff",
        command=lambda: browse_target_folder(target_entry)
    ).grid(row=3, column=2, pady=2)

    frame.columnconfigure(1, weight=1)

    btn = tk.Button(
        root,
        text="EXPORT_DDS",
        bg=BTN_BG,
        fg="#ffffff",
        font=("Segoe UI", 12, "bold"),
        padx=20,
        pady=10,
        activebackground="#555555",
        activeforeground="#ffffff",
        command=lambda: run_export(texconv_entry, author_entry, export_target_var, target_entry, log_text, root)
    )
    btn.pack(pady=10)

    log_label = tk.Label(root, text="Log:", anchor=tk.W, bg=BG, fg=FG)
    log_label.pack(fill=tk.X, padx=10)

    log_text = tk.Text(root, wrap=tk.WORD, height=15, bg=ENTRY_BG, fg=FG, insertbackground=FG)
    log_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

    scrollbar = tk.Scrollbar(log_text, bg=BTN_BG, troughcolor=BG)
    scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
    log_text.config(yscrollcommand=scrollbar.set)
    scrollbar.config(command=log_text.yview)

    root.mainloop()


if __name__ == "__main__":
    main()
