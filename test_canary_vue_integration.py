#!/usr/bin/env python3
"""
Standalone test script to demonstrate Vue support working with the actual canary repository.
This tests that serena's TypeScript language server can find references to authStore
between the real canary files:
- /Users/trjordan/repos/canary/frontend/authorizations/src/store.ts
- /Users/trjordan/repos/canary/frontend/authorizations/src/App.vue
"""

import logging
import time
from pathlib import Path

from serena.constants import SERENA_MANAGED_DIR_IN_HOME
from solidlsp.ls import SolidLanguageServer
from solidlsp.ls_config import Language, LanguageServerConfig
from solidlsp.ls_logger import LanguageServerLogger
from solidlsp.settings import SolidLSPSettings


def test_canary_vue_references():
    """Test finding authStore references between store.ts and App.vue in the canary repo"""
    
    # Path to the canary frontend authorizations directory
    repo_path = "/Users/trjordan/repos/canary/frontend/authorizations"
    
    print(f"🔍 Testing Vue support with canary repository at: {repo_path}")
    print(f"   Looking for authStore references between:")
    print(f"   - src/store.ts")
    print(f"   - src/App.vue")
    print()
    
    # Check if Vue plugin is available
    vue_plugin_path = Path(repo_path).parent / "node_modules" / "@vue" / "typescript-plugin"
    if vue_plugin_path.exists():
        print(f"✅ Found @vue/typescript-plugin at: {vue_plugin_path}")
    else:
        print(f"❌ @vue/typescript-plugin not found at: {vue_plugin_path}")
        print("   Make sure to run 'pnpm install' in the frontend directory")
    
    # Create TypeScript language server
    config = LanguageServerConfig(
        code_language=Language.TYPESCRIPT,
        ignored_paths=["node_modules", "dist", "coverage"],
        trace_lsp_communication=False
    )
    logger = LanguageServerLogger(log_level=logging.ERROR)
    
    ls = SolidLanguageServer.create(
        config,
        logger,
        repo_path,
        solidlsp_settings=SolidLSPSettings(solidlsp_dir=SERENA_MANAGED_DIR_IN_HOME)
    )
    
    print("✅ Created TypeScript language server")
    
    # Start the language server
    ls.start()
    print("✅ Started language server")
    
    try:
        # Open both files and keep them open during the reference search
        print("\n🧪 Opening both files...")
        with ls.open_file("src/store.ts") as store_buffer, ls.open_file("src/App.vue") as vue_buffer:
            print(f"   - Opened store.ts with language ID: '{store_buffer.language_id}'")
            print(f"   - Opened App.vue with language ID: '{vue_buffer.language_id}'")
            
            if vue_buffer.language_id == "vue":
                print("   ✅ Vue file opened with correct language ID")
            else:
                print(f"   ❌ Vue file opened with incorrect language ID: {vue_buffer.language_id}")
            
            # Give the language server time to process both files
            print("   ⏳ Waiting for language server to process files...")
            time.sleep(3)
            
            # Find the authStore symbol in store.ts
            print("\n📄 Looking for authStore in src/store.ts...")
            symbols = ls.request_document_symbols("src/store.ts")
            
            # Find authStore export (should be around line 22)
            auth_store_symbol = None
            for sym in symbols[0]:
                if sym.get("name") == "authStore":
                    auth_store_symbol = sym
                    break
            
            if auth_store_symbol:
                line = auth_store_symbol['location']['range']['start']['line'] + 1
                print(f"✅ Found authStore symbol at line {line}")
            else:
                print("❌ Could not find authStore symbol")
                return
            
            # Get references to authStore
            print("\n🔍 Finding all references to authStore...")
            sel_start = auth_store_symbol["selectionRange"]["start"]
            refs = ls.request_references("src/store.ts", sel_start["line"], sel_start["character"])
            
            print(f"📍 Found {len(refs)} references total\n")
            
            # Group references by file
            refs_by_file = {}
            for ref in refs:
                file_path = ref.get("relativePath", "")
                if file_path not in refs_by_file:
                    refs_by_file[file_path] = []
                refs_by_file[file_path].append(ref)
            
            # Show references in each file
            for file_path, file_refs in sorted(refs_by_file.items()):
                print(f"📄 In {file_path}:")
                for ref in sorted(file_refs, key=lambda r: r["range"]["start"]["line"]):
                    line = ref["range"]["start"]["line"] + 1
                    col = ref["range"]["start"]["character"] + 1
                    print(f"   Line {line}, Column {col}")
                print()
            
            # Verify we found references in both files
            has_store_refs = "src/store.ts" in refs_by_file
            has_vue_refs = "src/App.vue" in refs_by_file
            
            print("✅ Summary:")
            print(f"   - Found references in store.ts: {'YES' if has_store_refs else 'NO'}")
            print(f"   - Found references in App.vue: {'YES' if has_vue_refs else 'NO'}")
            
            if has_store_refs and has_vue_refs:
                store_count = len(refs_by_file["src/store.ts"])
                vue_count = len(refs_by_file["src/App.vue"])
                print(f"\n🎉 SUCCESS! The TypeScript language server found {store_count} references in store.ts and {vue_count} references in App.vue!")
                print("   This confirms that Vue support is working correctly with the canary repository.")
            else:
                print("\n❌ Failed to find references in both files")
            
    finally:
        ls.stop()
        print("\n✅ Stopped language server")


if __name__ == "__main__":
    test_canary_vue_references()