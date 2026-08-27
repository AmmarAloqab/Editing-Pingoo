class FlowWorkerError(RuntimeError):
    code = "FLOW_WORKER_ERROR"


class FlowAuthRequired(FlowWorkerError):
    code = "FLOW_AUTH_REQUIRED"


class FlowUiChanged(FlowWorkerError):
    code = "FLOW_UI_CHANGED"


class FlowBlockedEmptyDom(FlowUiChanged):
    code = "FLOW_BLOCKED_EMPTY_DOM"


class FlowProjectNavigationChanged(FlowUiChanged):
    code = "FLOW_UI_CHANGED_PROJECT_NAVIGATION"


class FlowProjectCreateChanged(FlowProjectNavigationChanged):
    code = "FLOW_UI_CHANGED_PROJECT_CREATE"


class FlowWorkspaceLoadChanged(FlowUiChanged):
    code = "FLOW_UI_CHANGED_WORKSPACE_LOAD"


class FlowProjectNavigationFailed(FlowWorkspaceLoadChanged):
    code = "FLOW_PROJECT_NAVIGATION_FAILED"


class FlowPromptInputChanged(FlowUiChanged):
    code = "FLOW_UI_CHANGED_PROMPT_INPUT"


class FlowGenerateActionChanged(FlowUiChanged):
    code = "FLOW_UI_CHANGED_GENERATE_ACTION"


class FlowGenerateButtonChanged(FlowGenerateActionChanged):
    code = "FLOW_UI_CHANGED_GENERATE_BUTTON"


class FlowGenerationFailed(FlowWorkerError):
    code = "FLOW_GENERATION_FAILED"


class FlowGenerationTimeout(FlowWorkerError):
    code = "FLOW_GENERATION_TIMEOUT"


class FlowDownloadFailed(FlowWorkerError):
    code = "FLOW_DOWNLOAD_FAILED"


class PingooUploadFailed(FlowWorkerError):
    code = "PINGOO_UPLOAD_FAILED"
