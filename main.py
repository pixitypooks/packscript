#!/usr/bin/env python3
"""
Flatten ItemsAdder folders IN PLACE inside a Minecraft server directory.

Usage:
    python flatten_inplace_itemsadder.py

It detects `plugins/ItemsAdder` automatically and flattens:
 - contents/<namespace> → contents/resourcepack/assets/{models,textures,sounds}
 - data/resource_pack/assets/<namespace> → same assets
 - data/items_packs → data/ (flattened configs)

After running, you can do `/ia reload` in-game to apply changes.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

try:
    import aiofiles
    from rich.console import Console
    from rich.progress import (
        Progress,
        BarColumn,
        TextColumn,
        TimeElapsedColumn,
        TimeRemainingColumn,
    )
except ImportError:
    import subprocess

    subprocess.check_call([sys.executable, "-m", "pip", "install", "rich", "aiofiles", "-q"])
    import aiofiles
    from rich.console import Console
    from rich.progress import (
        Progress,
        BarColumn,
        TextColumn,
        TimeElapsedColumn,
        TimeRemainingColumn,
    )

console = Console()


async def async_move(src: Path, dst: Path):
    """Move a file asynchronously and ensure parent dirs exist safely."""
    loop = asyncio.get_running_loop()

    def do_move():
        # Skip if source doesn't exist (already moved)
        if not src.exists():
            return False
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.replace(dst)
        return True

    return await loop.run_in_executor(None, do_move)


def safe_name(namespace: str, name: str, dest: Path):
    """Generate unique filename with namespace prefix."""
    base, ext = os.path.splitext(name)
    new_name = f"{namespace}_{base}{ext}"
    i = 1
    while (dest / new_name).exists():
        new_name = f"{namespace}_{base}_{i}{ext}"
        i += 1
    return new_name


async def replace_in_file(file: Path, mapping: dict):
    try:
        async with aiofiles.open(file, "r", encoding="utf-8") as f:
            content = await f.read()
    except Exception:
        return False
    new_content = content
    for old, new in mapping.items():
        if old in new_content:
            new_content = new_content.replace(old, new)
    if new_content != content:
        async with aiofiles.open(file, "w", encoding="utf-8") as f:
            await f.write(new_content)
        return True
    return False


async def main():
    base_dir = Path.cwd()
    ia_dir = base_dir / "plugins" / "ItemsAdder"
    if not ia_dir.exists():
        console.print("[red]Error:[/red] Could not find plugins/ItemsAdder directory.")
        sys.exit(1)

    contents = ia_dir / "contents"
    data = ia_dir / "data"

    rp_assets = data / "resource_pack" / "assets"
    items_packs = data / "items_packs"

    models_dst = contents / "resourcepack" / "assets" / "models"
    textures_dst = contents / "resourcepack" / "assets" / "textures"
    sounds_dst = contents / "resourcepack" / "assets" / "sounds"
    configs_dst = data

    for d in [models_dst, textures_dst, sounds_dst, configs_dst]:
        d.mkdir(parents=True, exist_ok=True)

    namespaces = []
    if contents.exists():
        # Add top-level namespace directories
        namespaces += [p for p in contents.iterdir() if p.is_dir() and not p.name.startswith("_")]
        # Also scan for nested resourcepack/assets directories within namespaces
        for ns_dir in contents.iterdir():
            if ns_dir.is_dir() and not ns_dir.name.startswith("_"):
                nested_assets = ns_dir / "resourcepack" / "assets"
                if nested_assets.exists():
                    namespaces += [p for p in nested_assets.iterdir() if p.is_dir()]
    if rp_assets.exists():
        namespaces += [p for p in rp_assets.iterdir() if p.is_dir()]

    moved = {}
    special = {"sounds.json": [], "fonts.json": []}

    all_files = []
    for ns in namespaces:
        for p in ns.rglob("*"):
            if not p.is_file():
                continue
            if p.name in special:
                special[p.name].append(p)
                continue
            all_files.append((ns.name, p))

    if items_packs.exists():
        for p in items_packs.rglob("*"):
            if p.is_file():
                all_files.append(("items_packs", p))

    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeRemainingColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Flattening...", total=len(all_files))
        for ns, p in all_files:
            ext = p.suffix.lower()
            if ext == ".png":
                dest = textures_dst
            elif ext == ".mcmeta":
                # Move .mcmeta files alongside their textures/models/sounds
                # Check if it's next to a PNG (texture), OGG (sound), or JSON (model)
                parent_path = str(p.parent).lower()
                if "textures" in parent_path or p.with_suffix(".png").exists():
                    dest = textures_dst
                elif "sounds" in parent_path or p.with_suffix(".ogg").exists():
                    dest = sounds_dst
                elif "models" in parent_path:
                    dest = models_dst
                else:
                    # Default to textures if unclear
                    dest = textures_dst
            elif ext in {".yml", ".yaml"} and "items_packs" in str(p):
                dest = configs_dst
            elif ext == ".json" and "items_packs" in str(p):
                dest = configs_dst
            elif ext == ".json" and "models" in str(p).lower():
                dest = models_dst
            elif ext == ".ogg":
                dest = sounds_dst
            else:
                progress.update(task, advance=1)
                continue

            new_name = safe_name(ns, p.name, dest)
            new_path = dest / new_name
            moved_ok = await async_move(p, new_path)
            if moved_ok:
                rel = f"{ns}:{p.name}"
                moved[rel] = new_name
                moved[p.name] = new_name
            progress.update(task, advance=1)

    # merge sounds.json
    if special["sounds.json"]:
        merged = {}
        for path in special["sounds.json"]:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                merged.update(data)
            except Exception:
                pass
        out = sounds_dst / "sounds.json"
        out.write_text(json.dumps(merged, indent=2), encoding="utf-8")
        console.print(f"Merged sounds.json -> {out}")

    # merge fonts.json
    if special["fonts.json"]:
        merged = {"providers": []}
        for path in special["fonts.json"]:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if "providers" in data:
                    merged["providers"].extend(data["providers"])
            except Exception:
                pass
        out = models_dst.parent.parent / "fonts.json"
        out.write_text(json.dumps(merged, indent=2), encoding="utf-8")
        console.print(f"Merged fonts.json -> {out}")

    # update config references
    updated = 0
    cfg_files = []
    for d in [configs_dst, models_dst, textures_dst, sounds_dst]:
        for p in d.rglob("*"):
            if p.suffix.lower() in {".json", ".yml", ".yaml", ".mcmeta", ".txt"}:
                cfg_files.append(p)

    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeRemainingColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Updating configs...", total=len(cfg_files))
        for f in cfg_files:
            if await replace_in_file(f, moved):
                updated += 1
            progress.update(task, advance=1)

    console.print(f"\n[green]Done![/green] Updated {updated} config files.")
    console.print("[cyan]Done.[/cyan]")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console.print("[red]Cancelled by user.[/red]")
