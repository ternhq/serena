import os

import pytest

from solidlsp import SolidLanguageServer
from solidlsp.ls_config import Language
from solidlsp.ls_utils import SymbolUtils


@pytest.mark.typescript
@pytest.mark.vue
class TestVueLanguageServer:
    @pytest.mark.parametrize("language_server", [Language.TYPESCRIPT], indirect=True)
    def test_find_symbol_in_vue_files(self, language_server: SolidLanguageServer) -> None:
        """Test that symbols can be found in Vue files"""
        symbols = language_server.request_full_symbol_tree()
        
        # Check that we can find symbols from both .ts and .vue files
        assert SymbolUtils.symbol_tree_contains_name(symbols, "authStore"), "authStore not found in symbol tree"
        assert SymbolUtils.symbol_tree_contains_name(symbols, "App"), "App component not found in symbol tree"
        # Note: The TypeScript language server may not parse Vue file internals deeply
        # The important thing is that it recognizes Vue files and can find references

    @pytest.mark.parametrize("language_server", [Language.TYPESCRIPT], indirect=True)
    def test_find_references_across_vue_and_ts_files(self, language_server: SolidLanguageServer) -> None:
        """Test that references can be found across .vue and .ts files"""
        # Find references to authStore from store.ts
        file_path = "store.ts"
        symbols = language_server.request_document_symbols(file_path)
        
        # Find the authStore export
        auth_store_symbol = None
        for sym in symbols[0]:
            if sym.get("name") == "authStore":
                auth_store_symbol = sym
                break
        
        assert auth_store_symbol is not None, "Could not find 'authStore' symbol in store.ts"
        
        # Get references to authStore
        sel_start = auth_store_symbol["selectionRange"]["start"]
        refs = language_server.request_references(file_path, sel_start["line"], sel_start["character"])
        
        # Check that we find references in both store.ts and App.vue
        ref_files = {ref.get("relativePath", "") for ref in refs}
        assert "store.ts" in ref_files, "store.ts should contain references to authStore"
        assert "App.vue" in ref_files, "App.vue should contain references to authStore"

    @pytest.mark.parametrize("language_server", [Language.TYPESCRIPT], indirect=True)
    def test_vue_file_language_id(self, language_server: SolidLanguageServer) -> None:
        """Test that Vue files are opened with correct language ID"""
        # This test verifies that our override of open_file works correctly
        vue_file = "App.vue"
        ts_file = "store.ts"
        
        # Open Vue file and check it works
        with language_server.open_file(vue_file) as buffer:
            assert buffer.language_id == "vue", f"Vue file should have 'vue' language ID, got {buffer.language_id}"
            assert buffer.uri.endswith("App.vue")
        
        # Open TypeScript file and check it works
        with language_server.open_file(ts_file) as buffer:
            assert buffer.language_id == "typescript", f"TypeScript file should have 'typescript' language ID, got {buffer.language_id}"
            assert buffer.uri.endswith("store.ts")

    @pytest.mark.parametrize("language_server", [Language.TYPESCRIPT], indirect=True)
    def test_vue_file_pattern_matching(self, language_server: SolidLanguageServer) -> None:
        """Test that Vue files are recognized by the file pattern matcher"""
        # Get the file pattern matcher for TypeScript language
        matcher = Language.TYPESCRIPT.get_source_fn_matcher()
        
        # Test various file extensions
        assert matcher.is_relevant_filename("test.vue"), "Vue files should be recognized"
        assert matcher.is_relevant_filename("test.ts"), "TypeScript files should be recognized"
        assert matcher.is_relevant_filename("test.tsx"), "TSX files should be recognized"
        assert matcher.is_relevant_filename("test.js"), "JavaScript files should be recognized"
        assert matcher.is_relevant_filename("test.jsx"), "JSX files should be recognized"
        assert not matcher.is_relevant_filename("test.py"), "Python files should not be recognized"