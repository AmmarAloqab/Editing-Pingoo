class FlowWorkerError(RuntimeError):
    code = "FLOW_WORKER_ERROR"


class FlowAuthRequired(FlowWorkerError):
    code = "FLOW_AUTH_REQUIRED"


class FlowUiChanged(FlowWorkerError):
    code = "FLOW_UI_CHANGED"


class FlowGenerationFailed(FlowWorkerError):
    code = "FLOW_GENERATION_FAILED"


class FlowGenerationTimeout(FlowWorkerError):
    code = "FLOW_GENERATION_TIMEOUT"


class FlowDownloadFailed(FlowWorkerError):
    code = "FLOW_DOWNLOAD_FAILED"


class PingooUploadFailed(FlowWorkerError):
    code = "PINGOO_UPLOAD_FAILED"
