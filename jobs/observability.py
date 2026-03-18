import logging


logger = logging.getLogger("nodo.lifecycle")


def log_job_transition(job_id, from_status, to_status, source):
    logger.info(
        "[JOB_TRANSITION] job=%s from=%s to=%s source=%s",
        job_id,
        from_status,
        to_status,
        source,
    )


def log_assignment_event(job_id, assignment_id, action, provider_id):
    logger.info(
        "[ASSIGNMENT_EVENT] job=%s assignment=%s action=%s provider=%s",
        job_id,
        assignment_id,
        action,
        provider_id,
    )


def log_marketplace_timeout(job_id, action):
    logger.info(
        "[MARKETPLACE_TIMEOUT] job=%s action=%s",
        job_id,
        action,
    )
