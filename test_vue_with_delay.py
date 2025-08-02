#!/usr/bin/env python3
"""
Test Vue support with more aggressive file opening and delays
"""

import json
import logging
import time
from pathlib import Path

from serena.constants import SERENA_MANAGED_DIR_IN_HOME
from solidlsp.ls import SolidLanguageServer
from solidlsp.ls_config import Language, LanguageServerConfig
from solidlsp.ls_logger import LanguageServerLogger
from solidlsp.settings import SolidLSPSettings


def test_vue_with_delays():
    repo_path = "/Users/trjordan/repos/canary/frontend/authorizations"
    
    print(f"🔍 Testing Vue support with delays for: {repo_path}")
    
    logger = LanguageServerLogger(log_level=logging.ERROR)
    
    config = LanguageServerConfig(
        code_language=Language.TYPESCRIPT,
        ignored_paths=["node_modules", "dist", "coverage"],
        trace_lsp_communication=False
    )
    
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
        # Give it time to initialize
        print("⏳ Waiting for initialization...")
        time.sleep(3)
        
        # Open multiple Vue files to ensure they're indexed
        vue_files = [
            "src/App.vue",
            "src/components/AuthorizationForm.vue",
            "src/components/Authorization.vue"
        ]
        
        print("\n📄 Opening Vue files to ensure they're indexed...")
        for vue_file in vue_files:
            try:
                with ls.open_file(vue_file) as buffer:
                    print(f"   ✅ Opened {vue_file}")
                    time.sleep(1)  # Give time between files
            except Exception as e:
                print(f"   ❌ Failed to open {vue_file}: {e}")
        
        # Also open the TypeScript file
        print("\n📄 Opening store.ts...")
        with ls.open_file("src/store.ts") as buffer:
            print("   ✅ Opened store.ts")
        
        # Give more time for indexing
        print("\n⏳ Waiting for indexing to complete...")
        time.sleep(5)
        
        # Now try to find references
        print("\n🔍 Searching for authStore references...")
        
        # Get symbols from store.ts
        symbols = ls.request_document_symbols("src/store.ts")
        auth_store_symbol = None
        
        for sym in symbols[0]:
            if sym.get("name") == "authStore":
                auth_store_symbol = sym
                print(f"✅ Found authStore at line {auth_store_symbol['location']['range']['start']['line'] + 1}")
                break
        
        if auth_store_symbol:
            # Try multiple times with delays
            for attempt in range(3):
                print(f"\n🔄 Attempt {attempt + 1} to find references...")
                
                sel_start = auth_store_symbol["selectionRange"]["start"]
                refs = ls.request_references("src/store.ts", sel_start["line"], sel_start["character"])
                
                print(f"📍 Found {len(refs)} references:")
                
                files_found = set()
                for ref in refs:
                    path = ref.get("relativePath", "")
                    line = ref["range"]["start"]["line"] + 1
                    print(f"   - {path}:{line}")
                    files_found.add(path)
                
                if any(".vue" in f for f in files_found):
                    print("\n🎉 SUCCESS! Found references in Vue files!")
                    break
                elif attempt < 2:
                    print("\n⏳ No Vue references yet, waiting before retry...")
                    time.sleep(3)
        
        # Also try searching from a Vue file
        print("\n🔍 Trying reverse search from App.vue...")
        try:
            # Search for authStore text in App.vue
            app_symbols = ls.request_document_symbols("src/App.vue")
            print(f"   Found {len(app_symbols)} symbols in App.vue")
        except Exception as e:
            print(f"   ❌ Error getting symbols from App.vue: {e}")
            
    finally:
        ls.stop()
        print("\n✅ Stopped language server")


if __name__ == "__main__":
    test_vue_with_delays()