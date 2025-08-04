"""
Provides TypeScript specific instantiation of the LanguageServer class. Contains various configurations and settings specific to TypeScript.
"""

import logging
import os
import pathlib
import shutil
import threading
import time
from contextlib import contextmanager
from pathlib import PurePath
from time import sleep

from overrides import override
from sensai.util.logging import LogTime

from solidlsp.ls import LSPFileBuffer, ReferenceInSymbol, SolidLanguageServer
from solidlsp.ls_config import LanguageServerConfig
from solidlsp.ls_exceptions import SolidLSPException
from solidlsp.ls_logger import LanguageServerLogger
from solidlsp.ls_types import Location as LSLocation
from solidlsp.ls_utils import FileUtils, PlatformId, PlatformUtils
from solidlsp.lsp_protocol_handler.lsp_constants import LSPConstants
from solidlsp.lsp_protocol_handler.lsp_types import InitializeParams
from solidlsp.lsp_protocol_handler.server import ProcessLaunchInfo
from solidlsp.settings import SolidLSPSettings


class ServerIndexingException(SolidLSPException):
    """Raised when the language server is still indexing and results would be incomplete."""
    pass

from .common import RuntimeDependency, RuntimeDependencyCollection

# Platform-specific imports
if os.name != "nt":  # Unix-like systems
    import pwd
else:
    # Dummy pwd module for Windows
    class pwd:
        @staticmethod
        def getpwuid(uid):
            return type("obj", (), {"pw_name": os.environ.get("USERNAME", "unknown")})()


# Conditionally import pwd module (Unix-only)
if not PlatformUtils.get_platform_id().value.startswith("win"):
    pass


class TypeScriptLanguageServer(SolidLanguageServer):
    """
    Provides TypeScript specific instantiation of the LanguageServer class. Contains various configurations and settings specific to TypeScript.
    """

    def __init__(
        self, config: LanguageServerConfig, logger: LanguageServerLogger, repository_root_path: str, solidlsp_settings: SolidLSPSettings
    ):
        """
        Creates a TypeScriptLanguageServer instance. This class is not meant to be instantiated directly. Use LanguageServer.create() instead.
        """
        ts_lsp_executable_path = self._setup_runtime_dependencies(logger, config, solidlsp_settings)
        super().__init__(
            config,
            logger,
            repository_root_path,
            ProcessLaunchInfo(cmd=ts_lsp_executable_path, cwd=repository_root_path),
            "typescript",
            solidlsp_settings,
        )
        self.server_ready = threading.Event()
        self.initialize_searcher_command_available = threading.Event()
        self._is_quiescent = threading.Event()
        self._last_quiescent_time = 0
        self._vue_plugin_path = None
        self._initial_indexing_complete = False
        self._initial_indexing_start_time = None
        self._opened_vue_files = set()  # Track which Vue files we've already opened
    
    def _get_language_id_for_file(self, file_path: str) -> str:
        """
        Get the appropriate language ID for a given file path.
        Vue files need 'vue' as language ID, others use 'typescript' or 'javascript'.
        """
        if file_path.endswith('.vue'):
            return 'vue'
        elif file_path.endswith(('.js', '.jsx', '.mjs', '.cjs')):
            return 'javascript'
        else:  # .ts, .tsx, etc.
            return 'typescript'
    
    def wait_for_quiescence(self, timeout: float = 60.0) -> bool:
        """
        Wait for the TypeScript server to become quiescent (idle).
        This is important before making requests that depend on indexing being complete.
        
        :param timeout: Maximum time to wait in seconds (increased to 60s for large projects)
        :return: True if server became quiescent, False if timeout
        """
        start_time = time.time()
        
        # First, clear the event to ensure we catch the next quiescent state
        self._is_quiescent.clear()
        
        # Wait for quiescent state
        if self._is_quiescent.wait(timeout=timeout):
            self.logger.log(f"Server became quiescent after {time.time() - start_time:.2f}s", logging.DEBUG)
            return True
        else:
            # For Vue projects, timeout is expected since TypeScript server doesn't send quiescence notifications
            if self._vue_plugin_path:
                self.logger.log(f"Quiescence timeout after {timeout}s (expected for Vue projects)", logging.DEBUG)
            else:
                self.logger.log(f"Timeout waiting for server to become quiescent after {timeout}s", logging.WARNING)
            return False
    
    @contextmanager
    def open_file(self, relative_file_path: str):
        """
        Override open_file to use the correct language ID for Vue files.
        """
        if not self.server_started:
            self.logger.log(
                "open_file called before Language Server started",
                logging.ERROR,
            )
            raise SolidLSPException("Language Server not started")

        absolute_file_path = str(PurePath(self.repository_root_path, relative_file_path))
        uri = pathlib.Path(absolute_file_path).as_uri()

        if uri in self.open_file_buffers:
            assert self.open_file_buffers[uri].uri == uri
            assert self.open_file_buffers[uri].ref_count >= 1

            self.open_file_buffers[uri].ref_count += 1
            yield self.open_file_buffers[uri]
            self.open_file_buffers[uri].ref_count -= 1
        else:
            contents = FileUtils.read_file(self.logger, absolute_file_path)

            version = 0
            # Use the correct language ID based on file extension
            language_id = self._get_language_id_for_file(relative_file_path)
            self.open_file_buffers[uri] = LSPFileBuffer(uri, contents, version, language_id, 1)

            self.server.notify.did_open_text_document(
                {
                    LSPConstants.TEXT_DOCUMENT: {
                        LSPConstants.URI: uri,
                        LSPConstants.LANGUAGE_ID: language_id,
                        LSPConstants.VERSION: 0,
                        LSPConstants.TEXT: contents,
                    }
                }
            )
            yield self.open_file_buffers[uri]
            self.open_file_buffers[uri].ref_count -= 1

        if uri in self.open_file_buffers and self.open_file_buffers[uri].ref_count == 0:
            self.server.notify.did_close_text_document(
                {
                    LSPConstants.TEXT_DOCUMENT: {
                        LSPConstants.URI: uri,
                    }
                }
            )
            del self.open_file_buffers[uri]

    @override
    def is_ignored_dirname(self, dirname: str) -> bool:
        return super().is_ignored_dirname(dirname) or dirname in [
            "node_modules",
            "dist",
            "build",
            "coverage",
        ]

    @classmethod
    def _setup_runtime_dependencies(
        cls, logger: LanguageServerLogger, config: LanguageServerConfig, solidlsp_settings: SolidLSPSettings
    ) -> list[str]:
        """
        Setup runtime dependencies for TypeScript Language Server and return the command to start the server.
        """
        platform_id = PlatformUtils.get_platform_id()

        valid_platforms = [
            PlatformId.LINUX_x64,
            PlatformId.LINUX_arm64,
            PlatformId.OSX,
            PlatformId.OSX_x64,
            PlatformId.OSX_arm64,
            PlatformId.WIN_x64,
            PlatformId.WIN_arm64,
        ]
        assert platform_id in valid_platforms, f"Platform {platform_id} is not supported for multilspy javascript/typescript at the moment"

        deps = RuntimeDependencyCollection(
            [
                RuntimeDependency(
                    id="typescript",
                    description="typescript package",
                    command=["npm", "install", "--prefix", "./", "typescript@5.5.4"],
                    platform_id="any",
                ),
                RuntimeDependency(
                    id="typescript-language-server",
                    description="typescript-language-server package",
                    command=["npm", "install", "--prefix", "./", "typescript-language-server@4.3.3"],
                    platform_id="any",
                ),
            ]
        )

        # Verify both node and npm are installed
        is_node_installed = shutil.which("node") is not None
        assert is_node_installed, "node is not installed or isn't in PATH. Please install NodeJS and try again."
        is_npm_installed = shutil.which("npm") is not None
        assert is_npm_installed, "npm is not installed or isn't in PATH. Please install npm and try again."

        # Verify both node and npm are installed
        is_node_installed = shutil.which("node") is not None
        assert is_node_installed, "node is not installed or isn't in PATH. Please install NodeJS and try again."
        is_npm_installed = shutil.which("npm") is not None
        assert is_npm_installed, "npm is not installed or isn't in PATH. Please install npm and try again."

        # Install typescript and typescript-language-server if not already installed
        tsserver_ls_dir = os.path.join(cls.ls_resources_dir(solidlsp_settings), "ts-lsp")
        tsserver_executable_path = os.path.join(tsserver_ls_dir, "node_modules", ".bin", "typescript-language-server")
        if not os.path.exists(tsserver_executable_path):
            logger.log(f"Typescript Language Server executable not found at {tsserver_executable_path}. Installing...", logging.INFO)
            with LogTime("Installation of TypeScript language server dependencies", logger=logger.logger):
                deps.install(logger, tsserver_ls_dir)

        if not os.path.exists(tsserver_executable_path):
            raise FileNotFoundError(
                f"typescript-language-server executable not found at {tsserver_executable_path}, something went wrong with the installation."
            )
        return [tsserver_executable_path, "--stdio"]

    def _should_suppress_vue_warnings(self, file_path: str) -> bool:
        """
        Check if we should suppress certain warnings for Vue files.
        """
        return self._vue_plugin_path is not None and file_path.endswith('.vue')
    
    def _get_initialize_params(self, repository_absolute_path: str) -> InitializeParams:
        """
        Returns the initialize params for the TypeScript Language Server.
        """
        root_uri = pathlib.Path(repository_absolute_path).as_uri()
        
        # Check if @vue/typescript-plugin is available
        vue_plugin_path = None
        
        # Look for Vue plugin in common locations
        potential_paths = [
            # Direct node_modules
            os.path.join(repository_absolute_path, "node_modules", "@vue", "typescript-plugin"),
            # Frontend subdirectory (common in monorepos)
            os.path.join(repository_absolute_path, "frontend", "node_modules", "@vue", "typescript-plugin"),
            # Parent directory
            os.path.join(repository_absolute_path, "..", "node_modules", "@vue", "typescript-plugin"),
            os.path.join(os.path.dirname(repository_absolute_path), "node_modules", "@vue", "typescript-plugin"),
        ]
        
        # If we detect this might be a subdirectory of a larger project, check parent paths
        if "frontend" in repository_absolute_path:
            # Extract the frontend directory path
            parts = repository_absolute_path.split(os.sep)
            for i, part in enumerate(parts):
                if part == "frontend":
                    frontend_dir = os.sep.join(parts[:i+1])
                    potential_paths.append(os.path.join(frontend_dir, "node_modules", "@vue", "typescript-plugin"))
                    break
        
        self.logger.log(f"Looking for Vue plugin in: {potential_paths}", logging.DEBUG)
        
        for path in potential_paths:
            if os.path.exists(path):
                vue_plugin_path = os.path.abspath(path)  # Use absolute path
                self._vue_plugin_path = vue_plugin_path
                self.logger.log(f"Found Vue TypeScript plugin at: {vue_plugin_path}", logging.INFO)
                break
        
        if not vue_plugin_path:
            self.logger.log(f"Vue TypeScript plugin not found. Vue file support will be limited.", logging.INFO)
        
        initialization_options = {}
        if vue_plugin_path:
            initialization_options["plugins"] = [
                {
                    "name": "@vue/typescript-plugin",
                    "location": vue_plugin_path,
                    "languages": ["vue"]
                }
            ]
        
        initialize_params = {
            "locale": "en",
            "initializationOptions": initialization_options,
            "capabilities": {
                "textDocument": {
                    "synchronization": {"didSave": True, "dynamicRegistration": True},
                    "completion": {"dynamicRegistration": True, "completionItem": {"snippetSupport": True}},
                    "definition": {"dynamicRegistration": True},
                    "references": {"dynamicRegistration": True},
                    "documentSymbol": {
                        "dynamicRegistration": True,
                        "hierarchicalDocumentSymbolSupport": True,
                        "symbolKind": {"valueSet": list(range(1, 27))},
                    },
                    "hover": {"dynamicRegistration": True, "contentFormat": ["markdown", "plaintext"]},
                    "signatureHelp": {"dynamicRegistration": True},
                    "codeAction": {"dynamicRegistration": True},
                },
                "workspace": {
                    "workspaceFolders": True,
                    "didChangeConfiguration": {"dynamicRegistration": True},
                    "symbol": {"dynamicRegistration": True},
                },
            },
            "processId": os.getpid(),
            "rootPath": repository_absolute_path,
            "rootUri": root_uri,
            "workspaceFolders": [
                {
                    "uri": root_uri,
                    "name": os.path.basename(repository_absolute_path),
                }
            ],
        }
        return initialize_params

    def _start_server(self):
        """
        Starts the TypeScript Language Server, waits for the server to be ready and yields the LanguageServer instance.

        Usage:
        ```
        async with lsp.start_server():
            # LanguageServer has been initialized and ready to serve requests
            await lsp.request_definition(...)
            await lsp.request_references(...)
            # Shutdown the LanguageServer on exit from scope
        # LanguageServer has been shutdown
        """

        def register_capability_handler(params):
            assert "registrations" in params
            for registration in params["registrations"]:
                if registration["method"] == "workspace/executeCommand":
                    self.initialize_searcher_command_available.set()
                    # TypeScript doesn't have a direct equivalent to resolve_main_method
                    # You might want to set a different flag or remove this line
                    # self.resolve_main_method_available.set()
            return

        def execute_client_command_handler(params):
            return []

        def do_nothing(params):
            return

        def window_log_message(msg):
            self.logger.log(f"LSP: window/logMessage: {msg}", logging.INFO)

        def check_experimental_status(params):
            """
            Also listen for experimental/serverStatus as a backup signal
            """
            self.logger.log(f"Received experimental/serverStatus: {params}", logging.DEBUG)
            if params.get("quiescent") == True:
                self.server_ready.set()
                self.completions_available.set()
                self._is_quiescent.set()
                self._last_quiescent_time = time.time()
                self.logger.log("TypeScript server is quiescent", logging.DEBUG)
            else:
                self._is_quiescent.clear()

        self.server.on_request("client/registerCapability", register_capability_handler)
        self.server.on_notification("window/logMessage", window_log_message)
        self.server.on_request("workspace/executeClientCommand", execute_client_command_handler)
        self.server.on_notification("$/progress", do_nothing)
        self.server.on_notification("textDocument/publishDiagnostics", do_nothing)
        self.server.on_notification("experimental/serverStatus", check_experimental_status)
        self.server.on_notification("$/typescriptVersion", lambda params: self.logger.log(f"TypeScript version: {params}", logging.INFO))

        self.logger.log("Starting TypeScript server process", logging.INFO)
        self.server.start()
        initialize_params = self._get_initialize_params(self.repository_root_path)

        self.logger.log(
            "Sending initialize request from LSP client to LSP server and awaiting response",
            logging.INFO,
        )
        init_response = self.server.send.initialize(initialize_params)

        # TypeScript-specific capability checks
        assert init_response["capabilities"]["textDocumentSync"] == 2
        assert "completionProvider" in init_response["capabilities"]
        assert init_response["capabilities"]["completionProvider"] == {
            "triggerCharacters": [".", '"', "'", "/", "@", "<"],
            "resolveProvider": True,
        }

        self.server.notify.initialized({})
        
        # For TypeScript server, mark as ready immediately after initialized
        # The server should be ready to handle requests at this point
        self.server_ready.set()
        self.completions_available.set()
        self.logger.log("TypeScript server marked as ready after initialization", logging.INFO)
        
        # Track initial indexing time
        self._initial_indexing_start_time = time.time()
        
        # If Vue plugin is configured, give it MORE time to initialize
        # But poll for quiescence instead of fixed wait!
        if self._vue_plugin_path:
            if self._is_quiescent.is_set():
                self.logger.log(f"Vue plugin detected, but server already quiescent - skipping initialization wait", logging.INFO)
            else:
                self.logger.log(f"Vue plugin detected, polling for initialization (max 5s)...", logging.INFO)
                # Poll instead of fixed wait
                start_time = time.time()
                while time.time() - start_time < 5.0:
                    if self._is_quiescent.is_set():
                        elapsed = time.time() - start_time
                        self.logger.log(f"Vue plugin initialized after {elapsed:.1f}s", logging.INFO)
                        break
                    sleep(0.1)
            
            self.logger.log("Vue plugin initialization complete", logging.DEBUG)
            # For Vue projects, consider initial indexing complete after the wait
            self._initial_indexing_complete = True
        else:
            # For non-Vue projects, consider indexing complete immediately
            self._initial_indexing_complete = True

    def is_indexing_complete(self) -> bool:
        """
        Check if the TypeScript server has completed initial indexing.
        For Vue projects, we need to ensure files are opened and indexed.
        """
        if not self._initial_indexing_complete:
            return False
            
        # For Vue projects, check if we've waited long enough
        # But if server is already quiescent, we're good to go!
        if self._vue_plugin_path and self._initial_indexing_start_time and not self._is_quiescent.is_set():
            elapsed = time.time() - self._initial_indexing_start_time
            # NO LIMITS! Give at least 10 seconds for Vue plugin indexing
            if elapsed < 10.0:
                return False
                
        return True
    
    @override
    def request_references(self, relative_file_path: str, line: int, column: int) -> list[LSLocation]:
        """
        Override request_references to handle Vue files properly.
        For Vue projects, we need to open relevant Vue files before searching for references.
        """
        # Check if indexing is complete
        if not self.is_indexing_complete():
            raise ServerIndexingException(
                "TypeScript language server is still indexing. Please wait a moment and try again."
            )
        
        if not self._vue_plugin_path:
            # No Vue plugin, use standard behavior
            return super().request_references(relative_file_path, line, column)
        
        # For Vue projects, we need special handling to open Vue files
        self.logger.log("Searching for references in Vue project...", logging.DEBUG)
        self.logger.log("Note: 'Could not find containing symbol' warnings for .vue files are expected and can be ignored", logging.INFO)
        
        if not self.server_started:
            self.logger.log("request_references called before Language Server started", logging.ERROR)
            raise SolidLSPException("Language Server not started")
        
        # Open the source file first
        with self.open_file(relative_file_path):
            # For Vue projects, also open some Vue files to help the server find references
            # This mimics what the working test does
            vue_files_to_check = []
            
            # Find Vue files, prioritizing those near the target file
            import glob
            repo_path = self.repository_root_path
            
            # Get the directory of the target file
            target_dir = os.path.dirname(relative_file_path)
            target_parts = target_dir.split(os.sep)
            
            # For store files, we want to focus on the module they belong to
            # e.g., for 'frontend/authorizations/src/store.ts', focus on 'frontend/authorizations'
            if 'store' in os.path.basename(relative_file_path).lower() and len(target_parts) > 2:
                # Go up to the module level (e.g., 'frontend/authorizations')
                module_dir = os.sep.join(target_parts[:2])
                self.logger.log(f"Store file detected, focusing on module: {module_dir}", logging.DEBUG)
            else:
                module_dir = target_dir
            
            # Collect Vue files with priority
            vue_files_by_priority = {
                1: [],  # Same directory
                2: [],  # Within module
                3: [],  # Sibling modules
                4: []   # Other files
            }
            
            for root, dirs, files in os.walk(repo_path):
                # Skip ignored directories
                dirs[:] = [d for d in dirs if not self.is_ignored_dirname(d)]
                
                for file in files:
                    if file.endswith('.vue'):
                        full_path = os.path.join(root, file)
                        rel_path = os.path.relpath(full_path, repo_path)
                        if not self.is_ignored_path(rel_path):
                            # Determine priority
                            file_dir = os.path.dirname(rel_path)
                            if file_dir == target_dir:
                                vue_files_by_priority[1].append(rel_path)
                            elif rel_path.startswith(module_dir + os.sep):
                                # File is within the same module
                                vue_files_by_priority[2].append(rel_path)
                            elif len(target_parts) > 1 and rel_path.startswith(target_parts[0] + os.sep):
                                # File is in a sibling module (same parent)
                                vue_files_by_priority[3].append(rel_path)
                            else:
                                vue_files_by_priority[4].append(rel_path)
            
            # Combine files by priority
            vue_files_to_check = []
            for priority in sorted(vue_files_by_priority.keys()):
                vue_files_to_check.extend(vue_files_by_priority[priority])
            
            # NO LIMITS! Let's see what happens
            self.logger.log(f"Found {len(vue_files_to_check)} Vue files, opening ALL of them (no limits!)", logging.WARNING)
            
            # Open Vue files - but skip ones we've already opened
            vue_contexts = []
            files_to_open = [f for f in vue_files_to_check if f not in self._opened_vue_files]
            already_opened = len(vue_files_to_check) - len(files_to_open)
            
            if already_opened > 0:
                self.logger.log(f"Skipping {already_opened} Vue files already opened, opening {len(files_to_open)} new files", logging.INFO)
            else:
                self.logger.log(f"Opening {len(files_to_open)} Vue files for reference search", logging.INFO)
                
            for vue_file in files_to_open:
                try:
                    ctx = self.open_file(vue_file)
                    vue_contexts.append((vue_file, ctx))
                    ctx.__enter__()
                    self._opened_vue_files.add(vue_file)
                except Exception as e:
                    self.logger.log(f"Failed to open Vue file {vue_file}: {e}", logging.DEBUG)
            
            # Wait for indexing - use dynamic timeout based on file count
            dynamic_timeout = min(5.0 + len(vue_contexts) * 0.05, 120.0)  # 5s base + 50ms per file, cap at 2 minutes
            self.logger.log(f"Waiting for quiescence with {dynamic_timeout:.1f}s timeout...", logging.DEBUG)
            if not self.wait_for_quiescence(timeout=dynamic_timeout):
                # For Vue projects, this is expected since TypeScript server doesn't send quiescence notifications
                if self._vue_plugin_path:
                    self.logger.log("Continuing without quiescence notification (expected for Vue projects)", logging.DEBUG)
                else:
                    self.logger.log("Server not quiescent, proceeding anyway", logging.WARNING)
            
            # Check if we actually need to wait - if server is already quiescent, skip the wait
            if self._is_quiescent.is_set():
                self.logger.log(f"Server already quiescent, skipping wait for {len(vue_contexts)} Vue files", logging.INFO)
            else:
                # Give time for Vue plugin to process files
                # More files = more time needed, NO CAP!
                wait_time = 2.0 + len(vue_contexts) * 0.05  # Base 2s + 50ms per file, no cap
                self.logger.log(f"Waiting {wait_time:.1f}s for Vue plugin to process {len(vue_contexts)} files (no cap!)", logging.WARNING)
                sleep(wait_time)
            
            try:
                # Now request references
                response = self._send_references_request(relative_file_path, line=line, column=column)
                
                # Convert response to expected format
                if response:
                    locations = []
                    for location in response:
                        uri = location.get("uri", "")
                        file_path = uri.replace("file://", "")
                        
                        # Convert to relative path
                        if file_path.startswith(self.repository_root_path):
                            relative_path = os.path.relpath(file_path, self.repository_root_path)
                        else:
                            relative_path = file_path
                        
                        # Filter out ignored paths
                        if not self.is_ignored_path(relative_path):
                            locations.append(LSLocation(
                                uri=uri,
                                range=location.get("range", {}),
                                absolutePath=file_path,
                                relativePath=relative_path
                            ))
                    
                    unique_files = set(loc['relativePath'] for loc in locations if loc['relativePath'])
                    vue_refs = sum(1 for f in unique_files if f.endswith('.vue'))
                    self.logger.log(f"Found {len(locations)} references across {len(unique_files)} files ({vue_refs} Vue files)", logging.INFO)
                    return locations
                else:
                    self.logger.log("No references found", logging.INFO)
                    return []
                    
            finally:
                # Close Vue files
                for vue_file, ctx in vue_contexts:
                    try:
                        ctx.__exit__(None, None, None)
                    except Exception as e:
                        self.logger.log(f"Error closing Vue file {vue_file}: {e}", logging.DEBUG)
    
    @override
    def request_referencing_symbols(
        self,
        relative_file_path: str,
        line: int,
        column: int,
        include_imports: bool = True,
        include_self: bool = False,
        include_body: bool = False,
        include_file_symbols: bool = False,
    ) -> list[ReferenceInSymbol]:
        """Override to check if server is ready before making request."""
        if not self.is_indexing_complete():
            self.logger.log("TypeScript server is still indexing, references may be incomplete", logging.WARNING)
            raise ServerIndexingException(
                "TypeScript language server is still indexing. Please wait a moment and try again."
            )
        return super().request_referencing_symbols(
            relative_file_path, line, column, include_imports, include_self, include_body, include_file_symbols
        )
    
    @override
    def _send_references_request(self, relative_file_path: str, line: int, column: int):
        """
        Send references request, but wait for server to be quiescent first.
        The TypeScript server needs to finish indexing before it can return complete results.
        """
        # Base implementation without extra waits since we handle that in request_references
        return super()._send_references_request(relative_file_path, line, column)
