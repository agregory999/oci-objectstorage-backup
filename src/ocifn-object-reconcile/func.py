import io
import json
import logging
import os
import time
import uuid
from typing import Optional

import oci
from fdk import response
from oci.object_storage.models import CopyObjectDetails


_TRUTHY_VALUES = {"1", "true", "yes", "y", "on"}
_LIST_PAGE_SIZE = 1000


def _configure_logger(ctx):
    """Use INFO logging by default and enable DEBUG only when configured."""
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    if str(ctx.Config().get("DEBUG", "")).strip().lower() in _TRUTHY_VALUES:
        logger.setLevel(logging.DEBUG)
        logger.debug("DEBUG config detected; log level set to DEBUG")
    return logger


def _response(ctx, payload, status_code=200):
    return response.Response(
        ctx,
        response_data=json.dumps(payload),
        headers={"Content-Type": "application/json"},
        status_code=status_code,
    )


def _invocation_payload(data):
    """Return an optional JSON payload supplied by Resource Scheduler or a direct caller."""
    raw = data.getvalue() if data else b""
    return json.loads(raw) if raw else {}


def _required_value(payload, config, key):
    """Use an invocation-payload value first, then fall back to function configuration."""
    value = payload.get(key) or config.get(key)
    if not value:
        raise ValueError(f"Missing required configuration: {key}")
    return value


def _create_client(region):
    signer = oci.auth.signers.get_resource_principals_signer()
    client = oci.object_storage.ObjectStorageClient(
        {}, signer=signer, retry_strategy=oci.retry.DEFAULT_RETRY_STRATEGY
    )
    client.base_client.set_region(region)
    return client


def _get_namespace(client):
    """Get this tenancy's immutable Object Storage namespace from OCI."""
    return client.get_namespace(retry_strategy=oci.retry.DEFAULT_RETRY_STRATEGY).data


def _ensure_backup_bucket(client, namespace, bucket_name, compartment_ocid, logger):
    """Create the Archive-tier destination bucket only when it does not already exist."""
    try:
        client.get_bucket(namespace, bucket_name)
        logger.info("Backup bucket %s exists", bucket_name)
        return
    except oci.exceptions.ServiceError as error:
        if error.status != 404:
            raise

    logger.info("Backup bucket %s not found; creating it as an Archive bucket", bucket_name)
    client.create_bucket(
        namespace,
        oci.object_storage.models.CreateBucketDetails(
            name=bucket_name,
            compartment_id=compartment_ocid,
            storage_tier="Archive",
        ),
    )


def _source_bucket_exists(client, namespace, bucket_name, logger):
    """Return whether the scheduled source bucket exists before creating a backup."""
    try:
        client.get_bucket(namespace, bucket_name)
        logger.info("Source bucket %s exists", bucket_name)
        return True
    except oci.exceptions.ServiceError as error:
        if error.status != 404:
            raise
        return False


def _iter_object_names(client, namespace, bucket_name, prefix):
    """Yield object names in lexical order using bounded-memory ListObjects pages."""
    start_after = None
    while True:
        request = {"limit": _LIST_PAGE_SIZE}
        if prefix:
            request["prefix"] = prefix
        if start_after:
            request["start_after"] = start_after

        page = client.list_objects(namespace, bucket_name, **request).data.objects
        if not page:
            return

        for object_summary in page:
            yield object_summary.name

        if len(page) < _LIST_PAGE_SIZE:
            return
        start_after = page[-1].name


def _submit_copy(client, namespace, source_bucket, object_name, destination_bucket, region):
    """Submit one server-side copy request; no data is downloaded by the function."""
    details = CopyObjectDetails(
        source_object_name=object_name,
        destination_object_name=object_name,
        destination_namespace=namespace,
        destination_bucket=destination_bucket,
        destination_region=region,
        destination_object_if_none_match_e_tag="*",
    )
    try:
        client.copy_object(
            namespace,
            source_bucket,
            details,
            opc_client_request_id=f"fn-reconcile-{uuid.uuid4()}",
            retry_strategy=oci.retry.DEFAULT_RETRY_STRATEGY,
        )
        return True
    except oci.exceptions.ServiceError as error:
        if error.status != 412:
            raise
        return False


def _reconcile(client, namespace, source_bucket, destination_bucket, prefix, region, logger):
    """Merge two ordered listings and copy only source names absent from destination."""
    source_names = _iter_object_names(client, namespace, source_bucket, prefix)
    destination_names = _iter_object_names(client, namespace, destination_bucket, prefix)
    destination_name = next(destination_names, None)
    source_count = 0
    copy_count = 0
    start_time = time.perf_counter()

    for source_name in source_names:
        source_count += 1
        while destination_name is not None and destination_name < source_name:
            destination_name = next(destination_names, None)

        if destination_name == source_name:
            destination_name = next(destination_names, None)
            continue

        copied = _submit_copy(
            client,
            namespace,
            source_bucket,
            source_name,
            destination_bucket,
            region,
        )
        if copied:
            copy_count += 1
            logger.debug("Submitted missing-object copy: %s", source_name)
        else:
            logger.debug("Backup object appeared during reconciliation: %s", source_name)

    elapsed = time.perf_counter() - start_time
    logger.info(
        "Reconciliation complete: evaluated=%d, copies_submitted=%d, elapsed_seconds=%.4f",
        source_count,
        copy_count,
        elapsed,
    )
    return source_count, copy_count, elapsed


def handler(ctx, data: Optional[io.BytesIO] = None):
    """Run a scheduled, missing-object reconciliation for one source bucket."""
    logger = _configure_logger(ctx)
    config = ctx.Config()

    try:
        payload = _invocation_payload(data)
        source_bucket = _required_value(payload, config, "SOURCE_BUCKET")
        backup_compartment_ocid = _required_value(payload, config, "BACKUP_COMPARTMENT_OCID")
    except (AttributeError, ValueError, json.JSONDecodeError) as error:
        logger.error("Invalid reconciliation configuration: %s", error)
        return _response(ctx, {"error": str(error)}, status_code=400)

    region = os.environ.get("OCI_RESOURCE_PRINCIPAL_REGION")
    if not region:
        logger.error("OCI_RESOURCE_PRINCIPAL_REGION is not set")
        return _response(ctx, {"error": "Missing function region"}, status_code=500)

    suffix = payload.get("BACKUP_BUCKET_SUFFIX") or config.get(
        "BACKUP_BUCKET_SUFFIX", "-backup"
    )
    prefix = payload.get("SOURCE_PREFIX") or config.get("SOURCE_PREFIX")
    destination_bucket = f"{source_bucket}{suffix}"
    logger.info(
        "Reconciling source bucket %s against backup bucket %s (prefix=%s)",
        source_bucket,
        destination_bucket,
        prefix or "<all objects>",
    )

    try:
        client = _create_client(region)
        namespace = _get_namespace(client)
        logger.debug("Retrieved Object Storage namespace: %s", namespace)
        if not _source_bucket_exists(client, namespace, source_bucket, logger):
            logger.error("Source bucket %s does not exist; not creating a backup bucket", source_bucket)
            return _response(ctx, {"error": "Source bucket does not exist"}, status_code=404)
        _ensure_backup_bucket(
            client, namespace, destination_bucket, backup_compartment_ocid, logger
        )
        source_count, copy_count, elapsed = _reconcile(
            client,
            namespace,
            source_bucket,
            destination_bucket,
            prefix,
            region,
            logger,
        )
    except Exception as error:
        logger.exception("Reconciliation failed: %s", error)
        return _response(
            ctx, {"error": "Reconciliation failed", "detail": str(error)}, status_code=500
        )

    # Resource Scheduler does not display this body; it remains useful for direct invocations.
    return _response(
        ctx,
        {
            "status": "success",
            "source_bucket": source_bucket,
            "backup_bucket": destination_bucket,
            "source_objects_evaluated": source_count,
            "copies_submitted": copy_count,
            "elapsed_seconds": round(elapsed, 4),
        },
    )
