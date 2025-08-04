#!/usr/bin/env python3
"""
Test Vue file reference support in TypeScript language server
"""

import os
import time
import unittest
from pathlib import Path

from solidlsp.ls import SolidLanguageServer
from solidlsp.ls_config import Language, LanguageServerConfig
from solidlsp.ls_logger import LanguageServerLogger
from solidlsp.settings import SolidLSPSettings


class TestVueReferences(unittest.TestCase):
    """Test that TypeScript language server can find references in Vue files"""
    
    @classmethod
    def setUpClass(cls):
        """Set up test environment"""
        cls.repo_path = "/Users/trjordan/repos/canary/frontend/authorizations"
        
        # Check if test repository exists
        if not os.path.exists(cls.repo_path):
            raise unittest.SkipTest(f"Test repository not found at {cls.repo_path}")
        
        # Check if Vue plugin is available
        vue_plugin_path = "/Users/trjordan/repos/canary/frontend/node_modules/@vue/typescript-plugin"
        if not os.path.exists(vue_plugin_path):
            raise unittest.SkipTest(f"Vue TypeScript plugin not found at {vue_plugin_path}")
    
    def setUp(self):
        """Create language server for each test"""
        self.logger = LanguageServerLogger(log_level=20)  # INFO level
        
        config = LanguageServerConfig(
            code_language=Language.TYPESCRIPT,
            ignored_paths=["node_modules", "dist", "coverage"],
            trace_lsp_communication=False
        )
        
        settings = SolidLSPSettings(solidlsp_dir=os.path.expanduser("~/.serena/solidlsp"))
        
        self.ls = SolidLanguageServer.create(
            config,
            self.logger,
            self.repo_path,
            solidlsp_settings=settings
        )
        
        self.ls.start()
        # Give time for initialization
        time.sleep(5)
    
    def tearDown(self):
        """Clean up after each test"""
        if hasattr(self, 'ls'):
            self.ls.stop()
    
    def test_vue_plugin_detected(self):
        """Test that Vue plugin is detected"""
        # TypeScript language server should have detected Vue plugin
        self.assertTrue(hasattr(self.ls, '_vue_plugin_path'))
        self.assertIsNotNone(self.ls._vue_plugin_path)
        self.assertIn("@vue/typescript-plugin", self.ls._vue_plugin_path)
    
    def test_open_vue_file(self):
        """Test that Vue files can be opened with correct language ID"""
        with self.ls.open_file("src/App.vue") as buffer:
            self.assertEqual(buffer.language_id, "vue")
    
    def test_find_references_across_ts_and_vue_files(self):
        """Test finding references to authStore across TypeScript and Vue files"""
        # Find authStore symbol in store.ts
        symbols = self.ls.request_document_symbols("src/store.ts")
        self.assertIsNotNone(symbols)
        self.assertGreater(len(symbols[0]), 0)
        
        auth_store_symbol = None
        for sym in symbols[0]:
            if sym.get("name") == "authStore":
                auth_store_symbol = sym
                break
        
        self.assertIsNotNone(auth_store_symbol, "authStore symbol not found in store.ts")
        
        # Request references
        sel_start = auth_store_symbol["selectionRange"]["start"]
        refs = self.ls.request_references("src/store.ts", sel_start["line"], sel_start["character"])
        
        # Verify we found references
        self.assertGreater(len(refs), 0, "No references found")
        
        # Separate Vue and TypeScript references
        vue_refs = [ref for ref in refs if ref["relativePath"].endswith('.vue')]
        ts_refs = [ref for ref in refs if ref["relativePath"].endswith('.ts')]
        
        # We should find at least one reference in TypeScript files
        self.assertGreater(len(ts_refs), 0, "No TypeScript references found")
        
        # We should find references in Vue files (App.vue uses authStore)
        self.assertGreater(len(vue_refs), 0, "No Vue references found")
        
        # Verify specific expected references
        app_vue_refs = [ref for ref in vue_refs if ref["relativePath"] == "src/App.vue"]
        self.assertGreater(len(app_vue_refs), 0, "No references found in App.vue")
    
    def test_reference_locations_are_accurate(self):
        """Test that reference locations point to actual usage"""
        # Request references for authStore
        refs = self.ls.request_references("src/store.ts", 21, 13)  # Line 22, char 14 (0-based)
        
        # Find App.vue references
        app_vue_refs = [ref for ref in refs if ref["relativePath"] == "src/App.vue"]
        
        # We expect at least the import statement reference
        import_refs = [ref for ref in app_vue_refs if ref["range"]["start"]["line"] == 8]  # Line 9 (0-based)
        self.assertEqual(len(import_refs), 1, "Import statement reference not found at expected line")


if __name__ == "__main__":
    unittest.main()