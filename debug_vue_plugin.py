#!/usr/bin/env python3
"""
Debug script to check if Vue plugin is being loaded correctly
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


def debug_vue_plugin():
    repo_path = "/Users/trjordan/repos/canary/frontend/authorizations"
    
    print(f"🔍 Debugging Vue plugin loading for: {repo_path}")
    
    # Create a more verbose logger
    logger = LanguageServerLogger(log_level=logging.DEBUG)
    
    config = LanguageServerConfig(
        code_language=Language.TYPESCRIPT,
        ignored_paths=["node_modules", "dist", "coverage"],
        trace_lsp_communication=True  # Enable LSP tracing
    )
    
    ls = SolidLanguageServer.create(
        config,
        logger,
        repo_path,
        solidlsp_settings=SolidLSPSettings(solidlsp_dir=SERENA_MANAGED_DIR_IN_HOME)
    )
    
    print("✅ Created TypeScript language server with trace enabled")
    
    # Start the language server
    ls.start()
    print("✅ Started language server")
    
    try:
        # Give it more time to initialize
        print("⏳ Waiting for full initialization...")
        time.sleep(5)
        
        # Try to open a Vue file and see what happens
        print("\n🧪 Opening App.vue...")
        with ls.open_file("src/App.vue") as buffer:
            print(f"   - Opened with URI: {buffer.uri}")
            print(f"   - Language ID: {buffer.language_id}")
            print(f"   - Content length: {len(buffer.contents)} chars")
            
        # Now try to find references
        print("\n🔍 Searching for authStore references...")
        
        # First get symbols from store.ts
        symbols = ls.request_document_symbols("src/store.ts")
        auth_store_symbol = None
        
        for sym in symbols[0]:
            if sym.get("name") == "authStore":
                auth_store_symbol = sym
                print(f"✅ Found authStore at line {auth_store_symbol['location']['range']['start']['line'] + 1}")
                break
        
        if auth_store_symbol:
            sel_start = auth_store_symbol["selectionRange"]["start"]
            refs = ls.request_references("src/store.ts", sel_start["line"], sel_start["character"])
            
            print(f"\n📍 Found {len(refs)} references:")
            for ref in refs:
                path = ref.get("relativePath", "")
                line = ref["range"]["start"]["line"] + 1
                print(f"   - {path}:{line}")
                
    finally:
        ls.stop()
        print("\n✅ Stopped language server")


if __name__ == "__main__":
    debug_vue_plugin()