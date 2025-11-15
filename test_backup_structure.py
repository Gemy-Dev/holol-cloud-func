#!/usr/bin/env python3
"""
Test script to validate backup ZIP structure locally.
This simulates what the Cloud Function does when you upload a backup.

Usage:
    python test_backup_structure.py path/to/your/backup.zip
"""

import sys
import os
import zipfile
import tempfile
import shutil


def _safe_extract_zip(zip_file: zipfile.ZipFile, extract_dir: str):
    """Safely extract zip ensuring no path traversal."""
    base_path = os.path.abspath(extract_dir)
    for member in zip_file.namelist():
        member_path = os.path.abspath(os.path.join(base_path, member))
        if not member_path.startswith(base_path):
            raise ValueError(f"Illegal path detected in archive entry: {member}")
    zip_file.extractall(extract_dir)


def _find_export_root(extracted_dir: str):
    """Locate Firestore export root directory (contains overall_export_metadata)."""
    for root, _, files in os.walk(extracted_dir):
        if "overall_export_metadata" in files:
            return root
    return None


def _validate_and_prepare_backup_structure(extracted_dir: str):
    """
    Validate and prepare backup structure for upload.
    Handles multiple archive formats:
    1. Files at root level (overall_export_metadata in extracted_dir)
    2. Single wrapper folder (backup_YYYYMMDD_HHMMSS/overall_export_metadata)
    3. Nested structure (any subdirectory containing overall_export_metadata)
    
    Returns the export root directory or None if invalid.
    """
    print(f"🔍 Analyzing extracted directory: {extracted_dir}")
    
    # List what's in the root
    try:
        root_contents = os.listdir(extracted_dir)
        print(f"📂 Root contains {len(root_contents)} items:")
        for item in root_contents[:10]:  # Show first 10 items
            item_path = os.path.join(extracted_dir, item)
            item_type = "dir" if os.path.isdir(item_path) else "file"
            print(f"  - {item} ({item_type})")
    except Exception as e:
        print(f"❌ Error listing directory: {e}")
        return None
    
    # Strategy 1: Check root level
    if "overall_export_metadata" in root_contents:
        print(f"✓ Found Firestore export at root level: {extracted_dir}")
        return extracted_dir
    
    # Strategy 2: If root contains exactly one item and it's a directory, check inside it
    # This handles the common case of a single wrapper folder
    if len(root_contents) == 1:
        single_item = root_contents[0]
        single_item_path = os.path.join(extracted_dir, single_item)
        if os.path.isdir(single_item_path):
            print(f"  🔍 Root has single directory '{single_item}', checking inside...")
            try:
                inner_contents = os.listdir(single_item_path)
                print(f"  📂 Inside '{single_item}' contains {len(inner_contents)} items:")
                for item in inner_contents[:10]:
                    item_path = os.path.join(single_item_path, item)
                    item_type = "dir" if os.path.isdir(item_path) else "file"
                    print(f"    - {item} ({item_type})")
                
                if "overall_export_metadata" in inner_contents:
                    print(f"✓ Found Firestore export in wrapper folder: {single_item_path}")
                    return single_item_path
            except Exception as e:
                print(f"❌ Error checking single directory: {e}")
    
    # Strategy 3: Search all subdirectories recursively
    print(f"⚠️  No export at root or in single wrapper, searching all subdirectories...")
    export_root = _find_export_root(extracted_dir)
    if export_root:
        print(f"✓ Found Firestore export via recursive search: {export_root}")
        return export_root
    
    print(f"❌ No valid Firestore export found in archive")
    return None


def test_backup_file(zip_path):
    """Test a backup ZIP file."""
    if not os.path.exists(zip_path):
        print(f"❌ File not found: {zip_path}")
        return False
    
    print(f"=" * 70)
    print(f"Testing backup file: {zip_path}")
    print(f"File size: {os.path.getsize(zip_path):,} bytes")
    print(f"=" * 70)
    print()
    
    # Step 1: List ZIP contents
    print("📦 Step 1: Inspecting ZIP contents...")
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            zip_contents = zf.namelist()
            print(f"   ZIP contains {len(zip_contents)} files")
            print(f"   First 20 files in ZIP:")
            for i, name in enumerate(zip_contents[:20]):
                print(f"     {i+1}. {name}")
            
            if len(zip_contents) > 20:
                print(f"     ... and {len(zip_contents) - 20} more files")
            
            # Check if overall_export_metadata exists anywhere
            metadata_files = [f for f in zip_contents if 'overall_export_metadata' in f]
            if metadata_files:
                print(f"\n   ✓ Found 'overall_export_metadata' at:")
                for mf in metadata_files:
                    print(f"     - {mf}")
            else:
                print(f"\n   ✗ 'overall_export_metadata' NOT found in ZIP")
    except Exception as e:
        print(f"❌ Error reading ZIP: {e}")
        return False
    
    print()
    
    # Step 2: Extract and validate
    print("📦 Step 2: Extracting and validating structure...")
    with tempfile.TemporaryDirectory() as tmp_dir:
        extract_dir = os.path.join(tmp_dir, "extracted")
        os.makedirs(extract_dir, exist_ok=True)
        
        try:
            with zipfile.ZipFile(zip_path, "r") as zip_file:
                _safe_extract_zip(zip_file, extract_dir)
            
            print(f"   Extraction complete to: {extract_dir}")
            print()
            
            export_root = _validate_and_prepare_backup_structure(extract_dir)
            
            print()
            print("=" * 70)
            if export_root:
                print("✅ SUCCESS: Backup structure is valid!")
                print(f"   Export root: {export_root}")
                
                # Count files
                file_count = 0
                for root, dirs, files in os.walk(export_root):
                    file_count += len(files)
                print(f"   Total files to upload: {file_count}")
                return True
            else:
                print("❌ FAILED: Backup structure is invalid!")
                print("   The backup does not contain a valid Firestore export.")
                return False
        except Exception as e:
            print(f"❌ Error during extraction/validation: {e}")
            import traceback
            traceback.print_exc()
            return False


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python test_backup_structure.py <path_to_backup.zip>")
        print()
        print("Example:")
        print("  python test_backup_structure.py ~/Downloads/backup_20251109_231230.zip")
        sys.exit(1)
    
    zip_path = sys.argv[1]
    success = test_backup_file(zip_path)
    
    sys.exit(0 if success else 1)

