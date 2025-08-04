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
    
    def wait_for_quiescence(self, timeout: float = 10.0) -> bool:
        """
        Wait for the TypeScript server to become quiescent (idle).
        This is important before making requests that depend on indexing being complete.
        
        :param timeout: Maximum time to wait in seconds
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
            self.logger.log(f"Vue TypeScript plugin not found in any of: {potential_paths}", logging.WARNING)
        
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
        
        # If Vue plugin is configured, give it a moment to initialize
        # The working test shows 2 seconds is sufficient
        if self._vue_plugin_path:
            self.logger.log(f"Vue plugin configured at {self._vue_plugin_path}, waiting for initialization...", logging.INFO)
            sleep(2.0)
            self.logger.log("Vue plugin initialization wait complete", logging.INFO)
            # For Vue projects, consider initial indexing complete after the wait
            self._initial_indexing_complete = True
        else:
            self.logger.log("No Vue plugin found, proceeding without Vue support", logging.WARNING)
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
        if self._vue_plugin_path and self._initial_indexing_start_time:
            elapsed = time.time() - self._initial_indexing_start_time
            # Give at least 3 seconds for Vue plugin indexing
            if elapsed < 3.0:
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
            self.logger.log("TypeScript server is still indexing, references may be incomplete", logging.WARNING)
            raise ServerIndexingException(
                "TypeScript language server is still indexing. Please wait a moment and try again."
            )
        
        if not self._vue_plugin_path:
            # No Vue plugin, use standard behavior
            return super().request_references(relative_file_path, line, column)
        
        # For Vue projects, we need special handling
        self.logger.log("Vue project detected, using enhanced reference search", logging.DEBUG)
        
        if not self.server_started:
            self.logger.log("request_references called before Language Server started", logging.ERROR)
            raise SolidLSPException("Language Server not started")
        
        # Open the source file first
        with self.open_file(relative_file_path):
            # For Vue projects, also open some Vue files to help the server find references
            # This mimics what the working test does
            vue_files_to_check = []
            
            # Find Vue files in the same directory and common locations
            import glob
            repo_path = self.repository_root_path
            patterns = [
                "src/*.vue",
                "src/components/*.vue", 
                "src/views/*.vue",
                "*.vue"
            ]
            
            for pattern in patterns:
                full_pattern = os.path.join(repo_path, pattern)
                for vue_file in glob.glob(full_pattern):
                    rel_path = os.path.relpath(vue_file, repo_path)
                    if not self.is_ignored_path(rel_path):
                        vue_files_to_check.append(rel_path)
            
            # Limit to first 10 Vue files to avoid opening too many
            vue_files_to_check = vue_files_to_check[:10]
            
            # Open Vue files
            vue_contexts = []
            for vue_file in vue_files_to_check:
                try:
                    ctx = self.open_file(vue_file)
                    vue_contexts.append((vue_file, ctx))
                    ctx.__enter__()
                    self.logger.log(f"Opened Vue file: {vue_file}", logging.DEBUG)
                except Exception as e:
                    self.logger.log(f"Failed to open Vue file {vue_file}: {e}", logging.DEBUG)
            
            # Wait for indexing
            if not self.wait_for_quiescence(timeout=10.0):
                self.logger.log("Server not quiescent, proceeding anyway", logging.WARNING)
            
            # Give time for Vue plugin to process files
            sleep(2.0)
            
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
                    
                    return locations
                else:
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
