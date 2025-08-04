# Vue Support in Serena

This document describes the Vue support implementation in Serena, which enables the TypeScript language server to work with Vue Single File Components (SFCs).

## Overview

Vue support has been added by extending the existing TypeScript language server rather than creating a separate language type. This approach leverages the fact that Vue files contain TypeScript/JavaScript code and can be handled by the TypeScript language server with the appropriate plugin.

## Implementation Details

### 1. File Pattern Recognition

Updated `ls_config.py` to include `.vue` files in the TypeScript language file patterns:

```python
case self.TYPESCRIPT | self.TYPESCRIPT_VTS:
    # ... existing patterns ...
    # Add Vue file support
    path_patterns.append("*.vue")
    return FilenameMatcher(*path_patterns)
```

### 2. Vue Plugin Configuration

Modified `typescript_language_server.py` to:

- Automatically detect and configure the `@vue/typescript-plugin` if available in the project
- Search for the plugin in common locations (node_modules)
- Add the plugin to initialization options when starting the language server

### 3. Language ID Handling

Implemented proper language ID handling for different file types:

- `.vue` files use language ID `"vue"`
- `.js`/`.jsx` files use language ID `"javascript"`
- `.ts`/`.tsx` files use language ID `"typescript"`

This is handled by overriding the `open_file` method in the TypeScript language server.

## Usage

To use Vue support in a project:

1. Ensure `@vue/typescript-plugin` is installed in the project:
   ```bash
   npm install --save-dev @vue/typescript-plugin
   ```

2. The TypeScript language server will automatically detect and use the plugin

3. Vue files will be recognized and processed, enabling:
   - Cross-file references between `.ts` and `.vue` files
   - Symbol navigation
   - Type checking
   - Other LSP features

## Testing

Vue support is tested in `test/solidlsp/vue/test_vue_basic.py`, which verifies:

- File pattern matching for Vue files
- Correct language ID assignment
- Cross-file reference finding between TypeScript and Vue files
- Basic symbol detection

## Limitations

- The depth of Vue file parsing depends on the capabilities of the `@vue/typescript-plugin`
- Some Vue-specific features (like template syntax) may have limited support compared to pure TypeScript files

## Future Improvements

- Consider adding more Vue-specific configuration options
- Enhance template and style section support
- Add support for Vue-specific LSP features if needed