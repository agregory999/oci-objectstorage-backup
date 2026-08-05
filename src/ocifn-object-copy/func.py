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


def _configure_logger(ctx):
    """Configure INFO logging by default, with optional DEBUG detail."""
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    debug_value = str(ctx.Config().get("DEBUG", "")).strip().lower()
    if debug_value in _TRUTHY_VALUES:
        logger.setLevel(logging.DEBUG)
        logger.debug("DEBUG config detected; log level set to DEBUG")

    return logger


def _json_response(ctx, payload):
    """Build a JSON response for direct invokers of the function."""
    return response.Response(
        ctx,
        response_data=json.dumps(payload),
        headers={"Content-Type": "application/json"},
    )


def _parse_event(data, logger):
    """Parse the Object Storage event payload and log its receipt."""
    raw = data.getvalue() if data else b""
    event = json.loads(raw)
    logger.info("Received Object Storage event")
    logger.debug("Incoming event JSON: %s", json.dumps(event))
    logger.debug("Raw event size: %d bytes", len(raw))
    return event


def _extract_object_details(event):
    """Return the source namespace, bucket, object, and resource ID from an event."""
    event_data = event["data"]
    details = event_data["additionalDetails"]
    return (
        details["namespace"],
        details["bucketName"],
        event_data["resourceName"],
        event_data["resourceId"],
    )


def _create_object_storage_client(region, logger):
    """Create a resource-principal Object Storage client for the function region."""
    signer = oci.auth.signers.get_resource_principals_signer()
    client = oci.object_storage.ObjectStorageClient(
        {},
        signer=signer,
        retry_strategy=oci.retry.DEFAULT_RETRY_STRATEGY,
    )
    client.base_client.set_region(region)
    logger.info("Set Object Storage client region to %s", region)
    return client


def _backup_bucket_exists(client, namespace, bucket_name, logger):
    """Return whether the derived backup bucket already exists."""
    logger.debug("Checking backup bucket %s in namespace %s", bucket_name, namespace)
    try:
        client.get_bucket(namespace, bucket_name)
        logger.info("Backup bucket %s exists", bucket_name)
        return True
    except oci.exceptions.ServiceError as error:
        if error.status != 404:
            raise
        return False


def _source_bucket_exists(client, namespace, bucket_name, logger):
    """Return whether the event's source bucket still exists before creating a backup."""
    logger.debug("Checking source bucket %s in namespace %s", bucket_name, namespace)
    try:
        client.get_bucket(namespace, bucket_name)
        logger.info("Source bucket %s exists", bucket_name)
        return True
    except oci.exceptions.ServiceError as error:
        if error.status != 404:
            raise
        return False


def _create_backup_bucket(client, namespace, bucket_name, compartment_ocid, logger):
    """Create the missing backup bucket with Archive as its default storage tier."""
    logger.info("Backup bucket %s not found; creating it as an Archive bucket", bucket_name)
    client.create_bucket(
        namespace,
        oci.object_storage.models.CreateBucketDetails(
            name=bucket_name,
            compartment_id=compartment_ocid,
            storage_tier="Archive",
        ),
    )
    logger.debug("Created Archive bucket %s in compartment %s", bucket_name, compartment_ocid)


def _copy_object(
    client,
    namespace,
    source_bucket,
    source_object,
    destination_bucket,
    region,
    logger,
):
    """Submit an OCI server-side copy request without transferring object bytes through Fn."""
    copy_details = CopyObjectDetails(
        source_object_name=source_object,
        destination_object_name=source_object,
        destination_namespace=namespace,
        destination_bucket=destination_bucket,
        destination_region=region,
        destination_object_if_none_match_e_tag="*",
    )
    request_id = f"fn-copy-{uuid.uuid4()}"
    logger.debug(
        "Submitting server-side copy: source=%s/%s, destination=%s/%s, request_id=%s",
        source_bucket,
        source_object,
        destination_bucket,
        source_object,
        request_id,
    )

    start_time = time.perf_counter()
    try:
        client.copy_object(
            namespace,
            source_bucket,
            copy_details,
            opc_client_request_id=request_id,
            retry_strategy=oci.retry.DEFAULT_RETRY_STRATEGY,
        )
    except oci.exceptions.ServiceError as error:
        if error.status != 412:
            raise
        logger.info(
            "Backup object %s already exists in %s; treating copy as successful",
            source_object,
            destination_bucket,
        )
        return

    elapsed = time.perf_counter() - start_time
    logger.info(
        "Submitted copy of %s to backup bucket %s in %.4fs",
        source_object,
        destination_bucket,
        elapsed,
    )


def handler(ctx, data: Optional[io.BytesIO] = None):
    """Copy each Object Storage create-event object to its configured backup bucket."""
    logger = _configure_logger(ctx)
    backup_compartment_ocid = ctx.Config().get("BACKUP_COMPARTMENT_OCID", "NOT_SET")
    backup_bucket_suffix = ctx.Config().get("BACKUP_BUCKET_SUFFIX", "-backup")
    logger.info("Configured BACKUP_COMPARTMENT_OCID: %s", backup_compartment_ocid)
    logger.info("Configured BACKUP_BUCKET_SUFFIX: %s", backup_bucket_suffix)

    try:
        event = _parse_event(data, logger)
    except Exception as error:
        logger.error("Error parsing event JSON: %s", error)
        return _json_response(ctx, {"error": "Invalid event JSON"})

    try:
        namespace, source_bucket, source_object, resource_id = _extract_object_details(event)
    except Exception as error:
        logger.error("Missing Object Storage event details: %s", error)
        return _json_response(ctx, {"error": "Missing Object Storage event details"})

    logger.info(
        "Processing resource %s from bucket %s: %s",
        resource_id,
        source_bucket,
        source_object,
    )
    region = os.environ.get("OCI_RESOURCE_PRINCIPAL_REGION")
    if not region:
        logger.error("OCI_RESOURCE_PRINCIPAL_REGION is not set")
        return _json_response(ctx, {"error": "Missing function region"})

    destination_bucket = f"{source_bucket}{backup_bucket_suffix}"
    logger.debug("Derived backup bucket name: %s", destination_bucket)

    try:
        object_storage = _create_object_storage_client(region, logger)
        if not _source_bucket_exists(object_storage, namespace, source_bucket, logger):
            logger.error("Source bucket %s does not exist; not creating a backup bucket", source_bucket)
            return _json_response(ctx, {"error": "Source bucket does not exist"})
        if not _backup_bucket_exists(object_storage, namespace, destination_bucket, logger):
            _create_backup_bucket(
                object_storage,
                namespace,
                destination_bucket,
                backup_compartment_ocid,
                logger,
            )
    except Exception as error:
        logger.error("Failed to get or create backup bucket %s: %s", destination_bucket, error)
        return _json_response(ctx, {"error": "Failed to get or create backup bucket"})

    try:
        _copy_object(
            object_storage,
            namespace,
            source_bucket,
            source_object,
            destination_bucket,
            region,
            logger,
        )
    except oci.exceptions.ServiceError as error:
        logger.error("Failed to copy object to backup bucket %s: %s", destination_bucket, error)
        return _json_response(ctx, {"error": "Failed to copy object to backup bucket"})
    except Exception as error:
        logger.error("Unexpected error during copy: %s", error)
        return _json_response(ctx, {"error": "Failed to copy object to backup bucket"})

    # Direct CLI/SDK invokers receive this body. OCI Events does not display it;
    # the INFO log above is the operational record for event-driven copies.
    return _json_response(
        ctx,
        {
            "status": "success",
            "source_bucket": source_bucket,
            "backup_bucket": destination_bucket,
            "object": source_object,
        },
    )
