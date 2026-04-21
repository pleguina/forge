"""IP archive unpacker for topgen."""

from __future__ import annotations

import fnmatch
import shutil
import tarfile
import time
import zipfile
from pathlib import Path
from typing import List


def unpack_ip_archives(
    src_dir: Path,
    ip_root: Path,
    force: bool = False,
    verbose: bool = True
) -> List[Path]:
    """
    Extract IP archives from source directory into IP root.
    
    Args:
        src_dir: Directory containing *.zip or *.tar.* archives
        ip_root: Destination root directory (creates <ip_root>/<module_name>/)
        force: Overwrite existing directories
        verbose: Print progress messages
        
    Returns:
        List of paths to extracted IP directories
        
    Raises:
        FileNotFoundError: If src_dir doesn't exist
        ValueError: If no archives found
    """
    if not src_dir.is_dir():
        raise FileNotFoundError(f"Source folder not found: {src_dir}")
    
    ip_root.mkdir(parents=True, exist_ok=True)
    
    # Find all supported archive types
    patterns = ["*.zip", "*.ZIP", "*.Zip",
                "*.tar", "*.tar.*", "*.tgz", "*.tar.gz", "*.tar.xz"]
    archives = sorted({p for pat in patterns for p in src_dir.glob(pat)})
    
    if not archives:
        raise ValueError(f"No archive files found in {src_dir}")
    
    if verbose:
        print(f"📦 Found {len(archives)} archives in {src_dir}")
    
    extracted_paths = []
    start = time.time()
    
    for arc in archives:
        mod_name = arc.stem.split(".")[0]
        dst_mod = ip_root / mod_name
        
        if dst_mod.exists():
            if force:
                shutil.rmtree(dst_mod)
            else:
                if verbose:
                    print(f"↷  {arc.name:30}  (skip – dest exists; use force=True)")
                continue
        
        dst_mod.mkdir(parents=True)
        
        if verbose:
            print(f"📦 {arc.name:30} → {dst_mod.name}")
        
        try:
            if fnmatch.fnmatch(arc.name.lower(), "*.zip"):
                with zipfile.ZipFile(arc) as zf:
                    zf.extractall(dst_mod)
            else:
                with tarfile.open(arc) as tf:
                    tf.extractall(dst_mod)
        except Exception as e:
            if verbose:
                print(f"   ❌ failed: {e}")
            shutil.rmtree(dst_mod, ignore_errors=True)
            continue
        
        # Flatten single-directory structures
        children = list(dst_mod.iterdir())
        if len(children) == 1 and children[0].is_dir():
            wrapper = children[0]
            for item in wrapper.iterdir():
                shutil.move(str(item), dst_mod)
            wrapper.rmdir()
        
        # Verify component.xml exists
        comp_xmls = list(dst_mod.rglob("component.xml"))
        if verbose:
            status = "✅ extracted" if comp_xmls else "⚠️  component.xml not found"
            print(f"   {status}")
        
        extracted_paths.append(dst_mod)
    
    elapsed = time.time() - start
    if verbose:
        print(f"\n✨ Done in {elapsed:.1f}s. Extracted {len(extracted_paths)} IPs to {ip_root}")
    
    return extracted_paths
