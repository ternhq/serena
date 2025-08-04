import os

import pytest

from solidlsp import SolidLanguageServer
from solidlsp.ls_config import Language


@pytest.mark.typescript
@pytest.mark.vue
class TestVueDetailedReferences:
    @pytest.mark.parametrize("language_server", [Language.TYPESCRIPT], indirect=True)
    def test_authstore_references_with_details(self, language_server: SolidLanguageServer) -> None:
        """Detailed test showing all authStore references found across files"""
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
        print(f"\n✅ Found authStore symbol at line {auth_store_symbol['location']['range']['start']['line'] + 1}")
        
        # Get references to authStore
        sel_start = auth_store_symbol["selectionRange"]["start"]
        refs = language_server.request_references(file_path, sel_start["line"], sel_start["character"])
        
        print(f"\n📍 Found {len(refs)} references to authStore:")
        
        # Group references by file
        refs_by_file = {}
        for ref in refs:
            file_name = ref.get("relativePath", "")
            if file_name not in refs_by_file:
                refs_by_file[file_name] = []
            refs_by_file[file_name].append(ref)
        
        # Print detailed information about each reference
        for file_name, file_refs in refs_by_file.items():
            print(f"\n  In {file_name}:")
            for ref in file_refs:
                line = ref["range"]["start"]["line"] + 1
                col = ref["range"]["start"]["character"] + 1
                
                # Read the actual line of code for context
                try:
                    file_content = language_server._read_file(file_name)
                    lines = file_content.split('\n')
                    if line <= len(lines):
                        code_line = lines[line - 1].strip()
                        print(f"    Line {line}, Col {col}: {code_line}")
                except:
                    print(f"    Line {line}, Col {col}")
        
        # Verify we found references in both files
        assert "store.ts" in refs_by_file, "Should find references in store.ts"
        assert "App.vue" in refs_by_file, "Should find references in App.vue"
        
        # Verify specific references
        store_refs = refs_by_file["store.ts"]
        vue_refs = refs_by_file["App.vue"]
        
        print(f"\n✅ Summary:")
        print(f"  - Found {len(store_refs)} references in store.ts")
        print(f"  - Found {len(vue_refs)} references in App.vue")
        print(f"\n🎉 Cross-file references working correctly!")
        
        # The Vue file should have at least the import and some usage
        assert len(vue_refs) >= 2, f"Expected at least 2 references in App.vue (import + usage), found {len(vue_refs)}"