import io
import json
import logging
import time
from typing import Optional
import oci
import uuid
from oci.object_storage.models import CopyObjectDetails
from fdk import response
import os


def handler(ctx, data: Optional[io.BytesIO] = None):

    logger = logging.getLogger()
    # Elevate to DEBUG if function config DEBUG is truthy ("true", "1", "yes", "y", "on")
    debug_cfg = str(ctx.Config().get("DEBUG", "")).strip().lower()
    if debug_cfg in {"1", "true", "yes", "y", "on"}:
        logger.setLevel(logging.DEBUG)
        logger.debug("DEBUG config detected; log level set to DEBUG")
    backup_compartment_ocid = ctx.Config().get("BACKUP_COMPARTMENT_OCID", "NOT_SET")
    logger.info(f"Configured BACKUP_COMPARTMENT_OCID: {backup_compartment_ocid}")

    try:
        raw = data.getvalue() if data else b""
        event_json = json.loads(raw)
        logger.info(f"Incoming event JSON: {json.dumps(event_json)}")
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"Raw event size: {len(raw)} bytes")
    except Exception as ex:
        logger.error(f"Error parsing event JSON: {ex}")
        return response.Response(
            ctx,
            response_data=json.dumps({"error": "Invalid event JSON"}),
            headers={"Content-Type": "application/json"}
        )

    # Extract resourceId, bucketName, and objectName
    try:
        resource_id = event_json["data"]["resourceId"]
        bucket_name = event_json["data"]["additionalDetails"]["bucketName"]
        object_name = event_json["data"]["resourceName"]
        logger.info(f"resourceId: {resource_id}, bucketName: {bucket_name}, objectName: {object_name}")
    except Exception as ex:
        logger.error(f"Missing resourceId, bucketName, or resourceName: {ex}")
        return response.Response(
            ctx,
            response_data=json.dumps({"error": "Missing resourceId, bucketName, or resourceName"}),
            headers={"Content-Type": "application/json"}
        )

    # Set up OCI clients
    signer = oci.auth.signers.get_resource_principals_signer()
    object_storage = oci.object_storage.ObjectStorageClient(
        {},
        signer=signer,
        retry_strategy=oci.retry.DEFAULT_RETRY_STRATEGY
    )
    # Set the region from the Client
    region = os.environ.get("OCI_RESOURCE_PRINCIPAL_REGION")
    if region:
        object_storage.base_client.set_region(region)
        logger.info(f"Set Object Storage client region to {region}")
    else:
        logger.error("Missing region in event data")
        return response.Response(
            ctx,
            response_data=json.dumps({"error": "Missing region in event data"}),
            headers={"Content-Type": "application/json"}
        )

    # Get namespace
    try:
        namespace = event_json["data"]["additionalDetails"]["namespace"]
    except Exception as ex:
        logger.error(f"Missing namespace: {ex}")
        return response.Response(
            ctx,
            response_data=json.dumps({"error": "Missing namespace"}),
            headers={"Content-Type": "application/json"}
        )

    # Prepare backup bucket name
    backup_bucket_name = f"{bucket_name}-backup"
    logger.debug(f"Prepared backup bucket name: {backup_bucket_name}")

    # Check if backup bucket exists, create if not
    logger.debug(f"Checking/creating backup bucket in namespace '{namespace}'")
    try:
        object_storage.get_bucket(namespace, backup_bucket_name)
        logger.info(f"Backup bucket {backup_bucket_name} exists.")
    except oci.exceptions.ServiceError as e:
        if e.status == 404:
            logger.info(f"Backup bucket {backup_bucket_name} not found. Creating as archive bucket.")
            object_storage.create_bucket(
                namespace,
                oci.object_storage.models.CreateBucketDetails(
                    name=backup_bucket_name,
                    compartment_id=backup_compartment_ocid,
                    storage_tier="Archive"
                )
            )
            logger.debug(f"CreateBucketDetails(name={backup_bucket_name}, compartment_id={backup_compartment_ocid}, storage_tier='Archive')")
        else:
            logger.error(f"Error checking/creating backup bucket: {e}")
            return response.Response(
                ctx,
                response_data=json.dumps({"error": "Failed to get or create backup bucket"}),
                headers={"Content-Type": "application/json"}
            )


    # Server-side copy to the backup bucket (no download/put through the function)
    start_copy = None  # perf counter set just before initiating copy
    try:
        addl = event_json["data"].get("additionalDetails", {})
        src_etag = addl.get("eTag")
        src_version = addl.get("versionId")
        logger.debug(f"Source details for copy: namespace={namespace}, bucket={bucket_name}, object={object_name}, eTag={src_etag}, versionId={src_version}")

        copy_details = CopyObjectDetails(
            source_object_name=object_name,
            destination_object_name=object_name,
            destination_namespace=namespace,
            destination_bucket=backup_bucket_name,
            destination_region=region,
        )

        logger.debug(f"Prepared CopyObjectDetails for destination bucket={backup_bucket_name}, object={object_name}, storage_tier='Archive', if_none_match='*'")

        opc_client_request_id = f"fn-copy-{uuid.uuid4()}"
        logger.debug(f"Initiating server-side copy with opc_client_request_id={opc_client_request_id}")
        start_copy = time.perf_counter()
        object_storage.copy_object(
            namespace,
            bucket_name,
            copy_details,
            opc_client_request_id=opc_client_request_id,
            retry_strategy=oci.retry.DEFAULT_RETRY_STRATEGY
        )
        copy_time_sec = time.perf_counter() - start_copy
        logger.info(f"Copied object {object_name} to backup bucket {backup_bucket_name} via server-side copy in {copy_time_sec:.4f}s")
    except oci.exceptions.ServiceError as e:
        # 412 => destination already has the object due to IF-NONE-MATCH; treat as success/idempotent
        if e.status == 412:
            if start_copy is not None:
                copy_time_sec = time.perf_counter() - start_copy
                logger.info(f"Backup object {object_name} already exists in {backup_bucket_name}; treating as success (attempt took {copy_time_sec:.4f}s)")
            else:
                logger.info(f"Backup object {object_name} already exists in {backup_bucket_name}; treating as success")
        else:
            logger.error(f"Failed to copy object to backup bucket: {e}")
            return response.Response(
                ctx,
                response_data=json.dumps({"error": "Failed to copy object to backup bucket"}),
                headers={"Content-Type": "application/json"}
            )
    except Exception as ex:
        logger.error(f"Unexpected error during copy: {ex}")
        return response.Response(
            ctx,
            response_data=json.dumps({"error": "Failed to copy object to backup bucket"}),
            headers={"Content-Type": "application/json"}
        )

    return response.Response(
        ctx,
        response_data=json.dumps({
            "status": "success",
            "source_bucket": bucket_name,
            "backup_bucket": backup_bucket_name,
            "object": object_name
        }),
        headers={"Content-Type": "application/json"}
    )
