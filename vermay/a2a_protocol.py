"""Shared names for Vermay's supported A2A protocol surface."""

A2A_PROTOCOL_VERSION = "0.3.0"

MESSAGE_SEND_METHOD = "message/send"
MESSAGE_STREAM_METHOD = "message/stream"
TASK_GET_METHOD = "tasks/get"
TASK_CANCEL_METHOD = "tasks/cancel"
TASK_RESUBSCRIBE_METHOD = "tasks/resubscribe"

# A Vermay extension for resuming a local approval continuation. A2A 0.3 does
# not define an equivalent standard method.
TASK_RESUME_METHOD = "tasks/resume"
TASK_RESUME_EXTENSION_URI = "urn:vermay:a2a:task-approval-resume:0.1"
TASK_RESUME_EXTENSION_VERSION = "0.1"
